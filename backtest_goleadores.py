"""
backtest_goleadores.py -- ¿predice mejor al goleador el xG del jugador que sus goles? (2026-11-15)
Y al asistente el xA que sus asistencias (--asistencias, 2026-11-16), y "anotará o asistirá"
juntando los dos (--combinado).

Datos: datos/goleadores_fotmob.jsonl (descargar_goleadores.py): partidos al azar con, para cada
jugador que jugo, su xG, goles, minutos y titularidad en los 40 partidos anteriores del equipo.
Cada caso solo usa lo anterior al partido, y solo partidos con xG. Solo TITULARES de campo (sin
porteros) con 3+ apariciones previas.

  lambda  = ritmo por 90' x minutos esperados / 90 x factor del partido x c
  ritmo   = (w * xG + (1 - w) * goles + K * ritmo de su posicion) / (minutos / 90 + K)
  minutos = sus minutos de titular, encogidos hacia los de su posicion (K_MIN)
  factor  = goles esperados del equipo segun las cuotas de apertura de bet365 (Más/Menos 2.5 y
            1X2, simulador.anclar_al_mercado) / su xG medio en liga (encogido hacia la liga)
  P(marca) = 1 - exp(-lambda)

Variantes: posicion (solo el ritmo de su posicion), goles (w = 0), xg (w = 1), mezcla (w libre);
y "sin mercado" (factor = 1) para ver lo que aporta el partido. Parametros ajustados en los
partidos anteriores a CORTE, medidos despues. Sin cuotas historicas de goleador: mide
calibracion y log-loss, no valor.

Asistencias: lo mismo con xA y asistencias (datos/asistencias_fotmob.jsonl, los mismos partidos);
el factor del partido sigue siendo el de goles del equipo.
Anotara o asistira: P = 1 - exp(-kappa (lambda_goles + lambda_asist)), kappa ajustado en el periodo
de ajuste (un jugador no marca y asiste el mismo gol, pero sus goles y asistencias no son
independientes: kappa lo mide).

  python backtest_goleadores.py                 -> resultados/goleadores_backtest.txt, datos/agregados/goleadores_coef.json
  python backtest_goleadores.py --asistencias   -> resultados/asistencias_backtest.txt, datos/agregados/asistencias_coef.json
  python backtest_goleadores.py --combinado     -> resultados/anota_o_asiste_backtest.txt (kappa en asistencias_coef.json)
"""
import json
from pathlib import Path
import numpy as np, pandas as pd
from scipy.optimize import minimize

DIR = Path(__file__).parent
OBJ = {'goles': dict(datos=DIR / 'datos' / 'goleadores_fotmob.jsonl', nombre='goleadores',
                     x='xG', real='goles', verbo='marcan'),
       'asist': dict(datos=DIR / 'datos' / 'asistencias_fotmob.jsonl', nombre='asistencias',
                     x='xA', real='asistencias', verbo='asisten')}
CORTE = '2026-02-01'
K_EQ = 5.0                      # partidos virtuales de la media de la liga en el xG del equipo


def casos(datos):
    """Celdas [valor esperado (xG/xA), valor real (goles/asist), minutos, titular]; en las
    columnas, el xG del equipo (filtro de partidos con datos y su xG medio)."""
    filas = []
    for l in datos.read_text(encoding='utf-8').splitlines():
        r = json.loads(l)
        cols = r['cols']
        e = next((i for i, c in enumerate(cols) if c and c['este']), None)
        if e is None:
            continue
        ce = cols[e]
        # solo partidos con xG (FotMob no lo tiene en Serie B, Ligue 2 y Hypermotion antes de
        # 2025-07 ni en muchas copas): los mismos para todas las variantes
        prev = [i for i, c in enumerate(cols) if c and i != e and c['fecha'] < ce['fecha'] and (c['xg'] or 0) > 0]
        liga_prev = [i for i in prev if cols[i]['liga'] == ce['liga']]
        for j in r['jugadores']:
            c = j['c']
            if e >= len(c) or c[e] is None or j['pos'] == 'POR':
                continue
            xg_e, g_e, m_e, t_e = c[e]
            P = [c[i] for i in prev if i < len(c) and c[i] is not None and c[i][2]]
            a = np.array([[p[0] or 0, p[1] or 0, p[2], p[3]] for p in P], float).reshape(-1, 4)
            filas.append(dict(partido=r['partido'], equipo_id=r['equipo_id'], fecha=ce['fecha'][:10],
                              liga=ce['liga'], local=ce['local'], jugador=j['id'], nombre=j['nombre'],
                              pos=j['pos'], tit=bool(t_e), min=m_e, y=float((g_e or 0) >= 1), v=g_e or 0,
                              xg_s=a[:, 0].sum(), g_s=a[:, 1].sum(), mins=a[:, 2].sum(),
                              n_tit=a[:, 3].sum(), min_tit=(a[:, 2] * a[:, 3]).sum(), n_ap=len(a),
                              xg_sub=(a[:, 0] * (1 - a[:, 3])).sum(), g_sub=(a[:, 1] * (1 - a[:, 3])).sum(),
                              min_sub=(a[:, 2] * (1 - a[:, 3])).sum(),
                              eq_xg=sum(cols[i]['xg'] for i in liga_prev), eq_n=len(liga_prev)))
    return pd.DataFrame(filas)


def mercado(D):
    """Goles esperados del equipo segun la apertura de bet365 (partidos.csv emparejado con FotMob)."""
    import motor, stats_equipos, simulador as sm
    P = stats_equipos.unir(motor.cargar(DIR / 'datos' / 'partidos.csv'))
    P = P[P.fm_partido_id.isin(D.partido.unique())]
    out = {}
    for x in P.itertuples():
        o = [getattr(x, k) for k in ('b365_1', 'b365_x', 'b365_2', 'b365_over25', 'b365_under25')]
        if any(pd.isna(v) for v in o):
            continue
        R = sm.anclar_al_mercado(dict(lh=1.5, la=1.2), *o)
        if R:
            out[int(x.fm_partido_id)] = (R['lh'], R['la'])
    mu = [out.get(p, (np.nan, np.nan))[0 if l == 1 else 1] for p, l in zip(D.partido, D.local)]
    return D.assign(mu=mu)


def lam(D, th, base, con_mercado=True):
    w, K, Km, c = th
    rpos = w * base['rx'] + (1 - w) * base['rg']
    rp = D.pos.map(rpos).fillna(rpos.mean()).values
    r90 = (w * D.xg_s + (1 - w) * D.g_s + K * rp) / (D.mins / 90 + K)
    mp = D.pos.map(base['min_pos']).fillna(base['min_pos'].mean()).values
    mesp = (D.min_tit + Km * mp) / (D.n_tit + Km)
    f = (D.mu / D.eq_rate).values if con_mercado else 1.0
    return np.clip(r90 * mesp / 90 * f * c, 1e-4, 5)


def ll(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def base_de(F):
    # historiales de jugadores distintos: cada jugador una vez (su ultimo caso) para las medias
    u = F.sort_values('fecha').drop_duplicates('jugador', keep='last')
    g = u.groupby('pos')[['xg_s', 'g_s', 'mins', 'min_tit', 'n_tit', 'xg_sub', 'g_sub', 'min_sub']].sum()
    return dict(rx=g.xg_s / (g.mins / 90), rg=g.g_s / (g.mins / 90), min_pos=g.min_tit / g.n_tit,
                rsx=g.xg_sub / (g.min_sub / 90), rsg=g.g_sub / (g.min_sub / 90))


def ajustar(F, base, fijo=None, con_mercado=True):
    """fijo: {indice: valor} de parametros que no se ajustan (w de la variante)."""
    th0 = np.array([0.5, 8.0, 3.0, 1.0])
    libres = [i for i in range(4) if i not in (fijo or {})]
    def full(x):
        th = th0.copy(); th[libres] = x
        for i, v in (fijo or {}).items():
            th[i] = v
        return th
    def obj(x):
        th = full(x)
        if not (0 <= th[0] <= 1 and th[1] > 0.1 and th[2] > 0.1 and th[3] > 0.2):
            return 1e9
        return ll(1 - np.exp(-lam(F, th, base, con_mercado)), F.y.values).mean()
    r = minimize(obj, th0[libres], method='Nelder-Mead', options=dict(xatol=1e-3, fatol=1e-7, maxiter=2000))
    return full(r.x)


def preparar(obj):
    D = mercado(casos(OBJ[obj]['datos']))
    D = D[D.tit & D.mu.notna() & (D.n_ap >= 3)].copy()     # 3+ partidos previos con xG
    lig = D.drop_duplicates(['partido', 'equipo_id']).groupby('liga').apply(lambda g: g.eq_xg.sum() / max(g.eq_n.sum(), 1))
    D['eq_rate'] = (D.eq_xg + K_EQ * D.liga.map(lig)) / (D.eq_n + K_EQ)
    return D, lig


def ic_partidos(d, gid, seed=1):
    s = pd.Series(d).groupby(gid).sum(); cnt = pd.Series(1, index=range(len(d))).groupby(gid).sum()
    rng = np.random.default_rng(seed); u = len(s)
    bs = [s.values[k].sum() / cnt.values[k].sum() for k in (rng.integers(0, u, u) for _ in range(2000))]
    return np.percentile(bs, [2.5, 97.5])


def main(obj='goles'):
    O = OBJ[obj]
    D, lig = preparar(obj)
    F, T = D[D.fecha < CORTE], D[D.fecha >= CORTE]
    base = base_de(F)
    L = []
    def p(x=''):
        print(x); L.append(x)
    p(f'Backtest de {O["nombre"]} (titulares de campo, {pd.Timestamp.now():%Y-%m-%d})')
    p(f'ajuste: {len(F)} titulares de {F.partido.nunique()} partidos (< {CORTE}); '
      f'prueba: {len(T)} de {T.partido.nunique()} partidos; {O["verbo"]} el {T.y.mean():.1%}')
    rx, rr = O['x'], O['real']
    var = {'posicion': (dict({0: 1.0, 1: 1e6}), True), rr: ({0: 0.0}, True), rx: ({0: 1.0}, True),
           'mezcla': ({}, True), 'mezcla sin mercado': ({}, False)}
    pr = {}
    for v, (fijo, cm) in var.items():
        th = ajustar(F, base, fijo, cm)
        pr[v] = 1 - np.exp(-lam(T, th, base, cm))
        y = T.y.values
        p(f'  {v:19s} w={th[0]:.2f} K={th[1]:.1f} K_min={th[2]:.1f} c={th[3]:.2f}  '
          f'log-loss {ll(pr[v], y).mean():.4f}  Brier {np.mean((pr[v] - y) ** 2):.4f}  prob media {pr[v].mean():.1%}')
    y = T.y.values; gid = T.partido.values
    def ic(a, b):
        d = ll(pr[a], y) - ll(pr[b], y)
        lo, hi = ic_partidos(d, gid)
        return f'{d.mean() * 1000:+.2f} x1000 IC95 [{lo * 1000:+.2f}, {hi * 1000:+.2f}]'
    p('\nDiferencias de log-loss en prueba (negativo = la primera es mejor; bootstrap por partidos):')
    for a, b in [(rx, rr), ('mezcla', rr), ('mezcla', rx), ('mezcla', 'posicion'), ('mezcla', 'mezcla sin mercado')]:
        p(f'  {a} - {b}: {ic(a, b)}')
    p('\nCalibracion de la mezcla en prueba (por tramos de prob):')
    q = pd.cut(pr['mezcla'], [0, .1, .2, .3, .4, .5, 1])
    g = pd.DataFrame(dict(p=pr['mezcla'], y=y, q=q)).groupby('q', observed=True).agg(n=('y', 'size'), prob=('p', 'mean'), real=('y', 'mean'))
    for k, r in g.iterrows():
        p(f'  {str(k):12s} n={int(r.n):5d}  prevista {r.prob:.1%}  real {r.real:.1%}')
    p('\nPor posicion (mezcla, prueba):')
    for k, g in pd.DataFrame(dict(p=pr['mezcla'], y=y, pos=T.pos.values)).groupby('pos'):
        p(f'  {k:4s} n={len(g):5d}  prevista {g.p.mean():.1%}  real {g.y.mean():.1%}')
    (DIR / 'resultados' / f'{O["nombre"]}_backtest.txt').write_text('\n'.join(L), encoding='utf-8')
    # coeficientes finales: ajustados con TODO (ajuste + prueba)
    baseT = base_de(D); th = ajustar(D, baseT, {}, True)
    f = DIR / 'datos' / 'agregados' / f'{O["nombre"]}_coef.json'
    viejo = json.loads(f.read_text(encoding='utf-8')) if f.exists() else {}
    co = dict(w=th[0], K=th[1], K_min=th[2], c=th[3], K_eq=K_EQ, rx=baseT['rx'].to_dict(), rg=baseT['rg'].to_dict(),
              min_pos=baseT['min_pos'].to_dict(),
              r_sub=(th[0] * baseT['rsx'] + (1 - th[0]) * baseT['rsg']).to_dict(), xg_liga={int(k): v for k, v in lig.items()},
              n=len(D), fecha=f'{pd.Timestamp.now():%Y-%m-%d}')
    if 'kappa' in viejo:
        co['kappa'] = viejo['kappa']
    f.write_text(json.dumps(co, indent=1), encoding='utf-8')
    p(f'\ncoeficientes con toda la muestra: w={th[0]:.2f} K={th[1]:.1f} K_min={th[2]:.1f} c={th[3]:.2f} -> {f.relative_to(DIR).as_posix()}')


def combinado():
    """Anotara o asistira: suma de las dos lambdas (mezcla de cada una, ajustadas antes de CORTE)."""
    R = {}
    for obj in ('goles', 'asist'):
        D, _ = preparar(obj)
        F = D[D.fecha < CORTE]; base = base_de(F); th = ajustar(F, base, {}, True)
        R[obj] = D.assign(lam=lam(D, th, base))[['partido', 'jugador', 'fecha', 'pos', 'v', 'lam']]
    M = R['goles'].merge(R['asist'], on=['partido', 'jugador', 'fecha', 'pos'], suffixes=('_g', '_a'))
    M['y'] = ((M.v_g >= 1) | (M.v_a >= 1)).astype(float)
    F, T = M[M.fecha < CORTE], M[M.fecha >= CORTE]
    s = lambda X, k: 1 - np.exp(-k * (X.lam_g + X.lam_a).values)
    kap = minimize(lambda x: ll(s(F, x[0]), F.y.values).mean(), [1.0], method='Nelder-Mead').x[0]
    L = []
    def p(x=''):
        print(x); L.append(x)
    p(f'Anotara o asistira (titulares de campo, {pd.Timestamp.now():%Y-%m-%d}): {len(F)} de ajuste, {len(T)} de prueba; '
      f'en la prueba marcan o asisten el {T.y.mean():.1%}')
    p(f'  kappa = {kap:.3f} (1 = goles y asistencias suman sin solaparse)')
    y = T.y.values; p1, pk = s(T, 1.0), s(T, kap)
    d = ll(pk, y) - ll(p1, y); lo, hi = ic_partidos(d, T.partido.values)
    p(f'  log-loss prueba: suma simple {ll(p1, y).mean():.4f}, con kappa {ll(pk, y).mean():.4f} '
      f'(diferencia {d.mean() * 1000:+.2f} x1000 IC95 [{lo * 1000:+.2f}, {hi * 1000:+.2f}])')
    p('  calibracion con kappa en prueba:')
    g = pd.DataFrame(dict(p=pk, y=y)).groupby(pd.cut(pk, [0, .1, .2, .3, .4, .5, 1]), observed=True).agg(
        n=('y', 'size'), prob=('p', 'mean'), real=('y', 'mean'))
    for k, r in g.iterrows():
        p(f'    {str(k):12s} n={int(r.n):5d}  prevista {r.prob:.1%}  real {r.real:.1%}')
    # 2026-11-16: kappa 0,985 empeora la prueba (+0,34 x1000, IC95 > 0): se usa la suma simple
    # (kappa = 1). Solo se guarda otro kappa si mejora fuera de muestra con IC95 < 0.
    kt = kap if hi < 0 else 1.0
    f = DIR / 'datos' / 'agregados' / 'asistencias_coef.json'
    co = json.loads(f.read_text(encoding='utf-8')); co['kappa'] = kt
    f.write_text(json.dumps(co, indent=1), encoding='utf-8')
    p(f'  kappa que se usa: {kt:.3f} -> {f.relative_to(DIR).as_posix()}')
    (DIR / 'resultados' / 'anota_o_asiste_backtest.txt').write_text('\n'.join(L), encoding='utf-8')


if __name__ == '__main__':
    import sys
    if '--combinado' in sys.argv:
        combinado()
    else:
        main('asist' if '--asistencias' in sys.argv else 'goles')

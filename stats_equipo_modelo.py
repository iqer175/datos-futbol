"""
stats_equipo_modelo.py -- modelo generico para mercados de estadisticas de equipo (faltas,
entradas, fueras de juego, paradas, centros...) con datos de FotMob (stats_equipos.csv).

Mismo esquema que remates_equipo.py (validado para remates):
  perfil   lo que hace el equipo (f) y lo que concede (c), cada partido dividido por la media
           de su liga en ese momento (ultimos VENTANA_LIGA partidos), ponderado por antigüedad
           (motor.HALF_LIFE) y encogido hacia 1 con motor.K_SHRINK partidos.
  media    local = nivel * (f_local + c_visitante) / 2 + a_l + b_l * gap_Elo, visitante igual.
  reparto  binomial negativa (var = mu + alpha mu^2) por equipo, correlacion rho (copula).

  python stats_equipo_modelo.py            # ajuste 2025-08..2026-01, prueba 2026-02.. -> coeficientes
  -> datos/agregados/stats_equipo_coef.json y resultados/stats_equipo.txt
"""
import json
from pathlib import Path
import numpy as np, pandas as pd
from scipy.stats import nbinom
from scipy.optimize import minimize_scalar

import motor

DIR = Path(__file__).parent
COEF_F = DIR / 'datos' / 'agregados' / 'stats_equipo_coef.json'
STATS = ['faltas', 'entradas', 'offsides', 'atajadas', 'centros', 'corners', 'remates', 'al_arco',
         'xg', 'pases']
CONTINUAS = {'xg', 'pases'}            # sin mercado de conteo pequeño: se informa solo la media
VENTANA_LIGA, MIN_PARTIDOS = 120, 3
AJ = ('2025-08-01', '2026-02-01')      # ajuste
PR = '2026-02-01'                      # prueba desde


def tabla(df, s):
    """Partidos con la estadistica s de los dos equipos, con nivel de liga y perfiles previos."""
    g = df[df[f'fm_{s}_l'].notna() & df[f'fm_{s}_v'].notna()].sort_values('fecha').copy()
    niv = []
    for liga, h in g.groupby('liga', sort=False):
        m = ((h[f'fm_{s}_l'] + h[f'fm_{s}_v']) / 2).rolling(VENTANA_LIGA, min_periods=40).mean().shift()
        niv.append(m)
    g['nivel'] = pd.concat(niv)
    g = g[g.nivel.notna()]
    L = pd.concat([
        pd.DataFrame(dict(eq=g.local, fecha=g.fecha, f=g[f'fm_{s}_l'] / g.nivel, c=g[f'fm_{s}_v'] / g.nivel)),
        pd.DataFrame(dict(eq=g.visitante, fecha=g.fecha, f=g[f'fm_{s}_v'] / g.nivel, c=g[f'fm_{s}_l'] / g.nivel))])
    idx = {eq: (x.fecha.values, x[['f', 'c']].values) for eq, x in L.sort_values('fecha').groupby('eq')}
    P = np.full((len(g), 4), np.nan)
    for i, r in enumerate(g.itertuples()):
        for j, eq in enumerate((r.local, r.visitante)):
            p = perfil(idx.get(eq), r.fecha)
            if p is not None:
                P[i, 2 * j:2 * j + 2] = p
    g[['fl', 'cl', 'fv', 'cv']] = P
    g['gap'] = (g.elo_l_pre - g.elo_v_pre) / 100
    g['bl'] = g.nivel * (g.fl + g.cv) / 2
    g['bv'] = g.nivel * (g.fv + g.cl) / 2
    return g[g.bl.notna() & g.bv.notna()]


def perfil(h, t):
    """h = (fechas, [[f, c], ...]) de un equipo; perfil encogido hacia 1 antes de t."""
    if h is None:
        return None
    ts, v = h
    k = ts < np.datetime64(t)
    if k.sum() < MIN_PARTIDOS:
        return None
    ts, v = ts[k][-motor.MAX_N:], v[k][-motor.MAX_N:]
    w = 0.5 ** ((np.datetime64(t) - ts).astype('timedelta64[D]').astype(float) / motor.HALF_LIFE)
    return ((w[:, None] * v).sum(0) + motor.K_SHRINK) / (w.sum() + motor.K_SHRINK)


def simular(m, co, n, rng):
    """Valores (l, v) de n partidos: binomial negativa por equipo con correlacion rho."""
    from scipy.stats import norm
    z1 = rng.standard_normal(n)
    z2 = co['rho'] * z1 + np.sqrt(1 - co['rho'] ** 2) * rng.standard_normal(n)
    nb = 1 / co['alpha']
    return {lado: nbinom.ppf(np.clip(norm.cdf(z), 1e-12, 1 - 1e-12), nb, nb / (nb + mu)).astype(np.int64)
            for lado, z, mu in (('l', z1, m['ml']), ('v', z2, m['mv']))}


def nb_ll(y, mu, alpha):
    n = 1 / alpha
    return nbinom.logpmf(np.round(y).astype(int), n, n / (n + mu))


def evaluar(df, s):
    g = tabla(df, s)
    a = g[(g.fecha >= AJ[0]) & (g.fecha < AJ[1])]
    t = g[g.fecha >= PR]
    co = {}
    for lado in ('l', 'v'):
        y, b = a[f'fm_{s}_{lado}'], a[f'b{lado}']
        X = np.c_[np.ones(len(a)), a.gap]
        coef = np.linalg.lstsq(X, y - b, rcond=None)[0]
        co[f'a_{lado}'], co[f'b_{lado}'] = float(coef[0]), float(coef[1])
    def medias(x):
        ml = np.maximum(x.bl + co['a_l'] + co['b_l'] * x.gap, 0.3)
        mv = np.maximum(x.bv + co['a_v'] + co['b_v'] * x.gap, 0.3)
        return ml, mv
    ml, mv = medias(a)
    res = dict(stat=s, n_ajuste=len(a), n_prueba=len(t), **co)
    if s in CONTINUAS:
        return res, None
    yl, yv = a[f'fm_{s}_l'], a[f'fm_{s}_v']
    r = minimize_scalar(lambda al: -(nb_ll(yl, ml, al).sum() + nb_ll(yv, mv, al).sum()),
                        bounds=(1e-4, 2), method='bounded')
    co['alpha'] = float(r.x)
    co['rho'] = float(np.corrcoef(yl - ml, yv - mv)[0, 1])
    # prueba: modelo frente a "media de la liga" (mismo reparto, alpha propio)
    ml, mv = medias(t); yl, yv = t[f'fm_{s}_l'], t[f'fm_{s}_v']
    dl = (a[f'fm_{s}_l'] - a[f'fm_{s}_v']).mean() / 2
    r0 = minimize_scalar(lambda al: -(nb_ll(a[f'fm_{s}_l'], a.nivel + dl, al).sum()
                                      + nb_ll(a[f'fm_{s}_v'], a.nivel - dl, al).sum()), bounds=(1e-4, 2), method='bounded')
    l1 = (nb_ll(yl, ml, co['alpha']) + nb_ll(yv, mv, co['alpha'])) / 2
    l0 = (nb_ll(yl, t.nivel + dl, r0.x) + nb_ll(yv, t.nivel - dl, r0.x)) / 2
    d = np.asarray(l1 - l0)
    bs = [np.random.default_rng(i).choice(d, len(d)).mean() for i in range(1000)]
    # calibracion con lineas FIJAS (como las de bet365): total y del local en la .5 bajo la media
    # del ajuste; probabilidades simuladas con la correlacion rho, frente a la "media de la liga"
    tot, mt = yl + yv, ml + mv
    L_tot = np.floor((a[f'fm_{s}_l'] + a[f'fm_{s}_v']).mean()) + 0.5
    L_loc = np.floor(a[f'fm_{s}_l'].mean()) + 0.5
    def probs(m1, m2, al, rho):
        S = simular(dict(ml=m1, mv=m2), dict(alpha=al, rho=rho), 1500, np.random.default_rng(0))
        return (S['l'] + S['v'] > L_tot).mean(), (S['l'] > L_loc).mean()
    P1 = np.array([probs(m1, m2, co['alpha'], co['rho']) for m1, m2 in zip(ml, mv)])
    P0 = np.array([probs(m1, m2, r0.x, co['rho']) for m1, m2 in zip(t.nivel + dl, t.nivel - dl)])
    cal = []
    for k, (nombre, ln, y) in enumerate((('total', L_tot, tot.values), ('local', L_loc, yl.values))):
        real = (y > ln).astype(float)
        b1, b0 = np.mean((P1[:, k] - real) ** 2), np.mean((P0[:, k] - real) ** 2)
        q = pd.qcut(P1[:, k], 4, duplicates='drop')
        c = pd.DataFrame(dict(p=P1[:, k], y=real)).groupby(q, observed=True).agg(p=('p', 'mean'), y=('y', 'mean'), n=('y', 'size'))
        cal.append((nombre, ln, b0, b1, c))
    po, real = P1[:, 0], (tot.values > L_tot)
    res.update(co, dll=float(d.mean()), ic=(float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))),
               media_real=float(tot.mean()), media_prev=float(mt.mean()),
               nivel=g.groupby('liga').apply(lambda x: float(((x[f'fm_{s}_l'] + x[f'fm_{s}_v']) / 2).tail(VENTANA_LIGA).mean())).to_dict(),
               brier=float(np.mean((po - real) ** 2)))
    return res, cal


def main():
    import stats_equipos
    df = stats_equipos.unir(motor.cargar(DIR / 'datos' / 'partidos.csv'))
    df = df[df.liga != 'Primera Division']      # todas las ligas con datos de FotMob
    out, L = {}, []
    def p(x=''): print(x); L.append(x)
    for s in STATS:
        r, cal = evaluar(df, s)
        out[s] = r
        if cal is None:
            p(f'{s}: media continua (n ajuste {r["n_ajuste"]})'); continue
        p(f'{s:9s} prueba n={r["n_prueba"]}: dLL por equipo {r["dll"]:+.4f} IC95 [{r["ic"][0]:+.4f}, {r["ic"][1]:+.4f}]'
          f'  total real {r["media_real"]:.1f} / previsto {r["media_prev"]:.1f}  alpha {r["alpha"]:.3f} rho {r["rho"]:+.2f}'
          f'  Brier linea {r["brier"]:.4f}')
        for nombre, ln, b0, b1, c in cal:
            p(f'    {nombre} mas {ln}: Brier media liga {b0:.4f} -> modelo {b1:.4f} ({100*(b0-b1)/b0:+.1f} %)  '
              + '  '.join(f'{x.p:.2f}->{x.y:.2f}' for _, x in c.iterrows()))
    COEF_F.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding='utf-8')
    (DIR / 'resultados' / 'stats_equipo.txt').write_text('\n'.join(L), encoding='utf-8')


if __name__ == '__main__':
    main()

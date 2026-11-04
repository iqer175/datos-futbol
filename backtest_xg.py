#!/usr/bin/env python3
"""
backtest_xg.py -- ¿mejora el xG al motor o al mercado?

football-data trae xG (HxG/AxG) solo desde la temporada 2026-27, en 18 ligas.
Muestra pequena: los resultados son orientativos y se acompanan de IC pareados.

Dos pruebas:

A) MOTOR CON xG. El GLM de goles se ajusta a un objetivo mixto
       y = alpha·goles + (1 - alpha)·xG      (partidos sin xG: y = goles)
   y el motor usa el GLM para el total (peso W) y, opcionalmente, para una
   parte de la supremacia (peso V; hoy V = 0, supremacia 100 % Elo).
   Se compara contra el motor actual (alpha = 1, W = 0.35, V = 0) y contra
   la apertura de bet365, en 1X2 y Mas/Menos 2,5.

B) "SUERTE" (goles - xG) Y MERCADO. Si el mercado sobrerreacciona a los goles,
   un equipo que ha marcado mas de lo que dice su xG estara sobrevalorado.
   Se regresa el resultado sobre la probabilidad de apertura (en logit) mas la
   diferencia de suerte entre local y visitante. beta < 0 significativo =
   ineficiencia aprovechable. Tambien se mira si la linea se mueve con ella.

Uso:  python backtest_xg.py
Salida: resultados/xg_resumen.txt, resultados/xg_predicciones.csv
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
from scipy.optimize import minimize
from scipy.stats import poisson

import motor
from backtest_clv import potencia, probs_1x2

DIR = Path(__file__).parent
OUT = DIR / 'resultados'
INI_EVAL = pd.Timestamp('2026-09-07')
ALPHAS = [1.0, 0.7, 0.5, 0.3, 0.0]
WS = [0.35, 0.6, 1.0]
VS = [0.0, 0.3, 0.5]
N_SUERTE = 6
SEMILLA = 11


def ajustar_glm_y(df, hasta, yl, yv, ridge=motor.RIDGE, ventana=motor.VENTANA, half=motor.HALF_LIFE):
    """motor.ajustar_glm con objetivo arbitrario (quasi-Poisson: y puede ser no entero)."""
    m = (df.fecha < hasta) & (df.fecha >= hasta - pd.Timedelta(days=ventana))
    d = df[m]
    gl, gv = yl[m.values], yv[m.values]
    eqs = pd.Index(sorted(set(d.local) | set(d.visitante)))
    lgs = pd.Index(sorted(d.liga.unique()))
    il, iv = eqs.get_indexer(d.local.values), eqs.get_indexer(d.visitante.values)
    ig = lgs.get_indexer(d.liga.values)
    w = 0.5 ** ((hasta - d.fecha).dt.days.values / half)
    ne, nl = len(eqs), len(lgs)

    def obj(p):
        att, dfn = p[:ne], p[ne:2*ne]
        base, loc = p[2*ne:2*ne+nl], p[2*ne+nl:]
        eh = base[ig] + loc[ig] + att[il] - dfn[iv]
        ea = base[ig] + att[iv] - dfn[il]
        mh, ma = np.exp(np.clip(eh, -3, 2.5)), np.exp(np.clip(ea, -3, 2.5))
        nll = np.sum(w*(mh - gl*eh)) + np.sum(w*(ma - gv*ea)) + ridge*(att@att + dfn@dfn)
        rh, ra = w*(mh - gl), w*(ma - gv)
        return nll, np.concatenate([
            np.bincount(il, rh, ne) + np.bincount(iv, ra, ne) + 2*ridge*att,
            -(np.bincount(iv, rh, ne) + np.bincount(il, ra, ne)) + 2*ridge*dfn,
            np.bincount(ig, rh, nl) + np.bincount(ig, ra, nl),
            np.bincount(ig, rh, nl)])

    p0 = np.zeros(2*ne + 2*nl); p0[2*ne:2*ne+nl] = np.log(1.3)
    r = minimize(obj, p0, jac=True, method='L-BFGS-B', options=dict(maxiter=400))
    return dict(att=r.x[:ne], dfn=r.x[ne:2*ne], base=r.x[2*ne:2*ne+nl],
                loc=r.x[2*ne+nl:], eqs=eqs, lgs=lgs)


def lambdas_glm(G, g):
    """(lambda_local, lambda_visitante) del GLM para cada fila; NaN si falta un equipo."""
    il = G['eqs'].get_indexer(g.local.values); iv = G['eqs'].get_indexer(g.visitante.values)
    ig = G['lgs'].get_indexer(g.liga.values)
    ok = (il >= 0) & (iv >= 0) & (ig >= 0)
    lh = np.full(len(g), np.nan); la = np.full(len(g), np.nan)
    lh[ok] = np.exp(G['base'][ig[ok]] + G['loc'][ig[ok]] + G['att'][il[ok]] - G['dfn'][iv[ok]])
    la[ok] = np.exp(G['base'][ig[ok]] + G['att'][iv[ok]] - G['dfn'][il[ok]])
    return lh, la


def suerte_previa(df):
    """Por partido: (goles - xG) a favor menos en contra de cada equipo en sus
    ultimos N_SUERTE partidos con xG de la temporada, solo con partidos anteriores."""
    x = df[df.xg_l.notna()]
    L = pd.concat([
        pd.DataFrame(dict(eq=x.local, fecha=x.fecha, s=(x.gl - x.xg_l) - (x.gv - x.xg_v),
                          dxg=x.xg_l - x.xg_v)),
        pd.DataFrame(dict(eq=x.visitante, fecha=x.fecha, s=(x.gv - x.xg_v) - (x.gl - x.xg_l),
                          dxg=x.xg_v - x.xg_l))]).sort_values(['eq', 'fecha'])
    g = L.groupby('eq')
    L['suerte'] = g.s.transform(lambda v: v.shift().rolling(N_SUERTE, min_periods=3).mean())
    L['xgdif'] = g.dxg.transform(lambda v: v.shift().rolling(N_SUERTE, min_periods=3).mean())
    return L.set_index(['eq', 'fecha'])[['suerte', 'xgdif']]


def ll3(q, res):
    i = np.select([res == 'H', res == 'D'], [0, 1], 2)
    return -np.log(np.clip(q[np.arange(len(q)), i], 1e-9, 1))


def llb(p, y):
    p = np.clip(p, 1e-9, 1 - 1e-9)
    return -np.where(y, np.log(p), np.log(1 - p))


def ic_pareado(a, b, semanas, n=3000):
    """IC 95 % de mean(a - b) con bootstrap por semanas."""
    rng = np.random.default_rng(SEMILLA)
    dd = pd.DataFrame(dict(d=a - b, s=semanas)).groupby('s').d.agg(['sum', 'size'])
    s, c = dd['sum'].values, dd['size'].values
    k = rng.integers(0, len(dd), (n, len(dd)))
    m = s[k].sum(1) / c[k].sum(1)
    return np.percentile(m, [2.5, 97.5])


def logit(p): p = np.clip(p, 1e-6, 1 - 1e-6); return np.log(p / (1 - p))


def reg_logistica(X, y):
    """Logistica sin regularizar; devuelve coeficientes y errores tipicos."""
    def f(b):
        z = X @ b; p = 1 / (1 + np.exp(-z))
        return -np.sum(y*z - np.log1p(np.exp(z))), X.T @ (p - y)
    r = minimize(f, np.zeros(X.shape[1]), jac=True, method='BFGS')
    p = 1 / (1 + np.exp(-X @ r.x))
    H = X.T @ (X * (p*(1-p))[:, None])
    return r.x, np.sqrt(np.diag(np.linalg.inv(H)))


def main():
    OUT.mkdir(exist_ok=True)
    df = motor.cargar(DIR / 'datos' / 'partidos.csv')
    hay = df.xg_l.notna() & df.xg_v.notna()
    gl, gv = df.gl.values.astype(float), df.gv.values.astype(float)
    xl = np.where(hay, df.xg_l.values, gl); xv = np.where(hay, df.xg_v.values, gv)

    ev = df[(df.fecha >= INI_EVAL) & hay & df.b365_1.notna() & df.avgc_1.notna()
            & df.b365_over25.notna() & df.avgc_over25.notna()].copy()
    ev['semana'] = ev.fecha.dt.to_period('W-SUN').dt.start_time

    trozos = []
    for t in sorted(ev.semana.unique()):
        g = ev[ev.semana == t].copy()
        co = motor.coeficientes(df, t)
        g = g[g.liga.isin(co)]
        C = pd.DataFrame(co).T.loc[g.liga]
        d = (g.elo_l_pre.values - g.elo_v_pre.values) / 100.0
        g['sup_elo'] = C.a.values * d + C.b.values
        g['tot_elo'] = C.bt.values + C['at'].values * np.abs(d)
        for a in ALPHAS:
            G = ajustar_glm_y(df, t, a*gl + (1-a)*xl, a*gv + (1-a)*xv)
            lh, la = lambdas_glm(G, g)
            g[f'tot_g{a}'] = lh + la; g[f'sup_g{a}'] = lh - la
        trozos.append(g)
    r = pd.concat(trozos).reset_index(drop=True)
    print(f'{len(r)} partidos evaluados', file=sys.stderr)

    # mercado sin margen
    pa = potencia(r[['b365_1', 'b365_x', 'b365_2']].values)
    pc = potencia(r[['avgc_1', 'avgc_x', 'avgc_2']].values)
    pao = potencia(r[['b365_over25', 'b365_under25']].values)[:, 0]
    pco = potencia(r[['avgc_over25', 'avgc_under25']].values)[:, 0]
    over = (r.gl + r.gv).values > 2.5
    res, sem = r.res.values, r.semana.values

    lineas = []
    def p(s=''):
        print(s); lineas.append(s)

    p(f'Partidos evaluados: {len(r)}  ({r.fecha.min().date()} a {r.fecha.max().date()}), '
      f'{r.liga.nunique()} ligas')
    p(f'Mercado  1X2: apertura_b365 {ll3(pa, res).mean():.4f}  cierre_medio {ll3(pc, res).mean():.4f}')
    p(f'Mercado  O/U: apertura_b365 {llb(pao, over).mean():.4f}  cierre_medio {llb(pco, over).mean():.4f}')

    # ---- A. variantes del motor ----------------------------------------------
    res_var = {}
    for a in ALPHAS:
        for W in WS:
            for V in VS:
                tg = r[f'tot_g{a}'].values; sg = r[f'sup_g{a}'].values
                tot = np.where(np.isnan(tg), r.tot_elo, (1-W)*r.tot_elo + W*tg) + motor.CAL_TOTAL
                sup = np.where(np.isnan(sg), r.sup_elo, (1-V)*r.sup_elo + V*sg)
                lh = np.maximum(0.15, (tot + sup) / 2); la = np.maximum(0.15, (tot - sup) / 2)
                q = probs_1x2(lh, la, True)
                po = 1 - poisson.cdf(2, lh + la)
                res_var[(a, W, V)] = dict(q=q, po=po, ll1=ll3(q, res), llo=llb(po, over))
    base = res_var[(1.0, 0.35, 0.0)]
    p('\n=== A. MOTOR CON xG: log-loss (menor es mejor). Base = motor actual (alpha=1, W=0.35, V=0) ===')
    p('alpha = peso de los goles en el objetivo del GLM (0 = solo xG); W = peso del GLM en el total; '
      'V = peso del GLM en la supremacia')
    p(f'{"alpha":>5} {"W":>5} {"V":>4}  {"1X2":>7} {"dif_1X2 [IC95]":>26}  {"O/U":>7} {"dif_OU [IC95]":>26}')
    filas = []
    for k, v in res_var.items():
        d1 = v['ll1'].mean() - base['ll1'].mean(); i1 = ic_pareado(v['ll1'], base['ll1'], sem)
        do = v['llo'].mean() - base['llo'].mean(); io = ic_pareado(v['llo'], base['llo'], sem)
        filas.append((k, v['ll1'].mean(), d1, i1, v['llo'].mean(), do, io))
    for k, l1, d1, i1, lo, do, io in sorted(filas, key=lambda f: f[1]):
        p(f'{k[0]:5.1f} {k[1]:5.2f} {k[2]:4.1f}  {l1:.4f} {d1:+.4f} [{i1[0]:+.4f},{i1[1]:+.4f}]  '
          f'{lo:.4f} {do:+.4f} [{io[0]:+.4f},{io[1]:+.4f}]')
    p(f'({len(res_var)} variantes probadas sobre {len(r)} partidos: el mejor de la lista esta '
      'sesgado a favor por la busqueda; solo cuenta si el IC excluye el 0 con holgura)')

    # mejor variante 1X2 y O/U: ¿anade informacion a la apertura?
    k1 = min(res_var, key=lambda k: res_var[k]['ll1'].mean())
    ko = min(res_var, key=lambda k: res_var[k]['llo'].mean())
    p(f'\nMejor 1X2: alpha={k1[0]} W={k1[1]} V={k1[2]}   Mejor O/U: alpha={ko[0]} W={ko[1]} V={ko[2]}')
    p('Mezcla con la apertura bet365 (w = peso del motor):')
    for etiqueta, k in (('actual', (1.0, 0.35, 0.0)), ('mejor_1X2', k1)):
        q = res_var[k]['q']
        txt = []
        for w in (0.0, 0.1, 0.2, 0.3):
            m = q**w * pa**(1-w); m /= m.sum(1, keepdims=True)
            txt.append(f'w={w}: {ll3(m, res).mean():.4f}')
        p(f'  1X2 {etiqueta:10s} ' + '  '.join(txt))
    for etiqueta, k in (('actual', (1.0, 0.35, 0.0)), ('mejor_OU', ko)):
        po = res_var[k]['po']
        txt = []
        for w in (0.0, 0.1, 0.2, 0.3, 0.4, 0.5):
            m = 1 / (1 + np.exp(-(w*logit(po) + (1-w)*logit(pao))))
            l = llb(m, over)
            i = ic_pareado(l, llb(pao, over), sem)
            txt.append(f'w={w}: {l.mean():.4f} [{i[0]:+.4f},{i[1]:+.4f}]')
        p(f'  O/U {etiqueta:10s} ' + '  '.join(txt))
    p('  (entre corchetes: IC95 de la diferencia frente a la apertura sola)')

    # ---- B. suerte (goles - xG) frente al mercado --------------------------
    S = suerte_previa(df)
    def unir(eq_col):
        return S.reindex(pd.MultiIndex.from_arrays([r[eq_col], r.fecha])).values
    sl, sv = unir('local'), unir('visitante')
    r['suerte_dif'] = sl[:, 0] - sv[:, 0]       # >0: el local ha tenido mas suerte que el visitante
    r['xg_dif'] = sl[:, 1] - sv[:, 1]           # forma por xG (diferencia de xG por partido)
    ok = r.suerte_dif.notna().values
    p(f'\n=== B. SUERTE (goles - xG, ultimos {N_SUERTE} partidos) FRENTE AL MERCADO  (n={ok.sum()}) ===')
    p('Logistica: resultado ~ logit(p_mercado) + beta·variable.  beta<0 en "suerte" = el mercado '
      'sobrevalora a los equipos con suerte (aprovechable si es significativo).')
    for nombre, pm in (('apertura', pa), ('cierre', pc)):
        for var in ('suerte_dif', 'xg_dif'):
            z = r[var].values[ok]; z = (z - z.mean()) / z.std()
            for lado, k, code in (('local', 0, 'H'), ('visit', 2, 'A')):
                y = (res[ok] == code).astype(float)
                X = np.column_stack([np.ones(ok.sum()), logit(pm[ok, k]), z if lado == 'local' else -z])
                b, se = reg_logistica(X, y)
                p(f'  {nombre:8s} {var:10s} {lado:6s}: beta {b[2]:+.3f} (t={b[2]/se[2]:+.2f})  '
                  f'pendiente_mercado {b[1]:.2f}')
        o_ = (pao if nombre == 'apertura' else pco)[ok]
        for var in ('suerte_dif', 'xg_dif'):
            z = r[var].values[ok]; z = (z - z.mean()) / z.std()
            X = np.column_stack([np.ones(ok.sum()), logit(o_), np.abs(z)])
            b, se = reg_logistica(X, over[ok].astype(float))
            p(f'  {nombre:8s} {var:10s} over  : beta(|z|) {b[2]:+.3f} (t={b[2]/se[2]:+.2f})')
    mov = pc[ok, 0] - pa[ok, 0]
    for var in ('suerte_dif', 'xg_dif'):
        z = r[var].values[ok]
        p(f'  movimiento de linea local vs {var}: correlacion {np.corrcoef(z, mov)[0,1]:+.3f}')

    r.to_csv(OUT / 'xg_predicciones.csv', index=False)
    (OUT / 'xg_resumen.txt').write_text('\n'.join(lineas), encoding='utf-8')


if __name__ == '__main__':
    main()

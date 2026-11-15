"""
backtest_faltas_tarjetas.py -- mejoran las faltas (FotMob) la prevision de tarjetas?

Base: la de motor.secundarios (tarjetas de cada equipo) x factor de arbitro (arbitros.py),
la que usa hoy el simulador. Se le anade un factor de faltas:
    E = (faltas que comete el local + las que provoca el visitante
         + faltas que comete el visitante + las que provoca el local) / 2
    m = (E / media de faltas de la liga) ** beta
con medias ponderadas por antigüedad (vida media 180 d, encogidas con 6 partidos) y SOLO
partidos anteriores (todas las competiciones de FotMob). beta se ajusta en 2025-07..12 y se
evalua en 2026, en las 5 grandes (las ligas con historico de stats_equipos.csv).

  python backtest_faltas_tarjetas.py
"""
from collections import defaultdict
import numpy as np, pandas as pd
from scipy.stats import poisson
from scipy.optimize import minimize_scalar
import backtest_arbitros as bt, arbitros

HALF, KS = 180.0, 6.0
LINEAS = (3.5, 4.5, 5.5)


def faltas_previas(p):
    S = pd.read_csv('datos/stats_equipos.csv')
    S['t'] = pd.to_datetime(S.fecha, utc=True).dt.tz_localize(None)
    S = S.dropna(subset=['s_faltas', 'r_faltas']).drop_duplicates(['partido_id', 'equipo_id'])
    H = {eq: (g.t.values, g.s_faltas.values.astype(float), g.r_faltas.values.astype(float))
         for eq, g in S.sort_values('t').groupby('equipo')}
    A = pd.read_csv('datos/arbitros_equipos.csv')
    al = {(r.liga, r.equipo): r.fotmob for r in A.itertuples()}
    media = S.s_faltas.mean()

    def eq_med(eq, t):
        if eq not in H:
            return np.nan, np.nan
        ts, c, r = H[eq]; k = ts < np.datetime64(t)
        if not k.any():
            return np.nan, np.nan
        w = 0.5 ** ((np.datetime64(t) - ts[k]).astype('timedelta64[D]').astype(float) / HALF)
        return ((np.sum(w * c[k]) + KS * media) / (np.sum(w) + KS),
                (np.sum(w * r[k]) + KS * media) / (np.sum(w) + KS))
    E = np.full(len(p), np.nan)
    for i, r in enumerate(p.itertuples()):
        cl, rl = eq_med(al.get((r.liga, r.local)), r.fecha)
        cv, rv = eq_med(al.get((r.liga, r.visitante)), r.fecha)
        E[i] = (cl + rv + cv + rl) / 2
    return E


def ll(y, lam): return poisson.logpmf(y, lam).mean()


def main():
    p = bt.cargar()
    p = p[p.liga.isin(['LaLiga', 'Premier League', 'Serie A', 'Bundesliga', 'Ligue 1'])].reset_index(drop=True)
    p['pred'] = bt.base(p)
    p = p[np.isfinite(p.pred)].reset_index(drop=True)
    f, _ = bt.factor_arbitro(p, arbitros.K, arbitros.VIDA)
    p['b'] = p.pred * f                                   # lo que usa hoy el simulador
    p = p[p.fecha >= '2025-07-01'].reset_index(drop=True)
    p['E'] = faltas_previas(p)
    p = p[np.isfinite(p.E)].reset_index(drop=True)
    # media de faltas de la liga en los 365 dias anteriores (de las propias previsiones)
    p['Em'] = p.groupby('liga').E.transform(lambda s: s.expanding().mean().shift().fillna(s.iloc[0]))
    p['x'] = np.log(p.E / p.Em)
    aj, pr = p.fecha < '2026-01-01', p.fecha >= '2026-01-01'
    res = minimize_scalar(lambda b: -ll(p.tot[aj], p.b[aj] * np.exp(b * p.x[aj])), bounds=(-3, 3), method='bounded')
    beta = res.x
    print(f'n ajuste {aj.sum()}, n prueba {pr.sum()}; beta = {beta:.2f} (1 = tarjetas proporcionales a las faltas)')
    lam0, lam1 = p.b[pr], p.b[pr] * np.exp(beta * p.x[pr])
    y = p.tot[pr]
    print(f'2026: LL base+arbitro {ll(y, lam0):.4f} -> +faltas {ll(y, lam1):.4f}  (x1000 {1000*(ll(y, lam1)-ll(y, lam0)):+.2f})')
    for ln in LINEAS:
        b0 = np.mean(((y > ln) - poisson.sf(int(ln), lam0)) ** 2); b1 = np.mean(((y > ln) - poisson.sf(int(ln), lam1)) ** 2)
        print(f'  Brier Mas {ln}: {b0:.4f} -> {b1:.4f} ({100*(b0-b1)/b0:+.2f} %)')
    d = np.asarray(poisson.logpmf(y, lam1) - poisson.logpmf(y, lam0))
    bs = [np.random.default_rng(i).choice(d, len(d)).mean() for i in range(2000)]
    print(f'  IC95 dLL x1000 [{1000*np.percentile(bs,2.5):+.2f}, {1000*np.percentile(bs,97.5):+.2f}]')
    g = p[pr].assign(q=pd.qcut(p.x[pr], 5, labels=False))
    print('Por quintil de faltas esperadas (2026): real / base+arbitro / +faltas')
    for q, h in g.groupby('q'):
        print(f'  q{q}: E {h.E.mean():5.1f}  real {h.tot.mean():.2f}  base {h.b.mean():.2f}  '
              f'+faltas {(h.b*np.exp(beta*h.x)).mean():.2f}  n={len(h)}')


if __name__ == '__main__':
    main()

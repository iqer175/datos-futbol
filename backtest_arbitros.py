"""
backtest_arbitros.py -- vale la pena un factor de arbitro en las tarjetas?

Base (lo que hace hoy motor.secundarios): tarjetas esperadas = media de amarillas
de cada equipo en su liga, ponderada por antigüedad (vida media HALF_LIFE) y
encogida hacia la media de la liga con K_SHRINK partidos ficticios.

Factor de arbitro, solo con partidos ANTERIORES del arbitro:
    f = (sum w*reales + K*m) / (sum w*previstas + K*m)
donde 'previstas' es la base en esos partidos (asi el factor mide al arbitro y no
a los equipos que le tocaron) y m la prevision media. K = partidos ficticios.

Ajuste (K y vida media del arbitro) en 2024-25; evaluacion en 2025-26 y 2026-27.
Metricas: log-verosimilitud Poisson del total y Brier de Mas 3.5 / 4.5 / 5.5.

  python backtest_arbitros.py
"""
from collections import defaultdict
import numpy as np, pandas as pd
from scipy.stats import poisson
import motor

HALF, KS, MAXN = motor.HALF_LIFE, motor.K_SHRINK, motor.MAX_N
LINEAS = (3.5, 4.5, 5.5)


def cargar():
    p = pd.read_csv('datos/partidos.csv', low_memory=False)
    a = pd.read_csv('datos/arbitros_partidos.csv')
    assert len(a) == len(p)
    p['ref'] = a.arbitro_fm.values
    p['fecha'] = pd.to_datetime(p.fecha)
    p = p[p.amar_l.notna() & p.amar_v.notna()].sort_values('fecha').reset_index(drop=True)
    p['tot'] = p.amar_l + p.amar_v
    return p


def base(p):
    """Prevision de tarjetas de la base para cada partido, solo con el pasado."""
    hist = defaultdict(list)          # (equipo, liga) -> [(fecha, amarillas)]
    liga_sum = defaultdict(lambda: [0.0, 0])
    pred = np.full(len(p), np.nan)
    for fecha, g in p.groupby('fecha', sort=True):
        for i, r in g.iterrows():
            s, n = liga_sum[r.liga]
            if n < 50:
                continue
            m = s / n                           # media de la liga (total del partido)
            lt = 0.0
            for eq in (r.local, r.visitante):
                h = hist[(eq, r.liga)][-MAXN:]
                if h:
                    d = np.array([(fecha - f).days for f, _ in h]); x = np.array([a for _, a in h])
                    w = 0.5 ** (d / HALF)
                    lt += (np.sum(w * x) + KS * m / 2) / (np.sum(w) + KS)
                else:
                    lt += m / 2
            pred[i] = lt
        for i, r in g.iterrows():
            hist[(r.local, r.liga)].append((fecha, r.amar_l)); hist[(r.visitante, r.liga)].append((fecha, r.amar_v))
            liga_sum[r.liga][0] += r.tot; liga_sum[r.liga][1] += 1
    return pred


def factor_arbitro(p, K, half_ref):
    """Factor del arbitro antes de cada partido (1 si no hay arbitro o historia)."""
    h = defaultdict(list)   # arbitro -> [(fecha, real, prevista)]
    f = np.ones(len(p)); nprev = np.zeros(len(p))
    for fecha, g in p.groupby('fecha', sort=True):
        for i, r in g.iterrows():
            if isinstance(r.ref, str) and h[r.ref]:
                d = np.array([(fecha - x[0]).days for x in h[r.ref]])
                re = np.array([x[1] for x in h[r.ref]]); pr = np.array([x[2] for x in h[r.ref]])
                w = 0.5 ** (d / half_ref) if half_ref else np.ones(len(d))
                m = pr.mean()
                f[i] = (np.sum(w * re) + K * m) / (np.sum(w * pr) + K * m)
                nprev[i] = len(d)
        for i, r in g.iterrows():
            if isinstance(r.ref, str) and np.isfinite(r.pred):
                h[r.ref].append((fecha, r.tot, r.pred))
    return f, nprev


def metricas(y, lam):
    ll = poisson.logpmf(y, lam).mean()
    br = [np.mean(((y > ln).astype(float) - poisson.sf(int(ln), lam)) ** 2) for ln in LINEAS]
    return ll, br


def main():
    p = cargar()
    print(f'{len(p)} partidos con tarjetas; con arbitro: {p.ref.notna().mean():.0%}')
    p['pred'] = base(p)
    p = p[np.isfinite(p.pred)].reset_index(drop=True)
    # recalibrar la base por temporada pasada no hace falta: comparamos base vs base*f
    # 2024-25 = calentamiento (historia de arbitros); ajuste 2025-07..12; prueba 2026
    ajuste = (p.fecha >= '2025-07-01') & (p.fecha < '2026-01-01')
    prueba = p.fecha >= '2026-01-01'
    con_ref = p.ref.notna()

    print('\nAjuste 2025-07..12 (partidos con arbitro):')
    ll0, _ = metricas(p.tot[ajuste & con_ref], p.pred[ajuste & con_ref])
    mejor = None
    for half_ref in (None, 730, 365):
        for K in (5, 10, 20, 40, 80):
            f, _ = factor_arbitro(p, K, half_ref)
            m = ajuste & con_ref
            ll, br = metricas(p.tot[m], p.pred[m] * f[m])
            print(f'  vida {half_ref or "inf":>4} K {K:>3}: dLL x1000 {1000*(ll-ll0):+6.2f}  Brier 4.5 {br[1]:.4f}')
            if mejor is None or ll > mejor[0]:
                mejor = (ll, K, half_ref, f)
    _, K, half_ref, f = mejor
    print(f'\nElegido: K={K}, vida media={half_ref or "infinita"}')
    p['f'] = f

    print('\nFuera de muestra (2026), partidos con arbitro:')
    m = prueba & con_ref
    ll0, b0 = metricas(p.tot[m], p.pred[m]); ll1, b1 = metricas(p.tot[m], p.pred[m] * p.f[m])
    print(f'  n={m.sum()}  LL base {ll0:.4f} -> arbitro {ll1:.4f}  (x1000 {1000*(ll1-ll0):+.2f})')
    for ln, a, b in zip(LINEAS, b0, b1):
        print(f'  Brier Mas {ln}: {a:.4f} -> {b:.4f}  ({100*(a-b)/a:+.2f} %)')
    print(f'  dispersion del factor: p10 {np.percentile(p.f[m],10):.2f}  p50 {np.median(p.f[m]):.2f}  p90 {np.percentile(p.f[m],90):.2f}')

    print('\nPor liga (2026):')
    filas = []
    for liga, g in p[m].groupby('liga'):
        a, _ = metricas(g.tot, g.pred); b, br = metricas(g.tot, g.pred * g.f)
        filas.append((liga, len(g), 1000 * (b - a)))
    for liga, n, d in sorted(filas, key=lambda x: -x[2]):
        print(f'  {liga:22s} n={n:4d}  dLL x1000 {d:+6.2f}')

    # bootstrap del efecto fuera de muestra
    g = p[m]; d = poisson.logpmf(g.tot, g.pred * g.f) - poisson.logpmf(g.tot, g.pred)
    rng = np.random.default_rng(1)
    d = np.asarray(d); bs = [rng.choice(d, len(d)).mean() for _ in range(2000)]
    print(f'\ndLL x1000 IC95: [{1000*np.percentile(bs,2.5):+.2f}, {1000*np.percentile(bs,97.5):+.2f}]')

    # control: el mismo factor agrupando por LIGA (deriva de la liga, no del arbitro)
    q = p.copy(); q['ref'] = np.where(p.ref.notna(), p.liga, None)
    fl, _ = factor_arbitro(q, K, half_ref)
    l_liga, _ = metricas(p.tot[m], p.pred[m] * fl[m.values])
    # arbitro ENCIMA de la base ya corregida por liga
    q2 = p.copy(); q2['pred'] = p.pred * fl
    f2, _ = factor_arbitro(q2, K, half_ref)
    l_ab, b_ab = metricas(p.tot[m], q2.pred[m] * f2[m.values])
    print(f'\nControl: solo deriva de liga dLL x1000 {1000*(l_liga-ll0):+.2f}')
    print(f'         liga + arbitro      dLL x1000 {1000*(l_ab-ll0):+.2f}  -> lo que aporta el arbitro: {1000*(l_ab-l_liga):+.2f}')
    print('         Brier liga+arbitro: ' + '  '.join(f'Mas {ln}: {b:.4f}' for ln, b in zip(LINEAS, b_ab)))

    # calibracion por cuantil del factor: los arbitros "duros" sacan de verdad mas tarjetas?
    g = g.assign(q=pd.qcut(g.f, 5, labels=False, duplicates='drop'))
    print('\nPor quintil del factor (fuera de muestra): media real / base / base*f')
    for q, h in g.groupby('q'):
        print(f'  q{q}: f {h.f.mean():.2f}  real {h.tot.mean():.2f}  base {h.pred.mean():.2f}  base*f {(h.pred*h.f).mean():.2f}  n={len(h)}')


if __name__ == '__main__':
    main()

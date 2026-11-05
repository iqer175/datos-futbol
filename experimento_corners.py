#!/usr/bin/env python3
"""
experimento_corners.py -- corners por equipo segun el dominio del partido.

El simulador repartia los corners con los perfiles de cada equipo (media de
corners a favor y en contra), sin tener en cuenta quien domina el partido. En
2026-27 eso daba de menos al favorito y de mas al debil.

Protocolo (sin mirar el futuro):
  1. 2025-26, walk-forward semanal: perfiles del motor con datos anteriores a
     cada semana; se ajusta  real - predicho = a + b * (Elo local - visitante)/100
     por separado para el local y para el visitante.
  2. 2026-27, walk-forward semanal, con los coeficientes CONGELADOS del paso 1:
     log-loss de las lineas de corners por equipo y del total, con y sin
     correccion.

Uso:  python experimento_corners.py
Salida: resultados/corners_dominio.txt (y los coeficientes a pegar en simulador.py)
"""
from pathlib import Path

import numpy as np, pandas as pd
from scipy.stats import nbinom

import motor

DIR = Path(__file__).parent
LIGAS_CORNERS = None      # todas las que traen corners
VAR_MEDIA = 1.16


def recoger(df, desde, hasta):
    ev = df[(df.fecha >= desde) & (df.fecha < hasta) & df.corners_l.notna() & df.corners_v.notna()].copy()
    ev['semana'] = ev.fecha.dt.to_period('W-SUN').dt.start_time
    filas = []
    for t, g in ev.groupby('semana'):
        B = motor.Base(df[df.fecha < t], hasta=t)
        for x in g.itertuples():
            if x.liga not in B.co:
                continue
            try:
                R = motor.analizar(B, x.local, x.visitante, x.liga, hasta=t)
            except SystemExit:
                continue
            sl, sv = R['sl'], R['sv']
            if not (sl and sv) or not np.isfinite(R['lc']):
                continue
            filas.append(dict(fecha=x.fecha, liga=x.liga, gap=(R['elo_l'] - R['elo_v']) / 100,
                              pl=(sl['cor_f'] + sv['cor_c']) / 2, pv=(sv['cor_f'] + sl['cor_c']) / 2,
                              rl=float(x.corners_l), rv=float(x.corners_v)))
    return pd.DataFrame(filas)


def p_mas(lam_l, lam_v, que, linea):
    """P(mas de 'linea') del local, visitante o total, con el reparto del simulador
    (total binomial negativa var/media 1.16 y reparto binomial)."""
    lam = lam_l + lam_v
    k = np.arange(0, 41)
    a = (VAR_MEDIA - 1) / lam; n = 1 / a
    pt = nbinom.pmf(k[None, :], n[:, None], (n / (n + lam))[:, None])          # P(total = k)
    if que == 'total':
        return (pt * (k[None, :] > linea)).sum(1)
    s = (lam_l / lam) if que == 'local' else (lam_v / lam)
    from scipy.stats import binom
    out = np.zeros(len(lam))
    for kk in range(41):
        out += pt[:, kk] * (1 - binom.cdf(np.floor(linea), kk, s))
    return out


def llb(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6); return float(-np.mean(np.where(y, np.log(p), np.log(1 - p))))


def main():
    df = motor.cargar(DIR / 'datos' / 'partidos.csv')
    A = recoger(df, pd.Timestamp('2025-08-01'), pd.Timestamp('2026-07-01'))
    al, bl = np.polyfit(A.gap, A.rl - A.pl, 1)[::-1]
    av, bv = np.polyfit(A.gap, A.rv - A.pv, 1)[::-1]
    L = [f'Ajuste en 2025-26 ({len(A):,} partidos): local  real-pred = {al:+.3f} {bl:+.3f}·gap   '
         f'visitante real-pred = {av:+.3f} {bv:+.3f}·gap   (gap = Elo local - visitante, en cientos)']
    T = recoger(df, pd.Timestamp('2026-07-01'), df.fecha.max() + pd.Timedelta(days=1))
    T['cl'] = np.maximum(T.pl + al + bl * T.gap, 0.8); T['cv'] = np.maximum(T.pv + av + bv * T.gap, 0.8)
    L.append(f'Prueba en 2026-27 ({len(T):,} partidos) con los coeficientes congelados:')
    L.append(f'{"mercado":28s} {"real":>6s} {"sin corr.":>9s} {"con corr.":>9s} {"LL sin":>7s} {"LL con":>7s}')
    for que, ln in (('local', 3.5), ('local', 4.5), ('local', 5.5), ('local', 6.5),
                    ('visitante', 2.5), ('visitante', 3.5), ('visitante', 4.5), ('visitante', 5.5), ('total', 9.5), ('total', 10.5)):
        y = (T.rl if que == 'local' else T.rv if que == 'visitante' else T.rl + T.rv).values > ln
        p0 = p_mas(T.pl.values, T.pv.values, que, ln); p1 = p_mas(T.cl.values, T.cv.values, que, ln)
        L.append(f'{que + " más de " + str(ln):28s} {y.mean()*100:5.1f}% {p0.mean()*100:8.1f}% {p1.mean()*100:8.1f}% '
                 f'{llb(p0, y):7.4f} {llb(p1, y):7.4f}')
    T['tramo'] = pd.cut(T.gap, [-6, -1.5, -0.5, 0.5, 1.5, 6])
    g = T.groupby('tramo', observed=True).agg(n=('gap', 'size'), sin_l=('pl', 'mean'), con_l=('cl', 'mean'), real_l=('rl', 'mean'),
                                              sin_v=('pv', 'mean'), con_v=('cv', 'mean'), real_v=('rv', 'mean'))
    L += ['', 'Medias por tramo de Elo en 2026-27 (sin corregir / corregido / real):', g.round(2).to_string(), '',
          f'Coeficientes para simulador.py: CORNERS_DOMINIO = dict(a_l={al:.3f}, b_l={bl:.3f}, a_v={av:.3f}, b_v={bv:.3f})']
    txt = '\n'.join(L); print(txt)
    (DIR / 'resultados').mkdir(exist_ok=True)
    (DIR / 'resultados' / 'corners_dominio.txt').write_text(txt + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
validar_simulador.py -- calibracion walk-forward del simulador.

Cada semana se reconstruye motor.Base solo con partidos anteriores, se simula
cada partido de las ligas con estadisticas y se compara con lo que paso:
  - log-loss del simulador frente a la tasa media de la liga (referencia simple)
  - en combinadas, frente al producto ingenuo de las patas (mide si la
    correlacion simulada es real o inventada)
  - calibracion por quintiles

Uso:  python validar_simulador.py [--desde 2026-08-01] [--n 4000]
Salida: resultados/simulador_calibracion.txt
"""
import argparse
from pathlib import Path

import numpy as np, pandas as pd

import motor
import simulador as sm

DIR = Path(__file__).parent
MERCADOS = ['Más 2.5', 'Ambos marcan - Sí', 'Córners más 9.5', 'Córners más 10.5', 'Tarjetas más 3.5',
            'Tarjetas más 4.5', '1 & Más 2.5', '2 & Más 2.5', 'X & Menos 2.5', 'Más 2.5 & Ambos marcan - Sí',
            'Menos 2.5 & Tarjetas más 4.5', 'Córners más 9.5 & Tarjetas más 4.5']


def real(x, nombre):
    gl, gv, cor, tar = x.gl, x.gv, x.corners_l + x.corners_v, x.amar_l + x.amar_v
    t = {'1': gl > gv, '2': gl < gv, 'X': gl == gv, 'Más 2.5': gl + gv > 2.5, 'Menos 2.5': gl + gv < 2.5,
         'Ambos marcan - Sí': (gl > 0) & (gv > 0), 'Córners más 9.5': cor > 9.5, 'Córners más 10.5': cor > 10.5,
         'Tarjetas más 3.5': tar > 3.5, 'Tarjetas más 4.5': tar > 4.5}
    return all(t[p.strip()] for p in nombre.split('&'))


def llb(p, y):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6); y = np.asarray(y, bool)
    return float(-np.mean(np.where(y, np.log(p), np.log(1 - p))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--desde', default='2026-08-01')
    ap.add_argument('--n', type=int, default=4000)
    a = ap.parse_args()
    df = motor.cargar(DIR / 'datos' / 'partidos.csv')
    ev = df[(df.fecha >= a.desde) & df.corners_l.notna() & df.amar_l.notna()].copy()
    for c in ('gl', 'gv', 'corners_l', 'corners_v', 'amar_l', 'amar_v'):
        ev[c] = ev[c].astype(float)
    ev['semana'] = ev.fecha.dt.to_period('W-SUN').dt.start_time
    filas = []
    for t, g in ev.groupby('semana'):
        B = motor.Base(df[df.fecha < t], hasta=t)
        hist = df[df.fecha < t]
        for x in g.itertuples():
            if x.liga not in B.co:
                continue
            try:
                R = motor.analizar(B, x.local, x.visitante, x.liga, hasta=t)
            except SystemExit:
                continue
            if not (np.isfinite(R['lc']) and np.isfinite(R['lt'])):
                continue
            S = sm.simular(R, a.n, semilla=int(x.Index) % 100000); C = sm.catalogo(S)
            r = dict(fecha=x.fecha, liga=x.liga)
            for m in MERCADOS:
                r[f'p|{m}'] = sm.prob(S, C, m)[0]
                if '&' in m:
                    r[f'i|{m}'] = sm.prob_independiente(S, C, m)
                r[f'y|{m}'] = real(x, m)
            filas.append(r)
    R = pd.DataFrame(filas)
    L = [f'Partidos simulados: {len(R):,} ({R.fecha.min().date()} a {R.fecha.max().date()}), {a.n} simulaciones cada uno', '',
         f'{"mercado":38s} {"real":>6s} {"pred":>6s} {"LL sim":>7s} {"LL base":>7s} {"LL ingenuo":>10s}  calibracion por quintiles (pred→real)']
    for m in MERCADOS:
        p, y = R[f'p|{m}'].values, R[f'y|{m}'].values.astype(bool)
        # referencia: tasa de la liga en la temporada anterior al partido (sin mirar el futuro)
        base = np.array([y[(R.liga.values == lg) & (R.fecha.values < f)].mean() if ((R.liga.values == lg) & (R.fecha.values < f)).sum() >= 20 else y.mean()
                         for lg, f in zip(R.liga.values, R.fecha.values)])
        ing = llb(R[f'i|{m}'], y) if f'i|{m}' in R else np.nan
        q = pd.qcut(p, 5, duplicates='drop')
        cal = pd.DataFrame(dict(p=p, y=y, q=q)).groupby('q', observed=True).agg(pred=('p', 'mean'), real=('y', 'mean'))
        L.append(f'{m:38s} {y.mean()*100:5.1f}% {p.mean()*100:5.1f}% {llb(p, y):7.4f} {llb(base, y):7.4f} '
                 f'{"" if np.isnan(ing) else f"{ing:10.4f}":>10s}  ' + ' '.join(f'{r.pred*100:.0f}→{r.real*100:.0f}' for r in cal.itertuples()))
    L += ['', 'LL = log-loss (menor es mejor). LL base = tasa de la liga hasta esa fecha; LL ingenuo = producto de las patas.',
          'El simulador solo aporta algo si su LL es menor que el de la base y, en combinadas, que el ingenuo.']
    txt = '\n'.join(L); print(txt)
    (DIR / 'resultados').mkdir(exist_ok=True)
    (DIR / 'resultados' / 'simulador_calibracion.txt').write_text(txt + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()

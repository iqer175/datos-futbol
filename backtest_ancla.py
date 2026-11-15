#!/usr/bin/env python3
"""
backtest_ancla.py -- ¿mejora el simulador si los goles salen del mercado y no del motor?

Hipotesis: el motor no aporta nada sobre el mercado en 1X2 ni en Mas/Menos (backtest_clv.py,
peso optimo 0). Si los goles esperados del simulador se sacan de las cuotas de APERTURA de
bet365 (1X2 + Mas/Menos 2,5, sin margen), los mercados DERIVADOS (otras lineas de goles,
ambos marcan, handicaps, combinadas, tarjetas) deberian salir mejor calibrados.

Tres variantes, walk-forward semanal (motor.Base solo con partidos anteriores):
  motor   simulador actual: lh, la del motor v5 + CAL_LOCAL / CAL_EMPATE
  ancla   lh, la y el peso del empate se resuelven para reproducir EXACTAMENTE las
          probabilidades sin margen de 1, X y Mas 2,5 de la apertura de bet365.
          No tiene ningun parametro ajustado con resultados: no hay nada que sobreajustar.
  ancla+c igual, y ademas el dominio de los corners (CORNERS_DOMINIO) usa la diferencia
          de Elo que implica la supremacia del mercado en vez del Elo propio.

Se mide el log-loss partido a partido y la diferencia emparejada ancla - motor con su
IC95 (negativo = gana el ancla), en total y por temporada (julio a junio).

Uso:  python backtest_ancla.py [--desde 2023-07-01] [--n 20000]
Salida: resultados/ancla_mercado.txt y resultados/ancla_predicciones.csv
"""
import argparse
import contextlib
import io
from math import exp, log
from pathlib import Path

import numpy as np, pandas as pd
from scipy.optimize import root

import ligas
import motor
import simulador as sm

DIR = Path(__file__).parent
GOLES = ['1', 'X', '2', 'Más 2.5',                                   # anclados (control)
         'Más 1.5', 'Más 3.5', 'Ambos marcan - Sí', 'Local más 1.5', 'Visitante más 1.5',
         'Hándicap local -1.5', 'Hándicap visitante -1.5',
         '1 & Más 2.5', '2 & Más 2.5', 'X & Menos 2.5', '1 & Ambos marcan - Sí',
         '2 & Ambos marcan - Sí', 'Más 2.5 & Ambos marcan - Sí']
STATS = ['Córners más 9.5', 'Córners más 10.5', 'Córners local más 4.5', 'Córners visitante más 4.5',
         'Tarjetas más 3.5', 'Tarjetas más 4.5', 'Menos 2.5 & Tarjetas más 4.5']
ANCLADOS = {'1', 'X', '2', 'Más 2.5'}
K = np.arange(11)
I, J = np.indices((11, 11))
LOGFACT = np.array([log(float(np.prod(np.arange(1, k + 1)))) if k else 0.0 for k in K])


def potencia(o):
    q = 1 / np.asarray(o, float); lo, hi = .2, 5
    for _ in range(60):
        k = (lo + hi) / 2
        if (q ** k).sum() < 1: hi = k
        else: lo = k
    p = q ** ((lo + hi) / 2); return p / p.sum()


def matriz(lh, la, cd):
    M = np.outer(np.exp(-lh + K * log(lh) - LOGFACT), np.exp(-la + K * log(la) - LOGFACT))
    M[I == J] *= exp(cd)
    return M / M.sum()


def anclar(p1, px, po, lh0, la0):
    """(lh, la, peso del empate) que reproducen p1, px y P(Mas 2,5) del mercado."""
    def f(z):
        M = matriz(exp(z[0]), exp(z[1]), z[2])
        return [M[I > J].sum() - p1, M[I == J].sum() - px, M[I + J > 2.5].sum() - po]
    r = root(f, [log(lh0), log(la0), motor.CAL_EMPATE], method='hybr')
    if not r.success or np.abs(r.fun).max() > 1e-4:
        return None
    return exp(r.x[0]), exp(r.x[1]), r.x[2]


def simular(R, n, semilla, cal_local, cal_empate):
    """sm.simular con otros pesos de 1X2 (el simulador los lee del modulo motor)."""
    a, b = motor.CAL_LOCAL, motor.CAL_EMPATE
    motor.CAL_LOCAL, motor.CAL_EMPATE = cal_local, cal_empate
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            return sm.simular(R, n, semilla)
    finally:
        motor.CAL_LOCAL, motor.CAL_EMPATE = a, b


def real(x, hay_stats):
    S = dict(gl=np.array([x.gl]), gv=np.array([x.gv]),
             cl=np.array([x.corners_l if hay_stats else 0]), cv=np.array([x.corners_v if hay_stats else 0]),
             tl=np.array([x.amar_l if hay_stats else 0]), tv=np.array([x.amar_v if hay_stats else 0]))
    C = sm.catalogo(S)
    return lambda m: bool(all(C[p.strip()][0][0] for p in m.split('&')))


def ll(p, y):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    return -np.where(np.asarray(y, bool), np.log(p), np.log(1 - p))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--desde', default='2023-07-01')
    ap.add_argument('--n', type=int, default=20000)
    a = ap.parse_args()
    df = motor.cargar(DIR / 'datos' / 'partidos.csv')
    cu = ['b365_1', 'b365_x', 'b365_2', 'b365_over25', 'b365_under25']
    ev = df[(df.fecha >= a.desde) & df.liga.isin(ligas.LIGAS) & df[cu].notna().all(axis=1)].copy()
    ev['semana'] = ev.fecha.dt.to_period('W-SUN').dt.start_time
    filas, sin_ancla = [], 0
    for t, g in ev.groupby('semana'):
        B = motor.Base(df[df.fecha < t], hasta=t)
        for x in g.itertuples():
            if x.liga not in B.co:
                continue
            try:
                R = motor.analizar(B, x.local, x.visitante, x.liga, hasta=t)
            except SystemExit:
                continue
            p1, px, _ = potencia([x.b365_1, x.b365_x, x.b365_2])
            po = motor.sin_margen_2(x.b365_over25, x.b365_under25)
            z = anclar(p1, px, po, R['lh'], R['la'])
            if z is None:
                sin_ancla += 1; continue
            lh, la, cd = z
            Co = B.co[x.liga]
            d_mk = ((lh - la) - Co['b']) / Co['a']               # diferencia de Elo (en cientos) que implica el mercado
            Ra = dict(R, lh=lh, la=la)
            Rc = dict(Ra, elo_l=R['elo_v'] + 100 * d_mk)
            sem = int(x.Index) % 100000
            V = {'motor': simular(R, a.n, sem, motor.CAL_LOCAL, motor.CAL_EMPATE),
                 'ancla': simular(Ra, a.n, sem, 0.0, cd),
                 'ancla+c': simular(Rc, a.n, sem, 0.0, cd)}
            hay = bool(np.isfinite(R['lc']) and np.isfinite(R['lt']) and pd.notna(x.corners_l) and pd.notna(x.amar_l))
            y = real(x, hay)
            r = dict(fecha=x.fecha, liga=x.liga, local=x.local, visitante=x.visitante,
                     lh_motor=R['lh'], la_motor=R['la'], lh_ancla=lh, la_ancla=la, cd=cd)
            for v, S in V.items():
                C = sm.catalogo(S)
                for m in GOLES + (STATS if hay else []):
                    r[f'{v}|{m}'] = sm.prob(S, C, m)[0]
            for m in GOLES + (STATS if hay else []):
                r[f'y|{m}'] = y(m)
            filas.append(r)
        print(f'{t.date()}  {len(filas):5d} partidos', flush=True)
    T = pd.DataFrame(filas)
    (DIR / 'resultados').mkdir(exist_ok=True)
    T.to_csv(DIR / 'resultados' / 'ancla_predicciones.csv', index=False)

    ini = (T.fecha - pd.DateOffset(months=6)).dt.year          # temporada: de julio a junio
    temp = (ini.astype(str) + '-' + ((ini + 1) % 100).astype(str).str.zfill(2)).values
    TEMPS = sorted(set(temp))
    L = [f'Partidos: {len(T):,} ({T.fecha.min().date()} a {T.fecha.max().date()}), {a.n} simulaciones, '
         f'{sin_ancla} sin ancla (no converge)',
         f'Goles esperados medios: motor {T.lh_motor.mean() + T.la_motor.mean():.2f}, ancla '
         f'{T.lh_ancla.mean() + T.la_ancla.mean():.2f}; peso medio del empate en el ancla {T.cd.mean():+.3f} '
         f'(motor {motor.CAL_EMPATE:+.3f})', '',
         f'{"mercado":30s} {"n":>5s} {"real":>6s} {"motor":>6s} {"ancla":>6s} {"LL motor":>8s} {"LL ancla":>8s} '
         f'{"dif":>8s} {"IC95":>17s} ' + ' '.join(f'{s[2:]:>8s}' for s in TEMPS) + f'  {"dif +c":>8s}']
    for m in GOLES + STATS:
        ok = T[f'y|{m}'].notna().values
        y = T.loc[ok, f'y|{m}'].astype(bool).values
        lm, la_, lc = (ll(T.loc[ok, f'{v}|{m}'], y) for v in ('motor', 'ancla', 'ancla+c'))
        d = la_ - lm; se = d.std(ddof=1) / np.sqrt(len(d))
        ds = [d[temp[ok] == s].mean() for s in TEMPS]
        L.append(f'{m + (" *" if m in ANCLADOS else ""):30s} {ok.sum():5d} {y.mean()*100:5.1f}% '
                 f'{T.loc[ok, f"motor|{m}"].mean()*100:5.1f}% {T.loc[ok, f"ancla|{m}"].mean()*100:5.1f}% '
                 f'{lm.mean():8.4f} {la_.mean():8.4f} {d.mean():+8.4f} [{d.mean()-1.96*se:+.4f},{d.mean()+1.96*se:+.4f}] '
                 + ' '.join(f'{x:+8.4f}' for x in ds) + f'  {(lc - lm).mean():+8.4f}')
    L += ['', '* = mercado anclado (control: el ancla ES la apertura de bet365 sin margen).',
          'dif = LL ancla - LL motor, emparejada por partido; negativo = gana el ancla. IC95 normal.',
          'dif +c = igual con el dominio de corners sacado del mercado (solo cambia en corners).']
    txt = '\n'.join(L); print(txt)
    (DIR / 'resultados' / 'ancla_mercado.txt').write_text(txt + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()

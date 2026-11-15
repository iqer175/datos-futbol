#!/usr/bin/env python3
"""
backtest_shin.py -- ¿quitar el margen de bet365 con el método de Shin mejora el ancla?

El ancla (simulador.anclar_al_mercado) saca las probabilidades sin margen de 1X2 y Más/Menos 2.5
con el método de la potencia. Aquí se comparan tres métodos sobre los mismos partidos que
backtest_ancla.py (resultados/ancla_predicciones.csv da partidos y punto de arranque):
  prop      proporcional (1/cuota normalizado)
  potencia  el vigente
  shin      Shin (1992/1993): margen atribuido a apostadores informados; corrige más las cuotas altas
Medidas (diferencia emparejada frente a potencia, negativo = mejor):
  1. log-loss directo de 1X2 y Más 2.5 (lo que cambia el método)
  2. log-loss de los mercados derivados tras anclar con la matriz de marcadores (analítica)
  3. hándicap asiático: CLV de las apuestas con EV >= 0 (lógica de backtest_ah.py)
Uso:  python backtest_shin.py
Salida: resultados/shin_backtest.txt
"""
from math import exp, log
from pathlib import Path

import numpy as np, pandas as pd
from scipy.optimize import brentq, root

import backtest_ah as ah
import motor

DIR = Path(__file__).parent
K = np.arange(11); I, J = np.indices((11, 11))
LOGF = np.cumsum(np.log(np.maximum(K, 1)))


def prop(o):
    q = 1 / np.asarray(o, float); return q / q.sum()


def potencia(o):
    q = 1 / np.asarray(o, float); lo, hi = .2, 5
    for _ in range(60):
        k = (lo + hi) / 2
        if (q ** k).sum() < 1: hi = k
        else: lo = k
    p = q ** ((lo + hi) / 2); return p / p.sum()


def shin(o):
    q = 1 / np.asarray(o, float); Q = q.sum()
    if Q <= 1:
        return q / Q
    f = lambda z: (np.sqrt(z * z + 4 * (1 - z) * q * q / Q) - z).sum() / (2 * (1 - z)) - 1
    z = brentq(f, 0.0, 0.5)
    p = (np.sqrt(z * z + 4 * (1 - z) * q * q / Q) - z) / (2 * (1 - z)); return p / p.sum()


METODOS = {'prop': prop, 'potencia': potencia, 'shin': shin}


def matriz(lh, la, cd):
    M = np.outer(np.exp(-lh + K * log(lh) - LOGF), np.exp(-la + K * log(la) - LOGF))
    M[I == J] *= exp(cd); return M / M.sum()


def anclar(p1, px, po, z0):
    def f(z):
        M = matriz(exp(z[0]), exp(z[1]), z[2])
        return [M[I > J].sum() - p1, M[I == J].sum() - px, M[I + J > 2.5].sum() - po]
    r = root(f, z0, method='hybr')
    if not r.success or np.abs(r.fun).max() > 1e-4:
        return None
    return matriz(exp(r.x[0]), exp(r.x[1]), r.x[2])


DERIVADOS = {
    'Más 1.5': lambda M: M[I + J > 1.5].sum(), 'Más 3.5': lambda M: M[I + J > 3.5].sum(),
    'Ambos marcan - Sí': lambda M: M[(I > 0) & (J > 0)].sum(),
    'Local más 1.5': lambda M: M[I > 1.5].sum(), 'Visitante más 1.5': lambda M: M[J > 1.5].sum(),
    'Hándicap local -1.5': lambda M: M[I - J > 1.5].sum(), 'Hándicap visitante -1.5': lambda M: M[J - I > 1.5].sum(),
    '1 & Más 2.5': lambda M: M[(I > J) & (I + J > 2.5)].sum(), '2 & Más 2.5': lambda M: M[(I < J) & (I + J > 2.5)].sum(),
    'X & Menos 2.5': lambda M: M[(I == J) & (I + J < 2.5)].sum(),
    'Más 2.5 & Ambos marcan - Sí': lambda M: M[(I + J > 2.5) & (I > 0) & (J > 0)].sum(),
}
REAL = {
    'Más 1.5': lambda g, v: g + v > 1.5, 'Más 3.5': lambda g, v: g + v > 3.5, 'Ambos marcan - Sí': lambda g, v: g > 0 and v > 0,
    'Local más 1.5': lambda g, v: g > 1.5, 'Visitante más 1.5': lambda g, v: v > 1.5,
    'Hándicap local -1.5': lambda g, v: g - v > 1.5, 'Hándicap visitante -1.5': lambda g, v: v - g > 1.5,
    '1 & Más 2.5': lambda g, v: g > v and g + v > 2.5, '2 & Más 2.5': lambda g, v: g < v and g + v > 2.5,
    'X & Menos 2.5': lambda g, v: g == v and g + v < 2.5,
    'Más 2.5 & Ambos marcan - Sí': lambda g, v: g + v > 2.5 and g > 0 and v > 0,
}


def ll(p, y):
    p = min(max(p, 1e-6), 1 - 1e-6); return -log(p if y else 1 - p)


def main():
    P = pd.read_csv(DIR / 'resultados/ancla_predicciones.csv',
                    usecols=['fecha', 'local', 'visitante', 'lh_ancla', 'la_ancla', 'cd'])
    B = pd.read_csv(DIR / 'datos/partidos.csv', low_memory=False,
                    usecols=['fecha', 'local', 'visitante', 'temporada', 'gl', 'gv', 'b365_1', 'b365_x', 'b365_2',
                             'b365_over25', 'b365_under25', 'ah_linea', 'b365_ah1', 'b365_ah2', 'ahc_linea',
                             'bfec_ah1', 'bfec_ah2', 'avgc_ah1', 'avgc_ah2'])
    T = P.merge(B, on=['fecha', 'local', 'visitante']).dropna(subset=['gl', 'gv', 'b365_1', 'b365_over25', 'lh_ancla'])
    filas, ahf, fallos = [], [], {m: 0 for m in METODOS}
    for r in T.itertuples(index=False):
        g, v = int(r.gl), int(r.gv); res = 0 if g > v else (1 if g == v else 2)
        z0 = [log(r.lh_ancla), log(r.la_ancla), r.cd]
        fila = dict(fecha=r.fecha, temporada=r.temporada)
        ok = True
        Ms = {}
        for m, fn in METODOS.items():
            p = fn([r.b365_1, r.b365_x, r.b365_2]); po = fn([r.b365_over25, r.b365_under25])[0]
            fila[f'{m}|1x2'] = -log(max(p[res], 1e-6))
            fila[f'{m}|Más 2.5'] = ll(po, g + v > 2.5)
            fila[f'{m}|p1'] = p[0]; fila[f'{m}|p2'] = p[2]; fila[f'{m}|po'] = po
            M = anclar(p[0], p[1], po, z0)
            if M is None:
                fallos[m] += 1; ok = False; break
            Ms[m] = M
            for k, f in DERIVADOS.items():
                fila[f'{m}|{k}'] = ll(f(M), REAL[k](g, v))
        if not ok:
            continue
        filas.append(fila)
        # hándicap asiático
        if pd.notna(r.ah_linea) and r.b365_ah1 > 1 and r.b365_ah2 > 1:
            misma = r.ahc_linea == r.ah_linea
            for lado, o in (('L', r.b365_ah1), ('V', r.b365_ah2)):
                if o < 1.5:
                    continue
                clv = np.nan
                if misma and r.bfec_ah1 > 1 and r.bfec_ah2 > 1:
                    clv = ah.ev_cierre(o, r.bfec_ah1, r.bfec_ah2, lado)
                elif misma and r.avgc_ah1 > 1 and r.avgc_ah2 > 1:
                    clv = ah.ev_cierre(o, r.avgc_ah1, r.avgc_ah2, lado)
                a = dict(pago=ah.pago(g - v, r.ah_linea, o, lado), clv=clv)
                for m, M in Ms.items():
                    dd = {d: M[I - J == d].sum() for d in range(-10, 11)}
                    a[f'ev_{m}'] = ah.ev(dd, r.ah_linea, o, lado)
                ahf.append(a)
    F = pd.DataFrame(filas); A = pd.DataFrame(ahf)
    temps = sorted(F.temporada.dropna().unique())
    L = [f'Partidos: {len(F):,} ({F.fecha.min()} a {F.fecha.max()}); sin solución del ancla: {fallos}',
         'Media de probabilidades: ' + '  '.join(
             f'{m}: 1 {F[f"{m}|p1"].mean():.3f} 2 {F[f"{m}|p2"].mean():.3f} Más2.5 {F[f"{m}|po"].mean():.3f}' for m in METODOS),
         '', 'Log-loss (media) y diferencia emparejada frente a potencia (negativo = mejor), IC95 y por temporada',
         f'{"mercado":28s} {"potencia":>8s} {"prop-pot":>9s} {"shin-pot":>9s} {"IC95 shin":>19s}  ' + ' '.join(f'{t:>8s}' for t in temps)]
    for k in ['1x2', 'Más 2.5'] + list(DERIVADOS):
        base = F[f'potencia|{k}']
        dp = F[f'prop|{k}'] - base; ds = F[f'shin|{k}'] - base
        se = ds.std() / np.sqrt(len(ds))
        L.append(f'{k:28s} {base.mean():8.4f} {dp.mean():+9.5f} {ds.mean():+9.5f} [{ds.mean()-1.96*se:+.5f},{ds.mean()+1.96*se:+.5f}]  '
                 + ' '.join(f'{ds[F.temporada == t].mean():+8.5f}' for t in temps))
    L += ['', 'Hándicap asiático (cuota >= 1.50), apuestas con EV >= umbral según cada método:']
    for m in METODOS:
        for u in (0.0, 0.03):
            s = A[A[f'ev_{m}'] >= u]; c = s.clv.dropna()
            L.append(f'  {m:9s} EV >= {u:.0%}  n={len(s):>5,}  ROI {s.pago.mean():+.1%}  CLV {c.mean():+.2%} '
                     f'[±{1.96*c.std()/np.sqrt(max(len(c),1)):.2%}] (n={len(c):,})')
    c = A.clv.dropna(); L.append(f'  control (todas)   n={len(A):>5,}  ROI {A.pago.mean():+.1%}  CLV {c.mean():+.2%}')
    txt = '\n'.join(L); print(txt)
    (DIR / 'resultados/shin_backtest.txt').write_text(txt + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()

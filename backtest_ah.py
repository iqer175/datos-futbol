#!/usr/bin/env python3
"""
backtest_ah.py -- ¿el sistema nuevo (goles anclados a bet365) encuentra valor real en el
hándicap asiático, y mejor que el sistema viejo (motor v5 sin ancla)?

Es el único mercado derivado de goles con cuotas históricas de bet365 (apertura) y de cierre
(Betfair Exchange / media). El 1X2 y el Más/Menos 2.5 no se pueden probar con el ancla: son el
propio ancla (probabilidad = bet365 sin margen, nunca hay valor).

Entrada: resultados/ancla_predicciones.csv (backtest_ancla.py: lh/la del motor v5 y del ancla,
         peso del empate del ancla) + datos/partidos.csv (cuotas y resultado).
Para cada partido y lado del hándicap de apertura de bet365 (líneas de cuarto partidas en dos):
  EV del modelo = sum(prob x pago) a la cuota de apertura de bet365.
  Apuesta si EV >= umbral y cuota >= 1.50.  Se mide:
    - beneficio real por unidad (ROI)
    - CLV: EV de esa cuota con las probabilidades del cierre de Betfair sin margen (misma línea),
      y con la media de cierre si no hay Betfair.
Uso:  python backtest_ah.py
Salida: resultados/ah_backtest.txt
"""
from math import exp, log
from pathlib import Path

import numpy as np, pandas as pd

DIR = Path(__file__).parent
K = np.arange(11); I, J = np.indices((11, 11)); D = I - J
LOGF = np.cumsum(np.log(np.maximum(K, 1)))
CAL_LOCAL_V5, CAL_EMPATE_V5 = 0.061, 0.177      # motor v5 de producción (motor_v6_verificacion.txt)


def dist_dif(lh, la, cd, cl=0.0):
    M = np.outer(np.exp(-lh + K * log(lh) - LOGF), np.exp(-la + K * log(la) - LOGF))
    M[I == J] *= exp(cd); M[I > J] *= exp(cl); M /= M.sum()
    return {d: M[D == d].sum() for d in range(-10, 11)}


def partes(h):
    """Línea de cuarto -> dos medias apuestas."""
    return [h - 0.25, h + 0.25] if abs((h * 4) % 2 - 1) < 1e-9 else [h]


def ev(pd_, h, o, lado):
    """EV por unidad apostada al lado 'L' (local con hándicap h) o 'V' (visitante con -h)."""
    ps = partes(h); tot = 0.0
    for x in ps:
        for d, p in pd_.items():
            m = (d + x) if lado == 'L' else (-d - x)
            tot += p * ((o - 1) if m > 0 else (0 if m == 0 else -1)) / len(ps)
    return tot


def pago(dif, h, o, lado):
    ps = partes(h); tot = 0.0
    for x in ps:
        m = (dif + x) if lado == 'L' else (-dif - x)
        tot += ((o - 1) if m > 0 else (0 if m == 0 else -1)) / len(ps)
    return tot


def ev_cierre(o, oc1, oc2, lado):
    """EV de la cuota o con las cuotas de cierre de la MISMA línea: el cierre sin margen da
    q = prob de ganar 'efectiva' del lado; EV ≈ o*q_eff - 1 con q_eff = (1/oc)/(1/oc1+1/oc2)."""
    q1, q2 = 1 / oc1, 1 / oc2
    q = (q1 if lado == 'L' else q2) / (q1 + q2)
    return o * q - 1          # aproximación: trata la línea como de dos salidas (sin nula)


def main():
    P = pd.read_csv(DIR / 'resultados/ancla_predicciones.csv',
                    usecols=['fecha', 'liga', 'local', 'visitante', 'lh_motor', 'la_motor', 'lh_ancla', 'la_ancla', 'cd'])
    B = pd.read_csv(DIR / 'datos/partidos.csv', low_memory=False,
                    usecols=['fecha', 'local', 'visitante', 'temporada', 'gl', 'gv', 'ah_linea', 'b365_ah1', 'b365_ah2',
                             'ahc_linea', 'bfec_ah1', 'bfec_ah2', 'avgc_ah1', 'avgc_ah2'])
    T = P.merge(B, on=['fecha', 'local', 'visitante'], how='inner').dropna(subset=['ah_linea', 'b365_ah1', 'b365_ah2', 'gl', 'gv'])
    T = T[(T.b365_ah1 > 1) & (T.b365_ah2 > 1)]
    filas = []
    for r in T.itertuples(index=False):
        dists = {'viejo': dist_dif(r.lh_motor, r.la_motor, CAL_EMPATE_V5, CAL_LOCAL_V5)}
        if np.isfinite(r.lh_ancla):
            dists['nuevo'] = dist_dif(r.lh_ancla, r.la_ancla, r.cd)
        misma = r.ahc_linea == r.ah_linea
        for lado, o in (('L', r.b365_ah1), ('V', r.b365_ah2)):
            clv_bf = ev_cierre(o, r.bfec_ah1, r.bfec_ah2, lado) if misma and r.bfec_ah1 > 1 and r.bfec_ah2 > 1 else np.nan
            clv_av = ev_cierre(o, r.avgc_ah1, r.avgc_ah2, lado) if misma and r.avgc_ah1 > 1 and r.avgc_ah2 > 1 else np.nan
            fila = dict(fecha=r.fecha, temporada=r.temporada, liga=r.liga, lado=lado, linea=r.ah_linea, cuota=o,
                        pago=pago(r.gl - r.gv, r.ah_linea, o, lado), clv_bf=clv_bf, clv_av=clv_av)
            for k, dd in dists.items():
                fila[f'ev_{k}'] = ev(dd, r.ah_linea, o, lado)
            filas.append(fila)
    F = pd.DataFrame(filas)
    F['clv'] = F.clv_bf.fillna(F.clv_av)
    F.to_csv(DIR / 'resultados/ah_backtest.csv', index=False)

    out = [f'Partidos con hándicap asiático de bet365 y predicción: {len(T):,} ({T.fecha.min()} a {T.fecha.max()}); '
           f'filas lado-partido {len(F):,}; con CLV (misma línea al cierre) {F.clv.notna().mean():.0%} '
           f'(Betfair {F.clv_bf.notna().mean():.0%})',
           'viejo = motor v5 sin ancla; nuevo = goles anclados a 1X2 y Más/Menos 2.5 de bet365 (apertura)',
           'EV = valor esperado según el modelo a la cuota de apertura de bet365; ROI = beneficio real; '
           'CLV = valor de esa cuota según el cierre (Betfair sin margen; media si no hay)', '']

    def bloque(sel, nombre):
        n = len(sel)
        if n == 0:
            return f'  {nombre:<34} n=0'
        roi = sel.pago.mean(); se = sel.pago.std() / np.sqrt(n)
        c = sel.clv.dropna(); cse = c.std() / np.sqrt(max(len(c), 1))
        return (f'  {nombre:<34} n={n:>6,}  EV modelo {sel.ev.mean():+.1%}  ROI {roi:+.1%} [±{1.96*se:.1%}]  '
                f'CLV {c.mean():+.2%} [±{1.96*cse:.2%}] (n={len(c):,})')

    base = F.assign(ev=0.0)
    out.append('Control: todas las apuestas posibles (los dos lados, cuota >= 1.50)')
    out.append(bloque(base[base.cuota >= 1.5], 'todas'))
    out.append('')
    for k in ('viejo', 'nuevo'):
        out.append(f'Sistema {k}:')
        G = F.dropna(subset=[f'ev_{k}']).assign(ev=lambda x: x[f'ev_{k}'])
        G = G[G.cuota >= 1.5]
        for u in (0.0, 0.03, 0.06, 0.10):
            out.append(bloque(G[G.ev >= u], f'EV >= {u:.0%}'))
        out.append('  por temporada (EV >= 3%):')
        for t, g in G[G.ev >= 0.03].groupby('temporada'):
            out.append('  ' + bloque(g, str(t)))
        out.append('')
    # comparación emparejada: misma muestra, apuestas donde solo uno de los dos ve valor
    G = F.dropna(subset=['ev_nuevo'])
    G = G[G.cuota >= 1.5]
    out.append('Cuando discrepan (EV >= 3% en uno y < 0 en el otro):')
    out.append(bloque(G[(G.ev_nuevo >= .03) & (G.ev_viejo < 0)].assign(ev=lambda x: x.ev_nuevo), 'solo el nuevo apuesta'))
    out.append(bloque(G[(G.ev_viejo >= .03) & (G.ev_nuevo < 0)].assign(ev=lambda x: x.ev_viejo), 'solo el viejo apuesta'))
    out.append('')
    out.append('Calibración del EV del nuevo (todas las filas con cuota >= 1.50), por tramo de EV:')
    G['tramo'] = pd.cut(G.ev_nuevo, [-1, -.06, -.03, 0, .03, .06, .1, 1])
    for t, g in G.groupby('tramo', observed=True):
        c = g.clv.dropna()
        out.append(f'  {str(t):<16} n={len(g):>6,}  EV {g.ev_nuevo.mean():+.1%}  ROI {g.pago.mean():+.1%}  CLV {c.mean():+.2%}')
    txt = '\n'.join(out); print(txt)
    (DIR / 'resultados/ah_backtest.txt').write_text(txt + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()

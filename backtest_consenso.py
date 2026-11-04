#!/usr/bin/env python3
"""
backtest_consenso.py -- ¿se puede ganar a bet365 apostando cuando se aparta del consenso?

No usa el motor. Es la estrategia de Kaunitz, Zhong y Kreiner (2017): en el
mismo instante (cuota "previa" de football-data, capturada a la vez para todas
las casas), si bet365 paga mas que la probabilidad justa del consenso, la
apuesta tiene valor esperado positivo... siempre que el consenso sea mejor
estimador que bet365.

Estrategias (entrada SIEMPRE a la cuota previa de bet365):
  1X2_vs_media    consenso = media previa de casas (avg_*) sin margen
  1X2_vs_betfair  consenso = Betfair Exchange previo (bfe_*) sin margen   [desde 2024-25]
  OU_vs_media     Mas/Menos 2,5, consenso = avg_over25/avg_under25
  OU_vs_betfair   Mas/Menos 2,5, consenso = bfe_over25/bfe_under25        [desde 2024-25]

CLV de cada apuesta frente a dos cierres sin margen (potencia):
  clv_media   = cuota * p(avgc)  - 1
  clv_betfair = cuota * p(bfec)  - 1    (solo 2024-25 en adelante)
El cierre de Betfair es la referencia mas eficiente que hay en los datos.

Uso:  python backtest_consenso.py
Salida: resultados/consenso_resumen.txt y resultados/consenso_apuestas.csv
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd

from backtest_clv import potencia, ic_semanas

DIR = Path(__file__).parent
OUT = DIR / 'resultados'
UMBRALES = [0.0, 0.01, 0.02, 0.03, 0.05]
INI_RESERVADO = pd.Timestamp('2024-07-01')

MERCADOS = {
    '1X2': dict(sel=['1', 'x', '2'], res=['H', 'D', 'A'],
                b365=['b365_1', 'b365_x', 'b365_2'],
                media=['avg_1', 'avg_x', 'avg_2'], betfair=['bfe_1', 'bfe_x', 'bfe_2'],
                c_media=['avgc_1', 'avgc_x', 'avgc_2'], c_betfair=['bfec_1', 'bfec_x', 'bfec_2']),
    'OU': dict(sel=['over', 'under'], res=['O', 'U'],
               b365=['b365_over25', 'b365_under25'],
               media=['avg_over25', 'avg_under25'], betfair=['bfe_over25', 'bfe_under25'],
               c_media=['avgc_over25', 'avgc_under25'], c_betfair=['bfec_over25', 'bfec_under25']),
}


def sin_margen(df, cols):
    """Probabilidades sin margen; NaN en las filas incompletas o con cuotas < 1.01."""
    o = df[cols].values.astype(float)
    ok = np.isfinite(o).all(1) & (o > 1.01).all(1)
    p = np.full(o.shape, np.nan)
    if ok.any():
        p[ok] = potencia(o[ok])
    return p


def apuestas(df, mercado, consenso, umbral):
    M = MERCADOS[mercado]
    o = df[M['b365']].values.astype(float)
    pr = sin_margen(df, M[consenso])
    pcm, pcb = sin_margen(df, M['c_media']), sin_margen(df, M['c_betfair'])
    if mercado == '1X2':
        real = df.res.values
    else:
        real = np.where(df.gl + df.gv > 2.5, 'O', 'U')
    filas = []
    for k, (s, code) in enumerate(zip(M['sel'], M['res'])):
        edge = o[:, k] * pr[:, k] - 1
        sel = np.isfinite(edge) & (edge > umbral) & np.isfinite(pcm[:, k])
        if not sel.any():
            continue
        x = df.loc[sel, ['fecha', 'semana', 'liga', 'local', 'visitante', 'bloque']].copy()
        x['mercado'] = mercado; x['consenso'] = consenso; x['umbral'] = umbral
        x['sel'] = s; x['cuota'] = o[sel, k]; x['edge'] = edge[sel]
        x['clv_media'] = o[sel, k] * pcm[sel, k] - 1
        x['clv_betfair'] = o[sel, k] * pcb[sel, k] - 1
        x['gana'] = real[sel] == code
        x['beneficio'] = np.where(x.gana, x.cuota - 1, -1.0)
        filas.append(x)
    return pd.concat(filas) if filas else pd.DataFrame()


def linea(b, col):
    v = b.dropna(subset=[col])
    if len(v) < 20:
        return f'{col}: n<20'
    lo, hi = ic_semanas(v, col)
    return f'{col} {v[col].mean()*100:+.2f}% [{lo*100:+.2f},{hi*100:+.2f}] (n={len(v)})'


def main():
    OUT.mkdir(exist_ok=True)
    df = pd.read_csv(DIR / 'datos' / 'partidos.csv', low_memory=False)
    df['fecha'] = pd.to_datetime(df.fecha)
    df = df[df.b365_1.notna()].copy()     # solo ligas con cuota previa de bet365
    df['semana'] = df.fecha.dt.to_period('W-SUN').dt.start_time
    df['bloque'] = np.where(df.fecha >= INI_RESERVADO, 'reservado', 'desarrollo')
    df = df.reset_index(drop=True)

    lineas, todas = [], []
    def p(s=''):
        print(s); lineas.append(s)

    p(f'Partidos con cuota previa bet365: {len(df):,}  ({df.fecha.min().date()} a {df.fecha.max().date()})')
    for mercado in MERCADOS:
        for consenso in ('media', 'betfair'):
            p(f'\n=== {mercado}: bet365 frente a {consenso} previa (valor = cuota_b365 · p_consenso - 1 > umbral) ===')
            for bl, g in df.groupby('bloque'):
                p(f'[{bl}]')
                for u in UMBRALES:
                    b = apuestas(g, mercado, consenso, u)
                    if not len(b):
                        p(f'  umbral {u*100:3.0f}%  sin apuestas'); continue
                    todas.append(b)
                    p(f'  umbral {u*100:3.0f}%  n={len(b):5d} ({len(b)/len(g)*100:4.1f}% de partidos)  '
                      f'{linea(b, "clv_media")}  {linea(b, "clv_betfair")}  '
                      f'ROI {b.beneficio.mean()*100:+.1f}%  cuota media {b.cuota.mean():.2f}')

    T = pd.concat(todas)
    T.to_csv(OUT / 'consenso_apuestas.csv', index=False)

    # --- desglose del reservado por liga, umbral 2 % -------------------------
    for mercado, consenso in (('1X2', 'betfair'), ('OU', 'betfair'), ('1X2', 'media'), ('OU', 'media')):
        b = T[(T.bloque == 'reservado') & (T.mercado == mercado) & (T.consenso == consenso) & (T.umbral == 0.02)]
        if not len(b):
            continue
        p(f'\n=== RESERVADO por liga: {mercado} vs {consenso}, umbral 2 % ===')
        t = b.groupby('liga').agg(n=('cuota', 'size'), clv_media=('clv_media', 'mean'),
                                  clv_betfair=('clv_betfair', 'mean'),
                                  clv_bf_pos=('clv_betfair', lambda x: (x > 0).mean()),
                                  roi=('beneficio', 'mean'))
        p((t.sort_values('clv_betfair', ascending=False) * [1, 100, 100, 100, 100]).round(2).to_string())
        p('--- por seleccion ---')
        p((b.groupby('sel').agg(n=('cuota', 'size'), clv_media=('clv_media', 'mean'),
                                clv_betfair=('clv_betfair', 'mean'), roi=('beneficio', 'mean'))
           * [1, 100, 100, 100]).round(2).to_string())

    (OUT / 'consenso_resumen.txt').write_text('\n'.join(lineas), encoding='utf-8')
    print(f'\nEscrito en {OUT}/', file=sys.stderr)


if __name__ == '__main__':
    main()

"""
backtest_ou_xg_historico.py -- la variante CONGELADA O/U xG (motor.OU_XG_*) en 2025-26.

La variante se eligio con partidos de 2026-09/11 (xG de Football-Data, solo 2026-27). Con el
xG historico de FotMob (descargar_stats_equipos.py; correlacion 0,991 con el de Football-Data)
se evalua TAL CUAL en la temporada 2025-26 de las 5 grandes, que no se uso para elegirla.
No cambia ninguna constante: es una prueba fuera de muestra adicional, no un reajuste, y NO
sustituye a los criterios de seguimiento_ou_xg.py.

  python backtest_ou_xg_historico.py
  -> resultados/ou_xg_historico.csv / .txt
"""
from pathlib import Path
import numpy as np, pandas as pd
import motor, stats_equipos
from seguimiento_ou_xg import predecir, ic_semanas, llb

DIR = Path(__file__).parent
OUT = DIR / 'resultados'
DESDE, HASTA = '2025-08-15', '2026-06-30'
GRANDES = ['LaLiga', 'Premier League', 'Serie A', 'Bundesliga', 'Ligue 1']


def main():
    df = motor.cargar(DIR / 'datos' / 'partidos.csv')
    df = stats_equipos.unir(df)
    for s in ('l', 'v'):          # xG de FotMob donde Football-Data no lo trae
        df[f'xg_{s}'] = df[f'xg_{s}'].fillna(df[f'fm_xg_{s}'])
    df = df[[c for c in df.columns if not c.startswith('fm_')]]
    df = df[df.fecha <= HASTA]
    r = predecir(df, pd.Timestamp(DESDE))
    r = r[r.liga.isin(GRANDES)]
    r.to_csv(OUT / 'ou_xg_historico.csv', index=False)
    y = r.over.astype(float).values
    L = []
    def p(s=''): print(s); L.append(s)
    p(f'Variante congelada O/U xG en {DESDE}..{HASTA}, 5 grandes: {len(r)} partidos')
    sem = pd.to_datetime(r.semana).values
    for nombre, col in [('apertura bet365', 'p_apertura'), ('motor actual', 'p_motor_actual'),
                        ('motor xG', 'p_motor_xg'), ('mezcla congelada', 'p_mezcla')]:
        p(f'  log-loss {nombre:17s} {llb(r[col].values, y).mean():.4f}')
    d = llb(r.p_mezcla.values, y) - llb(r.p_apertura.values, y)
    lo, hi = ic_semanas(d, sem)
    p(f'  mezcla - apertura: {d.mean():+.4f}  IC95 [{lo:+.4f}, {hi:+.4f}]')
    for liga, g in r.groupby('liga'):
        dd = llb(g.p_mezcla.values, g.over.values.astype(float)) - llb(g.p_apertura.values, g.over.values.astype(float))
        p(f'    {liga:16s} n={len(g):4d}  {dd.mean():+.4f}')
    # CLV de la regla de la fase 2 (apuesta si p_mezcla * cuota - 1 > 0)
    ap = []
    for x in r.itertuples():
        for lado, pm, c, pc in (('Mas', x.p_mezcla, x.b365_over25, x.p_cierre_bf),
                                ('Menos', 1 - x.p_mezcla, x.b365_under25, 1 - x.p_cierre_bf)):
            if pm * c - 1 > 0 and np.isfinite(pc):
                ap.append(c * pc - 1)
    if ap:
        ap = np.array(ap); bs = [np.random.default_rng(i).choice(ap, len(ap)).mean() for i in range(2000)]
        p(f'  apuestas regla fase 2: {len(ap)}  CLV medio vs cierre Betfair {ap.mean():+.3%}  '
          f'IC95 [{np.percentile(bs, 2.5):+.3%}, {np.percentile(bs, 97.5):+.3%}]')
    (OUT / 'ou_xg_historico.txt').write_text('\n'.join(L), encoding='utf-8')


if __name__ == '__main__':
    main()

"""
stats_equipos.py -- cruza datos/stats_equipos.csv (FotMob) con datos/partidos.csv.

  import stats_equipos
  df = stats_equipos.unir(df)    # anade fm_<stat>_l / fm_<stat>_v y fm_partido_id a cada partido emparejado

Emparejado por nombre de equipo (datos/arbitros_equipos.csv: partidos.csv -> FotMob),
fecha +-1 dia y marcador. Partidos sin emparejar quedan en NaN.
"""
from pathlib import Path
import numpy as np, pandas as pd

RAIZ = Path(__file__).parent
STATS = RAIZ / 'datos' / 'stats_equipos.csv'
ALIAS = RAIZ / 'datos' / 'arbitros_equipos.csv'
CAMPOS = ['goles', 'remates', 'al_arco', 'corners', 'faltas', 'tarjetas', 'recibidas', 'entradas',
          'generados', 'xg', 'xa', 'pases', 'centros', 'offsides', 'amarillas', 'rem_fuera',
          'arco_fuera', 'cabeza', 'cabeza_arco', 'rem_pt', 'arco_pt', 'atajadas']


def unir(df):
    S = pd.read_csv(STATS)
    S['dia'] = pd.to_datetime(S.fecha, utc=True).dt.tz_localize(None).dt.normalize()
    S = S[S.local == 1].drop_duplicates('partido_id')      # una fila por partido, vista del local
    A = pd.read_csv(ALIAS)
    al = {(r.liga, r.equipo): r.fotmob for r in A.itertuples()}
    d = df.copy()
    d['_dia'] = pd.to_datetime(d.fecha).dt.normalize()
    d['_fl'] = [al.get((l, e)) for l, e in zip(d.liga, d.local)]
    d['_fv'] = [al.get((l, e)) for l, e in zip(d.liga, d.visitante)]
    d['_i'] = np.arange(len(d))
    cols = {f's_{c}': f'fm_{c}_l' for c in CAMPOS if f's_{c}' in S}
    cols.update({f'r_{c}': f'fm_{c}_v' for c in CAMPOS if f'r_{c}' in S})
    out = []
    for k in (0, -1, 1):     # mismo dia primero; luego +-1 (husos)
        S2 = S.assign(_dia=S.dia + pd.Timedelta(days=k))
        m = d.merge(S2[['_dia', 'equipo', 'rival', 'gf', 'gc', 'partido_id'] + list(cols)],
                    left_on=['_dia', '_fl', '_fv'], right_on=['_dia', 'equipo', 'rival'])
        m = m[(m.gf == m.gl) & (m.gc == m.gv)]
        out.append(m[['_i', 'partido_id'] + list(cols)])
    M = pd.concat(out).drop_duplicates('_i').rename(columns={**cols, 'partido_id': 'fm_partido_id'})
    d = d.merge(M, on='_i', how='left').drop(columns=['_dia', '_fl', '_fv', '_i'])
    return d


if __name__ == '__main__':
    import motor
    df = unir(motor.cargar(RAIZ / 'datos' / 'partidos.csv'))
    x = df[df.fecha >= '2024-08-15']
    print(x.groupby('liga').fm_xg_l.apply(lambda s: s.notna().mean()).round(2).to_string())
    y = df[df.xg_l.notna() & df.fm_xg_l.notna()]
    if len(y):
        print(f'xG Football-Data vs FotMob (n={len(y)}): corr {np.corrcoef(y.xg_l, y.fm_xg_l)[0,1]:.3f}, '
              f'medias {y.xg_l.mean():.2f} / {y.fm_xg_l.mean():.2f}')

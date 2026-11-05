#!/usr/bin/env python3
"""
emparejar_nombres.py -- traduce los nombres de equipo de las capturas
(The Odds API, API-Football) a los de football-data (partidos.csv).

Para cada liga se emparejan los nombres de la fuente con los equipos que han
jugado esa liga en los ultimos 400 dias, con una asignacion uno a uno
(algoritmo hungaro) sobre una similitud de texto normalizado. La asignacion
resuelve por eliminacion los casos imposibles por texto (Vitoria SC ->
Guimaraes) siempre que el resto de la liga este bien.

El mapa se guarda en datos/mapa_nombres.csv:
  fuente, liga, nombre_src, nombre_fd, metodo (auto | manual), similitud
Las filas con metodo=manual NUNCA se sobrescriben: para corregir un error,
cambia nombre_fd y pon metodo=manual. Las de similitud < 0.45 se listan como
"revisar".

Uso:  python emparejar_nombres.py
"""
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np, pandas as pd
from scipy.optimize import linear_sum_assignment

DIR = Path(__file__).parent
MAPA = DIR / 'datos' / 'mapa_nombres.csv'
DIR_CAP = DIR / 'datos' / 'capturas'
UMBRAL_REVISAR = 0.45

RUIDO = {'fc', 'cf', 'afc', 'sc', 'sk', 'jk', 'fk', 'ad', 'cd', 'sd', 'ud', 'us', 'kv', 'ksv',
         'ac', 'as', 'sv', 'vfl', 'bb', 'nfc', 'af', 'usl', 'raal', '1', '1929', '96', '98'}
ABREV = {'ath': 'atletico', 'sp': 'sporting', 'man': 'manchester', 'utd': 'united',
         'st': 'saint', "nott'm": 'nottingham', 'nottm': 'nottingham', 'for': 'fortuna',
         'weds': 'wednesday', 'peterboro': 'peterborough', 'qpr': 'queens park rangers',
         'wolves': 'wolverhampton', 'espanol': 'espanyol', 'goztep': 'goztepe',
         'buyuksehyr': 'basaksehir', 'olympiakos': 'olympiacos', 'athletic': 'atletico',
         'lisbon': 'lisboa', 'b': 'b'}


# nombres que no se parecen por texto: nombre normalizado de la fuente -> de football-data
ALIAS = {'vitoria': 'guimaraes', 'celta fortuna': 'celta b', 'stade lavallois': 'laval',
         'amed': 'amedspor', 'erzurum': 'erzurumspor', 'gazisehir gaziantep': 'gaziantep'}


def norm(s):
    s = unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode().lower()
    s = s.replace('-', ' ').replace('.', ' ').replace("'", '')
    toks = [ABREV.get(t, t) for t in re.split(r'\s+', s) if t]
    toks = [t for t in toks if t not in RUIDO]
    n = ' '.join(toks)
    return ALIAS.get(n, n)


def similitud(a, b):
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    r = SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    # contencion de palabras: "standard liege" ~ "standard", "leuven" ~ "oud heverlee leuven"
    cont = len(ta & tb) / min(len(ta), len(tb))
    # prefijo comun (abreviaturas tipo "Sp Gijon" / "Sporting Gijon" ya expandidas)
    pre = max((SequenceMatcher(None, x, y).ratio() for x in ta for y in tb), default=0)
    return max(r, 0.9 * cont, 0.85 * pre if len(ta) == 1 or len(tb) == 1 else 0.7 * pre)


def equipos_fd(df, liga, hasta):
    """Equipos de la liga en la temporada en curso (para no emparejar con
    descendidos); si la temporada aun no ha empezado, los de los ultimos 400 dias."""
    d = df[(df.liga == liga) & (df.fecha >= hasta - pd.Timedelta(days=400))]
    if 'temporada' in d and len(d):
        act = d[d.temporada == d.sort_values('fecha').temporada.iloc[-1]]
        if len(act) >= 20:
            d = act
    return sorted(set(d.local) | set(d.visitante))


def emparejar(capturas, df):
    previo = pd.read_csv(MAPA) if MAPA.exists() else pd.DataFrame(
        columns=['fuente', 'liga', 'nombre_src', 'nombre_fd', 'metodo', 'similitud'])
    manual = previo[previo.metodo == 'manual']
    fijos = {(r.fuente, r.liga, r.nombre_src): r.nombre_fd for r in manual.itertuples()}
    hasta = df.fecha.max() + pd.Timedelta(days=1)
    filas = [dict(r._asdict()) for r in manual.itertuples(index=False)]
    # conservar lo ya emparejado de fuentes/ligas que no vienen en esta llamada
    # (si no, emparejar solo ESPN borraria el mapa de The Odds API)
    nuevas = set(zip(capturas.fuente, capturas.liga))
    filas += [dict(r._asdict()) for r in previo[previo.metodo != 'manual'].itertuples(index=False)
              if (r.fuente, r.liga) not in nuevas]
    for (fuente, liga), g in capturas.groupby(['fuente', 'liga']):
        src = sorted((set(g.local_src) | set(g.visitante_src)) - {np.nan, None})
        src = [s for s in src if isinstance(s, str) and (fuente, liga, s) not in fijos]
        fd = [e for e in equipos_fd(df, liga, hasta) if e not in
              {v for (f, l, _), v in fijos.items() if f == fuente and l == liga}]
        if not src or not fd:
            continue
        S = np.array([[similitud(a, b) for b in fd] for a in src])
        fi, co = linear_sum_assignment(-S)
        for i, j in zip(fi, co):
            filas.append(dict(fuente=fuente, liga=liga, nombre_src=src[i], nombre_fd=fd[j],
                              metodo='auto', similitud=round(float(S[i, j]), 3)))
    m = pd.DataFrame(filas).drop_duplicates(['fuente', 'liga', 'nombre_src'], keep='first')
    m = m.sort_values(['fuente', 'liga', 'nombre_src']).reset_index(drop=True)
    MAPA.parent.mkdir(parents=True, exist_ok=True)
    m.to_csv(MAPA, index=False)
    return m


def cargar_capturas():
    fs = sorted(DIR_CAP.glob('cuotas_*.csv'))
    return pd.concat([pd.read_csv(f) for f in fs]) if fs else pd.DataFrame()


def traducir(capturas, mapa=None):
    """Anade local/visitante con nombres de football-data a un DataFrame de capturas."""
    mapa = pd.read_csv(MAPA) if mapa is None else mapa
    k = {(r.fuente, r.liga, r.nombre_src): r.nombre_fd for r in mapa.itertuples()}
    c = capturas.copy()
    c['local'] = [k.get((f, l, n)) for f, l, n in zip(c.fuente, c.liga, c.local_src)]
    c['visitante'] = [k.get((f, l, n)) for f, l, n in zip(c.fuente, c.liga, c.visitante_src)]
    return c


def main():
    df = pd.read_csv(DIR / 'datos' / 'partidos.csv', low_memory=False, usecols=['liga', 'fecha', 'temporada', 'local', 'visitante'])
    df['fecha'] = pd.to_datetime(df.fecha)
    cap = cargar_capturas()
    if not len(cap):
        print('Sin capturas todavia.'); return
    m = emparejar(cap, df)
    rev = m[(m.metodo == 'auto') & (m.similitud < UMBRAL_REVISAR)]
    print(f'{len(m)} nombres en el mapa ({(m.metodo == "manual").sum()} manuales); '
          f'{len(rev)} con similitud < {UMBRAL_REVISAR} (revisar):')
    if len(rev):
        print(rev[['liga', 'nombre_src', 'nombre_fd', 'similitud']].to_string(index=False))


if __name__ == '__main__':
    main()

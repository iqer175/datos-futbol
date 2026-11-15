"""
descargar_arbitros.py -- arbitro de cada partido historico de las 18 ligas.

ESPN no publica el arbitro; Football-Data solo en Inglaterra y Escocia. La API
publica de LudoProps (datos de FotMob) da, dia a dia, el arbitro de cada partido
desde 2024 como minimo. Se guarda solo el nombre (nunca sus medias actuales, que
mirarian al futuro): las medias del arbitro se calculan despues con nuestros
propios partidos.csv y solo con partidos anteriores.

  python descargar_arbitros.py            # descarga los dias que falten (reanudable)
  python descargar_arbitros.py --unir     # cruza con partidos.csv -> datos/arbitros_partidos.csv

Peticiones lentas (1 por segundo) y User-Agent generico.
"""
import argparse, json, sys, time, unicodedata, re
from pathlib import Path
import pandas as pd
import requests

RAIZ = Path(__file__).parent
CRUDO = RAIZ / 'datos' / 'arbitros_fotmob.jsonl'
SALIDA = RAIZ / 'datos' / 'arbitros_partidos.csv'
ALIAS = RAIZ / 'datos' / 'arbitros_equipos.csv'   # nombre en partidos.csv -> nombre en FotMob
API = 'https://ludoprops.com/futbol/api/dia'

# id de liga de FotMob -> nombre de liga en partidos.csv
LIGAS = {47: 'Premier League', 48: 'Championship', 108: 'League One', 109: 'League Two',
         87: 'LaLiga', 140: 'LaLiga Hypermotion', 55: 'Serie A', 86: 'Serie B',
         54: 'Bundesliga', 146: '2. Bundesliga', 53: 'Ligue 1', 110: 'Ligue 2',
         57: 'Eredivisie', 61: 'Liga Portugal', 71: 'Super Lig', 40: 'Jupiler Pro League',
         64: 'Scottish Premiership', 112: 'Primera Division'}


def dias_hechos():
    if not CRUDO.exists():
        return set()
    return {json.loads(l)['dia'] for l in CRUDO.open(encoding='utf-8')}


DESDE = '2024-07-01'   # antes de 2024-25 la API no trae arbitro


def descargar(muestra=False):
    p = pd.read_csv(RAIZ / 'datos' / 'partidos.csv', usecols=['fecha'])
    fechas = pd.to_datetime(p.fecha).dt.normalize()
    fechas = fechas[fechas >= DESDE]
    if muestra:   # sabados y domingos (la mayoria de partidos en ~1/4 de peticiones)
        # + todos los dias de las ultimas 6 semanas, para que el factor vea las jornadas entre semana
        fechas = fechas[(fechas.dt.dayofweek >= 5) | (fechas >= pd.Timestamp.now() - pd.Timedelta(days=45))]
    dias = sorted({f.strftime('%Y-%m-%d') for f in fechas.unique()})
    hechos = dias_hechos()
    falta = [d for d in dias if d not in hechos]
    print(f'{len(dias)} dias, faltan {len(falta)}', flush=True)
    s = requests.Session(); s.headers['User-Agent'] = 'Mozilla/5.0'
    with CRUDO.open('a', encoding='utf-8') as fh:
        for i, dia in enumerate(falta, 1):
            for intento in range(3):
                try:
                    r = s.get(API, params={'fecha': dia}, timeout=30); r.raise_for_status()
                    j = r.json(); break
                except Exception as e:
                    print(f'  {dia}: {e}', flush=True); time.sleep(5 * (intento + 1)); j = None
            if j is None:
                continue
            filas = []
            for l in j.get('ligas', []):
                if l.get('id') not in LIGAS:
                    continue
                for q in l.get('partidos', []):
                    a = q.get('arbitro') or {}
                    filas.append({'id': q['id'], 'liga': LIGAS[l['id']], 'fecha': q['fecha'],
                                  'local': q['local']['nombre'], 'visita': q['visita']['nombre'],
                                  'gl': q.get('goles_local'), 'gv': q.get('goles_visita'),
                                  'arbitro': a.get('nombre'), 'arbitro_id': a.get('id')})
            fh.write(json.dumps({'dia': dia, 'partidos': filas}, ensure_ascii=False) + '\n'); fh.flush()
            if i % 50 == 0:
                print(f'  {i}/{len(falta)}', flush=True)
            time.sleep(1.0)


def norm(s):
    s = unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode().lower()
    s = re.sub(r'\b(fc|cf|ac|afc|sc|sv|cd|ud|rc|rcd|ss|as|club|de|the|vfl|vfb|tsg|1\.|fk|sk)\b', ' ', s)
    return re.sub(r'[^a-z0-9]', '', s)


def unir():
    filas = []
    for l in CRUDO.open(encoding='utf-8'):
        filas += json.loads(l)['partidos']
    f = pd.DataFrame(filas).drop_duplicates('id')
    f = f[f.arbitro.notna() & f.gl.notna()].copy()
    f['dia'] = pd.to_datetime(f.fecha, utc=True).dt.tz_localize(None).dt.normalize()
    p = pd.read_csv(RAIZ / 'datos' / 'partidos.csv')
    p['dia'] = pd.to_datetime(p.fecha).dt.normalize()
    p['idx'] = range(len(p))
    out, alias = [], []
    for liga, g in p[p.dia >= DESDE].groupby('liga'):
        h = f[f.liga == liga]
        if h.empty:
            continue
        # emparejar por fecha (+-1 dia) y marcador; desempatar por nombres parecidos
        for _, r in g.iterrows():
            c = h[(abs((h.dia - r.dia).dt.days) <= 1) & (h.gl == r.get('goles_l', r.get('gl'))) &
                  (h.gv == r.get('goles_v', r.get('gv')))]
            if c.empty:
                continue
            nl, nv = norm(r.local), norm(r.visitante)
            def sc(x):
                a, b = norm(x.local), norm(x.visita)
                return (int(a[:4] == nl[:4] or a in nl or nl in a) + int(b[:4] == nv[:4] or b in nv or nv in b))
            c = c.assign(s=c.apply(sc, axis=1)).sort_values('s', ascending=False)
            if c.s.iloc[0] >= 1 and (len(c) == 1 or c.s.iloc[0] > c.s.iloc[1]):
                out.append((r.idx, c.arbitro.iloc[0], c.s.iloc[0]))
                alias.append((liga, r.local, c.local.iloc[0])); alias.append((liga, r.visitante, c.visita.iloc[0]))
    m = pd.DataFrame(out, columns=['idx', 'arbitro_fm', 'confianza'])
    # equivalencias de nombres (partidos.csv -> FotMob): el mas frecuente de cada equipo
    A = pd.DataFrame(alias, columns=['liga', 'equipo', 'fotmob'])
    A = A.groupby(['liga', 'equipo']).fotmob.agg(lambda x: x.value_counts().index[0]).reset_index()
    A.to_csv(ALIAS, index=False)
    res = p.merge(m, on='idx', how='left')
    res[['liga', 'fecha', 'local', 'visitante', 'arbitro_fm', 'confianza']].to_csv(SALIDA, index=False)
    cob = res.groupby('liga').arbitro_fm.apply(lambda s: s.notna().mean()).round(2)
    print(cob.to_string()); print(f'-> {SALIDA}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--unir', action='store_true')
    ap.add_argument('--muestra', action='store_true', help='solo fines de semana')
    a = ap.parse_args()
    unir() if a.unir else descargar(a.muestra)

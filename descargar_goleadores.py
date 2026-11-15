"""
descargar_goleadores.py -- muestra de partidos con el xG y los goles de cada jugador (FotMob via
la API de LudoProps) para el modelo de goleadores (2026-11-15).

La ficha de un partido (/futbol/api/partido?id=X&stat=xg|goles&ultimos=40) trae, por cada jugador
que jugo ESE partido, su valor partido a partido en los 40 anteriores del equipo (con minutos y
titularidad) y el del propio partido. Asi cada partido de la muestra es un caso fuera de muestra
natural: lo de antes predice si marco en ese. Dos peticiones por partido (xg y goles).

  python descargar_goleadores.py --n 700          # muestra aleatoria (semilla fija), reanudable
  -> datos/goleadores_fotmob.jsonl  (un registro por partido y equipo; celdas [xg, goles, min, tit])
  python descargar_goleadores.py --asistencias    # los MISMOS partidos con xa y asist
  -> datos/asistencias_fotmob.jsonl (celdas [xa, asist, min, tit])
"""
import argparse, json, sys, time
from pathlib import Path
import numpy as np, pandas as pd
import requests

DIR = Path(__file__).parent
SALIDA = DIR / 'datos' / 'goleadores_fotmob.jsonl'
SALIDA_ASIST = DIR / 'datos' / 'asistencias_fotmob.jsonl'
API = 'https://ludoprops.com/futbol/api/partido'


def pedir(s, pid, stat):
    for i in range(3):
        try:
            r = s.get(API, params={'id': pid, 'stat': stat, 'linea': 0.5, 'ultimos': 40, 'supersub': 0}, timeout=40)
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            print(f'  {pid} {stat}: {e}', file=sys.stderr)
        time.sleep(3 * (i + 1))
    return None


def registros(pid, jx, jg):
    out = []
    goles = {e['id']: {p['id']: p['celdas'] for p in e.get('jugadores', [])} for e in jg.get('equipos', [])}
    for e in jx.get('equipos', []):
        cols = [None if c is None else dict(pid=c.get('partido_id'), fecha=c.get('fecha'), liga=c.get('liga_id'),
                                            este=bool(c.get('este')), local=c.get('local'), gf=c.get('gf'),
                                            gc=c.get('gc'), xg=(c.get('stats') or {}).get('xg'),
                                            xgc=(c.get('stats_rival') or {}).get('xg'))
                for c in e.get('columnas', [])]
        jug = []
        for p in e.get('jugadores', []):
            g = goles.get(e['id'], {}).get(p['id'])
            if g is None:
                continue
            cel = [None if a is None else [a.get('v'), (b or {}).get('v'), a.get('min'), bool(a.get('tit'))]
                   for a, b in zip(p['celdas'], g)]
            jug.append(dict(id=p['id'], nombre=p['nombre'], pos=p.get('posicion'), c=cel))
        out.append(dict(partido=pid, equipo_id=e['id'], equipo=e['nombre'], cols=cols, jugadores=jug))
    return out


def main(n, desde, hasta, asistencias=False):
    global SALIDA
    stats = ('xa', 'asist') if asistencias else ('xg', 'goles')
    S = pd.read_csv(DIR / 'datos' / 'stats_equipos.csv', usecols=['partido_id', 'fecha', 'liga_id', 'local'])
    S = S[(S.local == 1) & (S.fecha >= desde) & (S.fecha < hasta)].drop_duplicates('partido_id')
    S = S.sample(min(n, len(S)), random_state=7).sort_values('fecha')
    if asistencias:          # los mismos partidos que la muestra de goleadores
        ids = {json.loads(l)['partido'] for l in SALIDA.read_text(encoding='utf-8').splitlines()}
        S = pd.DataFrame({'partido_id': sorted(ids)})
        SALIDA = SALIDA_ASIST
    hechos = set()
    if SALIDA.exists():
        for l in SALIDA.read_text(encoding='utf-8').splitlines():
            hechos.add(json.loads(l)['partido'])
    s = requests.Session(); s.headers['User-Agent'] = 'Mozilla/5.0'
    pend = [int(p) for p in S.partido_id if int(p) not in hechos]
    print(f'{len(S)} partidos en la muestra, {len(pend)} por descargar')
    from concurrent.futures import ThreadPoolExecutor
    def uno(pid):
        jx = pedir(s, pid, stats[0])
        jg = pedir(s, pid, stats[1]) if jx else None
        return pid, (registros(pid, jx, jg) if jx and jg else [])
    with SALIDA.open('a', encoding='utf-8') as f, ThreadPoolExecutor(4) as ex:
        for i, (pid, rs) in enumerate(ex.map(uno, pend)):
            for r in rs:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
            f.flush()
            if i % 50 == 0:
                print(f'  {i}/{len(pend)}', flush=True)
    print('listo')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=700)
    ap.add_argument('--desde', default='2025-01-01')
    ap.add_argument('--hasta', default='2026-11-10')
    ap.add_argument('--asistencias', action='store_true', help='xa y asist de los partidos ya descargados')
    a = ap.parse_args()
    main(a.n, a.desde, a.hasta, a.asistencias)

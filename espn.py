#!/usr/bin/env python3
"""
espn.py -- calendario y alineaciones desde la API publica de ESPN.

AVISO: es la API que usa la web de ESPN, sin documentacion ni condiciones de
uso publicadas. Puede cambiar o cortarse sin aviso; todo lo que depende de ella
debe fallar con suavidad. Se usa poco (unas decenas de peticiones por ejecucion).

Endpoints usados (site.api.espn.com/apis/site/v2/sports/soccer/<codigo>/...):
  scoreboard?dates=AAAAMMDD   partidos de un dia (los rangos de fechas dan error 400)
  summary?event=<id>          ficha del partido; 'rosters' trae la alineacion con
                              'starter' en cuanto se publica (~1 h antes) y despues
  teams                       equipos de la liga (para emparejar nombres)

Uso:
  python espn.py calendario [--dias 8]       muestra el calendario de las ligas
  python espn.py titulares  [--dias 35]      guarda jugadores y estadisticas de partidos jugados
"""
import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

DIR = Path(__file__).parent
DIR_AL = DIR / 'datos' / 'alineaciones'
JUGADORES = DIR_AL / 'jugadores.csv'     # todos los convocados con sus estadisticas
TITULARES = JUGADORES                     # alias: alineaciones.py filtra titular == True
STATS = {'totalGoals': 'goles', 'goalAssists': 'asistencias', 'totalShots': 'tiros',
         'shotsOnTarget': 'tiros_puerta', 'yellowCards': 'amarillas', 'redCards': 'rojas',
         'foulsCommitted': 'faltas', 'saves': 'paradas', 'goalsConceded': 'goles_encajados'}
BASE = 'https://site.api.espn.com/apis/site/v2/sports/soccer'
UA = {'User-Agent': 'actualizador-futbol'}

# liga en partidos.csv -> codigo ESPN. Fuera: Escocia 2-4 (la lista de equipos de
# ESPN no corresponde a la temporada actual) y Polonia (codigo no encontrado).
CODIGOS = {
    'LaLiga': 'esp.1', 'LaLiga Hypermotion': 'esp.2', 'Premier League': 'eng.1',
    'Championship': 'eng.2', 'League One': 'eng.3', 'League Two': 'eng.4',
    'National League': 'eng.5', 'Serie A': 'ita.1', 'Serie B': 'ita.2',
    'Bundesliga': 'ger.1', '2. Bundesliga': 'ger.2', 'Ligue 1': 'fra.1', 'Ligue 2': 'fra.2',
    'Eredivisie': 'ned.1', 'Liga Portugal': 'por.1', 'Scottish Premiership': 'sco.1',
    'Jupiler Pro League': 'bel.1', 'Super Lig': 'tur.1', 'Super League Grecia': 'gre.1', 'Primera Division': 'arg.1',
}

_s = requests.Session(); _s.headers.update(UA)


def _get(ruta, **params):
    for intento in range(3):
        try:
            r = _s.get(f'{BASE}/{ruta}', params=params, timeout=30)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (400, 404):
                return None
        except (requests.RequestException, ValueError):
            pass
        time.sleep(2 * (intento + 1))
    return None


def calendario(ligas, desde, dias):
    """Partidos de las ligas entre 'desde' y 'desde + dias' (un GET por liga y dia)."""
    filas = []
    for liga in ligas:
        cod = CODIGOS.get(liga)
        if not cod:
            continue
        for k in range(dias + 1):
            d = (desde + timedelta(days=k)).strftime('%Y%m%d')
            j = _get(f'{cod}/scoreboard', dates=d)
            for e in (j or {}).get('events', []):
                c = e['competitions'][0]
                eq = {x['homeAway']: x['team'] for x in c['competitors']}
                filas.append(dict(fuente='espn', liga=liga, espn_id=e['id'], codigo=cod,
                                  inicio_utc=e['date'], estado=e['status']['type']['name'],
                                  local_src=eq['home']['displayName'], visitante_src=eq['away']['displayName'],
                                  local_id=eq['home']['id'], visitante_id=eq['away']['id']))
    cols = ['fuente', 'liga', 'espn_id', 'codigo', 'inicio_utc', 'estado', 'local_src',
            'visitante_src', 'local_id', 'visitante_id']
    return pd.DataFrame(filas, columns=cols).drop_duplicates('espn_id')


def jugadores_partido(codigo, espn_id):
    """Todos los convocados con titular/entro y sus estadisticas; vacio si no hay ficha."""
    j = _get(f'{codigo}/summary', event=espn_id)
    entra, sale = minutos_eventos(j)
    out = []
    for r in (j or {}).get('rosters', []):
        for p in r.get('roster', []):
            st = {x.get('name'): x.get('value') for x in p.get('stats', []) or []}
            out.append(dict(espn_id=espn_id, equipo_id=r['team']['id'], equipo=r['team']['displayName'],
                            lado=r.get('homeAway'), jugador_id=p['athlete'].get('id'),
                            jugador=p['athlete'].get('displayName'),
                            posicion=(p.get('position') or {}).get('abbreviation'),
                            titular=bool(p.get('starter')), entro=bool(p.get('subbedIn')),
                            salio=bool(p.get('subbedOut')),
                            minutos=_minutos(p, entra, sale),
                            **{v: st.get(k) for k, v in STATS.items()}))
    return out


def _minuto(ev):
    """Minuto de un evento: "56'" -> 56, "90'+3'" -> 90 (el tiempo anadido no se cuenta)."""
    import re
    m = re.match(r"\s*(\d+)", str((ev.get('clock') or {}).get('displayValue', '')))
    return min(int(m.group(1)), 90) if m else None


def minutos_eventos(j):
    """{jugador_id: minuto en que entra} y {jugador_id: minuto en que sale o es expulsado}."""
    entra, sale = {}, {}
    for ev in (j or {}).get('keyEvents', []) or []:
        tipo = str((ev.get('type') or {}).get('text', ''))
        part = [x.get('athlete', {}).get('id') for x in ev.get('participants', []) or []]
        mi = _minuto(ev)
        if mi is None:
            continue
        if tipo == 'Substitution' and len(part) >= 2:
            entra[part[0]] = mi; sale[part[1]] = mi
        elif 'Red Card' in tipo and part:
            sale[part[0]] = min(mi, sale.get(part[0], 90))
    return entra, sale


def _minutos(p, entra, sale):
    """Minutos jugados (sobre 90). None si no hay datos para decidir."""
    jid = p['athlete'].get('id')
    if p.get('starter'):
        return sale.get(jid, 90)
    if p.get('subbedIn') or jid in entra:
        ini = entra.get(jid)
        return None if ini is None else max(sale.get(jid, 90) - ini, 1)
    return 0


def alineacion(codigo, espn_id):
    """Titulares por equipo; lista vacia si aun no esta publicada."""
    return [x for x in jugadores_partido(codigo, espn_id) if x['titular']]


def equipos(liga):
    j = _get(f'{CODIGOS[liga]}/teams')
    try:
        return [t['team']['displayName'] for t in j['sports'][0]['leagues'][0]['teams']]
    except (TypeError, KeyError, IndexError):
        return []


def guardar_titulares(ligas, dias=35):
    """Anade a jugadores.csv los partidos TERMINADOS de los ultimos 'dias' que falten
    (todos los convocados con sus estadisticas)."""
    DIR_AL.mkdir(parents=True, exist_ok=True)
    previo = pd.read_csv(JUGADORES, dtype=str) if JUGADORES.exists() else pd.DataFrame(columns=['espn_id'])
    hechos = set(previo.espn_id)
    hoy = datetime.now(timezone.utc)
    cal = calendario(ligas, hoy - timedelta(days=dias), dias)
    cal = cal[(cal.estado == 'STATUS_FULL_TIME') & ~cal.espn_id.isin(hechos)]
    nuevas = []
    for x in cal.itertuples():
        jp = jugadores_partido(x.codigo, x.espn_id)
        for a in jp:
            a.update(liga=x.liga, fecha=x.inicio_utc[:10])
        nuevas += jp
    if nuevas:
        n = pd.DataFrame(nuevas).astype(str)
        pd.concat([previo, n]).to_csv(JUGADORES, index=False)
    print(f'jugadores: {len(cal)} partidos nuevos, {len(nuevas)} filas', file=sys.stderr)
    return len(nuevas)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('accion', choices=['calendario', 'titulares'])
    ap.add_argument('--dias', type=int, default=8)
    ap.add_argument('--ligas', default='LaLiga')
    a = ap.parse_args()
    ligas = list(CODIGOS) if a.ligas == 'todas' else a.ligas.split(',')
    if a.accion == 'calendario':
        print(calendario(ligas, datetime.now(timezone.utc), a.dias).to_string(index=False))
    else:
        guardar_titulares(ligas, a.dias)


if __name__ == '__main__':
    main()

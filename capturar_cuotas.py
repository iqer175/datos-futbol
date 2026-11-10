#!/usr/bin/env python3
"""
capturar_cuotas.py -- guarda una foto de las cuotas pre-partido con su hora.

football-data solo trae dos momentos (previa del viernes/martes y cierre). La
ventaja, si existe, esta antes: cuando las casas abren la linea. Este script se
ejecuta varias veces por semana (workflow capturar.yml) y va acumulando la
historia de cada linea en datos/capturas/cuotas_AAAA-MM.csv.

Fuentes (cada una solo se activa si existe su clave en el entorno):

  ODDS_API_KEY    The Odds API (the-odds-api.com). Gratis: 500 creditos/mes.
                  NO tiene bet365 en futbol. Da el mercado de referencia:
                  Pinnacle, Betfair Exchange y casas britanicas.
                  Coste: 2 creditos por liga y captura (1X2 + Mas/Menos).
  APIFOOTBALL_KEY API-Football (api-football.com). Gratis: 100 peticiones/dia.
                  Puede traer bet365 (bookmaker 8). Sin verificar que el plan
                  gratis cubra la temporada en curso: si no, el script lo dice.

Formato de salida (una fila por cuota):
  capturado_utc, fuente, liga, evento_id, inicio_utc, local_src, visitante_src,
  casa, mercado (1x2 | ou25), seleccion (1, x, 2, over, under), cuota

Uso:
  python capturar_cuotas.py                 # todas las fuentes con clave
  python capturar_cuotas.py --solo oddsapi  # una sola fuente
  python capturar_cuotas.py --dias 7        # partidos de los proximos N dias
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

DIR = Path(__file__).parent
DIR_CAP = DIR / 'datos' / 'capturas'
UA = {'User-Agent': 'actualizador-futbol'}

# ---- The Odds API -------------------------------------------------------------
# 500 creditos/mes / (2 creditos x 3 capturas/semana x 4,35 semanas) ~ 19 ligas.
# Se priorizan LaLiga (informe de los jueves) y las ligas con mas movimiento de
# linea medido en el backtest (Turquia, Belgica, Escocia, segundas).
# Las claves se validan contra /sports (gratis) antes de gastar creditos.
ODDSAPI_LIGAS = {
    'soccer_spain_la_liga':            'LaLiga',
    'soccer_spain_segunda_division':   'LaLiga Hypermotion',
    'soccer_turkey_super_league':      'Super Lig',
    'soccer_belgium_first_div':        'Jupiler Pro League',
    'soccer_spl':                      'Scottish Premiership',
    'soccer_italy_serie_b':            'Serie B',
    'soccer_efl_champ':                'Championship',
    'soccer_england_league1':          'League One',
    'soccer_england_league2':          'League Two',
    'soccer_germany_bundesliga2':      '2. Bundesliga',
    'soccer_netherlands_eredivisie':   'Eredivisie',
    'soccer_portugal_primeira_liga':   'Liga Portugal',
    'soccer_france_ligue_two':         'Ligue 2',
    'soccer_epl':                      'Premier League',
}
ODDSAPI_CASAS = ['pinnacle', 'betfair_ex_eu', 'betfair_ex_uk', 'williamhill', 'paddypower',
                 'skybet', 'unibet_eu', 'marathonbet', 'betsson', 'sport888']   # 10 = 1 "region"
ODDSAPI_RESERVA = 40        # no gastar por debajo de estos creditos restantes

# ---- API-Football ---------------------------------------------------------------
APIF_URL = 'https://v3.football.api-sports.io'
APIF_BET365 = 8
APIF_LIGAS = {
    140: 'LaLiga', 141: 'LaLiga Hypermotion', 39: 'Premier League', 40: 'Championship',
    41: 'League One', 42: 'League Two',
    78: 'Bundesliga', 79: '2. Bundesliga', 135: 'Serie A', 136: 'Serie B',
    61: 'Ligue 1', 62: 'Ligue 2', 88: 'Eredivisie', 94: 'Liga Portugal',
    179: 'Scottish Premiership', 144: 'Jupiler Pro League', 203: 'Super Lig',
}
APIF_RESERVA = 10           # peticiones diarias que se dejan libres


def ahora():
    return datetime.now(timezone.utc).replace(microsecond=0)


def temporada_europea(t):
    return t.year if t.month >= 7 else t.year - 1


# =============================================================== The Odds API
def capturar_oddsapi(clave, dias):
    s = requests.Session(); s.headers.update(UA)
    base = 'https://api.the-odds-api.com/v4'
    r = s.get(f'{base}/sports', params={'apiKey': clave, 'all': 'true'}, timeout=30)
    if r.status_code != 200:
        print(f'  oddsapi: error {r.status_code} en /sports: {r.text[:200]}', file=sys.stderr)
        return []
    activas = {x['key']: x for x in r.json()}
    for k in ODDSAPI_LIGAS:
        if k not in activas:
            print(f'  oddsapi: AVISO la clave {k} no existe en /sports, revisar', file=sys.stderr)
    t0 = ahora(); filas = []
    hasta = (t0 + timedelta(days=dias)).strftime('%Y-%m-%dT%H:%M:%SZ')
    restantes = None
    for k, liga in ODDSAPI_LIGAS.items():
        if k not in activas or not activas[k].get('active', True):
            continue
        if restantes is not None and restantes < ODDSAPI_RESERVA:
            print(f'  oddsapi: quedan {restantes} creditos (< {ODDSAPI_RESERVA}), paro', file=sys.stderr)
            break
        r = s.get(f'{base}/sports/{k}/odds', timeout=30, params={
            'apiKey': clave, 'bookmakers': ','.join(ODDSAPI_CASAS), 'markets': 'h2h,totals',
            'oddsFormat': 'decimal', 'commenceTimeTo': hasta})
        if 'x-requests-remaining' in r.headers:
            restantes = float(r.headers['x-requests-remaining'])
        if r.status_code != 200:
            print(f'  oddsapi: {k} error {r.status_code}: {r.text[:200]}', file=sys.stderr)
            continue
        n0 = len(filas)
        for ev in r.json():
            comun = dict(capturado_utc=t0.isoformat(), fuente='oddsapi', liga=liga,
                         evento_id=ev['id'], inicio_utc=ev['commence_time'],
                         local_src=ev['home_team'], visitante_src=ev['away_team'])
            for bk in ev.get('bookmakers', []):
                for mk in bk.get('markets', []):
                    for oc in mk.get('outcomes', []):
                        sel = None
                        if mk['key'] == 'h2h':
                            sel = ('1' if oc['name'] == ev['home_team'] else
                                   '2' if oc['name'] == ev['away_team'] else
                                   'x' if oc['name'].lower() == 'draw' else None)
                            mercado = '1x2'
                        elif mk['key'] == 'totals' and abs(float(oc.get('point', 0)) - 2.5) < 1e-9:
                            sel = oc['name'].lower()           # over / under
                            mercado = 'ou25'
                        if sel:
                            filas.append(dict(comun, casa=bk['key'], mercado=mercado,
                                              seleccion=sel, cuota=float(oc['price'])))
        print(f'  oddsapi: {liga:22s} {len(filas) - n0:5d} cuotas', file=sys.stderr)
    if restantes is not None:
        print(f'  oddsapi: creditos restantes este mes: {restantes:.0f}', file=sys.stderr)
    return filas


# =============================================================== API-Football
def capturar_apifootball(clave, dias):
    s = requests.Session(); s.headers.update(dict(UA, **{'x-apisports-key': clave}))
    r = s.get(f'{APIF_URL}/status', timeout=30)
    if r.status_code != 200:
        print(f'  apifootball: error {r.status_code} en /status', file=sys.stderr)
        return []
    st = r.json().get('response') or {}
    req = st.get('requests', {}) if isinstance(st, dict) else {}
    libres = (req.get('limit_day') or 0) - (req.get('current') or 0)
    print(f'  apifootball: peticiones libres hoy {libres}', file=sys.stderr)
    t0 = ahora(); temp = temporada_europea(t0); filas = []
    hasta = t0 + timedelta(days=dias)
    for lid, liga in APIF_LIGAS.items():
        pagina, total = 1, 1
        while pagina <= total:
            if libres <= APIF_RESERVA:
                print('  apifootball: cupo diario casi agotado, paro', file=sys.stderr)
                return filas
            r = s.get(f'{APIF_URL}/odds', timeout=30, params={
                'league': lid, 'season': temp, 'bookmaker': APIF_BET365, 'page': pagina})
            libres -= 1
            if r.status_code != 200:
                print(f'  apifootball: {liga} error {r.status_code}', file=sys.stderr); break
            j = r.json()
            if j.get('errors'):
                # p. ej. el plan gratis no cubre esta temporada: se avisa y se para
                print(f'  apifootball: {liga}: {j["errors"]}', file=sys.stderr)
                return filas
            total = (j.get('paging') or {}).get('total', 1) or 1
            for it in j.get('response', []):
                fx = it.get('fixture', {})
                ini = pd.Timestamp(fx.get('date')) if fx.get('date') else None
                if ini is None or ini < pd.Timestamp(t0) or ini > pd.Timestamp(hasta):
                    continue
                comun = dict(capturado_utc=t0.isoformat(), fuente='apifootball', liga=liga,
                             evento_id=fx.get('id'), inicio_utc=ini.isoformat(),
                             local_src=None, visitante_src=None)
                for bk in it.get('bookmakers', []):
                    for bet in bk.get('bets', []):
                        for v in bet.get('values', []):
                            val, sel, mercado = str(v.get('value')), None, None
                            if bet.get('name') == 'Match Winner':
                                sel = {'Home': '1', 'Draw': 'x', 'Away': '2'}.get(val); mercado = '1x2'
                            elif bet.get('name') == 'Goals Over/Under' and val in ('Over 2.5', 'Under 2.5'):
                                sel = val.split()[0].lower(); mercado = 'ou25'
                            if sel:
                                filas.append(dict(comun, casa='bet365', mercado=mercado,
                                                  seleccion=sel, cuota=float(v.get('odd'))))
            pagina += 1
    # /odds no trae los nombres: se piden los fixtures (1 peticion por fecha)
    ids = {f['evento_id'] for f in filas}
    if ids and libres > APIF_RESERVA:
        nombres = {}
        for d in sorted({f['inicio_utc'][:10] for f in filas}):
            if libres <= APIF_RESERVA:
                break
            r = s.get(f'{APIF_URL}/fixtures', params={'date': d}, timeout=30); libres -= 1
            if r.status_code == 200:
                for it in r.json().get('response', []):
                    nombres[it['fixture']['id']] = (it['teams']['home']['name'], it['teams']['away']['name'])
        for f in filas:
            f['local_src'], f['visitante_src'] = nombres.get(f['evento_id'], (None, None))
    print(f'  apifootball: {len(filas)} cuotas de bet365', file=sys.stderr)
    return filas


# =============================================================== main
def guardar(filas):
    if not filas:
        return None
    DIR_CAP.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(filas)
    ruta = DIR_CAP / f"cuotas_{df.capturado_utc.iloc[0][:7]}.csv"
    df.to_csv(ruta, mode='a', header=not ruta.exists(), index=False)
    return ruta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--solo', choices=['oddsapi', 'apifootball'])
    ap.add_argument('--dias', type=int, default=8)
    a = ap.parse_args()
    filas = []
    fuentes = [('oddsapi', 'ODDS_API_KEY', capturar_oddsapi),
               ('apifootball', 'APIFOOTBALL_KEY', capturar_apifootball)]
    activas = 0
    for nombre, var, f in fuentes:
        if a.solo and a.solo != nombre:
            continue
        clave = os.environ.get(var, '').strip()
        if not clave:
            print(f'{nombre}: sin {var}, se omite', file=sys.stderr); continue
        activas += 1
        print(f'{nombre}:', file=sys.stderr)
        try:
            filas += f(clave, a.dias)
        except requests.RequestException as e:
            print(f'  {nombre}: fallo de red: {e}', file=sys.stderr)
    ruta = guardar(filas)
    print(f'{len(filas)} cuotas guardadas' + (f' en {ruta}' if ruta else ''))
    if activas == 0:
        print('Ninguna clave configurada: no se ha capturado nada.')


if __name__ == '__main__':
    main()

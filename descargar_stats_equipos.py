"""
descargar_stats_equipos.py -- estadisticas de equipo partido a partido (FotMob via LudoProps).

Cada peticion a /api/partido?id=X devuelve, para los dos equipos, sus ultimos 40 partidos
(incluido X si ya se jugo) con 23 estadisticas propias y del rival: goles, remates, a puerta,
corners, faltas, tarjetas, faltas recibidas, entradas, remates generados, asistencias, xG, xA,
pases, centros, fueras de juego, amarillas, remates de fuera / de cabeza / del primer tiempo y
paradas. La API no tiene nada anterior a 2024-08.

Cobertura voraz: para cada equipo se pide el partido mas cercano por encima de la fecha mas
antigua que aun le falta, hasta llegar a 2024-08 (unas 3 peticiones por equipo; cada una vale
para los dos). Los ids de partido salen de datos/arbitros_fotmob.jsonl (descargar_arbitros.py).

  python descargar_stats_equipos.py               # historico de las 17 ligas (reanudable)
  python descargar_stats_equipos.py --recientes   # actualizacion semanal (ultimo partido de cada equipo)
  -> datos/stats_equipos.csv  (una fila por equipo y partido; columnas s_<stat> y r_<stat>)
"""
import argparse, json, time
from pathlib import Path
import pandas as pd, requests

RAIZ = Path(__file__).parent
IDS = RAIZ / 'datos' / 'arbitros_fotmob.jsonl'
SALIDA = RAIZ / 'datos' / 'stats_equipos.csv'
API = 'https://ludoprops.com/futbol/api/partido'
SUELO = pd.Timestamp('2024-08-20', tz='UTC')     # la API empieza en 2024-08


def partidos(ligas):
    filas = []
    for l in IDS.open(encoding='utf-8'):
        filas += json.loads(l)['partidos']
    p = pd.DataFrame(filas).drop_duplicates('id')
    p = p[p.liga.isin(ligas) & p.gl.notna()].copy()
    p['fecha'] = pd.to_datetime(p.fecha, utc=True)
    return p.sort_values('fecha')


def pedir(s, pid):
    for intento in range(3):
        try:
            r = s.get(API, params={'id': pid, 'stat': 'goles', 'linea': 0.5, 'ultimos': 40, 'supersub': 0},
                      timeout=30)
            r.raise_for_status(); return r.json()
        except Exception as e:
            print(f'  {pid}: {e}', flush=True); time.sleep(5 * (intento + 1))
    return None


def main():
    ap = argparse.ArgumentParser()
    import descargar_arbitros
    ap.add_argument('--ligas', nargs='+',
                    default=[l for l in descargar_arbitros.LIGAS.values() if l != 'Primera Division'])
    ap.add_argument('--recientes', action='store_true',
                    help='solo el ultimo partido de cada equipo (actualizacion semanal)')
    a = ap.parse_args()
    global SUELO
    if a.recientes:
        SUELO = pd.Timestamp.now(tz='UTC') - pd.Timedelta(days=40)
    P = partidos(a.ligas)
    filas = pd.read_csv(SALIDA).to_dict('records') if SALIDA.exists() else []
    vistos = {(f['partido_id'], f['equipo_id']) for f in filas}
    # fecha mas antigua cubierta por equipo (nombre FotMob)
    cubierto = {}
    for f in ([] if a.recientes else filas):
        k = f['equipo']; t = pd.Timestamp(f['fecha'])
        cubierto[k] = min(cubierto.get(k, t), t)
    equipos = sorted(set(P.local) | set(P.visita))
    s = requests.Session(); s.headers['User-Agent'] = 'Mozilla/5.0'
    n = 0
    while True:
        # equipo con mas hueco por cubrir (y que tenga partidos ahi)
        pend = []
        for eq in equipos:
            hasta = cubierto.get(eq, pd.Timestamp('2100-01-01', tz='UTC'))
            if hasta <= SUELO + pd.Timedelta(days=10):
                continue
            c = P[((P.local == eq) | (P.visita == eq)) & (P.fecha < hasta)]
            if len(c):
                pend.append((eq, c.iloc[-1]))      # el ultimo partido antes del hueco
        if not pend:
            break
        eq, q = pend[0]
        j = pedir(s, int(q.id)); n += 1
        if j is None:
            cubierto[eq] = SUELO; continue
        for e in j.get('equipos', []):
            nombre = e['nombre']; ts = []
            for c in e.get('columnas', []):
                t = pd.Timestamp(c['fecha']); ts.append(t)
                if (c['partido_id'], e['id']) in vistos:
                    continue
                vistos.add((c['partido_id'], e['id']))
                f = dict(partido_id=c['partido_id'], fecha=c['fecha'], liga_id=c['liga_id'],
                         equipo_id=e['id'], equipo=nombre, rival_id=c['rival']['id'],
                         rival=c['rival']['nombre'], local=c['local'], gf=c['gf'], gc=c['gc'])
                f.update({f's_{k}': v for k, v in (c.get('stats') or {}).items()})
                f.update({f'r_{k}': v for k, v in (c.get('stats_rival') or {}).items()})
                filas.append(f)
            if ts:
                cubierto[nombre] = min(cubierto.get(nombre, min(ts)), min(ts))
            if nombre != eq and not ts:
                pass
        if cubierto.get(eq, q.fecha) >= q.fecha:      # la respuesta no lo cubrio: no insistir
            cubierto[eq] = SUELO
        if n % 25 == 0:
            pd.DataFrame(filas).to_csv(SALIDA, index=False)
            print(f'  {n} peticiones, {len(filas)} filas', flush=True)
        time.sleep(1.0)
    pd.DataFrame(filas).to_csv(SALIDA, index=False)
    print(f'{n} peticiones, {len(filas)} filas -> {SALIDA}')


if __name__ == '__main__':
    main()

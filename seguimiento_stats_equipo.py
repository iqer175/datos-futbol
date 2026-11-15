"""
seguimiento_stats_equipo.py -- validacion de los mercados de estadisticas de equipo (faltas,
entradas, fueras de juego, paradas, centros) contra cuotas reales de bet365.

Recorre ligas/*/jornadas/*/evaluacion.csv, toma las filas de esos mercados (las cuotas que leyo
el recolector y la prob del simulador) y las resuelve con el resultado real de FotMob (API de
LudoProps: la ficha de un partido ya jugado trae sus estadisticas). Los resultados se guardan
en datos/resultados_stats_equipo.csv para no volver a pedirlos.

CRITERIOS FIJADOS ANTES DE VER LOS DATOS (2026-11-15). No se cambian despues.
  Por estadistica (faltas, entradas, offsides, atajadas, centros), solo con lineas en las que
  hay cuota de Mas y de Menos (para quitar el margen):
    - minimo 150 lineas resueltas;
    - DESBLOQUEA si el log-loss del modelo es menor que el de la cuota sin margen y el IC95 de
      la diferencia (bootstrap por partidos) queda entero por debajo de 0;
    - FALLA si la diferencia media es >= 0 con 150 lineas o mas;
    - si no se decide, se espera hasta 300 lineas con la misma regla; sin decidir a 300 = FALLA.
  Desbloquear quita el aviso "sin validar" SOLO de esa estadistica y requiere el visto bueno
  del usuario. Diagnostico (no decide): aciertos frente a prob media, y rendimiento plano de
  las que el simulador marca "con valor".

  python seguimiento_stats_equipo.py
  python seguimiento_stats_equipo.py --prueba <carpeta de jornada>   # solo mira, no escribe
  -> datos/seguimiento_stats_equipo.csv (una fila por linea) y resultados/seguimiento_stats_equipo.txt
"""
import json, re, sys, time
from pathlib import Path
import numpy as np, pandas as pd
import requests

import arbitros, ligas
from simulador import STATS_MERCADO, _RE_STATS

DIR = Path(__file__).parent
CACHE = DIR / 'datos' / 'resultados_stats_equipo.csv'
SALIDA = DIR / 'datos' / 'seguimiento_stats_equipo.csv'
INFORME = DIR / 'resultados' / 'seguimiento_stats_equipo.txt'
API = 'https://ludoprops.com/futbol/api/partido'
N_MIN, N_MAX = 150, 300


def filas_jornadas(carpeta=None):
    out = []
    evs = [Path(carpeta) / 'evaluacion.csv'] if carpeta else sorted(DIR.glob('ligas/*/jornadas/*/evaluacion.csv'))
    for ev in evs:
        cu = ev.parent / 'cuotas.json'
        if not cu.exists():
            continue
        try:
            liga = ligas.resolver(json.loads(cu.read_text(encoding='utf-8'))['liga'])
        except Exception:
            continue
        T = pd.read_csv(ev)
        if 'clave' not in T:
            continue                              # evaluacion.csv de antes de la columna clave
        T = T[T.clave.astype(str).str.match(_RE_STATS)]
        if len(T):
            out.append(T.assign(liga=liga, jornada=ev.parent.as_posix()))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def resultado(liga, partido, inicio_utc, s):
    """Estadisticas de FotMob del partido jugado: {stat: (local, visitante)} o None."""
    local, visit = partido.split(' - ', 1)
    q = arbitros.buscar_partido(liga, local, visit, inicio_utc)
    if not q or q.get('goles_local') is None:
        return None
    try:
        j = s.get(API, params={'id': q['id'], 'stat': 'goles', 'linea': 0.5, 'ultimos': 5, 'supersub': 0},
                  timeout=30).json()
    except Exception as e:
        print(f'  aviso: {partido}: {e}', file=sys.stderr); return None
    for e in j.get('equipos', []):
        if e['id'] != q['local']['id']:
            continue
        for c in e.get('columnas', []):
            if c.get('partido_id') == q['id'] and c.get('stats') and c.get('stats_rival'):
                return {k: (c['stats'].get(k), c['stats_rival'].get(k)) for k in STATS_MERCADO.values()}
    return None


def resolver(T):
    cache = pd.read_csv(CACHE) if CACHE.exists() else pd.DataFrame(columns=['liga', 'partido', 'inicio_utc'])
    hechos = set(zip(cache.liga, cache.partido, cache.inicio_utc.astype(str)))
    ahora = pd.Timestamp.now(tz='UTC')
    s = requests.Session(); s.headers['User-Agent'] = 'Mozilla/5.0'
    nuevos = []
    for (liga, partido, ini), _ in T.groupby(['liga', 'partido', 'inicio_utc']):
        if (liga, partido, str(ini)) in hechos:
            continue
        try:
            if pd.Timestamp(ini) > ahora - pd.Timedelta(hours=3):
                continue                      # aun no ha acabado
        except Exception:
            pass
        r = resultado(liga, partido, ini, s); time.sleep(1.0)
        if r:
            f = dict(liga=liga, partido=partido, inicio_utc=ini)
            for k, (a, b) in r.items():
                f[f'{k}_l'], f[f'{k}_v'] = a, b
            nuevos.append(f)
    if nuevos:
        cache = pd.concat([cache, pd.DataFrame(nuevos)], ignore_index=True)
        cache.to_csv(CACHE, index=False)
    return cache


def gana(clave, r):
    m = _RE_STATS.match(clave)
    st = STATS_MERCADO[m.group(1)]; lado = (m.group(2) or '').strip(); sentido = m.group(3); ln = float(m.group(4))
    a, b = r.get(f'{st}_l'), r.get(f'{st}_v')
    if pd.isna(a) or pd.isna(b):
        return st, np.nan
    x = a if lado == 'local' else b if lado == 'visitante' else a + b
    return st, float(x > ln if sentido == 'más' else x < ln)


def main(prueba=None):
    T = filas_jornadas(prueba)
    L = []
    def p(x=''): print(x); L.append(x)
    p(f'Seguimiento de estadisticas de equipo ({pd.Timestamp.now():%Y-%m-%d})')
    if T.empty:
        p('Todavia no hay cuotas de estos mercados en ninguna jornada (el recolector las lee desde 2026-11-15).')
        INFORME.write_text('\n'.join(L), encoding='utf-8'); return
    R = resolver(T)
    T = T.merge(R, on=['liga', 'partido', 'inicio_utc'], how='left')
    res = [gana(c, r) for c, r in zip(T.clave, T.to_dict('records'))]
    T['stat'] = [x[0] for x in res]; T['gana'] = [x[1] for x in res]
    T['linea_id'] = T.partido + '|' + T.clave.str.replace(r' (más|menos) ', ' ', regex=True)
    T['lado'] = np.where(T.clave.str.contains(' más '), 'más', 'menos')
    if not prueba:
        T.to_csv(SALIDA, index=False)
    J = T.dropna(subset=['gana'])
    p(f'{len(T)} cuotas leidas, {len(J)} resueltas')
    for st in STATS_MERCADO.values():
        g = J[J.stat == st]
        if g.empty:
            p(f'  {st:9s} sin datos'); continue
        # lineas con Mas y Menos: prob sin margen del mercado
        w = g.pivot_table(index='linea_id', columns='lado', values=['cuota', 'prob', 'gana'], aggfunc='first').dropna()
        if not {('cuota', 'más'), ('cuota', 'menos')} <= set(w.columns):
            w = w.iloc[0:0]
        n = len(w)
        txt = f'  {st:9s} {len(g)} cuotas, {n} lineas con las dos'
        if n:
            q = (1 / w.cuota['más']) / (1 / w.cuota['más'] + 1 / w.cuota['menos'])
            y = w.gana['más'].values; pm = np.clip(w.prob['más'].values, 1e-4, 1 - 1e-4)
            qm = np.clip(q.values, 1e-4, 1 - 1e-4)
            d = -(y * np.log(pm) + (1 - y) * np.log(1 - pm)) + (y * np.log(qm) + (1 - y) * np.log(1 - qm))
            bs = [np.random.default_rng(i).choice(d, n).mean() for i in range(2000)]
            lo, hi = np.percentile(bs, [2.5, 97.5])
            estado = ('faltan datos' if n < N_MIN else 'DESBLOQUEA (pedir visto bueno)' if hi < 0
                      else 'FALLA' if d.mean() >= 0 or n >= N_MAX else 'sin decidir, seguir hasta 300')
            txt += f'; log-loss modelo - mercado {d.mean():+.4f} IC95 [{lo:+.4f}, {hi:+.4f}] -> {estado}'
        p(txt)
        p(f'      aciertos {g.gana.mean():.1%} con prob media {g.prob.mean():.1%}')
        v = g[g.con_valor]
        if len(v):
            p(f'      "con valor": {len(v)} apuestas, rendimiento plano {np.mean(v.gana * v.cuota - 1):+.1%}')
    if prueba:
        return
    INFORME.write_text('\n'.join(L), encoding='utf-8')
    p(f'-> {SALIDA.as_posix()}  {INFORME.as_posix()}')


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--prueba', help='una carpeta de jornada; no escribe los ficheros de seguimiento')
    main(ap.parse_args().prueba)

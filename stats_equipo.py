"""
stats_equipo.py -- medias esperadas de faltas, entradas, fueras de juego, paradas y centros de
cada equipo para el simulador (en prueba desde 2026-11-15).

Modelo y coeficientes: stats_equipo_modelo.py (datos/agregados/stats_equipo_coef.json).
Datos del partido: UNA peticion a la API de LudoProps (FotMob) por partido, que trae los
ultimos 40 partidos de los dos equipos con todas sus estadisticas. Para el perfil solo se usan
los partidos de liga anteriores al inicio. Sin conexion, sin partido o sin coeficientes de la
liga: None (el simulador simplemente no ofrece esos mercados).

  import stats_equipo
  stq = stats_equipo.medias(liga, local, visitante, inicio_utc, elo_l, elo_v)
  # {'faltas': {'ml': 11.9, 'mv': 12.6, 'alpha': .., 'rho': ..}, ...}
"""
import json, sys
from pathlib import Path
import numpy as np, pandas as pd

import motor

DIR = Path(__file__).parent
API = 'https://ludoprops.com/futbol/api/partido'
MERCADOS = ['faltas', 'entradas', 'offsides', 'atajadas', 'centros']
_coef = None


def coeficientes():
    global _coef
    if _coef is None:
        f = DIR / 'datos' / 'agregados' / 'stats_equipo_coef.json'
        _coef = json.loads(f.read_text(encoding='utf-8')) if f.exists() else {}
    return _coef


def _perfil(cols, s, nivel, t):
    ts = np.array([np.datetime64(pd.Timestamp(c['fecha']).tz_convert(None)) for c in cols])
    v = np.array([[c['stats'][s] / nivel, c['stats_rival'][s] / nivel] for c in cols], float)
    k = ts < np.datetime64(t)
    if k.sum() < 3:
        return None
    ts, v = ts[k][-motor.MAX_N:], v[k][-motor.MAX_N:]
    w = 0.5 ** ((np.datetime64(t) - ts).astype('timedelta64[D]').astype(float) / motor.HALF_LIFE)
    return ((w[:, None] * v).sum(0) + motor.K_SHRINK) / (w.sum() + motor.K_SHRINK)


def medias(liga, local, visitante, inicio_utc, elo_l, elo_v):
    import requests, arbitros
    from descargar_arbitros import LIGAS
    C = coeficientes()
    if not C:
        return None
    q = arbitros.buscar_partido(liga, local, visitante, inicio_utc)
    if not q:
        return None
    try:
        j = requests.get(API, params={'id': q['id'], 'stat': 'goles', 'linea': 0.5, 'ultimos': 40,
                                      'supersub': 0}, headers={'User-Agent': 'Mozilla/5.0'}, timeout=30).json()
    except Exception as e:
        print(f'  aviso: sin estadisticas de equipo de {local} - {visitante} ({e})', file=sys.stderr)
        return None
    ids = {i for i, n in LIGAS.items() if n == liga}
    t = pd.Timestamp(q['fecha']).tz_convert(None)
    eq = {e['id']: e for e in j.get('equipos', [])}
    el, ev = eq.get(q['local']['id']), eq.get(q['visita']['id'])
    if not el or not ev:
        return None
    def liga_cols(e):
        return [c for c in e['columnas'] if c.get('liga_id') in ids and not c.get('este')
                and c.get('stats') and c.get('stats_rival')]
    cl, cv = liga_cols(el), liga_cols(ev)
    gap = (elo_l - elo_v) / 100
    out = {}
    for s in MERCADOS:
        co = C.get(s)
        nivel = (co or {}).get('nivel', {}).get(liga)
        if not co or not nivel or 'alpha' not in co:
            continue
        try:
            a, b = _perfil(cl, s, nivel, t), _perfil(cv, s, nivel, t)
        except (KeyError, TypeError):
            continue
        if a is None or b is None:
            continue
        ml = max(nivel * (a[0] + b[1]) / 2 + co['a_l'] + co['b_l'] * gap, 0.3)
        mv = max(nivel * (b[0] + a[1]) / 2 + co['a_v'] + co['b_v'] * gap, 0.3)
        out[s] = dict(ml=ml, mv=mv, alpha=co['alpha'], rho=co['rho'])
    return out or None


if __name__ == '__main__':
    B = motor.Base(motor.cargar(DIR / 'datos' / 'partidos.csv'))
    loc, vis, liga, ini = sys.argv[1:5] if len(sys.argv) >= 5 else ('Alaves', 'La Coruna', 'LaLiga', '2026-11-21T13:00:00Z')
    el, *_ = B.elo(loc); ev, *_ = B.elo(vis)
    for s, m in (medias(liga, loc, vis, ini, el, ev) or {}).items():
        print(f'{s:9s} local {m["ml"]:.1f}  visitante {m["mv"]:.1f}  total {m["ml"] + m["mv"]:.1f}')

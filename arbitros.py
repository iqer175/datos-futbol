"""
arbitros.py -- factor de arbitro para las tarjetas (backtest_arbitros.py, 2026-11-15).

El factor de cada arbitro = tarjetas reales en sus partidos / tarjetas que preveia el
motor en esos partidos, encogido hacia 1 con K partidos ficticios y con vida media de
365 dias. Fuera de muestra (2026, 3730 partidos): Brier de Mas 3.5/4.5/5.5 -1.4/-1.4/-1.6 %,
mejora en 15 de 17 ligas; la deriva de la liga sola no aporta nada.

  python arbitros.py actualizar        # baja los dias que falten, cruza y rehace la tabla
  python arbitros.py tabla             # solo rehace datos/agregados/arbitros_factor.csv
  python arbitros.py ver "Jesús Gil Manzano"

Desde el pipeline:
  arbitros.factor(nombre)                         -> (factor, partidos)   (1.0, 0) si no hay
  arbitros.proximo(liga, local, visitante, inicio_utc) -> nombre o None (si aun no esta designado)
"""
import sys, unicodedata, re
from datetime import datetime, timedelta, timezone
from pathlib import Path
import numpy as np, pandas as pd

RAIZ = Path(__file__).parent
TABLA = RAIZ / 'datos' / 'agregados' / 'arbitros_factor.csv'
K, VIDA = 20, 365.0          # elegidos en julio-diciembre de 2025 (backtest_arbitros.py)
API = 'https://ludoprops.com/futbol/api/dia'


def _n(s):
    s = unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z ]', '', s).strip()


# ------------------------------------------------------------------ tabla
def construir_tabla():
    import backtest_arbitros as bt
    p = bt.cargar()
    p['pred'] = bt.base(p)
    p = p[np.isfinite(p.pred) & p.ref.notna()]
    hoy = pd.Timestamp.now().normalize()
    filas = []
    for ref, g in p.groupby('ref'):
        w = 0.5 ** ((hoy - g.fecha).dt.days.values / VIDA)
        m = g.pred.mean()
        f = (np.sum(w * g.tot) + K * m) / (np.sum(w * g.pred) + K * m)
        filas.append(dict(arbitro=ref, clave=_n(ref), partidos=len(g), factor=round(f, 4),
                          reales=round(g.tot.mean(), 2), previstas=round(g.pred.mean(), 2),
                          ultimo=str(g.fecha.max())[:10], ligas=';'.join(sorted(g.liga.unique()))))
    T = pd.DataFrame(filas).sort_values('factor', ascending=False)
    TABLA.parent.mkdir(parents=True, exist_ok=True)
    T.to_csv(TABLA, index=False)
    print(f'{len(T)} arbitros -> {TABLA.as_posix()}  (factor p10 {T.factor.quantile(.1):.2f}, '
          f'p90 {T.factor.quantile(.9):.2f})')
    return T


_T = None
def factor(nombre):
    """(factor, partidos del arbitro en la base). Sin arbitro o sin historia: (1.0, 0)."""
    global _T
    if not nombre:
        return 1.0, 0
    if _T is None:
        _T = (pd.read_csv(TABLA).sort_values('partidos').drop_duplicates('clave', keep='last')
              .set_index('clave') if TABLA.exists() else pd.DataFrame())
    k = _n(nombre)
    if k not in _T.index:
        return 1.0, 0
    r = _T.loc[k]
    return float(r.factor), int(r.partidos)


# ------------------------------------------------------------------ arbitro designado
_cache = {}
def _dia(fecha):
    if fecha not in _cache:
        import requests
        try:
            r = requests.get(API, params={'fecha': fecha}, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
            r.raise_for_status(); _cache[fecha] = r.json().get('ligas', [])
        except Exception as e:
            print(f'  aviso: no se pudo leer el arbitro de {fecha} ({e})', file=sys.stderr)
            _cache[fecha] = []
    return _cache[fecha]


_alias = None
def _parecido(fm, eq, liga):
    """fm: nombre en FotMob; eq: nombre en partidos.csv / cuotas.json."""
    global _alias
    from descargar_arbitros import norm, ALIAS
    if _alias is None:
        _alias = ({(r.liga, r.equipo): r.fotmob for r in pd.read_csv(ALIAS).itertuples()}
                  if ALIAS.exists() else {})
    if (liga, eq) in _alias:
        return fm == _alias[(liga, eq)]
    a, b = norm(fm), norm(eq)
    return bool(a and b) and (a in b or b in a or a[:5] == b[:5])


def proximo(liga, local, visitante, inicio_utc=None):
    """Arbitro designado para el partido (nombre de FotMob) o None si aun no se sabe."""
    q = buscar_partido(liga, local, visitante, inicio_utc)
    return (q.get('arbitro') or {}).get('nombre') if q else None


def buscar_partido(liga, local, visitante, inicio_utc=None):
    """El partido tal como lo da la API de LudoProps (id de FotMob, arbitro, xi...) o None."""
    from descargar_arbitros import LIGAS
    ids = [i for i, n in LIGAS.items() if n == liga]
    if not ids:
        return None
    try:
        t = datetime.fromisoformat(str(inicio_utc).replace('Z', '+00:00'))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    # la API agrupa por dia de Argentina (UTC-3); miro ese dia y el de UTC
    dias = dict.fromkeys([(t - timedelta(hours=3)).strftime('%Y-%m-%d'), t.strftime('%Y-%m-%d')])
    for d in dias:
        for l in _dia(d):
            if l.get('id') not in ids:
                continue
            for q in l.get('partidos', []):
                if (_parecido(q['local']['nombre'], local, liga)
                        and _parecido(q['visita']['nombre'], visitante, liga)):
                    return q
    return None


def info(liga, local, visitante, inicio_utc=None, nombre=None):
    """Lo que se guarda en contexto.json y aplica el simulador."""
    fuente = 'contexto' if nombre else 'fotmob'
    nombre = nombre or proximo(liga, local, visitante, inicio_utc)
    f, n = factor(nombre)
    return {'nombre': nombre, 'factor': round(f, 3), 'partidos': n,
            'fuente': fuente if nombre else 'sin designar'}


def main():
    a = sys.argv[1:] or ['ver']
    if a[0] == 'actualizar':
        import descargar_arbitros as da
        da.descargar(muestra=True); da.unir(); construir_tabla()
    elif a[0] == 'tabla':
        construir_tabla()
    elif a[0] == 'ver':
        T = pd.read_csv(TABLA)
        if len(a) > 1:
            T = T[T.clave.str.contains(_n(a[1]))]
        print(T.drop(columns='clave').to_string(index=False))


if __name__ == '__main__':
    main()

"""
goleadores.py -- probabilidad de que un jugador marque, asista o haga una de las dos (bet365,
tabla "Anotará o asistirá": columnas Anotará, Asistirá y Anotará o asistirá, linea 1) con su xG
y xA de FotMob (API de LudoProps). En prueba (goleador desde 2026-11-15, asistente y anotará o
asistirá desde 2026-11-16): sin validar contra cuotas de bet365.

Modelo y coeficientes: backtest_goleadores.py (datos/agregados/goleadores_coef.json y
asistencias_coef.json).
  lambda = ritmo por 90' x minutos esperados / 90 x goles esperados del equipo / su xG medio x c
  ritmo  = (w * xG + (1 - w) * goles + K * ritmo de su posicion) / (minutos / 90 + K)
           (asistencias: lo mismo con xA y asistencias, con sus propios w, K, c)
  Sustituto+ (bet365): los minutos que no juega los llena un suplente de su posicion (r_sub).
  P(N o mas) con Poisson; anotara o asistira: 1 - exp(-kappa (lambda_goles + lambda_asist)).
Solo cuentan los partidos con xG anteriores al inicio. Cuatro peticiones por partido (xg, goles,
xa, asist), en memoria para todos sus jugadores.

  import goleadores
  G = goleadores.Partido(liga, local, visitante, inicio_utc, mu_local, mu_visitante)
  p, aviso, nombre_fotmob, titularidades = G.prob('Mario Soberon', 1, sustituto=True, mercado='goles')
  # mercado: 'goles' | 'asistencias' | 'goles o asistencias'

  python goleadores.py Alaves "La Coruna" LaLiga 2026-11-21T13:00:00Z 1.3 1.1   # lista
  python goleadores.py seguimiento     # valida contra las cuotas leidas (criterios abajo)

SEGUIMIENTO -- CRITERIOS FIJADOS ANTES DE VER LOS DATOS (goleador 2026-11-15; asistente y anotara
o asistira 2026-11-16, los mismos). No se cambian despues. Cada mercado por separado.
  bet365 solo da el "Si", asi que el margen no se puede quitar linea a linea: la prob del
  mercado es (1/cuota) / m, con m el margen medio que hace que la prob media del mercado sea la
  frecuencia real de la muestra (el mejor desmarginado constante: favorece al mercado). Con las
  lineas 1 resueltas:
    - minimo 300 lineas;
    - DESBLOQUEA si el log-loss del modelo es menor y el IC95 de la diferencia (bootstrap por
      partidos) queda entero por debajo de 0;
    - FALLA si la diferencia media es >= 0 con 300 o mas; sin decidir a 600 = FALLA.
  Desbloquear quita el aviso "sin validar" de ese mercado y requiere el visto bueno del usuario.
"""
import json, sys, time, unicodedata
from pathlib import Path
import numpy as np, pandas as pd

DIR = Path(__file__).parent
API = 'https://ludoprops.com/futbol/api/partido'
COEF = {'goles': DIR / 'datos' / 'agregados' / 'goleadores_coef.json',
        'asistencias': DIR / 'datos' / 'agregados' / 'asistencias_coef.json'}
MERCADOS = ('goles', 'asistencias', 'goles o asistencias')
AVISOS = {'goles': 'goleador (xG FotMob): sin validar contra bet365, en prueba desde 2026-11-15',
          'asistencias': 'asistente (xA FotMob): sin validar contra bet365, en prueba desde 2026-11-16',
          'goles o asistencias': 'anotará o asistirá (xG y xA FotMob): sin validar contra bet365, en prueba desde 2026-11-16'}
AVISO = AVISOS['goles']
MUESTRA_MIN = 3          # apariciones con xG minimas (como en el backtest)
_coef = {}


def coef(m='goles'):
    if m not in _coef:
        f = COEF[m]
        _coef[m] = json.loads(f.read_text(encoding='utf-8')) if f.exists() else {}
    return _coef[m]


def nz(s):
    return unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode().lower().replace('-', ' ')


def _pedir(pid, stat):
    import requests
    for i in range(3):
        try:
            r = requests.get(API, params={'id': pid, 'stat': stat, 'linea': 0.5, 'ultimos': 40, 'supersub': 0},
                             headers={'User-Agent': 'Mozilla/5.0'}, timeout=40)
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            print(f'  aviso goleadores: {pid} {stat}: {e}', file=sys.stderr)
        time.sleep(2 * (i + 1))
    return None


def poisson_mas(lam, n):
    from scipy.stats import poisson
    return float(poisson.sf(n - 1, lam))


def _celdas(j):
    return {e['id']: {p['id']: p['celdas'] for p in e.get('jugadores', [])} for e in (j or {}).get('equipos', [])}


class Partido:
    """Jugadores de los dos equipos con sus rasgos, listos para prob()."""
    def __init__(self, liga, local, visitante, inicio_utc, mu_l, mu_v):
        import arbitros
        self.ok, self.err, self.jug, self.asist = False, None, [], False
        co = coef('goles')
        if not co:
            self.err = 'sin coeficientes de goleadores'; return
        q = arbitros.buscar_partido(liga, local, visitante, inicio_utc)
        if not q:
            self.err = 'partido no encontrado en FotMob'; return
        jx, jg = _pedir(q['id'], 'xg'), _pedir(q['id'], 'goles')
        if not (jx and jg):
            self.err = 'sin respuesta de FotMob'; return
        ja, jas = (_pedir(q['id'], 'xa'), _pedir(q['id'], 'asist')) if coef('asistencias') else (None, None)
        self.asist = bool(ja and jas)
        t = str(pd.Timestamp(q['fecha']).tz_convert('UTC'))
        G, XA, A = _celdas(jg), _celdas(ja), _celdas(jas)
        for e in jx.get('equipos', []):
            es_local = e['id'] == q['local']['id']
            cols = e.get('columnas', [])
            prev = [i for i, c in enumerate(cols) if c and not c.get('este') and str(pd.Timestamp(c['fecha'])) < t
                    and ((c.get('stats') or {}).get('xg') or 0) > 0]
            lg = q.get('liga') or (jx.get('partido') or {}).get('liga')     # el calendario no trae la liga
            lid = lg['id'] if isinstance(lg, dict) else None
            lp = [i for i in prev if cols[i].get('liga_id') == lid] or prev
            xl = co['xg_liga'].get(str(lid), np.mean(list(co['xg_liga'].values())))
            eq_rate = (sum(cols[i]['stats']['xg'] for i in lp) + co['K_eq'] * xl) / (len(lp) + co['K_eq'])
            mu = mu_l if es_local else mu_v
            for p in e.get('jugadores', []):
                if p.get('posicion') == 'POR':
                    continue
                otras = [d.get(e['id'], {}).get(p['id'], []) for d in (G, XA, A)]
                a = [(p['celdas'][i], *[o[i] if i < len(o) else None for o in otras])
                     for i in prev if i < len(p['celdas'])]
                a = [r for r in a if r[0] and r[0].get('min')]
                v = lambda k: sum((r[k] or {}).get('v') or 0 for r in a)
                self.jug.append(dict(
                    nombre=p['nombre'], n=nz(p['nombre']), equipo=e['nombre'], pos=p.get('posicion') or 'MED',
                    baja=p.get('baja'), xg_s=v(0), g_s=v(1), xa_s=v(2), a_s=v(3), mins=sum(r[0]['min'] for r in a),
                    min_tit=sum(r[0]['min'] for r in a if r[0].get('tit')), n_tit=sum(bool(r[0].get('tit')) for r in a),
                    n_ap=len(a), f=mu / eq_rate))
        self.ok = bool(self.jug)

    def buscar(self, nombre):
        toks = nz(nombre).split()
        x = [j for j in self.jug if all(t in j['n'].split() or t in j['n'] for t in toks)]
        if len(x) > 1:                                    # mismo apellido: pedir todas las palabras exactas
            x = [j for j in x if all(t in j['n'].split() for t in toks)] or x
        if not x:                                         # orden o una letra distinta ("Chavez Fidel")
            from difflib import SequenceMatcher
            a = ' '.join(sorted(toks))
            r = sorted(((SequenceMatcher(None, a, ' '.join(sorted(j['n'].split()))).ratio(), i)
                        for i, j in enumerate(self.jug)), reverse=True)
            if r and r[0][0] >= 0.85 and (len(r) == 1 or r[1][0] < r[0][0] - 0.05):
                x = [self.jug[r[0][1]]]
        return x

    def lam(self, j, sustituto=True, m='goles'):
        """(lambda de goles o de asistencias, minutos esperados)."""
        co = coef(m); w, K, Km, c = co['w'], co['K'], co['K_min'], co['c']
        xs, rs = (j['xg_s'], j['g_s']) if m == 'goles' else (j['xa_s'], j['a_s'])
        rpos = w * co['rx'].get(j['pos'], 0.1) + (1 - w) * co['rg'].get(j['pos'], 0.1)
        r90 = (w * xs + (1 - w) * rs + K * rpos) / (j['mins'] / 90 + K)
        mp = co['min_pos'].get(j['pos'], 82)
        mesp = (j['min_tit'] + Km * mp) / (j['n_tit'] + Km)
        lam = r90 * mesp / 90
        if sustituto:
            lam += co['r_sub'].get(j['pos'], rpos) * max(90 - mesp, 0) / 90
        return lam * j['f'] * c, mesp

    def prob(self, nombre, linea=1, sustituto=True, mercado='goles'):
        """(prob de N o mas goles / asistencias / goles o asistencias si es TITULAR, aviso,
        nombre FotMob, titularidades)."""
        if not self.ok:
            return np.nan, f'sin datos de goleador ({self.err})', nombre, 0
        if mercado != 'goles' and not self.asist:
            return np.nan, 'sin datos de asistencias de FotMob', nombre, 0
        x = self.buscar(nombre)
        if not x:
            return np.nan, 'sin datos de goleador (jugador sin emparejar con FotMob)', nombre, 0
        if len(x) > 1:
            return np.nan, f'nombre ambiguo ({", ".join(j["nombre"] for j in x)})', nombre, 0
        j = x[0]
        lg, mesp = self.lam(j, sustituto, 'goles')
        if mercado == 'goles':
            p = poisson_mas(lg, linea)
        else:
            la = self.lam(j, sustituto, 'asistencias')[0]
            if mercado == 'asistencias':
                p = poisson_mas(la, linea)
            else:                                      # linea 1: marca o asiste
                p = float(1 - np.exp(-coef('asistencias').get('kappa', 1.0) * (lg + la)))
        av = (f"{j['n_tit']} titularidades con xG, {j['xg_s']:.1f} xG/{j['g_s']:.0f} goles"
              + (f", {j['xa_s']:.1f} xA/{j['a_s']:.0f} asist." if mercado != 'goles' else '')
              + f" en {j['mins']:.0f} min, {mesp:.0f} min esperados")
        if j['n_ap'] < MUESTRA_MIN:
            av += ' | MUESTRA CORTA'
        if j.get('baja'):
            av += f" | baja en FotMob: {j['baja']}"
        return p, av, j['nombre'], j['n_tit']


def seguimiento():
    """Resuelve las cuotas leidas (evaluacion.csv) de los tres mercados con FotMob y aplica los criterios."""
    import arbitros, ligas
    L = []
    def p(x=''):
        print(x); L.append(x)
    filas = []
    patron = r'\|(?:goles|asistencias|goles o asistencias)\|'
    for ev in sorted(DIR.glob('ligas/*/jornadas/*/evaluacion.csv')):
        cu = ev.parent / 'cuotas.json'
        T = pd.read_csv(ev)
        if 'clave' not in T or not cu.exists():
            continue
        T = T[T.clave.astype(str).str.contains(patron)]
        if len(T):
            liga = ligas.resolver(json.loads(cu.read_text(encoding='utf-8'))['liga'])
            filas.append(T.assign(liga=liga))
    p(f'Seguimiento de goleador, asistente y anotará o asistirá ({pd.Timestamp.now():%Y-%m-%d})')
    if not filas:
        p('Todavia no hay cuotas de estos mercados en ninguna jornada (el recolector las lee desde 2026-11-15/16).')
    else:
        T = pd.concat(filas, ignore_index=True)
        cache_f = DIR / 'datos' / 'resultados_goleadores.csv'
        C = pd.read_csv(cache_f) if cache_f.exists() else pd.DataFrame(
            columns=['liga', 'partido', 'inicio_utc', 'nombre', 'goles', 'asist'])
        if 'asist' not in C:                  # cache de antes de las asistencias: se rehace
            C = C.iloc[0:0].assign(asist=pd.Series(dtype=float))
        hechos = set(zip(C.liga, C.partido, C.inicio_utc.astype(str)))
        nuevos = []
        for (liga, partido, ini), _ in T.groupby(['liga', 'partido', 'inicio_utc']):
            if (liga, partido, str(ini)) in hechos or pd.Timestamp(ini) > pd.Timestamp.now(tz='UTC') - pd.Timedelta(hours=3):
                continue
            loc, vis = partido.split(' - ', 1)
            q = arbitros.buscar_partido(liga, loc, vis, ini)
            if not q or q.get('goles_local') is None:
                continue
            jg, ja = _pedir(q['id'], 'goles'), _pedir(q['id'], 'asist')
            A = _celdas(ja)
            for e in (jg or {}).get('equipos', []):
                k = next((i for i, c in enumerate(e['columnas']) if c and c.get('partido_id') == q['id']), None)
                for pl in e.get('jugadores', []):
                    c = pl['celdas'][k] if k is not None and k < len(pl['celdas']) else None
                    ca = A.get(e['id'], {}).get(pl['id'], [])
                    ca = ca[k] if k is not None and k < len(ca) else None
                    if c:
                        nuevos.append(dict(liga=liga, partido=partido, inicio_utc=ini, nombre=nz(pl['nombre']),
                                           goles=c.get('v') or 0, asist=(ca or {}).get('v') if ja else np.nan))
        if nuevos:
            C = pd.concat([C, pd.DataFrame(nuevos)], ignore_index=True); C.to_csv(cache_f, index=False)
        T['jug'] = T.clave.str.split('|').str[0].map(nz)
        T['merc'] = T.clave.str.split('|').str[1]
        T['linea'] = T.clave.str.split('|').str[2].astype(int)
        def gana(r):
            g = C[(C.liga == r.liga) & (C.partido == r.partido) & (C.inicio_utc.astype(str) == str(r.inicio_utc))]
            m = g[[all(t in n for t in r.jug.split()) for n in g.nombre.astype(str)]]
            if len(m) != 1:
                return np.nan                            # no jugo o ambiguo: nula
            gl, asi = m.goles.iloc[0], m.asist.iloc[0]
            if r.merc == 'goles':
                return float(gl >= r.linea)
            if pd.isna(asi):
                return np.nan
            return float(asi >= r.linea) if r.merc == 'asistencias' else float(gl + asi >= 1)
        T['gana'] = [gana(r) for r in T.itertuples()]
        for merc in MERCADOS:
            J = T[(T.merc == merc) & (T.linea == 1)].dropna(subset=['gana', 'prob'])
            n = len(J)
            p(f'{merc}: {int((T.merc == merc).sum())} cuotas leidas, {n} de linea 1 resueltas (jugo el partido)')
            if not n:
                continue
            q = 1 / J.cuota.values; m = q.mean() / max(J.gana.mean(), 1e-6)
            qm = np.clip(q / m, 1e-4, 1 - 1e-4); pm = np.clip(J.prob.values, 1e-4, 1 - 1e-4); y = J.gana.values
            d = -(y * np.log(pm) + (1 - y) * np.log(1 - pm)) + (y * np.log(qm) + (1 - y) * np.log(1 - qm))
            g = J.partido.values; u = np.unique(g); rng = np.random.default_rng(0)
            idx = {k: np.where(g == k)[0] for k in u}
            bs = [np.concatenate([idx[k] for k in rng.choice(u, len(u))]) for _ in range(2000)]
            lo, hi = np.percentile([d[b].mean() for b in bs], [2.5, 97.5])
            est = ('faltan datos' if n < 300 else 'DESBLOQUEA (pedir visto bueno)' if hi < 0
                   else 'FALLA' if d.mean() >= 0 or n >= 600 else 'sin decidir, seguir hasta 600')
            p(f'  margen medio del mercado {m:.3f}; log-loss modelo - mercado {d.mean():+.4f} IC95 [{lo:+.4f}, {hi:+.4f}] -> {est}')
            p(f'  aciertan {y.mean():.1%}; prob media modelo {pm.mean():.1%}, mercado sin margen {qm.mean():.1%}')
            v = J[J.prob * J.cuota - 1 > 0.001]
            if len(v):
                p(f'  "con valor": {len(v)} apuestas, rendimiento plano {np.mean(v.gana * v.cuota - 1):+.1%}')
    (DIR / 'resultados' / 'seguimiento_goleadores.txt').write_text('\n'.join(L), encoding='utf-8')


if __name__ == '__main__':
    if sys.argv[1:2] == ['seguimiento']:
        seguimiento(); sys.exit()
    import ligas
    loc, vis, liga, ini, ml, mv = sys.argv[1:7]
    G = Partido(ligas.resolver(liga), loc, vis, ini, float(ml), float(mv))
    if not G.ok:
        sys.exit(G.err)
    for j in sorted(G.jug, key=lambda j: -G.lam(j)[0]):
        r = {m: G.prob(j['nombre'], 1, True, m)[0] for m in MERCADOS}
        print(f"{j['equipo'][:18]:18s} {j['nombre'][:24]:24s} {j['pos']}  marca {r['goles']:5.1%}  "
              f"asiste {r['asistencias']:5.1%}  una de las dos {r['goles o asistencias']:5.1%}  {j['n_tit']} tit")

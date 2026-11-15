"""
goleadores.py -- probabilidad de que un jugador marque (bet365 "Anotará", linea 1) con su xG de
FotMob (API de LudoProps). En prueba desde 2026-11-15: sin validar contra cuotas de bet365.

Modelo y coeficientes: backtest_goleadores.py (datos/agregados/goleadores_coef.json).
  lambda = ritmo por 90' x minutos esperados / 90 x goles esperados del equipo / su xG medio x c
  ritmo  = (w * xG + (1 - w) * goles + K * ritmo de su posicion) / (minutos / 90 + K)
  Sustituto+ (bet365): los minutos que no juega los llena un suplente de su posicion (r_sub).
  P(N o mas goles) con Poisson.
Solo cuentan los partidos con xG anteriores al inicio. Una peticion por estadistica (xg, goles)
y partido; se guardan en memoria para todos los jugadores del partido.

  import goleadores
  G = goleadores.Partido(liga, local, visitante, inicio_utc, mu_local, mu_visitante)
  p, aviso, nombre_fotmob, titularidades = G.prob('Mario Soberon', 1, sustituto=True)

  python goleadores.py Alaves "La Coruna" LaLiga 2026-11-21T13:00:00Z 1.3 1.1   # lista
  python goleadores.py seguimiento     # valida contra las cuotas leidas (criterios abajo)

SEGUIMIENTO -- CRITERIOS FIJADOS ANTES DE VER LOS DATOS (2026-11-15). No se cambian despues.
  bet365 solo da el "Si" del goleador, asi que el margen no se puede quitar linea a linea: la
  prob del mercado es (1/cuota) / m, con m el margen medio que hace que la prob media del
  mercado sea la frecuencia real de la muestra (el mejor desmarginado constante: favorece al
  mercado). Con las lineas 1 (marca) resueltas:
    - minimo 300 lineas;
    - DESBLOQUEA si el log-loss del modelo es menor y el IC95 de la diferencia (bootstrap por
      partidos) queda entero por debajo de 0;
    - FALLA si la diferencia media es >= 0 con 300 o mas; sin decidir a 600 = FALLA.
  Desbloquear quita el aviso "sin validar" y requiere el visto bueno del usuario.
"""
import json, sys, time, unicodedata
from pathlib import Path
import numpy as np, pandas as pd

DIR = Path(__file__).parent
API = 'https://ludoprops.com/futbol/api/partido'
COEF = DIR / 'datos' / 'agregados' / 'goleadores_coef.json'
AVISO = 'goleador (xG FotMob): sin validar contra bet365, en prueba desde 2026-11-15'
MUESTRA_MIN = 3          # apariciones con xG minimas (como en el backtest)
_coef = None


def coef():
    global _coef
    if _coef is None:
        _coef = json.loads(COEF.read_text(encoding='utf-8')) if COEF.exists() else {}
    return _coef


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


class Partido:
    """Jugadores de los dos equipos con sus rasgos, listos para prob()."""
    def __init__(self, liga, local, visitante, inicio_utc, mu_l, mu_v):
        import arbitros
        self.ok, self.err, self.jug = False, None, []
        co = coef()
        if not co:
            self.err = 'sin coeficientes de goleadores'; return
        q = arbitros.buscar_partido(liga, local, visitante, inicio_utc)
        if not q:
            self.err = 'partido no encontrado en FotMob'; return
        jx, jg = _pedir(q['id'], 'xg'), _pedir(q['id'], 'goles')
        if not (jx and jg):
            self.err = 'sin respuesta de FotMob'; return
        t = str(pd.Timestamp(q['fecha']).tz_convert('UTC'))
        goles = {e['id']: {p['id']: p['celdas'] for p in e.get('jugadores', [])} for e in jg.get('equipos', [])}
        for e in jx.get('equipos', []):
            es_local = e['id'] == q['local']['id']
            cols = e.get('columnas', [])
            prev = [i for i, c in enumerate(cols) if c and not c.get('este') and str(pd.Timestamp(c['fecha'])) < t
                    and ((c.get('stats') or {}).get('xg') or 0) > 0]
            lid = q['liga']['id'] if isinstance(q.get('liga'), dict) else None
            lp = [i for i in prev if cols[i].get('liga_id') == lid] or prev
            xl = co['xg_liga'].get(str(lid), np.mean(list(co['xg_liga'].values())))
            eq_rate = (sum(cols[i]['stats']['xg'] for i in lp) + co['K_eq'] * xl) / (len(lp) + co['K_eq'])
            mu = mu_l if es_local else mu_v
            for p in e.get('jugadores', []):
                if p.get('posicion') == 'POR':
                    continue
                g = goles.get(e['id'], {}).get(p['id'], [])
                a = [(p['celdas'][i], g[i] if i < len(g) else None) for i in prev if i < len(p['celdas'])]
                a = [(x, y) for x, y in a if x and x.get('min')]
                self.jug.append(dict(
                    nombre=p['nombre'], n=nz(p['nombre']), equipo=e['nombre'], pos=p.get('posicion') or 'MED',
                    baja=p.get('baja'), xg_s=sum(x.get('v') or 0 for x, _ in a),
                    g_s=sum((y or {}).get('v') or 0 for _, y in a), mins=sum(x['min'] for x, _ in a),
                    min_tit=sum(x['min'] for x, _ in a if x.get('tit')), n_tit=sum(bool(x.get('tit')) for x, _ in a),
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

    def lam(self, j, sustituto=True):
        co = coef(); w, K, Km, c = co['w'], co['K'], co['K_min'], co['c']
        rpos = w * co['rx'].get(j['pos'], 0.1) + (1 - w) * co['rg'].get(j['pos'], 0.1)
        r90 = (w * j['xg_s'] + (1 - w) * j['g_s'] + K * rpos) / (j['mins'] / 90 + K)
        mp = co['min_pos'].get(j['pos'], 82)
        mesp = (j['min_tit'] + Km * mp) / (j['n_tit'] + Km)
        lam = r90 * mesp / 90
        if sustituto:
            lam += co['r_sub'].get(j['pos'], rpos) * max(90 - mesp, 0) / 90
        return lam * j['f'] * c, mesp

    def prob(self, nombre, linea=1, sustituto=True):
        """(prob de N o mas goles si es TITULAR, aviso, nombre FotMob, titularidades)."""
        if not self.ok:
            return np.nan, f'sin datos de goleador ({self.err})', nombre, 0
        x = self.buscar(nombre)
        if not x:
            return np.nan, 'sin datos de goleador (jugador sin emparejar con FotMob)', nombre, 0
        if len(x) > 1:
            return np.nan, f'nombre ambiguo ({", ".join(j["nombre"] for j in x)})', nombre, 0
        j = x[0]
        lam, mesp = self.lam(j, sustituto)
        av = (f"{j['n_tit']} titularidades con xG, {j['xg_s']:.1f} xG y {j['g_s']:.0f} goles en "
              f"{j['mins']:.0f} min, {mesp:.0f} min esperados")
        if j['n_ap'] < MUESTRA_MIN:
            av += ' | MUESTRA CORTA'
        if j.get('baja'):
            av += f" | baja en FotMob: {j['baja']}"
        return poisson_mas(lam, linea), av, j['nombre'], j['n_tit']


def seguimiento():
    """Resuelve las cuotas de goleador leidas (evaluacion.csv) con FotMob y aplica los criterios."""
    import arbitros, ligas
    L = []
    def p(x=''):
        print(x); L.append(x)
    filas = []
    for ev in sorted(DIR.glob('ligas/*/jornadas/*/evaluacion.csv')):
        cu = ev.parent / 'cuotas.json'
        T = pd.read_csv(ev)
        if 'clave' not in T or not cu.exists():
            continue
        T = T[T.clave.astype(str).str.contains(r'\|goles\|')]
        if len(T):
            liga = ligas.resolver(json.loads(cu.read_text(encoding='utf-8'))['liga'])
            filas.append(T.assign(liga=liga))
    p(f'Seguimiento de goleadores ({pd.Timestamp.now():%Y-%m-%d})')
    if not filas:
        p('Todavia no hay cuotas de goleador en ninguna jornada (el recolector las lee desde 2026-11-15).')
    else:
        T = pd.concat(filas, ignore_index=True)
        cache_f = DIR / 'datos' / 'resultados_goleadores.csv'
        C = pd.read_csv(cache_f) if cache_f.exists() else pd.DataFrame(columns=['liga', 'partido', 'inicio_utc', 'nombre', 'goles'])
        hechos = set(zip(C.liga, C.partido, C.inicio_utc.astype(str)))
        nuevos = []
        for (liga, partido, ini), _ in T.groupby(['liga', 'partido', 'inicio_utc']):
            if (liga, partido, str(ini)) in hechos or pd.Timestamp(ini) > pd.Timestamp.now(tz='UTC') - pd.Timedelta(hours=3):
                continue
            loc, vis = partido.split(' - ', 1)
            q = arbitros.buscar_partido(liga, loc, vis, ini)
            if not q or q.get('goles_local') is None:
                continue
            j = _pedir(q['id'], 'goles')
            for e in (j or {}).get('equipos', []):
                k = next((i for i, c in enumerate(e['columnas']) if c and c.get('partido_id') == q['id']), None)
                for pl in e.get('jugadores', []):
                    c = pl['celdas'][k] if k is not None and k < len(pl['celdas']) else None
                    if c:
                        nuevos.append(dict(liga=liga, partido=partido, inicio_utc=ini, nombre=nz(pl['nombre']), goles=c.get('v') or 0))
        if nuevos:
            C = pd.concat([C, pd.DataFrame(nuevos)], ignore_index=True); C.to_csv(cache_f, index=False)
        T['jug'] = T.clave.str.split('|').str[0].map(nz)
        T['linea'] = T.clave.str.split('|').str[2].astype(int)
        def gol(r):
            g = C[(C.liga == r.liga) & (C.partido == r.partido) & (C.inicio_utc.astype(str) == str(r.inicio_utc))]
            m = g[[all(t in n for t in r.jug.split()) for n in g.nombre.astype(str)]]
            return float(m.goles.iloc[0] >= r.linea) if len(m) == 1 else np.nan   # no jugo o ambiguo: nula
        T['gana'] = [gol(r) for r in T.itertuples()]
        J = T[(T.linea == 1)].dropna(subset=['gana', 'prob'])
        n = len(J)
        p(f'{len(T)} cuotas de goleador leidas, {n} de "marca" resueltas (jugo el partido)')
        if n:
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
            p(f'  marcan {y.mean():.1%}; prob media modelo {pm.mean():.1%}, mercado sin margen {qm.mean():.1%}')
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
        lam = G.lam(j)[0]
        print(f"{j['equipo'][:18]:18s} {j['nombre'][:24]:24s} {j['pos']}  marca {1 - np.exp(-lam):5.1%}  "
              f"(sin sustituto {1 - np.exp(-G.lam(j, False)[0]):5.1%})  {j['n_tit']} tit")

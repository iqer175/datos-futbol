"""
xi_fotmob.py -- titularidad de partida de cada jugador con los datos de FotMob (API de LudoProps):
sus titularidades recientes, el XI que da FotMob y sus bajas (lesiones, sanciones, selecciones).
Desde 2026-11-16. Es el punto de partida de titular_prob en contexto.json; el analista-contexto lo
ajusta con las noticias.

Lo que da FotMob por partido (una peticion):
  - xi: "predicted" (XI previsto, solo en algunos partidos), "lastStarting11" (el once del partido
    ANTERIOR: no es una prevision; marca titulares a sancionados y convocados), "jugado".
  - por jugador: titular segun ese XI, baja {tipo: injury | suspension | internationalDuty | ...,
    vuelta: "Doubtful" | "Back in training" | "Late November 2026" | "Out for season"...}.

Probabilidad de partida (medida, no inventada):
  base  = P(titular | titularidades en los 5 ultimos partidos de liga de su equipo, titular en el
          ultimo), tabla de datos/alineaciones/jugadores.csv (ESPN, 66.009 casos; fuera de muestra
          log-loss 0,432 frente a 0,466 solo con el ultimo partido) -> datos/agregados/titular_tabla.json
  bajas (regla, sin historico para medirla; se mide hacia delante con "seguimiento"):
          "Doubtful" x 0,5; "Back in training" x 0,7; vuelta prevista antes del partido x 0,6;
          resto (fuera, sancionado, convocado, vuelta posterior) -> 0,03

  python xi_fotmob.py tabla                                   # rehace la tabla con ESPN
  python xi_fotmob.py ver LaLiga Alaves "La Coruna" 2026-11-21T13:00:00Z
  python xi_fotmob.py seguimiento   # calibracion de titular_base y del titular_prob del analista
                                    # frente a los titulares reales -> resultados/titularidad.txt
"""
import json, re, sys, time
from datetime import datetime
from pathlib import Path
import numpy as np, pandas as pd

DIR = Path(__file__).parent
TABLA = DIR / 'datos' / 'agregados' / 'titular_tabla.json'
API = 'https://ludoprops.com/futbol/api/partido'
FUERA = 0.03
MESES = {m: i for i, m in enumerate(['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august',
                                      'september', 'october', 'november', 'december'], 1)}
TIPOS = {'injury': 'lesión', 'suspension': 'sanción', 'internationalDuty': 'con su selección'}


def nz(s):
    import unicodedata
    return unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode().lower().replace('-', ' ')


# ---------------------------------------------------------------- tabla (ESPN)
def hacer_tabla():
    j = pd.read_csv(DIR / 'datos' / 'alineaciones' / 'jugadores.csv', dtype=str)
    j['tit'] = j.titular == 'True'; j['fecha'] = pd.to_datetime(j.fecha)
    j = j.drop_duplicates(['espn_id', 'jugador_id'])
    m = j[['espn_id', 'equipo_id', 'fecha']].drop_duplicates().sort_values('fecha')
    m['k'] = m.groupby('equipo_id').cumcount()
    j = j.merge(m, on=['espn_id', 'equipo_id', 'fecha'])
    cnt = {}
    for _, g in j.groupby('equipo_id'):
        K = g.k.max() + 1
        A = g.pivot_table(index='jugador_id', columns='k', values='tit', aggfunc='first').reindex(columns=range(K)).values.astype(float)
        for k in range(5, K):
            prev = A[:, k - 5:k]
            for i in np.where(~np.all(np.isnan(prev), axis=1))[0]:
                key = f'{int(np.nansum(prev[i] == 1))}|{int(prev[i, -1] == 1)}'
                n, s = cnt.get(key, (0, 0)); cnt[key] = (n + 1, s + float(A[i, k] == 1))
    tab = {k: dict(n=n, p=round(s / n, 4)) for k, (n, s) in sorted(cnt.items())}
    TABLA.write_text(json.dumps(dict(tabla=tab, fecha=f'{pd.Timestamp.now():%Y-%m-%d}',
                                     fuente='datos/alineaciones/jugadores.csv'), indent=1), encoding='utf-8')
    for k, v in tab.items():
        print(f'  titular {k.split("|")[0]} de 5, en el ultimo {"si" if k[-1] == "1" else "no"}: {v["p"]:.1%} (n={v["n"]})')
    return tab


_tab = None
def tabla():
    global _tab
    if _tab is None:
        _tab = json.loads(TABLA.read_text(encoding='utf-8'))['tabla'] if TABLA.exists() else hacer_tabla()
    return _tab


def p_patron(tit5, ultimo):
    t = tabla()
    k = f'{int(tit5)}|{int(bool(ultimo))}'
    if k not in t:       # 5 de 5 sin el ultimo no existe: el mas cercano
        k = f'{int(tit5)}|{int(not ultimo)}'
    return t.get(k, {'p': 0.1})['p']


# ---------------------------------------------------------------- bajas
def _vuelta_fecha(v):
    m = re.match(r'(early|mid|late)\s+(\w+)\s+(\d{4})', str(v).strip().lower())
    if not m or m.group(2) not in MESES:
        return None
    return datetime(int(m.group(3)), MESES[m.group(2)], {'early': 5, 'mid': 15, 'late': 25}[m.group(1)])


def factor_baja(b, fecha_partido):
    """(factor o prob fija, texto). factor < 1 multiplica; None = fuera (prob FUERA)."""
    if not b:
        return 1.0, ''
    tipo, v = b.get('tipo'), str(b.get('vuelta') or '')
    txt = f"{TIPOS.get(tipo, tipo)}, vuelta {v or 'sin fecha'}"
    if v == 'Doubtful':
        return 0.5, txt
    if v == 'Back in training':
        return 0.7, txt
    f = _vuelta_fecha(v)
    if f is not None and fecha_partido is not None and f <= fecha_partido.replace(tzinfo=None):
        return 0.6, txt
    return None, txt


def _baja(nombre, b):
    """Formato de bajas_local / bajas_visitante de contexto.json."""
    return {'jugador': nombre, 'motivo': TIPOS.get(b.get('tipo'), b.get('tipo')), 'vuelta': b.get('vuelta'),
            'fuente': 'FotMob', 'fecha': f'{pd.Timestamp.now(tz="UTC"):%Y-%m-%d}'}


# ---------------------------------------------------------------- partido
def info(liga, local, visitante, inicio_utc):
    """{'xi': tipo, 'equipos': {'local'|'visitante': {...}}, 'jugadores': {nombre FotMob: {...}}} o None."""
    import arbitros, requests
    q = arbitros.buscar_partido(liga, local, visitante, inicio_utc)
    if not q:
        return None
    j = None
    for i in range(3):
        try:
            r = requests.get(API, params={'id': q['id'], 'stat': 'remates', 'linea': 0.5, 'ultimos': 10, 'supersub': 0},
                             headers={'User-Agent': 'Mozilla/5.0'}, timeout=30)
            if r.status_code == 200:
                j = r.json(); break
        except Exception as e:
            print(f'  aviso xi_fotmob: {e}', file=sys.stderr)
        time.sleep(2 * (i + 1))
    if not j:
        return None
    fecha = pd.Timestamp(q['fecha']).to_pydatetime()
    lg = q.get('liga') or (j.get('partido') or {}).get('liga')
    lid = lg['id'] if isinstance(lg, dict) else None
    out = dict(xi=q.get('xi') or (j.get('partido') or {}).get('xi'), id=q['id'], equipos={}, jugadores={})
    for e in j.get('equipos', []):
        lado = 'local' if e['id'] == q['local']['id'] else 'visitante'
        cols = e.get('columnas', [])
        liga_i = [i for i, c in enumerate(cols) if c and not c.get('este') and c.get('liga_id') == lid][:5]
        bajas = []
        for p in e.get('jugadores', []):
            cel = p.get('celdas') or []
            tits = [bool(cel[i] and cel[i].get('tit')) if i < len(cel) else False for i in liga_i]
            base = p_patron(sum(tits), tits[0] if tits else False)
            f, txt = factor_baja(p.get('baja'), fecha)
            prob = FUERA if f is None else base * f
            if txt:
                bajas.append(_baja(p['nombre'], p['baja']))
            out['jugadores'][p['nombre']] = dict(
                equipo=e['nombre'], lado=lado, posicion=p.get('posicion'), en_xi_fotmob=bool(p.get('titular')),
                titular_ultimos_5_liga=sum(tits), titular_en_el_ultimo=bool(tits and tits[0]),
                baja=txt or None, base=round(prob, 3))
        for b in e.get('bajas_sin_datos') or []:       # bajas de jugadores sin partidos
            bajas.append(_baja(b['nombre'], b))
        out['equipos'][lado] = dict(nombre=e['nombre'], bajas=bajas,
                                    xi=[p['nombre'] for p in e.get('jugadores', []) if p.get('titular')])
    return out


def casar(nombre, I):
    """Nombre FotMob del jugador de bet365/ESPN 'nombre' en info(), o None."""
    from difflib import SequenceMatcher
    if not I:
        return None
    t = nz(nombre).split()
    c = [n for n in I['jugadores'] if all(x in nz(n).split() for x in t) or all(x in t for x in nz(n).split())]
    if len(c) == 1:
        return c[0]
    if not c:
        r = sorted(((SequenceMatcher(None, ' '.join(sorted(t)), ' '.join(sorted(nz(n).split()))).ratio(), n)
                    for n in I['jugadores']), reverse=True)
        if r and r[0][0] >= 0.85 and (len(r) == 1 or r[1][0] < r[0][0] - 0.05):
            return r[0][1]
    return None


# ---------------------------------------------------------------- seguimiento
def seguimiento():
    """Titulares reales (FotMob) frente a titular_base y titular_prob de cada contexto.json jugado."""
    import arbitros, ligas, requests
    filas = []
    cache_f = DIR / 'datos' / 'titulares_reales.csv'
    C = pd.read_csv(cache_f) if cache_f.exists() else pd.DataFrame(columns=['liga', 'local', 'visitante', 'inicio_utc', 'nombre', 'titular'])
    hechos = set(zip(C.liga, C.local, C.visitante))
    nuevos = []
    for f in sorted(DIR.glob('ligas/*/jornadas/*/contexto.json')):
        cx = json.loads(f.read_text(encoding='utf-8'))
        liga = ligas.resolver(cx['liga'])
        for P in cx.get('partidos', []):
            ini = pd.to_datetime(P.get('inicio_utc'), utc=True, errors='coerce')
            if pd.isna(ini) or ini > pd.Timestamp.now(tz='UTC') - pd.Timedelta(hours=3):
                continue
            if (liga, P['local'], P['visitante']) not in hechos:
                q = arbitros.buscar_partido(liga, P['local'], P['visitante'], P['inicio_utc'])
                if q and q.get('goles_local') is not None:
                    r = requests.get(API, params={'id': q['id'], 'stat': 'remates', 'linea': 0.5, 'ultimos': 5, 'supersub': 0},
                                     headers={'User-Agent': 'Mozilla/5.0'}, timeout=30).json()
                    for e in r.get('equipos', []):
                        k = next((i for i, c in enumerate(e['columnas']) if c and c.get('partido_id') == q['id']), None)
                        for p in e.get('jugadores', []):
                            c = p['celdas'][k] if k is not None and k < len(p['celdas']) else None
                            nuevos.append(dict(liga=liga, local=P['local'], visitante=P['visitante'], inicio_utc=P['inicio_utc'],
                                               nombre=p['nombre'], titular=bool(c and c.get('tit'))))
                    hechos.add((liga, P['local'], P['visitante'])); time.sleep(0.5)
            for nom, pr in (P.get('titular_prob') or {}).items():
                filas.append(dict(liga=liga, local=P['local'], visitante=P['visitante'], jugador=nom,
                                  prob=pr, base=(P.get('titular_base') or {}).get(nom)))
    if nuevos:
        C = pd.concat([C, pd.DataFrame(nuevos)], ignore_index=True); C.to_csv(cache_f, index=False)
    L = [f'Titularidad prevista frente a la real ({pd.Timestamp.now():%Y-%m-%d})']
    T = pd.DataFrame(filas)
    if T.empty or C.empty:
        L.append('Todavia no hay partidos jugados con contexto.json y titulares de FotMob.')
    else:
        def real(r):
            g = C[(C.liga == r.liga) & (C.local == r.local) & (C.visitante == r.visitante)]
            if g.empty:
                return np.nan
            n = casar(r.jugador, {'jugadores': {x: 1 for x in g.nombre}})
            return float(g[g.nombre == n].titular.iloc[0]) if n else 0.0     # no aparece: no fue titular
        T['y'] = [real(r) for r in T.itertuples()]
        T = T.dropna(subset=['y'])
        ll = lambda p, y: -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))
        L.append(f'{len(T)} jugadores con cuota en {T[["local", "visitante"]].drop_duplicates().shape[0]} partidos; titulares {T.y.mean():.1%}')
        for col, nombre in (('prob', 'titular_prob (analista)'), ('base', 'titular_base (FotMob + tabla)')):
            x = T.dropna(subset=[col])
            if len(x):
                p = np.clip(x[col].astype(float).values, 0.01, 0.99)
                L.append(f'  {nombre:30s} n={len(x):4d}  log-loss {ll(p, x.y.values):.4f}  prob media {p.mean():.1%}  real {x.y.mean():.1%}')
                g = x.assign(t=pd.cut(p, [0, .5, .8, .9, 1])).groupby('t', observed=True).agg(n=('y', 'size'), p=(col, 'mean'), r=('y', 'mean'))
                for k, r in g.iterrows():
                    L.append(f'      {str(k):11s} n={int(r.n):4d}  prevista {r.p:.1%}  real {r.r:.1%}')
    txt = '\n'.join(L); print(txt)
    (DIR / 'resultados' / 'titularidad.txt').write_text(txt + '\n', encoding='utf-8')


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd == 'tabla':
        hacer_tabla()
    elif cmd == 'seguimiento':
        seguimiento()
    elif cmd == 'ver':
        import ligas
        I = info(ligas.resolver(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5])
        if not I:
            sys.exit('partido no encontrado')
        print(f"XI de FotMob: {I['xi']}")
        for lado, e in I['equipos'].items():
            print(f"{e['nombre']}: bajas " + ('; '.join(f"{b['jugador']} ({b['motivo']}, {b['vuelta']})" for b in e['bajas']) or 'ninguna'))
        for n, x in sorted(I['jugadores'].items(), key=lambda kv: (kv[1]['lado'], -kv[1]['base'])):
            print(f"  {x['equipo'][:16]:16s} {n[:24]:24s} {x['posicion'] or '':3s} base {x['base']:.2f}  "
                  f"tit {x['titular_ultimos_5_liga']}/5{' (ultimo)' if x['titular_en_el_ultimo'] else ''}"
                  f"{'  XI' if x['en_xi_fotmob'] else ''}{'  ' + x['baja'] if x['baja'] else ''}")
    else:
        print(__doc__)

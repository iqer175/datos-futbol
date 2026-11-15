"""
cuotas_ludoprops.py -- cuotas de bet365 de remates y remates a puerta por jugador que publica
LudoProps (/futbol/api/cuotas, datos de FotMob), para (1) comprobar las lecturas del recolector
y (2) leer los CIERRES sin lanzar el recolector a mano (CLV de remates). Desde 2026-11-16.

Limites de la fuente: API no oficial de un tercero (puede cambiar o cerrarse: nada depende de
ella); su bet365 es el de bet365.com, no bet365.es; solo remates y a puerta (no goleador,
asistencias, faltas ni tarjetas); solo partidos que no han empezado (no guarda historial). Pocas
peticiones: una por linea y mercado (unas 12 por partido) y una para los nombres.

  python cuotas_ludoprops.py leer --dir <carpeta de jornada> [--partido "Local - Visitante" ...]
      -> <carpeta>/ludoprops_<AAAAMMDDTHHMM>.json, mismo formato que cuotas.json, con los nombres
         de jugador de bet365 (los de cuotas.json) cuando casan; nunca toca cuotas.json
  python cuotas_ludoprops.py comparar           # todas las jornadas
      -> resultados/cuotas_ludoprops.txt: lineas comunes con el recolector leidas a menos de 30
         min, diferencias y si se cumple el criterio
  python cuotas_ludoprops.py cierre [--horas 3]
      lee LudoProps de los partidos con cuotas.json que empiezan en las proximas horas
      (los mismos que cierres_jugadores.py pendientes)

CRITERIO FIJADO ANTES DE VER LOS DATOS (2026-11-16). No se cambia despues.
  Lineas de jugador presentes en cuotas.json (recolector, bet365.es) y en una lectura de
  LudoProps hecha a menos de 30 min. COINCIDEN si hay >= 200 lineas de >= 5 partidos, el 90 % o
  mas difiere menos de un 3 % y la mediana del cociente LudoProps / bet365.es esta entre 0,99 y
  1,01. Solo entonces, y con el visto bueno del usuario (datos/ludoprops_validacion.json,
  "usar_en_cierres": true), cierres_jugadores.py cuenta las lecturas de LudoProps como cierre.
"""
import argparse, json, re, sys, time, unicodedata
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
import numpy as np, pandas as pd

DIR = Path(__file__).parent
API = 'https://ludoprops.com/futbol/api'
VALIDACION = DIR / 'datos' / 'ludoprops_validacion.json'
LINEAS = {'remates': ('remates', 10), 'a puerta': ('al_arco', 5)}     # mercado: (stat, linea maxima)
VENTANA_MIN, N_MIN, P_MIN, TOL = 30, 200, 0.90, 0.03


def nz(s):
    return unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode().lower().replace('-', ' ')


def usar_en_cierres():
    try:
        return bool(json.loads(VALIDACION.read_text(encoding='utf-8')).get('usar_en_cierres'))
    except (OSError, ValueError):
        return False


def _get(s, ruta, **params):
    for i in range(3):
        try:
            r = s.get(f'{API}/{ruta}', params=params, timeout=30)
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            print(f'  aviso ludoprops {ruta} {params}: {e}', file=sys.stderr)
        time.sleep(2 * (i + 1))
    return None


def casar_nombres(fotmob, bet365):
    """{id FotMob: nombre bet365} para los que casan sin duda; el resto se queda con el de FotMob."""
    out, b = {}, {n: nz(n).split() for n in bet365}
    for pid, nom in fotmob.items():
        f = nz(nom).split()
        c = [n for n, t in b.items() if t and (all(x in f for x in t) or all(x in t for x in f))]
        if len(c) != 1:
            r = sorted(((SequenceMatcher(None, ' '.join(sorted(f)), ' '.join(sorted(t))).ratio(), n) for n, t in b.items()), reverse=True)
            c = [r[0][1]] if r and r[0][0] >= 0.85 and (len(r) == 1 or r[1][0] < r[0][0] - 0.05) else []
        out[pid] = c[0] if c else nom
    return out


def leer_partido(s, liga, P):
    """Entrada de partido (formato cuotas.json) con las cuotas de bet365 de LudoProps, o None."""
    import arbitros
    q = arbitros.buscar_partido(liga, P['local'], P['visitante'], P.get('inicio_utc'))
    if not q:
        print(f"  {P['local']} - {P['visitante']}: no encontrado en LudoProps", file=sys.stderr); return None
    if q.get('estado') not in (None, 'pendiente'):
        print(f"  {P['local']} - {P['visitante']}: ya empezado ({q.get('estado')})", file=sys.stderr); return None
    j = _get(s, 'partido', id=q['id'], stat='remates', linea=0.5, ultimos=5, supersub=0) or {}
    fm = {str(p['id']): p['nombre'] for e in j.get('equipos', []) for p in e.get('jugadores', [])}
    nombres = casar_nombres(fm, {x['jugador'] for x in P.get('jugadores') or []})
    jug, casas = [], {}
    for mercado, (stat, lmax) in LINEAS.items():
        for n in range(1, lmax + 1):
            c = _get(s, 'cuotas', id=q['id'], stat=stat, linea=n - 0.5)
            time.sleep(0.3)
            if not c or not c.get('soporta'):
                break
            casas.update({k: v.get('estado') for k, v in (c.get('casas') or {}).items()})
            hay = False
            for pid, od in (c.get('jugadores') or {}).items():
                o = (od or {}).get('b365')
                if o:
                    hay = True
                    jug.append(dict(jugador=nombres.get(pid, fm.get(pid, pid)), fotmob_id=pid, mercado=mercado, linea=n, cuota=o))
            if not hay:
                break
    ahora = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%MZ')
    return dict(local=P['local'], visitante=P['visitante'], inicio_utc=P.get('inicio_utc'), leido_utc=ahora,
                fuente='ludoprops (bet365.com)', casas=casas, mercados={}, jugadores=jug)


def leer(carpeta, partidos=None):
    import requests, ligas
    carpeta = Path(carpeta)
    cu = json.loads((carpeta / 'cuotas.json').read_text(encoding='utf-8'))
    liga = ligas.resolver(cu['liga'])
    s = requests.Session(); s.headers['User-Agent'] = 'Mozilla/5.0'
    out = []
    for P in cu['partidos']:
        if partidos and f"{P['local']} - {P['visitante']}" not in partidos:
            continue
        x = leer_partido(s, liga, P)
        if x:
            out.append(x)
            print(f"  {P['local']} - {P['visitante']}: {len(x['jugadores'])} cuotas de jugador")
    if not out:
        print('Nada leido.'); return None
    ts = datetime.now(timezone.utc)
    f = carpeta / f'ludoprops_{ts:%Y%m%dT%H%M}.json'
    f.write_text(json.dumps(dict(liga=cu['liga'], leido_utc=ts.strftime('%Y-%m-%dT%H:%MZ'), fuente='ludoprops (bet365.com)',
                                 partidos=out), ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'-> {f.as_posix()}')
    return f


def comparar():
    filas = []
    for cu_f in sorted(DIR.glob('ligas/*/jornadas/*/cuotas.json')):
        cu = json.loads(cu_f.read_text(encoding='utf-8'))
        for lf in sorted(cu_f.parent.glob('ludoprops_*.json')):
            lp = json.loads(lf.read_text(encoding='utf-8'))
            for L in lp.get('partidos', []):
                P = next((p for p in cu['partidos'] if p['local'] == L['local'] and p['visitante'] == L['visitante']), None)
                if not P:
                    continue
                t_r = pd.to_datetime(P.get('leido_utc') or cu.get('leido_utc'), utc=True, errors='coerce')
                t_l = pd.to_datetime(L.get('leido_utc'), utc=True, errors='coerce')
                dmin = abs((t_l - t_r).total_seconds()) / 60 if pd.notna(t_r) and pd.notna(t_l) else np.nan
                ld = {(nz(x['jugador']), x['mercado'], int(x['linea'])): x['cuota'] for x in L['jugadores']}
                for x in P.get('jugadores') or []:
                    k = (nz(x['jugador']), x['mercado'], int(x['linea']))
                    if k in ld and x.get('cuota'):
                        filas.append(dict(jornada=cu_f.parent.as_posix(), partido=f"{P['local']} - {P['visitante']}",
                                          clave='|'.join(map(str, k)), bet365_es=x['cuota'], ludoprops=ld[k], min_entre=dmin))
    L = [f'Cuotas de LudoProps frente al recolector ({pd.Timestamp.now():%Y-%m-%d})']
    if not filas:
        L.append('Todavia no hay lecturas de LudoProps de partidos con cuotas.json '
                 '(lanzar "python cuotas_ludoprops.py leer --dir <carpeta>" justo despues del recolector).')
    else:
        T = pd.DataFrame(filas)
        T['ratio'] = T.ludoprops / T.bet365_es
        V = T[T.min_entre <= VENTANA_MIN]
        L.append(f'{len(T)} lineas comunes; {len(V)} leidas a menos de {VENTANA_MIN} min, de {V.partido.nunique()} partidos')
        if len(V):
            ok = (V.ratio - 1).abs() < TOL
            med = V.ratio.median()
            L.append(f'  difieren menos de un {TOL:.0%}: {ok.mean():.1%}; mediana LudoProps / bet365.es {med:.3f}; '
                     f'iguales al centimo {((V.ludoprops - V.bet365_es).abs() < 0.015).mean():.1%}')
            for m, g in V.groupby(V.clave.str.split('|').str[1]):
                L.append(f'    {m:9s} n={len(g):4d}  <{TOL:.0%}: {((g.ratio - 1).abs() < TOL).mean():.1%}  mediana {g.ratio.median():.3f}')
            if len(V) < N_MIN or V.partido.nunique() < 5:
                est = f'faltan datos (minimo {N_MIN} lineas de 5 partidos)'
            elif ok.mean() >= P_MIN and 0.99 <= med <= 1.01:
                est = 'COINCIDEN: se pueden usar como cierre con el visto bueno del usuario'
            else:
                est = 'NO COINCIDEN: no usar como cierre'
            L.append(f'  -> {est}')
            peores = V.assign(d=(V.ratio - 1).abs()).sort_values('d').tail(5)
            L.append('  mayores diferencias: ' + '; '.join(f'{r.clave} {r.bet365_es} vs {r.ludoprops}' for r in peores.itertuples()))
        T.to_csv(DIR / 'datos' / 'comparacion_ludoprops.csv', index=False)
    L.append(f'Uso en cierres: {"SI" if usar_en_cierres() else "NO"} ({VALIDACION.relative_to(DIR).as_posix()})')
    txt = '\n'.join(L); print(txt)
    (DIR / 'resultados' / 'cuotas_ludoprops.txt').write_text(txt + '\n', encoding='utf-8')


def cierre(horas):
    import cierres_jugadores as cj
    ahora = datetime.now(timezone.utc)
    for carpeta, d in cj.jornadas(DIR / 'ligas'):
        hechos = cj.cierres_de(carpeta)
        pend = []
        for P in d.get('partidos', []):
            ini = cj.hora(P.get('inicio_utc'))
            if pd.isna(ini) or not (ahora < ini <= ahora + timedelta(hours=horas)):
                continue
            c = hechos.get((P['local'], P['visitante']))
            if c and (ini - c[0]) <= timedelta(minutes=cj.MAX_ANTES):
                continue
            pend.append(f"{P['local']} - {P['visitante']}")
        if pend:
            print(f'{carpeta.relative_to(DIR).as_posix()}: {len(pend)} partidos')
            leer(carpeta, pend)
    if not usar_en_cierres():
        print('Aviso: estas lecturas aun NO cuentan como cierre (falta validar con "comparar" y el visto bueno).')


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    a = sub.add_parser('leer'); a.add_argument('--dir', required=True); a.add_argument('--partido', nargs='*')
    sub.add_parser('comparar')
    c = sub.add_parser('cierre'); c.add_argument('--horas', type=float, default=3)
    x = ap.parse_args()
    if x.cmd == 'leer':
        leer(x.dir, x.partido)
    elif x.cmd == 'comparar':
        comparar()
    else:
        cierre(x.horas)

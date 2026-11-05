#!/usr/bin/env python3
"""
resolver_picks.py -- resuelve las apuestas pendientes de la bitacora con los datos del repo.

La bitacora vive en la base de datos del artifact (coleccion `picks`). La sesion principal la
descarga a una carpeta de JSON (un fichero por apuesta), este script decide cada resultado y deja
`resoluciones.json`, que la sesion principal escribe de vuelta en el artifact en un solo lote.

Fuentes:
  - Remates / remates a puerta del jugador: datos/alineaciones/jugadores.csv (ESPN)
  - Goles, corners y resultado:            datos/partidos.csv (football-data)

Reglas de bet365 que se aplican:
  - Mercado de jugador: si no juega ni un minuto (no titular y no entra), la apuesta es NULA.
  - Si el partido no esta aun en los datos, queda pendiente.

Uso:
  python resolver_picks.py --picks <carpeta con los JSON> [--actualizar] [--salida resoluciones.json]
      --actualizar descarga antes de ESPN los partidos jugados de las ligas de las apuestas.
"""
import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd

DIR = Path(__file__).parent
sys.path.insert(0, str(DIR))
from emparejar_nombres import similitud  # noqa: E402

SIM_MIN = 0.6


def nz(s):
    return unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode().lower()


def cargar_picks(carpeta):
    out = []
    for f in sorted(Path(carpeta).glob('*.json')):
        if f.name == 'resoluciones.json':
            continue
        d = json.loads(f.read_text(encoding='utf-8'))
        out.append(dict(d.get('data', d), doc_id=d.get('id', f.stem)))
    return out


def buscar_partido(tabla, liga, fecha, local, visit, col_l, col_v):
    """Fila de 'tabla' que mejor casa con (liga, fecha +-1 dia, local, visitante)."""
    f = pd.Timestamp(fecha)
    c = tabla[(tabla.liga == liga) & (tabla.fecha >= f - pd.Timedelta(days=1)) & (tabla.fecha <= f + pd.Timedelta(days=1))]
    if not len(c):
        return None
    s = c.apply(lambda r: min(similitud(local, r[col_l]), similitud(visit, r[col_v])), axis=1)
    return c.loc[s.idxmax()] if s.max() >= SIM_MIN else None


def partidos_espn(J):
    """Un registro por partido ESPN: liga, fecha, equipo local (home) y visitante (away)."""
    g = J.groupby(['espn_id', 'lado']).agg(equipo=('equipo', 'first'), liga=('liga', 'first'), fecha=('fecha', 'first')).reset_index()
    P = g.pivot(index='espn_id', columns='lado', values='equipo').reset_index()
    P = P.merge(g.drop_duplicates('espn_id')[['espn_id', 'liga', 'fecha']], on='espn_id')
    P['fecha'] = pd.to_datetime(P.fecha)
    return P


def ganancia(p, estado):
    st, cu = float(p.get('stake') or 0), float(p.get('cuota') or 0)
    return round(st * (cu - 1), 3) if estado == 'ganada' else -st if estado == 'perdida' else 0.0


def resolver(p, df, J, PE):
    """(estado, detalle). estado: ganada | perdida | nula | pendiente | revisar."""
    mer, sel, liga = p.get('mercado', ''), p.get('seleccion', ''), p.get('competicion', '')
    loc, vis, fecha = p.get('local', ''), p.get('visitante', ''), p.get('fecha', '')
    # ---- jugador
    m = re.match(r'(.+?) - (\d+) o más remates( a puerta)?$', sel)
    if m:
        nombre, k, puerta = m.group(1), int(m.group(2)), bool(m.group(3))
        e = buscar_partido(PE, liga, fecha, loc, vis, 'home', 'away')
        if e is None:
            return 'pendiente', 'partido aún no está en jugadores.csv (ESPN)'
        x = J[J.espn_id == e.espn_id]
        toks = nz(nombre).split()
        x = x[x.jugador.map(nz).apply(lambda s: all(t in s for t in toks))]
        if x.jugador_id.nunique() > 1:
            return 'revisar', f'nombre ambiguo: {sorted(set(x.jugador))}'
        if not len(x):
            return 'nula', f'{nombre} no estaba convocado (ESPN {e.espn_id})'
        r = x.iloc[0]
        if not (r.titular == 'True' or r.entro == 'True'):
            return 'nula', f'{r.jugador} no jugó'
        v = pd.to_numeric(r.tiros_puerta if puerta else r.tiros, errors='coerce')
        if pd.isna(v):
            return 'revisar', f'{r.jugador}: ESPN sin dato de remates'
        rol = 'titular' if r.titular == 'True' else 'suplente'
        mins = pd.to_numeric(r.get('minutos'), errors='coerce')
        rol += '' if pd.isna(mins) else f', {int(mins)} min'
        return ('ganada' if v >= k else 'perdida'), f'{r.jugador} ({rol}): {int(v)} {"a puerta" if puerta else "remates"}'
    # ---- partido
    f = buscar_partido(df, liga, fecha, loc, vis, 'local', 'visitante')
    if f is None:
        return 'pendiente', 'partido aún no está en partidos.csv'
    gl, gv = f.gl, f.gv
    if pd.isna(gl):
        return 'pendiente', 'sin resultado todavía'
    m = re.match(r'(.+?) - (Más|Menos) de ([\d.]+) córners$', sel)
    if m:
        eq, sentido, ln = m.group(1), m.group(2), float(m.group(3))
        es_l = similitud(eq, f.local) >= similitud(eq, f.visitante)
        c = f.corners_l if es_l else f.corners_v
        if pd.isna(c):
            return 'revisar', 'football-data no trae corners de este partido'
        ok = c > ln if sentido == 'Más' else c < ln
        return ('ganada' if ok else 'perdida'), f'{f.local if es_l else f.visitante}: {int(c)} córners'
    m = re.match(r'(Más|Menos) de ([\d.]+)$', sel)
    if m:
        t = gl + gv; ln = float(m.group(2))
        ok = t > ln if m.group(1) == 'Más' else t < ln
        return ('ganada' if ok else 'perdida'), f'{int(gl)}-{int(gv)}'
    if mer.startswith('Resultado'):
        res = 'X' if nz(sel) == 'empate' else ('1' if similitud(sel, f.local) >= similitud(sel, f.visitante) else '2')
        real = '1' if gl > gv else '2' if gl < gv else 'X'
        return ('ganada' if res == real else 'perdida'), f'{f.local} {int(gl)}-{int(gv)} {f.visitante}'
    return 'revisar', f'mercado no reconocido: {mer} / {sel}'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--picks', required=True)
    ap.add_argument('--actualizar', action='store_true')
    ap.add_argument('--salida', default=None)
    a = ap.parse_args()
    picks = [p for p in cargar_picks(a.picks) if p.get('estado', 'pendiente') == 'pendiente']
    if a.actualizar:
        import espn
        ligas = sorted({p['competicion'] for p in picks if p.get('competicion') in espn.CODIGOS})
        espn.guardar_titulares(ligas, 14)
    df = pd.read_csv(DIR / 'datos' / 'partidos.csv', low_memory=False,
                     usecols=['liga', 'fecha', 'local', 'visitante', 'gl', 'gv', 'corners_l', 'corners_v'])
    df['fecha'] = pd.to_datetime(df.fecha)
    J = pd.read_csv(DIR / 'datos' / 'alineaciones' / 'jugadores.csv', dtype=str)
    PE = partidos_espn(J)
    out = []
    for p in picks:
        estado, det = resolver(p, df, J, PE)
        out.append(dict(doc_id=p['doc_id'], estado=estado, resultado=det, ganancia=ganancia(p, estado),
                        stake=p.get('stake'), cuota=p.get('cuota'), prob=p.get('prob'),
                        competicion=p.get('competicion'), mercado=p.get('mercado'), seleccion=p.get('seleccion')))
    sal = Path(a.salida) if a.salida else Path(a.picks) / 'resoluciones.json'
    sal.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    T = pd.DataFrame(out)
    print(T[['doc_id', 'estado', 'resultado', 'ganancia']].to_string(index=False))
    print('\n' + T.estado.value_counts().to_string())
    print(f'\n-> {sal.as_posix()}')


if __name__ == '__main__':
    main()

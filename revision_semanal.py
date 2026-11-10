#!/usr/bin/env python3
"""
revision_semanal.py -- calibracion de la bitacora y lecciones de los agentes, para la revision del martes.

Junta las apuestas de la bitacora (carpeta de JSON descargada del artifact) con las resoluciones
de resolver_picks.py, mide por tipo de apuesta, liga y tramo de cuota si se acierta lo que dice el
modelo, recoge las lecciones.md que dejaron los agentes y escribe una nota de Obsidian en
notas/revisiones/<fecha>.md para decidir que reglas cambiar.

Ninguna regla se cambia aqui: el script solo informa. Los cambios los aprueba el usuario y se
apuntan en reglas_aprendidas.md.

Uso:
  python revision_semanal.py --picks <carpeta JSON> [--resoluciones <resoluciones.json>] [--dias 7]
"""
import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

DIR = Path(__file__).parent
NOTAS = DIR.parent / 'notas' / 'revisiones'
N_MIN = 30   # apuestas resueltas de un tipo antes de cambiar una regla


def tipo(sel, mer):
    m = re.match(r'.+ - (\d+) o más remates( a puerta)?$', str(sel))
    if m:
        return f'{"a puerta" if m.group(2) else "remates"} {m.group(1)}+'
    m = re.match(r'.+ - (Más|Menos) de [\d.]+ córners$', str(sel))
    if m:
        return f'córners equipo {m.group(1).lower()}'
    m = re.match(r'(Más|Menos) de [\d.]+$', str(sel))
    if m:
        return f'goles {m.group(1).lower()}'
    if str(mer).startswith('Resultado'):
        return '1X2'
    return str(mer)


def tramo(c):
    return '<1.60' if c < 1.6 else '1.60-1.99' if c < 2 else '2.00-2.99' if c < 3 else '>=3.00'


def tabla(g, clave):
    r = g.groupby(clave).agg(n=('acierto', 'size'), acierto=('acierto', 'mean'), prob=('p', 'mean'),
                             ganancia=('ganancia', 'sum'), stake=('stake', 'sum')).reset_index()
    r['diferencia'] = r.acierto - r.prob
    r['rend'] = r.ganancia / r.stake.where(r.stake > 0)
    r['fiable'] = r.n >= N_MIN
    return r.sort_values('n', ascending=False)


def md(r, clave):
    filas = [f'| {clave} | n | acierto | prob. modelo | diferencia | ganancia (u) | yield | ¿muestra suficiente? |',
             '|---|---|---|---|---|---|---|---|']
    for x in r.itertuples():
        y = '' if pd.isna(x.rend) else f'{x.rend:+.0%}'
        filas.append(f'| {getattr(x, clave)} | {x.n} | {x.acierto:.0%} | {x.prob:.0%} | {x.diferencia:+.0%} | '
                     f'{x.ganancia:+.2f} | {y} | {"sí" if x.fiable else f"no (<{N_MIN})"} |')
    return '\n'.join(filas)


def lecciones(dias):
    desde = datetime.now().timestamp() - dias * 86400
    out = []
    # ligas/<slug>/jornadas/<fecha>/lecciones.md
    for f in sorted((DIR / 'ligas').glob('*/jornadas/*/lecciones.md'), key=lambda f: (f.parent.name, f.parts[-4])):
        if f.stat().st_mtime >= desde:
            out.append(f'### {f.parent.name} · {f.parts[-4]}\n\n' + f.read_text(encoding='utf-8').strip())
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--picks', required=True)
    ap.add_argument('--resoluciones', default=None)
    ap.add_argument('--dias', type=int, default=7)
    ap.add_argument('--salida', default=None, help='nota de salida (por defecto notas/revisiones/<fecha>.md)')
    a = ap.parse_args()
    P = []
    for f in Path(a.picks).glob('*.json'):
        if f.name == 'resoluciones.json':
            continue
        d = json.loads(f.read_text(encoding='utf-8'))
        P.append(dict(d.get('data', d), doc_id=d.get('id', f.stem)))
    P = pd.DataFrame(P)
    rf = Path(a.resoluciones) if a.resoluciones else Path(a.picks) / 'resoluciones.json'
    if rf.exists():
        R = pd.DataFrame(json.loads(rf.read_text(encoding='utf-8')))[['doc_id', 'estado', 'resultado']]
        P = P.drop(columns=[c for c in ('resultado',) if c in P]).merge(R, on='doc_id', how='left', suffixes=('', '_r'))
        P['estado'] = P.estado_r.fillna(P.estado)
    if 'resultado' not in P:      # bitacora sin ese campo y sin resoluciones.json: no reventar
        P['resultado'] = None
    P['tipo'] = [tipo(s, m) for s, m in zip(P.seleccion, P.mercado)]
    P['cuota'] = P.cuota.astype(float); P['stake'] = P.stake.fillna(0).astype(float)
    P['p'] = P.prob.astype(float) / 100
    L = P[P.estado.isin(['ganada', 'perdida'])].copy()
    L['acierto'] = (L.estado == 'ganada').astype(float)
    L['ganancia'] = [s * (c - 1) if e == 'ganada' else -s for s, c, e in zip(L.stake, L.cuota, L.estado)]
    L['tramo_cuota'] = L.cuota.map(tramo)
    L['grupo'] = L.stake.map(lambda s: 'apostada' if s > 0 else 'seguimiento (stake 0)')
    hoy = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    txt = [f'---\ntipo: revision\nfecha: {hoy}\n---', f'# Revisión semanal {hoy}', '',
           f'Apuestas en la bitácora: {len(P)} · resueltas: {len(L)} · nulas: {(P.estado == "nula").sum()} · '
           f'pendientes: {(P.estado == "pendiente").sum()} · a revisar: {(P.estado == "revisar").sum()}', '']
    if len(L):
        ap_ = L[L.stake > 0]
        if len(ap_):
            txt += [f'**Apostadas:** {len(ap_)} · acierto {ap_.acierto.mean():.0%} (modelo {ap_.p.mean():.0%}) · '
                    f'ganancia {ap_.ganancia.sum():+.2f}u · yield {ap_.ganancia.sum() / ap_.stake.sum():+.1%}', '']
        txt += ['## Por tipo de apuesta', md(tabla(L, 'tipo'), 'tipo'), '',
                '## Por liga', md(tabla(L.assign(liga=L.competicion.map(lambda x: f'[[{x}]]')), 'liga'), 'liga'), '',
                '## Por tramo de cuota', md(tabla(L, 'tramo_cuota'), 'tramo_cuota'), '',
                '## Apostadas frente a seguimiento', md(tabla(L, 'grupo'), 'grupo'), '']
        txt += ['## Detalle', '| fecha | partido | apuesta | cuota | prob | estado | resultado |', '|---|---|---|---|---|---|---|']
        for x in L.sort_values('fecha').itertuples():
            txt.append(f'| {x.fecha} | [[{x.local}]] - [[{x.visitante}]] | {x.seleccion} | {x.cuota:.2f} | {x.p:.0%} | {x.estado} | {x.resultado if isinstance(x.resultado, str) else ""} |')
        txt.append('')
    rv = P[P.estado == 'revisar']
    if len(rv):
        txt += ['## A revisar a mano'] + [f'- {x.doc_id}: {x.resultado}' for x in rv.itertuples()] + ['']
    lc = lecciones(a.dias)
    txt += ['## Lecciones de los agentes (últimos %d días)' % a.dias, ''] + (lc or ['(ninguna)']) + ['']
    txt += ['## Propuestas de cambio', '',
            f'Solo con muestra suficiente (n >= {N_MIN}) en el tipo afectado. Con menos, se apuntan aquí y se esperan.', '',
            '| propuesta | evidencia | decisión del usuario |', '|---|---|---|', '|  |  |  |', '']
    f = Path(a.salida) if a.salida else NOTAS / f'{hoy}.md'
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text('\n'.join(txt), encoding='utf-8')
    print('\n'.join(txt[:12]))
    print(f'\n-> {f.as_posix()}')


if __name__ == '__main__':
    main()

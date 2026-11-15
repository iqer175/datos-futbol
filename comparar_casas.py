#!/usr/bin/env python3
"""
comparar_casas.py -- busca la cuota más alta de cada apuesta candidata en otras casas.

La misma apuesta paga distinto según la casa; tomar la mejor cuota sube el valor sin cambiar el
modelo. Se miran solo las candidatas de la jornada (no todos los mercados):
las que en bet365 ya son limpias o lo serían con una cuota como mucho un 10 % más alta (--subida),
que es lo que otra casa puede llegar a pagar de más; más allá no merece la pena buscar.

Pasos (después de `analizar_jornada.py simular`):
  1. python comparar_casas.py candidatas --dir <carpeta> [--subida 0.10]
       -> <carpeta>/candidatas.json (lo que el recolector debe buscar en cada casa)
  2. recolector-cuotas en "modo comparación", una casa cada vez
       -> <carpeta>/casas_<casa>.json
  3. python comparar_casas.py evaluar --dir <carpeta>
       -> <carpeta>/comparacion_casas.json y .md (mejor casa de cada candidata y si queda limpia)

Remates de jugador: el Sustituto+ es de bet365 (si cambian al titular, cuentan los remates de quien
entra). En una casa sin esa regla cuentan solo los del jugador, así que la probabilidad es la de
`prob_sin_sustituto` (siempre menor). El recolector anota en cada cuota si la casa tiene la regla
(`"sustituto": true/false/null`); si no lo sabe (null) se usa la probabilidad sin Sustituto+.
Las reglas de apuesta limpia son las mismas que en analizar_jornada.py (cuota >= 1.50, margen >= 3 puntos,
sin avisos que la bloqueen).
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np, pandas as pd

import analizar_jornada as aj

MARGEN_MIN = 0.03          # el de reglas_aprendidas.md
CUOTA_MIN = 1.5
SUBIDA_MAX = 0.10          # cuota necesaria como mucho un 10 % por encima de la de bet365


def _k(local, visit, clave):
    """Clave de emparejamiento sin tildes ni mayúsculas (el recolector puede escribirlas distinto)."""
    return tuple(aj.nz(x).strip() for x in (local, visit, clave))


def _aviso_bloquea(aviso):
    return bool(pd.notna(aviso) and pd.Series([aviso]).str.contains(aj.NO_LIMPIA).iloc[0])


def candidatas(carpeta, subida):
    carpeta = Path(carpeta)
    T = pd.read_csv(carpeta / 'evaluacion.csv')
    necesaria = 1 / (T.prob - MARGEN_MIN).where(T.prob > MARGEN_MIN)      # cuota con la que sería limpia
    ok = (T.prob.notna() & (necesaria <= T.cuota * (1 + subida)) & (np.maximum(necesaria, T.cuota) >= CUOTA_MIN)
          & ~T.aviso.apply(_aviso_bloquea))
    C = T[ok].sort_values(['inicio_utc', 'partido', 'margen'], ascending=[True, True, False])
    partidos = []
    for (part, ini), g in C.groupby(['partido', 'inicio_utc'], sort=False):
        loc, vis = part.split(' - ', 1)
        sel = []
        for r in g.itertuples():
            d = dict(clave=r.clave, apuesta=r.apuesta, tipo=r.tipo, cuota_bet365=float(r.cuota),
                     prob=round(float(r.prob), 4), margen_bet365=round(float(r.margen), 4),
                     cuota_minima=round(1 / max(float(r.prob) - MARGEN_MIN, 1e-9), 2))
            if r.tipo == 'jugador':
                ps = getattr(r, 'prob_sin_sustituto', np.nan)
                d['prob_sin_sustituto'] = round(float(ps), 4) if pd.notna(ps) else None
                d['cuota_minima_sin_sustituto'] = (round(1 / max(float(ps) - MARGEN_MIN, 1e-9), 2)
                                                   if pd.notna(ps) else None)
            sel.append(d)
        partidos.append(dict(local=loc, visitante=vis, inicio_utc=ini, selecciones=sel))
    out = dict(carpeta=carpeta.as_posix(), creado_utc=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%MZ'),
               subida_maxima=subida, partidos=partidos)
    (carpeta / 'candidatas.json').write_text(json.dumps(out, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    n = sum(len(p['selecciones']) for p in partidos)
    print(f'{n} candidatas en {len(partidos)} partidos (limpias con una cuota hasta un {subida:.0%} mayor que bet365) '
          f'-> {(carpeta / "candidatas.json").as_posix()}')
    if 'prob_sin_sustituto' not in T.columns and (T.tipo == 'jugador').any():
        print('AVISO: evaluacion.csv no trae prob_sin_sustituto (vuelve a correr analizar_jornada.py simular); '
              'las de jugador solo valdrán en casas con Sustituto+.')
    for p in partidos:
        for s in p['selecciones']:
            print(f'  {p["local"]} - {p["visitante"]}: {s["apuesta"]} @ {s["cuota_bet365"]} '
                  f'(prob {s["prob"] * 100:.0f}%, hace falta >= {s["cuota_minima"]})')


def evaluar(carpeta):
    carpeta = Path(carpeta)
    cand = json.loads((carpeta / 'candidatas.json').read_text(encoding='utf-8'))
    lecturas = {}
    for f in sorted(carpeta.glob('casas_*.json')):
        d = json.loads(f.read_text(encoding='utf-8'))
        casa = d.get('casa') or f.stem.removeprefix('casas_')
        for c in d.get('cuotas', []):
            lecturas.setdefault(_k(c['local'], c['visitante'], c['clave']), []).append(dict(c, casa=casa, leido_utc=d.get('leido_utc', '')))
    if not lecturas:
        raise SystemExit(f'No hay casas_*.json en {carpeta.as_posix()}: falta el paso del recolector.')
    filas = []
    for p in cand['partidos']:
        for s in p['selecciones']:
            ofertas = [dict(casa='bet365', cuota=s['cuota_bet365'], prob=s['prob'], sustituto=True, nota='')]
            for c in lecturas.get(_k(p['local'], p['visitante'], s['clave']), []):
                if not c.get('cuota'):
                    continue
                pr = s['prob']
                if s['tipo'] == 'jugador' and c.get('sustituto') is not True:
                    pr = s.get('prob_sin_sustituto')
                if pr is None:
                    continue
                ofertas.append(dict(casa=c['casa'], cuota=float(c['cuota']), prob=pr, sustituto=c.get('sustituto'),
                                    nota=c.get('nota', '')))
            for o in ofertas:
                o['margen'] = o['prob'] - 1 / o['cuota']
                o['limpia'] = bool(o['cuota'] >= CUOTA_MIN and o['margen'] >= MARGEN_MIN)
            mejor = max(ofertas, key=lambda o: o['margen'])
            filas.append(dict(partido=f'{p["local"]} - {p["visitante"]}', inicio_utc=p['inicio_utc'], clave=s['clave'],
                              apuesta=s['apuesta'], tipo=s['tipo'], ofertas=ofertas, mejor_casa=mejor['casa'],
                              mejor_cuota=mejor['cuota'], prob=round(mejor['prob'], 4),
                              margen=round(mejor['margen'], 4), limpia=mejor['limpia'],
                              margen_bet365=round(ofertas[0]['margen'], 4)))
    casas = sorted({o['casa'] for f in filas for o in f['ofertas']} - {'bet365'})
    out = dict(carpeta=carpeta.as_posix(), evaluado_utc=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%MZ'),
               casas=casas, candidatas=filas)
    (carpeta / 'comparacion_casas.json').write_text(json.dumps(out, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')

    # informe
    L = [f'# Comparación de casas: {carpeta.parent.parent.name} {carpeta.name}', '',
         f'Casas leídas además de bet365: {", ".join(casas) or "ninguna"}. {len(filas)} candidatas.',
         'Margen = prob - 1/cuota; limpia si cuota >= 1.50 y margen >= 3 puntos. En remates de jugador, '
         'las casas sin Sustituto+ usan la probabilidad sin Sustituto+.', '']
    lim = [f for f in filas if f['limpia']]
    L.append(f'## Limpias con la mejor cuota ({len(lim)})')
    L.append('')
    if lim:
        L += ['| Partido | Apuesta | Mejor casa | Cuota | bet365 | Prob | Margen |', '|---|---|---|---|---|---|---|']
        for f in sorted(lim, key=lambda f: -f['margen']):
            L.append(f'| {f["partido"]} | {f["apuesta"]} | {f["mejor_casa"]} | {f["mejor_cuota"]:.2f} | '
                     f'{f["ofertas"][0]["cuota"]:.2f} | {f["prob"] * 100:.0f}% | {f["margen"] * 100:+.1f} |')
    else:
        L.append('Ninguna.')
    L += ['', '## Todas las candidatas', '',
          '| Partido | Apuesta | ' + ' | '.join(['bet365'] + casas) + ' | Mejor (margen) |',
          '|---|---|' + '---|' * (len(casas) + 2)]
    for f in filas:
        por = {o['casa']: o for o in f['ofertas']}
        celdas = []
        for c in ['bet365'] + casas:
            o = por.get(c)
            celdas.append(f'{o["cuota"]:.2f}' + ('' if o['sustituto'] is True or f['tipo'] != 'jugador' else '*')
                          if o else '—')
        L.append(f'| {f["partido"]} | {f["apuesta"]} | ' + ' | '.join(celdas)
                 + f' | {f["mejor_casa"]} ({f["margen"] * 100:+.1f}) |')
    L += ['', '`*` = cuota de jugador en casa sin Sustituto+ (o sin confirmar): se compara con la probabilidad '
          'sin Sustituto+. `—` = la casa no ofrece esa apuesta o no se encontró.']
    txt = '\n'.join(L) + '\n'
    (carpeta / 'comparacion_casas.md').write_text(txt, encoding='utf-8')
    print(txt)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest='cmd', required=True)
    c = sp.add_parser('candidatas'); c.add_argument('--dir', required=True)
    c.add_argument('--subida', type=float, default=SUBIDA_MAX)
    e = sp.add_parser('evaluar'); e.add_argument('--dir', required=True)
    a = ap.parse_args()
    candidatas(a.dir, a.subida) if a.cmd == 'candidatas' else evaluar(a.dir)


if __name__ == '__main__':
    main()

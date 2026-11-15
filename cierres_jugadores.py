#!/usr/bin/env python3
"""
cierres_jugadores.py -- cuotas de CIERRE de bet365 de los mercados de jugador (y del resto
de mercados de cuotas.json), para medir el CLV de las apuestas de remates.

football-data da el cierre de 1X2 y Mas/Menos, pero nadie guarda el de remates. La unica
forma es volver a leer bet365 poco antes del partido. El agente recolector-cuotas, en
"modo cierre", relee los partidos que estan a punto de empezar y guarda cada lectura en
    ligas/<slug>/jornadas/<fecha>/cierre_<AAAAMMDDTHHMM>.json
con el MISMO formato que cuotas.json (solo los partidos releidos). Nunca toca cuotas.json.
Desde 2026-11-16 tambien cuentan las lecturas ludoprops_*.json (cuotas_ludoprops.py cierre),
pero SOLO cuando se han validado contra el recolector y el usuario lo ha aprobado.
Una jornada dura varios dias: habra varias lecturas de cierre, una por tanda de partidos.

Los mercados "N o mas remates" no tienen el otro lado, asi que no se puede quitar el margen:
    CLV = cuota de apertura / cuota de cierre - 1
(positivo = la cuota bajo despues de leerla: el mercado se movio hacia esa apuesta).

Uso:
  python cierres_jugadores.py pendientes [--horas 3]
      Partidos con cuotas.json que empiezan en las proximas horas y aun no tienen lectura de
      cierre: lo que hay que pedirle al recolector en modo cierre.
  python cierres_jugadores.py consolidar
      Cruza cada cuotas.json con sus lecturas de cierre (la ultima anterior al inicio del
      partido) -> datos/cierres_jugadores.csv (se rehace entero cada vez) y
      resultados/cierres_jugadores.txt (movimiento medio, CLV de las recomendadas y si el
      margen del modelo anticipa hacia donde se mueve la cuota).
"""
import argparse
import json
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

DIR = Path(__file__).parent
MAX_ANTES = 6 * 60       # una lectura cuenta como cierre si es de las ultimas 6 horas antes del inicio


def nz(s):
    return unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode().lower()


def hora(s):
    return pd.to_datetime(s, utc=True, errors='coerce')


def lineas(P):
    """{clave: cuota} de un partido de cuotas.json: mercados de equipo y 'jugador|mercado|linea'."""
    out = {k: v for k, v in (P.get('mercados') or {}).items() if v}
    for j in P.get('jugadores') or []:
        if j.get('cuota'):
            out[f"{j['jugador']}|{j['mercado']}|{int(j['linea'])}"] = j['cuota']
    return out


def leer(f):
    try:
        return json.loads(f.read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        print(f'AVISO: no se puede leer {f}: {e}', file=sys.stderr)
        return {'partidos': []}


def cierres_de(carpeta):
    """{(local, visitante): (leido, partido)} con la ultima lectura de cierre valida de cada partido."""
    out = {}
    fs = sorted(carpeta.glob('cierre_*.json'))
    # lecturas de LudoProps (cuotas_ludoprops.py): solo si se validaron contra el recolector y el
    # usuario dio el visto bueno (datos/ludoprops_validacion.json)
    import cuotas_ludoprops
    if cuotas_ludoprops.usar_en_cierres():
        fs += sorted(carpeta.glob('ludoprops_*.json'))
    for f in fs:
        d = leer(f)
        for P in d.get('partidos', []):
            leido, ini = hora(P.get('leido_utc') or d.get('leido_utc')), hora(P.get('inicio_utc'))
            if pd.isna(leido):
                continue
            k = (P['local'], P['visitante'])
            if pd.notna(ini) and leido > ini:
                continue                                   # leido con el partido empezado: no es pre-partido
            if k not in out or leido > out[k][0]:
                out[k] = (leido, P)
    return out


def jornadas(raiz):
    for f in sorted(raiz.glob('*/jornadas/*/cuotas.json')):
        yield f.parent, leer(f)


# ---------------------------------------------------------------- pendientes
def pendientes(raiz, horas):
    ahora = datetime.now(timezone.utc); n = 0
    for carpeta, d in jornadas(raiz):
        hechos = cierres_de(carpeta)
        for P in d.get('partidos', []):
            ini = hora(P.get('inicio_utc'))
            if pd.isna(ini) or not (ahora < ini <= ahora + timedelta(hours=horas)):
                continue
            c = hechos.get((P['local'], P['visitante']))
            if c and (ini - c[0]) <= timedelta(minutes=MAX_ANTES):
                continue
            n += 1
            print(f"{d.get('liga', '?')} | {P['local']} - {P['visitante']} | empieza {P['inicio_utc']} "
                  f"(en {int((ini - ahora).total_seconds() // 60)} min) | {len(P.get('jugadores') or [])} lineas de jugador "
                  f"| carpeta {carpeta.relative_to(raiz.parent).as_posix()}")
    if not n:
        print(f'Ningun partido con cuotas empieza en las proximas {horas} h sin lectura de cierre.')
    return n


# ---------------------------------------------------------------- consolidar
def recomendadas(carpeta):
    """Claves 'jugador|mercado|linea' (nombre normalizado) de recomendaciones.json."""
    f = carpeta / 'recomendaciones.json'; out = set()
    if not f.exists():
        return out
    for x in leer(f) if isinstance(leer(f), list) else []:
        d = x.get('data', {})
        m = re.match(r'(.+?) - (\d+) o más remates( a puerta)?$', str(d.get('seleccion', '')))
        if m:
            out.add((d.get('local'), d.get('visitante'), nz(m.group(1)), 'a puerta' if m.group(3) else 'remates', int(m.group(2))))
    return out


def consolidar(raiz, salida_csv, salida_txt):
    filas = []
    for carpeta, d in jornadas(raiz):
        hechos = cierres_de(carpeta)
        ev = carpeta / 'evaluacion.csv'
        E = pd.read_csv(ev) if ev.exists() else pd.DataFrame()
        E = E.set_index(['partido', 'clave']) if 'clave' in E else None
        rec = recomendadas(carpeta)
        for P in d.get('partidos', []):
            k = (P['local'], P['visitante']); ap = lineas(P)
            c = hechos.get(k)
            ci = lineas(c[1]) if c else {}
            ini, l_ap = hora(P.get('inicio_utc')), hora(P.get('leido_utc') or d.get('leido_utc'))
            min_antes = (ini - c[0]).total_seconds() / 60 if c and pd.notna(ini) else np.nan
            valido = c is not None and (np.isnan(min_antes) or min_antes <= MAX_ANTES)
            for clave, o in ap.items():
                jug = clave.split('|') if clave.count('|') == 2 else None
                e = E.loc[(f'{k[0]} - {k[1]}', clave)] if E is not None and (f'{k[0]} - {k[1]}', clave) in E.index else None
                if isinstance(e, pd.DataFrame):
                    e = e.iloc[0]
                es_rec = bool(jug and any(r[0] == k[0] and r[1] == k[1] and r[3] == jug[1] and r[4] == int(jug[2])
                                          and (set(r[2].split()) <= set(nz(jug[0]).split()) or set(nz(jug[0]).split()) <= set(r[2].split()))
                                          for r in rec))
                oc = ci.get(clave) if valido else None
                filas.append(dict(liga=d.get('liga'), jornada=carpeta.name, local=k[0], visitante=k[1],
                                  inicio_utc=P.get('inicio_utc'), tipo='jugador' if jug else 'equipo', clave=clave,
                                  cuota_apertura=o, leido_apertura=l_ap, cuota_cierre=oc,
                                  leido_cierre=c[0] if valido else pd.NaT, min_antes=min_antes if valido else np.nan,
                                  clv=o / oc - 1 if oc else np.nan,
                                  prob=None if e is None else e.prob, margen=None if e is None else e.margen,
                                  limpia=None if e is None else e.limpia, recomendada=es_rec))
    T = pd.DataFrame(filas)
    salida_csv.parent.mkdir(parents=True, exist_ok=True); salida_txt.parent.mkdir(parents=True, exist_ok=True)
    T.to_csv(salida_csv, index=False)
    L = [f'Lineas con cuota de apertura: {len(T):,} ({(T.tipo == "jugador").sum():,} de jugador) en '
         f'{T[["liga", "local", "visitante", "jornada"]].drop_duplicates().shape[0] if len(T) else 0} partidos']
    C = T[T.cuota_cierre.notna()] if len(T) else T
    if not len(C):
        L.append('Todavia no hay ninguna lectura de cierre valida (ficheros cierre_*.json leidos antes del inicio).')
    else:
        L.append(f'Con cuota de cierre: {len(C):,} ({(C.tipo == "jugador").sum():,} de jugador) en '
                 f'{C[["liga", "local", "visitante", "jornada"]].drop_duplicates().shape[0]} partidos; '
                 f'lectura de cierre a una mediana de {C.min_antes.median():.0f} min del inicio')
        L.append('CLV = cuota de apertura / cuota de cierre - 1 (positivo = la cuota bajo tras leerla).')
        for nombre, x in (('jugadores, todas las lineas', C[C.tipo == 'jugador']),
                          ('jugadores, limpias del modelo', C[(C.tipo == 'jugador') & (C.limpia == True)]),   # noqa: E712
                          ('jugadores, recomendadas', C[(C.tipo == 'jugador') & C.recomendada]),
                          ('equipo, todas las lineas', C[C.tipo == 'equipo'])):
            if len(x):
                se = x.clv.std(ddof=1) / np.sqrt(len(x)) if len(x) > 1 else np.nan
                L.append(f'  {nombre:32s} n={len(x):5d}  CLV medio {x.clv.mean()*100:+6.2f} % '
                         f'[IC95 {((x.clv.mean()-1.96*se)*100):+.2f}, {((x.clv.mean()+1.96*se)*100):+.2f}]  '
                         f'bajan {((x.clv > 0).mean()*100):.0f} %, suben {((x.clv < 0).mean()*100):.0f} %')
        J = C[(C.tipo == 'jugador') & C.margen.notna()]
        if len(J) >= 30:
            L.append('¿El margen del modelo (prob - 1/cuota) anticipa el movimiento? Por tercios de margen:')
            J = J.assign(t=pd.qcut(J.margen.astype(float), 3, labels=['bajo', 'medio', 'alto'], duplicates='drop'))
            for t, x in J.groupby('t', observed=True):
                L.append(f'  margen {t:6s} ({x.margen.astype(float).mean()*100:+5.1f} pts) n={len(x):5d}  CLV medio {x.clv.mean()*100:+6.2f} %')
            L.append(f'  correlacion margen-CLV: {np.corrcoef(J.margen.astype(float), J.clv)[0, 1]:+.3f}')
        else:
            L.append('Menos de 30 lineas de jugador con margen del modelo y cierre: aun no se mide si el modelo anticipa el movimiento.')
    txt = '\n'.join(L); print(txt)
    salida_txt.write_text(txt + '\n', encoding='utf-8')
    return T


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('accion', choices=['pendientes', 'consolidar'])
    ap.add_argument('--horas', type=float, default=3)
    ap.add_argument('--raiz', default=str(DIR / 'ligas'), help='carpeta de ligas (para pruebas)')
    ap.add_argument('--salida', default=str(DIR), help='carpeta base de datos/ y resultados/ (para pruebas)')
    a = ap.parse_args()
    raiz, sal = Path(a.raiz), Path(a.salida)
    if a.accion == 'pendientes':
        pendientes(raiz, a.horas)
    else:
        consolidar(raiz, sal / 'datos' / 'cierres_jugadores.csv', sal / 'resultados' / 'cierres_jugadores.txt')


if __name__ == '__main__':
    main()

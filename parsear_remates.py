"""Pasa el texto de bet365 (get_page_text) de las tablas de remates de jugador a cuotas.json.

El recolector solo navega y guarda el texto tal cual en bruto/; este script hace la parte
delicada: casar cada cuota con su jugador.

get_page_text lee cada tabla por columnas: primero los jugadores (dorsal, nombre, remates de
los últimos 5 o "No disp."), luego "1+" con una cuota por jugador, "2+", ... Una casilla vacía
(candado o línea no ofrecida) desaparece sin dejar hueco. Por eso:
- Columna completa (una cuota por jugador): se casa por posición.
- Columna incompleta: bet365 solo ofrece la línea N a los jugadores cuya cuota no se sale de
  sus topes, es decir, a los de cuota más baja en la línea N-1 (líneas altas: faltan los de
  cuota más alta) o, en la 1+, a los de cuota más alta en la 2+ (faltan los que irían por
  debajo de 1.01). Se eligen así los jugadores con casilla y se les dan las cuotas en el orden
  de la tabla. Los empates se desempatan con las demás columnas; si aun así no se sabe a quién
  va una cuota, esa casilla no se copia (se avisa).
- El recolector puede fijar a mano los huecos de una columna en <fichero>.huecos.json:
  {"remates|4": ["Jugador sin casilla", ...], "a puerta|2": [...]}. Manda sobre la deducción.
Al final se comprueba que en cada jugador las cuotas suben con la línea; si no, la columna
se descarta.

Uso:
  python parsear_remates.py --cuotas <cuotas.json> --local X --visitante Y bruto/a.txt bruto/b.txt
  (--todas copia también las cuotas < 1.50, para el modo cierre; --ver solo enseña, no escribe;
   --solo "goles,asistencias,goles o asistencias" copia solo la tabla "Anotará o asistirá"
   de Populares: columnas Anotará, Asistirá y Anotará o asistirá)
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import re
import sys
from pathlib import Path

CABECERAS = {"Remates a puerta": "a puerta", "Remates": "remates",
             "Shots on Target": "a puerta", "Shots": "remates",
             # goleador (2026-11-15): tabla "Anotará o asistirá" de Populares (columna "Anotará" =
             # marca en cualquier momento, linea 1) o "Jugador - Anotará" de Crear apuesta
             "Anotará o asistirá": "goles", "Jugador - Anotará": "goles"}
# columnas con nombre en vez de "N+" (linea 1 de cada mercado): goleador, asistente (2026-11-16)
# y anotara o asistira; cada columna sale como una tabla de su mercado
COL_NOMBRE = {"Anotará": "goles", "Asistirá": "asistencias", "Anotará o asistirá": "goles o asistencias"}
RUIDO = {"CA", "SUSTITUTO+", "Encuentro", "Match", "No disp.", "Ver más", "Ver menos",
         "Mostrar más", "Mostrar menos", "Show more", "Show less"}
RE_LINEA = re.compile(r"(\d{1,2})\+")
RE_CUOTA = re.compile(r"\d+\.\d+")
RE_ENTERO = re.compile(r"\d+")
CUOTA_MIN = 1.50


def leer_tablas(texto: str) -> list[dict]:
    """Devuelve las tablas de jugadores del texto: mercado, equipo, jugadores y columnas."""
    L = [s.strip() for s in texto.splitlines()]
    tablas = []
    for i, s in enumerate(L):
        if not s.startswith(("Jugador", "Player")):
            continue
        # cabecera de la sección: la más cercana hacia arriba (en pocas líneas)
        mercado, equipo, j = None, None, i - 1
        while j >= 0 and i - j <= 6:
            if L[j] in CABECERAS:
                mercado = CABECERAS[L[j]]
                entre = [x for x in L[j + 1:i] if x and x not in RUIDO]
                equipo = entre[0] if entre else None
                break
            j -= 1
        if mercado is None:
            continue
        nombres, k = [], i + 1
        while k < len(L) and not RE_LINEA.fullmatch(L[k]) and not (mercado == "goles" and L[k] in COL_NOMBRE):
            x = L[k]
            if x in CABECERAS or x.startswith(("Jugador", "Player")):
                break  # otra sección sin columnas: esta tabla no tiene cuotas
            if x and not RE_ENTERO.fullmatch(x) and x not in RUIDO:
                nombres.append(x)
            k += 1
        cols, cur, porcol = {}, None, {}
        while k < len(L):
            x = L[k]
            m = RE_LINEA.fullmatch(x)
            if m:
                cur = int(m.group(1))
                cols[cur] = []
            elif mercado == "goles" and x in COL_NOMBRE:
                cur = 1
                cols = porcol.setdefault(COL_NOMBRE[x], {})
                cols[cur] = []
            elif cur is not None and RE_CUOTA.fullmatch(x):
                cols[cur].append(float(x))
            elif x:
                break
            k += 1
        for mc, cc in (porcol.items() if porcol else [(mercado, cols)]):
            if nombres and cc:
                tablas.append({"mercado": mc, "equipo": equipo, "jugadores": nombres, "cols": cc})
    return tablas


def _clave_orden(fila: dict, lineas: list[int]) -> tuple:
    return tuple(fila.get(l, math.inf) for l in lineas)


def casar(tabla: dict, huecos: dict | None = None) -> tuple[dict, list[str]]:
    """Casa las cuotas con los jugadores. Devuelve {(jugador, linea): cuota} y avisos."""
    nombres, cols = tabla["jugadores"], tabla["cols"]
    n = len(nombres)
    huecos = huecos or {}
    avisos, filas = [], [dict() for _ in range(n)]  # filas[i][linea] = cuota
    lineas = sorted(cols)
    pend = []
    for l in lineas:
        c = cols[l]
        clave = f"{tabla['mercado']}|{l}"
        if len(c) > n:
            avisos.append(f"{clave}: {len(c)} cuotas para {n} jugadores; columna descartada")
        elif clave in huecos:
            sin = set(huecos[clave])
            con = [i for i, nm in enumerate(nombres) if nm not in sin]
            if len(con) != len(c) or len(sin - set(nombres)):
                avisos.append(f"{clave}: los huecos anotados no cuadran ({len(con)} con casilla, "
                              f"{len(c)} cuotas); columna descartada")
            else:
                for i, v in zip(con, c):
                    filas[i][l] = v
        elif len(c) == n:
            for i, v in enumerate(c):
                filas[i][l] = v
        else:
            pend.append(l)
    # columnas incompletas: de la más cercana a una completa hacia fuera
    hechas = {l for l in lineas if l not in pend}
    while pend:
        avanzar = False
        for l in list(pend):
            ref = l - 1 if (l - 1) in hechas and l != 1 else (l + 1 if (l + 1) in hechas else None)
            if ref is None:
                continue
            pend.remove(l)
            hechas.add(l)
            avanzar = True
            c, k = cols[l], len(cols[l])
            # orden de "más probable" a "menos": cuota de referencia y, para desempatar, el resto
            otras = [ref] + [x for x in sorted(hechas) if x not in (l, ref)]
            cand = [i for i in range(n) if ref in filas[i]]
            if len(cand) < k:
                avisos.append(f"{tabla['mercado']}|{l}: no hay {k} jugadores con la línea {ref}; descartada")
                continue
            orden = sorted(cand, key=lambda i: _clave_orden(filas[i], otras))
            if l > ref:   # línea alta: la tienen los de cuota más baja en la anterior
                elegidos, frontera = orden[:k], orden[k - 1] if k else None
                resto = orden[k:]
            else:         # 1+: la tienen los de cuota más alta en la 2+
                elegidos, frontera = orden[len(orden) - k:], orden[len(orden) - k] if k else None
                resto = orden[:len(orden) - k]
            # empates en la frontera: probar todas las formas de repartir la cuota
            if k and resto:
                kf = _clave_orden(filas[frontera], otras)
                emp = [i for i in orden if _clave_orden(filas[i], otras) == kf]
                fijos = [i for i in elegidos if i not in emp]
                sitio = k - len(fijos)
                if sitio < len(emp):
                    asign = []
                    for comb in itertools.combinations(emp, sitio):
                        sel = sorted(fijos + list(comb))
                        asign.append(dict(zip(sel, c)))
                    seguras = {i: v for i, v in asign[0].items()
                               if all(a.get(i) == v for a in asign)}
                    dudosas = sorted({i for a in asign for i in a} - set(seguras))
                    for i, v in seguras.items():
                        filas[i][l] = v
                    if dudosas:
                        avisos.append(f"{tabla['mercado']}|{l}: empate, no se sabe a quién van las "
                                      f"casillas de {', '.join(nombres[i] for i in dudosas)}; no copiadas "
                                      f"(se pueden fijar en .huecos.json)")
                    continue
            for i, v in zip(sorted(elegidos), c):
                filas[i][l] = v
        if not avanzar:
            for l in pend:
                avisos.append(f"{tabla['mercado']}|{l}: sin columna completa al lado; descartada")
            break
    # comprobación: las cuotas suben con la línea
    malas = set()
    for i, f in enumerate(filas):
        ls = sorted(f)
        for a, b in zip(ls, ls[1:]):
            if f[b] < f[a]:
                malas.add(b)
                avisos.append(f"{tabla['mercado']}: {nombres[i]} {a}+ {f[a]} > {b}+ {f[b]}")
    for l in malas:
        avisos.append(f"{tabla['mercado']}|{l}: cuotas que bajan con la línea; columna descartada")
        for f in filas:
            f.pop(l, None)
    out = {(nombres[i], l): v for i, f in enumerate(filas) for l, v in f.items()}
    return out, avisos


def procesar(ficheros: list[Path], todas: bool, solo: set | None = None) -> tuple[list[dict], list[str]]:
    jugadores, avisos, vistos = [], [], {}
    for fp in ficheros:
        txt = fp.read_text(encoding="utf-8")
        hfp = fp.with_suffix(".huecos.json")
        huecos = json.loads(hfp.read_text(encoding="utf-8")) if hfp.exists() else {}
        tablas = [t for t in leer_tablas(txt) if not solo or t["mercado"] in solo]
        if not tablas:
            avisos.append(f"{fp.name}: no hay tablas de remates con cuotas")
        for t in tablas:
            res, av = casar(t, huecos)
            cuenta = {l: len(v) for l, v in sorted(t["cols"].items())}
            print(f"{fp.name}: {t['mercado']} ({t['equipo'] or 'los dos equipos'}): "
                  f"{len(t['jugadores'])} jugadores, cuotas por línea {cuenta}")
            avisos += [f"{fp.name}: {a}" for a in av]
            for (nm, l), v in res.items():
                if v < CUOTA_MIN and not todas:
                    continue
                k = (nm, t["mercado"], l)
                if k in vistos and vistos[k] != v:
                    avisos.append(f"{nm} {t['mercado']} {l}+ sale dos veces con cuotas distintas "
                                  f"({vistos[k]} y {v}); se queda la primera")
                    continue
                if k not in vistos:
                    vistos[k] = v
                    jugadores.append({"jugador": nm, "mercado": t["mercado"], "linea": l, "cuota": v})
    jugadores.sort(key=lambda j: (j["mercado"], j["jugador"], j["linea"]))
    return jugadores, avisos


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cuotas", required=True, help="cuotas.json (o cierre_*.json) a completar")
    ap.add_argument("--local", required=True)
    ap.add_argument("--visitante", required=True)
    ap.add_argument("--todas", action="store_true", help="copiar también las cuotas < 1.50 (modo cierre)")
    ap.add_argument("--ver", action="store_true", help="solo enseñar, no escribir")
    ap.add_argument("--solo", help="mercados a copiar separados por comas (remates,a puerta,goles); por defecto todos")
    ap.add_argument("ficheros", nargs="+", type=Path)
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    jugadores, avisos = procesar(a.ficheros, a.todas, set(a.solo.split(",")) if a.solo else None)
    for x in avisos:
        print("AVISO:", x)
    print(f"{len(jugadores)} cuotas de jugador "
          f"({sum(j['mercado'] == 'remates' for j in jugadores)} remates, "
          f"{sum(j['mercado'] == 'a puerta' for j in jugadores)} a puerta, "
          f"{sum(j['mercado'] == 'goles' for j in jugadores)} goleador, "
          f"{sum(j['mercado'] == 'asistencias' for j in jugadores)} asistente, "
          f"{sum(j['mercado'] == 'goles o asistencias' for j in jugadores)} anotará o asistirá)")
    if a.ver:
        return 0
    ruta = Path(a.cuotas)
    d = json.loads(ruta.read_text(encoding="utf-8"))
    p = next((p for p in d["partidos"] if p["local"] == a.local and p["visitante"] == a.visitante), None)
    if p is None:
        print(f"ERROR: {a.local} - {a.visitante} no está en {ruta}; añade antes el partido con sus mercados")
        return 1
    mercados = {j["mercado"] for j in jugadores}
    p["jugadores"] = [j for j in p.get("jugadores", []) if j["mercado"] not in mercados] + jugadores
    ruta.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Guardado en {ruta}: {a.local} - {a.visitante}, {len(p['jugadores'])} cuotas de jugador")
    return 0


if __name__ == "__main__":
    sys.exit(main())

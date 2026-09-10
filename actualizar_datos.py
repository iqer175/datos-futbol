#!/usr/bin/env python3
"""
Descarga, normaliza y consolida datos de football-data.co.uk.

Genera en datos/:
  partidos.csv   - todos los partidos normalizados, una fila por partido
  ratings.csv    - Elo actual + fuerza ataque/defensa por equipo
  resumen.json   - metadatos (fecha de actualizacion, conteos, cobertura)

Uso:
    python actualizar_datos.py                 # ultimas 6 temporadas
    python actualizar_datos.py --temporadas 10
    python actualizar_datos.py --sin-extra     # omite Brasil y Argentina
"""

import argparse
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

DIR_DATOS = Path(__file__).parent / "datos"
BASE_MAIN = "https://www.football-data.co.uk/mmz4281/{temp}/{div}.csv"
BASE_EXTRA = "https://www.football-data.co.uk/new/{pais}.csv"

# --- Las 10 ligas principales -------------------------------------------------
# codigo: (nombre, pais, nivel, elo_base)
# elo_base es un PRIOR de fuerza relativa entre ligas. Es lo unico que permite
# comparar equipos que nunca se enfrentan (necesario para Champions/Europa).
# Ajustalo con los resultados europeos reales conforme acumules muestra.
LIGAS = {
    "E0":  ("Premier League",   "Inglaterra", 1, 1650),
    "E1":  ("Championship",     "Inglaterra", 2, 1455),
    "SP1": ("LaLiga",           "España",     1, 1620),
    "SP2": ("LaLiga Hypermotion", "España",   2, 1430),
    "I1":  ("Serie A",          "Italia",     1, 1600),
    "I2":  ("Serie B",          "Italia",     2, 1415),
    "D1":  ("Bundesliga",       "Alemania",   1, 1600),
    "F1":  ("Ligue 1",          "Francia",    1, 1565),
    "N1":  ("Eredivisie",       "Holanda",    1, 1510),
    "P1":  ("Liga Portugal",    "Portugal",   1, 1525),
}

# --- Extra (Libertadores): menos columnas, sin estadisticas de partido --------
EXTRA = {
    "BRA": ("Brasileirao",       "Brasil",    1, 1500),
    "ARG": ("Primera Division",  "Argentina", 1, 1470),
}

COLS_SALIDA = [
    "liga", "pais", "nivel", "temporada", "fecha", "local", "visitante",
    "gl", "gv", "res", "gl_ht", "gv_ht",
    "tiros_l", "tiros_v", "tiros_p_l", "tiros_p_v",
    "corners_l", "corners_v", "amar_l", "amar_v", "rojas_l", "rojas_v",
    "arbitro",
    "b365_1", "b365_x", "b365_2",
    "b365c_1", "b365c_x", "b365c_2",
    "avgc_1", "avgc_x", "avgc_2",
    "b365c_over25", "b365c_under25",
]

MAPA_MAIN = {
    "Date": "fecha", "HomeTeam": "local", "AwayTeam": "visitante",
    "FTHG": "gl", "FTAG": "gv", "FTR": "res", "HTHG": "gl_ht", "HTAG": "gv_ht",
    "HS": "tiros_l", "AS": "tiros_v", "HST": "tiros_p_l", "AST": "tiros_p_v",
    "HC": "corners_l", "AC": "corners_v", "HY": "amar_l", "AY": "amar_v",
    "HR": "rojas_l", "AR": "rojas_v", "Referee": "arbitro",
    "B365H": "b365_1", "B365D": "b365_x", "B365A": "b365_2",
    "B365CH": "b365c_1", "B365CD": "b365c_x", "B365CA": "b365c_2",
    "AvgCH": "avgc_1", "AvgCD": "avgc_x", "AvgCA": "avgc_2",
    "B365C>2.5": "b365c_over25", "B365C<2.5": "b365c_under25",
}

MAPA_EXTRA = {
    "Date": "fecha", "Home": "local", "Away": "visitante",
    "HG": "gl", "AG": "gv", "Res": "res",
    "AvgCH": "avgc_1", "AvgCD": "avgc_x", "AvgCA": "avgc_2",
}


# ============================================================== descarga

def codigo_temporada(anio):
    """2026 -> '2627'"""
    return f"{str(anio)[-2:]}{(anio + 1) % 100:02d}"


def temporada_actual():
    """La temporada europea arranca en julio."""
    hoy = datetime.now(timezone.utc)
    return hoy.year if hoy.month >= 7 else hoy.year - 1


def leer_csv(contenido):
    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            df = pd.read_csv(io.BytesIO(contenido), encoding=enc,
                             on_bad_lines="skip")
            if len(df.columns) > 3:
                return df
        except Exception:
            continue
    return None


def bajar(url, sesion):
    try:
        r = sesion.get(url, timeout=45)
    except requests.RequestException as e:
        print(f"    fallo de red: {e}", file=sys.stderr)
        return None
    if r.status_code != 200 or len(r.content) < 200:
        return None
    return leer_csv(r.content)


# ============================================================== normalizado

def parsear_fechas(serie):
    f = pd.to_datetime(serie, format="%d/%m/%Y", errors="coerce", dayfirst=True)
    faltan = f.isna()
    if faltan.any():
        f2 = pd.to_datetime(serie[faltan], format="%d/%m/%y",
                            errors="coerce", dayfirst=True)
        f.loc[faltan] = f2
    faltan = f.isna()
    if faltan.any():
        f.loc[faltan] = pd.to_datetime(serie[faltan], errors="coerce",
                                       dayfirst=True)
    return f


def normalizar(df, mapa, liga, pais, nivel, temporada):
    df = df.rename(columns=mapa)
    for c in COLS_SALIDA:
        if c not in df.columns:
            df[c] = pd.NA
    df["liga"] = liga
    df["pais"] = pais
    df["nivel"] = nivel
    df["temporada"] = temporada
    df["fecha"] = parsear_fechas(df["fecha"])

    df = df.dropna(subset=["fecha", "local", "visitante", "gl", "gv"])
    df = df[df["local"].astype(str).str.strip() != ""]

    for c in ["gl", "gv", "gl_ht", "gv_ht", "tiros_l", "tiros_v",
              "tiros_p_l", "tiros_p_v", "corners_l", "corners_v",
              "amar_l", "amar_v", "rojas_l", "rojas_v"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
    for c in COLS_SALIDA:
        if c.startswith(("b365", "avgc")):
            df[c] = pd.to_numeric(df[c], errors="coerce")

    for c in ["local", "visitante", "arbitro"]:
        df[c] = df[c].astype(str).str.strip().replace("nan", pd.NA)

    return df[COLS_SALIDA]


def descargar_todo(n_temporadas, incluir_extra):
    sesion = requests.Session()
    sesion.headers.update({"User-Agent": "Mozilla/5.0 (actualizador-futbol)"})

    actual = temporada_actual()
    anios = list(range(actual - n_temporadas + 1, actual + 1))
    trozos = []

    print(f"Temporadas: {anios[0]}/{str(anios[0]+1)[-2:]} "
          f"a {anios[-1]}/{str(anios[-1]+1)[-2:]}\n")

    for div, (nombre, pais, nivel, _) in LIGAS.items():
        filas = 0
        for anio in anios:
            temp = codigo_temporada(anio)
            df = bajar(BASE_MAIN.format(temp=temp, div=div), sesion)
            if df is None:
                continue
            norm = normalizar(df, MAPA_MAIN, nombre, pais, nivel,
                              f"{anio}-{str(anio+1)[-2:]}")
            if len(norm):
                trozos.append(norm)
                filas += len(norm)
        estado = "ok " if filas else "SIN DATOS"
        print(f"  {estado} {div:4s} {nombre:22s} {filas:6d} partidos")

    if incluir_extra:
        print()
        for pais_cod, (nombre, pais, nivel, _) in EXTRA.items():
            df = bajar(BASE_EXTRA.format(pais=pais_cod), sesion)
            if df is None:
                print(f"  SIN DATOS {pais_cod}")
                continue
            df["temporada_src"] = df.get("Season", pd.NA)
            norm = normalizar(df, MAPA_EXTRA, nombre, pais, nivel, "varias")
            corte = pd.Timestamp.now() - pd.DateOffset(years=n_temporadas)
            norm = norm[norm["fecha"] >= corte]
            norm["temporada"] = norm["fecha"].dt.year.astype(str)
            trozos.append(norm)
            print(f"  ok  {pais_cod:4s} {nombre:22s} {len(norm):6d} partidos")

    if not trozos:
        raise SystemExit("No se descargo ningun dato. Revisa la conexion.")

    todo = pd.concat(trozos, ignore_index=True)
    todo = todo.sort_values("fecha").reset_index(drop=True)
    todo = todo.drop_duplicates(subset=["fecha", "local", "visitante"],
                                keep="last")
    return todo


# ============================================================== elo

K_BASE = 20
VENTAJA_LOCAL = 65
REGRESION_TEMPORADA = 0.25  # cuanto vuelve al nivel de liga cada verano


def calcular_elo(df):
    bases = {n: b for (n, _, _, b) in
             list(LIGAS.values()) + list(EXTRA.values())}
    elo = {}
    liga_equipo = {}
    ultima_temp = {}
    historial = []

    for fila in df.itertuples(index=False):
        base = bases.get(fila.liga, 1500)

        for equipo in (fila.local, fila.visitante):
            if equipo not in elo:
                elo[equipo] = base
            liga_previa = liga_equipo.get(equipo)
            # cambio de temporada o de division: regresion al nivel de la liga
            if ultima_temp.get(equipo) not in (None, fila.temporada):
                objetivo = bases.get(fila.liga, 1500)
                elo[equipo] += (objetivo - elo[equipo]) * REGRESION_TEMPORADA
            elif liga_previa and liga_previa != fila.liga:
                objetivo = bases.get(fila.liga, 1500)
                elo[equipo] += (objetivo - elo[equipo]) * REGRESION_TEMPORADA
            liga_equipo[equipo] = fila.liga
            ultima_temp[equipo] = fila.temporada

        el, ev = elo[fila.local], elo[fila.visitante]
        esperado_l = 1 / (1 + 10 ** ((ev - el - VENTAJA_LOCAL) / 400))

        gl, gv = int(fila.gl), int(fila.gv)
        real_l = 1.0 if gl > gv else (0.5 if gl == gv else 0.0)

        dif = abs(gl - gv)
        mult = 1.0 if dif <= 1 else (1.5 if dif == 2 else (2 + (dif - 2) / 8))
        k = K_BASE * mult

        cambio = k * (real_l - esperado_l)
        elo[fila.local] = el + cambio
        elo[fila.visitante] = ev - cambio

        historial.append({
            "fecha": fila.fecha, "liga": fila.liga,
            "local": fila.local, "visitante": fila.visitante,
            "elo_l_pre": round(el, 1), "elo_v_pre": round(ev, 1),
            "prob_elo_l": round(esperado_l, 4),
        })

    return elo, liga_equipo, pd.DataFrame(historial)


def tabla_ratings(df, elo, liga_equipo, ventana=20):
    """Elo actual + ataque/defensa de los ultimos N partidos, casa y fuera."""
    filas = []
    equipos = sorted(elo.keys())

    for eq in equipos:
        en_casa = df[df["local"] == eq].tail(ventana)
        fuera = df[df["visitante"] == eq].tail(ventana)
        jugados = pd.concat([en_casa, fuera]).sort_values("fecha").tail(ventana)
        if len(jugados) < 5:
            continue

        gf_casa = en_casa["gl"].astype(float).mean() if len(en_casa) else None
        gc_casa = en_casa["gv"].astype(float).mean() if len(en_casa) else None
        gf_fuera = fuera["gv"].astype(float).mean() if len(fuera) else None
        gc_fuera = fuera["gl"].astype(float).mean() if len(fuera) else None

        tp_favor = pd.concat([
            en_casa["tiros_p_l"].astype("Float64"),
            fuera["tiros_p_v"].astype("Float64")]).mean()
        tp_contra = pd.concat([
            en_casa["tiros_p_v"].astype("Float64"),
            fuera["tiros_p_l"].astype("Float64")]).mean()

        ultimo = jugados["fecha"].max()

        filas.append({
            "equipo": eq,
            "liga": liga_equipo.get(eq),
            "elo": round(elo[eq], 1),
            "partidos_muestra": len(jugados),
            "gf_casa": round(gf_casa, 2) if gf_casa is not None else None,
            "gc_casa": round(gc_casa, 2) if gc_casa is not None else None,
            "gf_fuera": round(gf_fuera, 2) if gf_fuera is not None else None,
            "gc_fuera": round(gc_fuera, 2) if gc_fuera is not None else None,
            "tiros_puerta_favor": (round(float(tp_favor), 2)
                                   if pd.notna(tp_favor) else None),
            "tiros_puerta_contra": (round(float(tp_contra), 2)
                                    if pd.notna(tp_contra) else None),
            "ultimo_partido": ultimo.date().isoformat(),
        })

    r = pd.DataFrame(filas).sort_values("elo", ascending=False)
    # solo equipos con actividad en los ultimos 15 meses
    corte = (pd.Timestamp.now() - pd.DateOffset(months=15)).date().isoformat()
    return r[r["ultimo_partido"] >= corte].reset_index(drop=True)


# ============================================================== main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--temporadas", type=int, default=6)
    ap.add_argument("--sin-extra", action="store_true")
    args = ap.parse_args()

    DIR_DATOS.mkdir(exist_ok=True)

    print("=" * 60)
    print("ACTUALIZANDO DATOS DE FUTBOL")
    print("=" * 60 + "\n")

    partidos = descargar_todo(args.temporadas, not args.sin_extra)
    print(f"\nTotal consolidado: {len(partidos):,} partidos")

    elo, liga_equipo, historial = calcular_elo(partidos)
    ratings = tabla_ratings(partidos, elo, liga_equipo)
    print(f"Ratings calculados: {len(ratings)} equipos activos")

    partidos_out = partidos.copy()
    partidos_out["fecha"] = partidos_out["fecha"].dt.strftime("%Y-%m-%d")
    partidos_out = partidos_out.merge(
        historial[["fecha", "local", "visitante", "elo_l_pre",
                   "elo_v_pre", "prob_elo_l"]].assign(
            fecha=lambda d: d["fecha"].dt.strftime("%Y-%m-%d")),
        on=["fecha", "local", "visitante"], how="left")

    partidos_out.to_csv(DIR_DATOS / "partidos.csv", index=False)
    ratings.to_csv(DIR_DATOS / "ratings.csv", index=False)

    resumen = {
        "actualizado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "partidos_totales": int(len(partidos)),
        "equipos_activos": int(len(ratings)),
        "temporadas_descargadas": args.temporadas,
        "ultimo_partido": partidos["fecha"].max().date().isoformat(),
        "cobertura": (partidos.groupby("liga")
                      .agg(partidos=("gl", "size"),
                           ultimo=("fecha", "max"))
                      .assign(ultimo=lambda d: d["ultimo"].dt.strftime("%Y-%m-%d"))
                      .to_dict("index")),
        "aviso_pinnacle": ("Desde 23/07/2025 las cuotas de Pinnacle en la fuente "
                           "son poco fiables y ya no entran en la media de "
                           "mercado. Usa avgc_* como referencia de cierre."),
    }
    (DIR_DATOS / "resumen.json").write_text(
        json.dumps(resumen, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nEscrito en {DIR_DATOS}/")
    print(f"  partidos.csv   {len(partidos_out):,} filas")
    print(f"  ratings.csv    {len(ratings)} filas")
    print("  resumen.json")
    print("\nTop 15 Elo:")
    print(ratings.head(15)[["equipo", "liga", "elo"]].to_string(index=False))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
alineaciones.py -- aviso de titulares habituales que no juegan.

Se ejecuta cada 20 minutos en dias de partido (workflow alineaciones.yml).
Para cada partido de las ligas vigiladas que empieza en las proximas 3 horas:
  1. pide la alineacion a ESPN (se publica ~1 h antes; si no esta, espera)
  2. calcula los titulares habituales de cada equipo: jugadores que salieron
     de inicio en al menos 4 de sus ultimos 5 partidos (datos/alineaciones/titulares.csv)
  3. registra los habituales que NO salen de inicio y los titulares nuevos

Cada partido revisado queda en datos/alineaciones/alertas.csv, tenga o no
ausencias: los partidos sin ausencias son el grupo de control para medir
despues si las alertas anticipan el movimiento de la cuota (CLV).

ESTO NO ES UNA SENAL DE APUESTA DEMOSTRADA. Es la hipotesis a medir: si falta
un titular importante y la cuota de bet365 aun no se ha movido, puede haber valor.

Uso:  python alineaciones.py [--ligas LaLiga] [--horas 3]
Salida: datos/alineaciones/alertas.csv y informes/alertas_alineaciones.md
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

import espn

DIR = Path(__file__).parent
ALERTAS = espn.DIR_AL / 'alertas.csv'
INFORME = DIR / 'informes' / 'alertas_alineaciones.md'
N_ULTIMOS, MIN_TITULAR = 5, 4


def habituales(tit, equipo_id, antes):
    """Jugadores con >= MIN_TITULAR titularidades en los ultimos N_ULTIMOS partidos."""
    t = tit[(tit.equipo_id == str(equipo_id)) & (tit.fecha < antes)]
    ult = t.drop_duplicates('espn_id').sort_values('fecha').espn_id.tail(N_ULTIMOS)
    t = t[t.espn_id.isin(ult)]
    if ult.nunique() < 3:
        return {}, ult.nunique()
    minimo = MIN_TITULAR if ult.nunique() >= N_ULTIMOS else ult.nunique() - 1
    c = t.groupby(['jugador_id', 'jugador', 'posicion']).size().reset_index(name='n')
    c = c.groupby('jugador_id').agg(jugador=('jugador', 'first'), posicion=('posicion', 'last'), n=('n', 'sum'))
    h = c[c.n >= minimo]
    return {i: (r.jugador, r.posicion, int(r.n)) for i, r in h.iterrows()}, ult.nunique()


def nombres_fd(cal):
    try:
        m = pd.read_csv(DIR / 'datos' / 'mapa_nombres.csv')
        k = {(r.liga, r.nombre_src): r.nombre_fd for r in m[m.fuente == 'espn'].itertuples()}
    except OSError:
        k = {}
    cal = cal.copy()
    cal['local'] = [k.get((l, n), n) for l, n in zip(cal.liga, cal.local_src)]
    cal['visitante'] = [k.get((l, n), n) for l, n in zip(cal.liga, cal.visitante_src)]
    return cal


def escribir_informe(al):
    INFORME.parent.mkdir(exist_ok=True)
    L = ['# Alertas de alineaciones', '',
         f'Actualizado {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")} UTC. Habitual = titular en '
         f'{MIN_TITULAR} de sus últimos {N_ULTIMOS} partidos de liga.', '',
         '> No es una señal de apuesta demostrada. Si falta alguien importante, mira si la cuota de '
         'bet365 ya se ha movido; si apuestas, apúntalo en `bitacora.csv` para medir el CLV.', '']
    con = al[al.n_ausentes.astype(int) > 0].sort_values('detectado_utc', ascending=False).head(30)
    if not len(con):
        L.append('Sin ausencias de habituales en los partidos revisados.')
    for r in con.itertuples():
        L.append(f'- **{r.local} - {r.visitante}** ({r.liga}, {str(r.inicio_utc)[:16].replace("T", " ")} UTC) — '
                 f'{r.equipo}: faltan **{r.ausentes}**' + (f'; entran {r.novedades}' if isinstance(r.novedades, str) and r.novedades else '')
                 + (' — **PORTERO**' if r.portero_ausente == 'True' else ''))
    INFORME.write_text('\n'.join(L) + '\n', encoding='utf-8')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ligas', default='LaLiga')
    ap.add_argument('--horas', type=float, default=3.0)
    a = ap.parse_args()
    ligas = a.ligas.split(',')
    ahora = datetime.now(timezone.utc)
    previo = pd.read_csv(ALERTAS, dtype=str) if ALERTAS.exists() else pd.DataFrame(columns=['espn_id'])
    revisados = set(previo.espn_id)
    cal = espn.calendario(ligas, ahora - timedelta(hours=3), 1)
    if not len(cal):
        print('Ningun partido de estas ligas hoy o manana (o ESPN no responde).'); return
    cal['inicio'] = pd.to_datetime(cal.inicio_utc, utc=True)
    ventana = cal[(cal.inicio > ahora - timedelta(minutes=30)) & (cal.inicio <= ahora + timedelta(hours=a.horas))
                  & ~cal.espn_id.isin(revisados)]
    ventana = nombres_fd(ventana)
    if not len(ventana):
        print('Ningun partido pendiente en la ventana.'); return
    tit = pd.read_csv(espn.TITULARES, dtype=str) if espn.TITULARES.exists() else pd.DataFrame(
        columns=['espn_id', 'equipo_id', 'jugador_id', 'jugador', 'posicion', 'fecha'])
    filas = []
    for x in ventana.itertuples():
        xi = pd.DataFrame(espn.alineacion(x.codigo, x.espn_id))
        if not len(xi):
            print(f'{x.local} - {x.visitante}: alineacion aun no publicada'); continue
        for lado, eq_id in (('home', x.local_id), ('away', x.visitante_id)):
            once = xi[xi.equipo_id == str(eq_id)]
            hab, n_part = habituales(tit, eq_id, x.inicio_utc[:10])
            ids = set(once.jugador_id)
            aus = [(v[0], v[1]) for k, v in hab.items() if k not in ids]
            nov = [r.jugador for r in once.itertuples()
                   if r.jugador_id not in hab and not len(tit[(tit.equipo_id == str(eq_id)) & (tit.jugador_id == r.jugador_id)])]
            filas.append(dict(
                detectado_utc=ahora.isoformat(timespec='minutes'), liga=x.liga, espn_id=x.espn_id,
                inicio_utc=x.inicio_utc, local=x.local, visitante=x.visitante,
                equipo=x.local if lado == 'home' else x.visitante, lado=lado,
                partidos_historial=n_part, n_habituales=len(hab), n_ausentes=len(aus),
                ausentes='; '.join(f'{n} ({p})' for n, p in aus), novedades='; '.join(nov),
                portero_ausente=any(p == 'G' for _, p in aus)))
            print(f'{x.local} - {x.visitante} [{lado}] ausentes: {[n for n, _ in aus] or "ninguno"}')
    if filas:
        espn.DIR_AL.mkdir(parents=True, exist_ok=True)
        al = pd.concat([previo, pd.DataFrame(filas).astype(str)])
        al.to_csv(ALERTAS, index=False)
        escribir_informe(al)


if __name__ == '__main__':
    main()

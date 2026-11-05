#!/usr/bin/env python3
"""
alineaciones.py -- aviso de titulares habituales que no juegan.

Se ejecuta cada 20 minutos en dias de partido (workflow alineaciones.yml).
Para cada partido de las ligas vigiladas que empieza en las proximas 3 horas:
  1. pide la alineacion a ESPN (se publica ~1 h antes; si no esta, espera)
  2. calcula los titulares habituales de cada equipo: jugadores que salieron
     de inicio en al menos 4 de sus ultimos 5 partidos (datos/alineaciones/titulares.csv)
  3. registra los habituales que NO salen de inicio y los titulares nuevos

Cada ausencia lleva una gravedad (ALTA/MEDIA/BAJA) segun el peso del jugador
en los tiros y en los goles+asistencias de su equipo (datos/alineaciones/jugadores.csv).

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


N_PESO = 10                 # partidos para medir el peso ofensivo (mas estable que 5)
UMBRAL_ALTA, UMBRAL_MEDIA = 0.18, 0.08
ORDEN = {'ALTA': 0, 'MEDIA': 1, 'BAJA': 2}


def _num(s):
    return pd.to_numeric(s, errors='coerce').fillna(0)


def habituales(hist, equipo_id, antes):
    """Habituales del equipo antes de la fecha 'antes', con su peso ofensivo.
    Habitual: titular en >= MIN_TITULAR de los ultimos N_ULTIMOS partidos.
    Peso: media de su parte en los tiros y en los goles+asistencias del equipo
    en los ultimos N_PESO partidos (no mide bien a defensas ni porteros)."""
    t = hist[(hist.equipo_id == str(equipo_id)) & (hist.fecha < antes)]
    orden = t.drop_duplicates('espn_id').sort_values('fecha').espn_id
    ult, ult_peso = orden.tail(N_ULTIMOS), orden.tail(N_PESO)
    if ult.nunique() < 3:
        return {}, ult.nunique()
    minimo = MIN_TITULAR if ult.nunique() >= N_ULTIMOS else ult.nunique() - 1
    tit = t[t.espn_id.isin(ult) & (t.titular == 'True')]
    n_tit = tit.groupby('jugador_id').size()
    w = t[t.espn_id.isin(ult_peso)].copy()
    for c in ('tiros', 'goles', 'asistencias'):
        w[c] = _num(w[c])
    tot_tiros, tot_goles = w.tiros.sum(), w.goles.sum()
    por = w.groupby('jugador_id').agg(tiros=('tiros', 'sum'), ga=('goles', 'sum'), asis=('asistencias', 'sum'))
    # posicion mas habitual sin contar 'SUB' (la de suplente no dice donde juega)
    pos_hab = (t[t.posicion.notna() & (t.posicion != 'SUB')].groupby('jugador_id').posicion
               .agg(lambda x: x.value_counts().index[0]))
    info = t.sort_values('fecha').groupby('jugador_id').agg(jugador=('jugador', 'last'), posicion=('posicion', 'last'))
    info['posicion'] = pos_hab.reindex(info.index).fillna(info.posicion)
    out = {}
    for jid, n in n_tit[n_tit >= minimo].items():
        s_t = por.tiros.get(jid, 0) / tot_tiros if tot_tiros else 0.0
        s_ga = (por.ga.get(jid, 0) + por.asis.get(jid, 0)) / tot_goles if tot_goles else 0.0
        peso = 0.5 * s_t + 0.5 * min(s_ga, 1.0)
        pos = info.posicion.get(jid)
        grav = ('ALTA' if pos == 'G' or peso >= UMBRAL_ALTA else
                'MEDIA' if peso >= UMBRAL_MEDIA else 'BAJA')
        out[jid] = dict(jugador=info.jugador.get(jid), posicion=pos, tit=int(n), de=ult.nunique(),
                        s_tiros=s_t, s_ga=s_ga, peso=peso, gravedad=grav)
    return out, ult.nunique()


def evaluar_equipo(hist, equipo_id, antes, once):
    """Compara el once (DataFrame de titulares) con los habituales del equipo."""
    hab, n_part = habituales(hist, equipo_id, antes)
    ids = set(once.jugador_id)
    aus = sorted((v for k, v in hab.items() if k not in ids), key=lambda v: (ORDEN[v['gravedad']], -v['peso']))
    vistos = set(hist[hist.equipo_id == str(equipo_id)].jugador_id)
    nov = [r.jugador for r in once.itertuples() if r.jugador_id not in vistos]
    txt = '; '.join(
        f"{v['jugador']} ({v['posicion']}, titular {v['tit']}/{v['de']}"
        + ('' if v['posicion'] == 'G' else f", {v['s_tiros']*100:.0f} % tiros, {v['s_ga']*100:.0f} % G+A")
        + f") [{v['gravedad']}]" for v in aus)
    return dict(partidos_historial=n_part, n_habituales=len(hab), n_ausentes=len(aus), ausentes=txt,
                gravedad_max=aus[0]['gravedad'] if aus else '', peso_ausente=round(sum(v['peso'] for v in aus), 3),
                novedades='; '.join(nov), portero_ausente=any(v['posicion'] == 'G' for v in aus))


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
    con = al[al.n_ausentes.astype(int) > 0].copy()
    con['_o'] = con.gravedad_max.map(ORDEN).fillna(3)
    con = con.sort_values(['inicio_utc', '_o'], ascending=[False, True]).head(40)
    if not len(con):
        L.append('Sin ausencias de habituales en los partidos revisados.')
    L += ['Gravedad: ALTA = portero o peso ofensivo >= 18 % (media de su parte en tiros y en goles+asistencias '
          f'del equipo en sus últimos {N_PESO} partidos); MEDIA >= 8 %; BAJA el resto. Los defensas salen casi '
          'siempre BAJA porque su importancia no se ve en tiros ni goles.', '']
    for r in con.itertuples():
        L.append(f'- **[{r.gravedad_max}] {r.local} - {r.visitante}** ({r.liga}, '
                 f'{str(r.inicio_utc)[:16].replace("T", " ")} UTC) — {r.equipo}: faltan {r.ausentes}'
                 + (f'; debutan con el equipo: {r.novedades}' if isinstance(r.novedades, str) and r.novedades else ''))
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
    tit = pd.read_csv(espn.JUGADORES, dtype=str) if espn.JUGADORES.exists() else pd.DataFrame(
        columns=['espn_id', 'equipo_id', 'jugador_id', 'jugador', 'posicion', 'fecha', 'titular',
                 'tiros', 'goles', 'asistencias'])
    filas = []
    for x in ventana.itertuples():
        xi = pd.DataFrame(espn.alineacion(x.codigo, x.espn_id))
        if not len(xi):
            print(f'{x.local} - {x.visitante}: alineacion aun no publicada'); continue
        for lado, eq_id in (('home', x.local_id), ('away', x.visitante_id)):
            once = xi[xi.equipo_id == str(eq_id)]
            ev = evaluar_equipo(tit, eq_id, x.inicio_utc[:10], once)
            filas.append(dict(
                detectado_utc=ahora.isoformat(timespec='minutes'), liga=x.liga, espn_id=x.espn_id,
                inicio_utc=x.inicio_utc, local=x.local, visitante=x.visitante,
                equipo=x.local if lado == 'home' else x.visitante, lado=lado, **ev))
            print(f'{x.local} - {x.visitante} [{lado}] ausentes: {ev["ausentes"] or "ninguno"}')
    if filas:
        espn.DIR_AL.mkdir(parents=True, exist_ok=True)
        al = pd.concat([previo, pd.DataFrame(filas).astype(str)])
        al.to_csv(ALERTAS, index=False)
        escribir_informe(al)


if __name__ == '__main__':
    main()

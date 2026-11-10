#!/usr/bin/env python3
"""
analizar_jornada.py -- simula una jornada con las cuotas de bet365 y el contexto de los partidos.

Es la pieza comun de los tres agentes (.claude/agents/), que sirven para cualquiera de
las 18 ligas activas (ligas.py). Cada liga tiene su carpeta ligas/<slug>/ con su ficha y
sus jornadas:
  recolector-cuotas  (Haiku)  -> ligas/<slug>/jornadas/<fecha>/cuotas.json
  este script, 'contexto'     -> contexto.json con todo lo que sale de los datos propios
                                 (motor, forma, H2H, historial de cada jugador con cuota)
  analista-contexto  (Haiku)  -> completa contexto.json con las noticias (bajas, sanciones,
                                 rotaciones) y ajusta titular_prob
  simulador-apuestas (Opus)   -> ejecuta 'simular' y escribe recomendaciones.json/.md

Uso:
  python analizar_jornada.py calendario --liga "Ligue 1" [--dias 7]
      Partidos de los proximos dias con los nombres de football-data (los que hay
      que usar en cuotas.json y contexto.json), la carpeta de la jornada y la ficha.
  python analizar_jornada.py contexto --dir ligas/ligue-1/jornadas/2026-11-07
      Escribe contexto.json a partir de cuotas.json y de los datos del repo. No busca
      noticias: deja "noticias": "pendiente" para el analista. No pisa un contexto.json
      que ya tenga noticias salvo con --forzar.
  python analizar_jornada.py simular --dir ligas/ligue-1/jornadas/2026-11-07
      Simula cada partido de cuotas.json (200.000 veces) y escribe evaluacion.csv.

Formato de cuotas.json:
  {"liga": "Ligue 1", "partidos": [
     {"local": "Toulouse", "visitante": "Strasbourg", "inicio_utc": "2026-11-06T19:45Z",
      "leido_utc": "2026-11-05T22:16Z",
      "mercados": {"1": 2.05, "X": 3.80, "Más 2.5": 1.61, "1 & Ambos marcan - Sí": 3.50,
                   "R:goles 2-4": 1.53, ...},
      "jugadores": [{"jugador": "Casper Tengstedt", "mercado": "remates", "linea": 3, "cuota": 1.50},
                    {"jugador": "Casper Tengstedt", "mercado": "a puerta", "linea": 2, "cuota": 2.25}]}]}
  Los nombres de mercado son los del catalogo de simulador.py mas los rangos 'R:...'
  (RANGOS). 'linea' es el minimo: 3 = "3 o mas".

Formato de contexto.json (opcional; si falta, ningun jugador queda marcado por titularidad):
  {"partidos": [{"local": "...", "visitante": "...",
     "titular_prob": {"Casper Tengstedt": 0.9},      # 0-1, por nombre tal como viene en cuotas.json
     "bajas_local": ["..."], "bajas_visitante": ["..."],
     "alertas": ["..."], "resumen": "..."}]}
"""
import argparse
import json
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import nbinom

DIR = Path(__file__).parent
sys.path.insert(0, str(DIR))

RANGOS = {'R:goles 2-4': ('goles', 2, 4), 'R:corners 6-8': ('corners', 6, 8),
          'R:corners 9-11': ('corners', 9, 11), 'R:corners 12-14': ('corners', 12, 14)}
MENOS_GOLES = ('Menos', 'Ambos marcan - No', 'R:goles 2-4')
W_MODELO = 0.35          # contraccion hacia el mercado (skill pronostico-futbol)
MUESTRA_MIN = 6          # titularidades minimas para fiarse de un jugador
P_TITULAR_MIN = 0.8      # por debajo, la apuesta de jugador queda marcada
NO_LIMPIA = 'sesgo|1X2|CORTA|sin datos|ambiguo|dudosa|sin emparejar|sin estad'


def nz(s):
    return unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode().lower()


def slug(s):
    return nz(s).replace('.', '').replace(' ', '-')


def potencia(o):
    """Probabilidades de mercado quitando el margen con el metodo de la potencia."""
    q = 1 / np.asarray(o, float); lo, hi = .2, 5
    for _ in range(60):
        k = (lo + hi) / 2
        if (q ** k).sum() < 1: hi = k
        else: lo = k
    p = q ** ((lo + hi) / 2); return p / p.sum()


# ---------------------------------------------------------------- calendario
def calendario(liga, dias):
    import espn
    import ligas
    liga = ligas.resolver(liga)
    cal = espn.calendario([liga], datetime.now(timezone.utc), dias)
    cal = cal[cal.estado == 'STATUS_SCHEDULED']
    if not len(cal):
        print('Sin partidos programados.'); return
    mapa = pd.read_csv(DIR / 'datos' / 'mapa_nombres.csv')
    m = {r.nombre_src: r.nombre_fd for r in mapa[(mapa.fuente == 'espn') & (mapa.liga == liga)].itertuples()}
    cal = cal.assign(local=cal.local_src.map(m), visitante=cal.visitante_src.map(m))
    carpeta = ligas.carpeta_jornada(liga, cal.inicio_utc.min())
    fi = ligas.ficha(liga)
    print(f'Liga: {liga}')
    print(f'Carpeta de la jornada: {carpeta.relative_to(DIR).as_posix()}')
    print(f'Ficha: {(ligas.carpeta(liga) / "ficha.json").relative_to(DIR).as_posix()}')
    print(f'bet365: {fi.get("bet365_url") or "SIN DIRECCION en la ficha: buscarla y anotarla en el informe"}')
    if fi.get('nombres_bet365'):
        print(f'Nombres en bet365 distintos: {fi["nombres_bet365"]}')
    for n in fi.get('notas', []):
        print(f'Nota de la liga: {n}')
    print(cal[['inicio_utc', 'local', 'visitante', 'local_src', 'visitante_src']].to_string(index=False))
    falta = cal[cal.local.isna() | cal.visitante.isna()]
    if len(falta):
        print('\nAVISO: equipos sin emparejar en datos/mapa_nombres.csv:',
              sorted(set(falta.local_src) | set(falta.visitante_src)))


# ---------------------------------------------------------------- jugadores
class Jugadores:
    def __init__(self, liga):
        J = pd.read_csv(DIR / 'datos' / 'alineaciones' / 'jugadores.csv', dtype=str)
        for c in ('tiros', 'tiros_puerta'):
            J[c] = pd.to_numeric(J[c], errors='coerce')
        J['titular'] = J.titular == 'True'
        J = J.drop_duplicates(['espn_id', 'jugador_id', 'titular'])
        J['n'] = J.jugador.map(nz)
        self.J = J
        # lo que concede cada rival, relativo a la media de SU liga (las ligas rematan distinto)
        E = J[J.liga == liga].groupby(['espn_id', 'equipo'])[['tiros', 'tiros_puerta']].sum().reset_index()
        RV = E.merge(E, on='espn_id', suffixes=('', '_r'))
        self.RV, self.E = RV[RV.equipo != RV.equipo_r], E
        mapa = pd.read_csv(DIR / 'datos' / 'mapa_nombres.csv')
        self.fd2espn = {r.nombre_fd: r.nombre_src
                        for r in mapa[(mapa.fuente == 'espn') & (mapa.liga == liga)].itertuples()}

    def concede(self, eq):
        """Remates y remates a puerta que concede el rival, relativos a la media."""
        x = self.RV[self.RV.equipo_r == eq]
        if not len(x):
            return 1.0, 1.0
        return x.tiros.mean() / self.E.tiros.mean(), x.tiros_puerta.mean() / self.E.tiros_puerta.mean()

    def buscar(self, nombre, local, visit, solo_titular=True):
        """(filas del jugador en los dos equipos del partido, aviso o None, equipo ESPN local, visitante)."""
        el, ev = self.fd2espn.get(local), self.fd2espn.get(visit)
        if not (el and ev):
            return None, 'equipo sin emparejar con ESPN', el, ev
        toks = nz(nombre).split()
        x = self.J[self.J.equipo.isin([el, ev]) & self.J.n.apply(lambda s: all(t in s for t in toks))]
        if solo_titular:
            x = x[x.titular]
        if not len(x):
            return None, 'sin datos en ESPN' + (' como titular' if solo_titular else ''), el, ev
        if x.jugador_id.nunique() > 1:
            return None, f'nombre ambiguo ({", ".join(sorted(set(x.jugador)))})', el, ev
        return x, None, el, ev

    def evaluar(self, nombre, mercado, linea, local, visit):
        """(prob, aviso, nombre_espn, titularidades).
        prob = minimo entre la binomial negativa ajustada al rival y su frecuencia real como titular."""
        x, err, el, ev = self.buscar(nombre, local, visit)
        if err:
            return np.nan, err, nombre, 0
        eq = x.equipo.iloc[-1]; rival = ev if eq == el else el
        ft, fp = self.concede(rival)
        col, f = ('tiros', ft) if mercado == 'remates' else ('tiros_puerta', fp)
        v = x[col].dropna().values
        if not len(v):
            return np.nan, 'sin estadisticas de remates', x.jugador.iloc[-1], 0
        mu = v.mean() * f
        a = max((max(v.var(), v.mean() * 1.05) - v.mean()) / max(v.mean(), .01) ** 2, 0.01); n = 1 / a
        pm = 1 - nbinom.cdf(linea - 1, n, n / (n + mu)) if mu > 0 else 0.0
        emp = (v >= linea).mean()
        aviso = f'{len(v)} titularidades, {emp * 100:.0f}% en sus partidos'
        if len(v) < MUESTRA_MIN:
            aviso += ' | MUESTRA CORTA'
        return min(pm, emp), aviso, x.jugador.iloc[-1], len(v)


# ---------------------------------------------------------------- simulacion
def simular_jornada(carpeta, n_sim=200_000):
    import motor
    import simulador as sm
    carpeta = Path(carpeta)
    cu = json.loads((carpeta / 'cuotas.json').read_text(encoding='utf-8'))
    ctx_f = carpeta / 'contexto.json'
    ctx = json.loads(ctx_f.read_text(encoding='utf-8')) if ctx_f.exists() else {}
    ctx = {(p['local'], p['visitante']): p for p in ctx.get('partidos', [])}
    import ligas
    liga = ligas.resolver(cu['liga'])
    B = motor.Base(motor.cargar(DIR / 'datos' / 'partidos.csv'))
    JG = Jugadores(liga)
    filas = []
    for P in cu['partidos']:
        loc, vis = P['local'], P['visitante']; part = f'{loc} - {vis}'
        try:
            R = motor.analizar(B, loc, vis, liga)
        except SystemExit as e:
            print(f'AVISO {part}: el motor no lo reconoce ({e})', file=sys.stderr); continue
        S = sm.simular(R, n_sim, 11); C = sm.catalogo(S)
        w = S['w']; serie = {'goles': S['gl'] + S['gv'], 'corners': S['cl'] + S['cv']}
        M = P.get('mercados', {})
        div = {}
        if all(M.get(k) for k in ('1', 'X', '2')):
            mk = potencia([M['1'], M['X'], M['2']])
            div = {s: R['m'][s] - mk[i] for i, s in enumerate('1X2')}
        cx = ctx.get((loc, vis), {})
        base = dict(partido=part, inicio_utc=P.get('inicio_utc', ''), leido_utc=P.get('leido_utc', ''))
        for k, o in M.items():
            if not o:
                continue
            try:
                if k in RANGOS:
                    s, a, b = RANGOS[k]; x = serie[s]
                    p = float(np.average((x >= a) & (x <= b), weights=w))
                else:
                    p = sm.prob(S, C, k)[0]
            except KeyError:
                print(f'AVISO {part}: mercado desconocido {k!r}', file=sys.stderr); continue
            av = []
            if any(t in k for t in MENOS_GOLES):
                av.append('sesgo de goles (el simulador ve menos goles)')
            res = [s for s in '1X2' if k == s or k.startswith(s + ' &')]
            res += ['1'] if k == 'Sin empate 1' else ['2'] if k == 'Sin empate 2' else []
            if res:
                av.append('1X2/DNB: el motor no tiene ventaja')
                if res[0] in div and abs(div[res[0]]) > 0.08:
                    av.append(f'motor y mercado se separan {div[res[0]] * 100:+.0f} pts')
            filas.append(dict(base, tipo='equipo', apuesta=k.replace('R:', ''), cuota=o, prob=p,
                              titularidades=np.nan, p_titular=np.nan, aviso=' | '.join(av)))
        for j in P.get('jugadores', []):
            nom, mk, ln, o = j['jugador'], j['mercado'], int(j['linea']), j['cuota']
            p, av, nom_e, nt = JG.evaluar(nom, mk, ln, loc, vis)
            pt = float(cx.get('titular_prob', {}).get(nom, np.nan))
            if not np.isnan(pt) and pt < P_TITULAR_MIN:
                av += f' | titularidad dudosa ({pt:.0%})'
            filas.append(dict(base, tipo='jugador', apuesta=f'{nom_e} {mk} {ln}+', cuota=o, prob=p,
                              titularidades=nt, p_titular=pt, aviso=av))
    T = pd.DataFrame(filas)
    T['necesaria'] = 1 / T.cuota
    T['margen'] = T.prob - T.necesaria
    T['prob_apuesta'] = W_MODELO * T.prob + (1 - W_MODELO) * T.necesaria
    T['limpia'] = ((T.cuota >= 1.5) & (T.margen >= 0.03) & T.prob.notna()
                   & ~T.aviso.str.contains(NO_LIMPIA, na=False))
    T.to_csv(carpeta / 'evaluacion.csv', index=False)
    L = T[T.limpia].sort_values('prob', ascending=False)
    print(f'{liga}: {len(cu["partidos"])} partidos, {len(T)} apuestas evaluadas, {len(L)} limpias '
          f'-> {(carpeta / "evaluacion.csv").as_posix()}')
    if len(L):
        print(L[['partido', 'apuesta', 'cuota', 'prob', 'necesaria', 'aviso']].assign(
            prob=lambda x: (x.prob * 100).round(1), necesaria=lambda x: (x.necesaria * 100).round(1)
        ).to_string(index=False))
    return T


# ---------------------------------------------------------------- contexto (datos propios)
def _forma(D, eq, n=6):
    """Ultimos n partidos de un equipo: racha y totales, vista desde el equipo."""
    m = D[(D.local == eq) | (D.visitante == eq)].sort_values('fecha').tail(n)
    if not len(m):
        return 'sin partidos en la base', []
    loc = (m.local == eq).values

    def lado(a, b):
        return np.where(loc, m[a], m[b]).astype(float)
    gf, gc = lado('gl', 'gv'), lado('gv', 'gl')
    racha = '-'.join('G' if a > b else 'E' if a == b else 'P' for a, b in zip(gf, gc))
    txt = f'{racha}, {int(gf.sum())} GF y {int(gc.sum())} GC en {len(m)}'
    if m.tiros_p_l.notna().any():
        txt += (f'; tiros a puerta {int(np.nansum(lado("tiros_p_l", "tiros_p_v")))}-'
                f'{int(np.nansum(lado("tiros_p_v", "tiros_p_l")))}')
    if m.corners_l.notna().any():
        txt += (f'; corners {int(np.nansum(lado("corners_l", "corners_v")))}-'
                f'{int(np.nansum(lado("corners_v", "corners_l")))}')
    det = [f'{str(r.fecha)[:10]} {r.local} {int(r.gl)}-{int(r.gv)} {r.visitante}' for r in m.itertuples()]
    return txt, det


def _h2h(D, a, b, n=5):
    m = D[((D.local == a) & (D.visitante == b)) | ((D.local == b) & (D.visitante == a))].sort_values('fecha').tail(n)
    if not len(m):
        return 'Sin enfrentamientos en la base.'
    ga = int((((m.local == a) & (m.gl > m.gv)) | ((m.visitante == a) & (m.gv > m.gl))).sum())
    e = int((m.gl == m.gv).sum())
    t = (m.gl + m.gv).astype(float)
    return (f'H2H ({len(m)}): {a} {ga}, {b} {len(m) - ga - e}, empates {e}; '
            f'{t.mean():.1f} goles de media, Más 2.5 en {int((t > 2.5).sum())}. '
            + '; '.join(f'{str(r.fecha)[:10]} {r.local} {int(r.gl)}-{int(r.gv)} {r.visitante}' for r in m.itertuples()))


def _titular_base(ult3, titular_ultimo):
    """Patron de titularidad en los 3 ultimos partidos de su equipo -> probabilidad de partida.
    Es solo el punto de partida: el analista la ajusta con las noticias."""
    if ult3 == 3:
        return 0.9
    if ult3 == 2:
        return 0.7 if titular_ultimo else 0.5
    return 0.3 if ult3 == 1 else 0.1


def _num(v, d=2):
    v = float(np.asarray(v, float).mean())
    return round(v, d) if np.isfinite(v) else None


def contexto_datos(carpeta, forzar=False):
    """contexto.json con lo que sale de los datos propios, sin gastar un modelo en ello."""
    import ligas
    import motor
    carpeta = Path(carpeta)
    cu = json.loads((carpeta / 'cuotas.json').read_text(encoding='utf-8'))
    liga = ligas.resolver(cu['liga'])
    sal = carpeta / 'contexto.json'
    if sal.exists() and not forzar:
        previo = json.loads(sal.read_text(encoding='utf-8'))
        if previo.get('noticias') != 'pendiente':
            raise SystemExit(f'{sal.as_posix()} ya tiene noticias del analista; usa --forzar para rehacerlo.')
    D = motor.cargar(DIR / 'datos' / 'partidos.csv')
    B = motor.Base(D)
    D = D[D.liga == liga]
    JG = Jugadores(liga)
    J = JG.J[JG.J.liga == liga]
    partidos = []
    for P in cu['partidos']:
        loc, vis = P['local'], P['visitante']
        alertas = []
        try:
            R = motor.analizar(B, loc, vis, liga); m = R['m']
            mot = {'elo_l': _num(R['elo_l'], 1), 'elo_v': _num(R['elo_v'], 1),
                   'p1': _num(m['1'], 3), 'px': _num(m['X'], 3), 'p2': _num(m['2'], 3),
                   'lh': _num(R['lh']), 'la': _num(R['la']), 'total': _num(R['tot']),
                   'over25': _num(m['over2.5'], 3), 'btts': _num(m['btts'], 3),
                   'corners': _num(R['lc']), 'tarjetas': _num(R['lt']),
                   'fiabilidad': R['fiabilidad']}
            if R['fiabilidad'] != 'alta':
                alertas.append(f'fiabilidad del motor {R["fiabilidad"]}')
        except SystemExit as e:
            mot = {}; alertas.append(f'el motor no reconoce el partido ({e})')
        fl, dl = _forma(D, loc); fv, dv = _forma(D, vis)
        tp, jug = {}, {}
        for nom in dict.fromkeys(j['jugador'] for j in P.get('jugadores', [])):
            x, err, el, ev = JG.buscar(nom, loc, vis, solo_titular=False)
            if err:
                jug[nom] = {'nota': err}; alertas.append(f'{nom}: {err}'); continue
            x = x[x.liga == liga]
            if not len(x):
                jug[nom] = {'nota': 'sin datos en ESPN en esta liga'}
                alertas.append(f'{nom}: sin datos en ESPN en esta liga'); continue
            eq = x.sort_values('fecha').equipo.iloc[-1]
            ult = J[J.equipo == eq].drop_duplicates('espn_id').sort_values('fecha').espn_id.tolist()[-3:]
            tit = x[x.titular & (x.equipo == eq)].sort_values('fecha')
            u3 = int(tit.espn_id.isin(ult).sum())
            ultimo_tit = bool(len(ult) and (tit.espn_id == ult[-1]).any())
            mins = pd.to_numeric(tit.minutos, errors='coerce')
            jug[nom] = {
                'nombre_espn': x.jugador.iloc[-1], 'equipo': eq,
                'titular_ultimos_3': u3, 'titular_en_el_ultimo': ultimo_tit,
                'titularidades': int(len(tit)),
                'suplencias_con_minutos': int((~x.titular & (x.entro == 'True')).sum()),
                'minutos_medios': None if mins.notna().sum() == 0 else int(round(mins.mean())),
                'remates_ultimos_5': [int(v) for v in tit.tiros.dropna().tail(5)],
                'a_puerta_ultimos_5': [int(v) for v in tit.tiros_puerta.dropna().tail(5)],
                'ultima_titularidad': None if not len(tit) else str(tit.fecha.iloc[-1])[:10],
                'nota': ''}
            tp[nom] = _titular_base(u3, ultimo_tit)
            if len(tit) < MUESTRA_MIN:
                alertas.append(f'{nom}: solo {len(tit)} titularidades (muestra corta)')
        partidos.append({
            'local': loc, 'visitante': vis, 'inicio_utc': P.get('inicio_utc', ''),
            'titular_prob': tp, 'jugadores': jug,
            'bajas_local': [], 'bajas_visitante': [],
            'forma_local': fl, 'forma_visitante': fv,
            'ultimos_local': dl, 'ultimos_visitante': dv,
            'motor': mot, 'h2h': _h2h(D, loc, vis),
            'alertas': alertas, 'resumen': ''})
    out = {'liga': liga, 'generado_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%MZ'),
           'datos_hasta': {'partidos': str(D.fecha.max())[:10], 'jugadores': str(J.fecha.max())[:10]},
           'noticias': 'pendiente', 'partidos': partidos}
    sal.write_text(json.dumps(out, indent=1, ensure_ascii=False) + '\n', encoding='utf-8', newline='\n')
    nj = sum(len(p['jugadores']) for p in partidos); na = sum(len(p['alertas']) for p in partidos)
    print(f'{liga}: {len(partidos)} partidos, {nj} jugadores con cuota, {na} alertas -> {sal.as_posix()}')
    print(f'Datos hasta: partidos {out["datos_hasta"]["partidos"]}, jugadores {out["datos_hasta"]["jugadores"]}')
    print('Falta: noticias (bajas, sanciones, rotaciones) y ajustar titular_prob -> analista-contexto')
    return out


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest='accion', required=True)
    c = sp.add_parser('calendario'); c.add_argument('--liga', required=True); c.add_argument('--dias', type=int, default=7)
    s = sp.add_parser('simular'); s.add_argument('--dir', required=True); s.add_argument('--n', type=int, default=200_000)
    x = sp.add_parser('contexto'); x.add_argument('--dir', required=True); x.add_argument('--forzar', action='store_true')
    a = ap.parse_args()
    if a.accion == 'calendario':
        calendario(a.liga, a.dias)
    elif a.accion == 'contexto':
        contexto_datos(a.dir, a.forzar)
    else:
        simular_jornada(a.dir, a.n)


if __name__ == '__main__':
    main()

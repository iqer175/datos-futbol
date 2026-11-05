#!/usr/bin/env python3
"""
analizar_jornada.py -- simula una jornada con las cuotas de bet365 y el contexto de los partidos.

Es la pieza comun de los tres agentes (.claude/agents/):
  recolector-cuotas  (Haiku)  -> jornadas/<fecha>/<liga>/cuotas.json
  analista-contexto  (Sonnet) -> jornadas/<fecha>/<liga>/contexto.json
  simulador-apuestas (Opus)   -> ejecuta este script y escribe recomendaciones.json/.md

Uso:
  python analizar_jornada.py calendario --liga "Ligue 1" [--dias 7]
      Partidos de los proximos dias con los nombres de football-data (los que hay
      que usar en cuotas.json y contexto.json) y la carpeta de la jornada.
  python analizar_jornada.py simular --dir jornadas/2026-11-07/ligue-1
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
    cal = espn.calendario([liga], datetime.now(timezone.utc), dias)
    cal = cal[cal.estado == 'STATUS_SCHEDULED']
    if not len(cal):
        print('Sin partidos programados.'); return
    mapa = pd.read_csv(DIR / 'datos' / 'mapa_nombres.csv')
    m = {r.nombre_src: r.nombre_fd for r in mapa[(mapa.fuente == 'espn') & (mapa.liga == liga)].itertuples()}
    cal = cal.assign(local=cal.local_src.map(m), visitante=cal.visitante_src.map(m))
    carpeta = DIR / 'jornadas' / cal.inicio_utc.min()[:10] / slug(liga)
    print(f'Carpeta de la jornada: {carpeta.relative_to(DIR).as_posix()}')
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

    def evaluar(self, nombre, mercado, linea, local, visit):
        """(prob, aviso, nombre_espn, titularidades).
        prob = minimo entre la binomial negativa ajustada al rival y su frecuencia real como titular."""
        el, ev = self.fd2espn.get(local), self.fd2espn.get(visit)
        if not (el and ev):
            return np.nan, 'equipo sin emparejar con ESPN', nombre, 0
        toks = nz(nombre).split()
        x = self.J[self.J.equipo.isin([el, ev]) & self.J.titular
                   & self.J.n.apply(lambda s: all(t in s for t in toks))]
        if not len(x):
            return np.nan, 'sin datos en ESPN como titular', nombre, 0
        if x.jugador_id.nunique() > 1:
            return np.nan, f'nombre ambiguo ({", ".join(sorted(set(x.jugador)))})', nombre, 0
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
    liga = cu['liga']
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


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest='accion', required=True)
    c = sp.add_parser('calendario'); c.add_argument('--liga', required=True); c.add_argument('--dias', type=int, default=7)
    s = sp.add_parser('simular'); s.add_argument('--dir', required=True); s.add_argument('--n', type=int, default=200_000)
    a = ap.parse_args()
    if a.accion == 'calendario':
        calendario(a.liga, a.dias)
    else:
        simular_jornada(a.dir, a.n)


if __name__ == '__main__':
    main()

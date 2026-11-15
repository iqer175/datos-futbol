#!/usr/bin/env python3
"""
simulador.py -- simula un partido N veces (goles, corners, tarjetas) y da la
probabilidad de cualquier apuesta simple o combinada del mismo partido.

Para que sirve: el motor ya da la probabilidad exacta de cada mercado por
separado. Lo que aporta la simulacion es la probabilidad CONJUNTA de una
combinada ("gana el local + Mas de 2.5 + Mas de 9.5 corners"), que NO es el
producto de las partes porque los mercados estan correlacionados.

Una apuesta no se elige por lo mucho que se repite, sino por su valor:
    valor = probabilidad x cuota - 1   (y por encima del colchon de la skill)
Una combinada de bet365 acumula el margen de cada pata: casi siempre sale
peor que las simples, salvo que bet365 calcule mal la correlacion.

Modelo de cada simulacion (parametros del motor v5 + medidos en 38.261 partidos):
  goles      Poisson(lh), Poisson(la) del motor; el 1X2 se recalibra con
             CAL_LOCAL / CAL_EMPATE reponderando las simulaciones.
             Con cuotas de 1X2 y Mas/Menos 2,5 del partido, anclar_al_mercado()
             sustituye lh, la y el peso del empate por los que reproducen esas
             cuotas sin margen (backtest_ancla.py, 2026-11-10: mejora los 13
             mercados derivados de goles en 4/4 temporadas, 21.002 partidos)
  corners    total ~ binomial negativa (varianza/media 1.16, medida) repartida
             entre los dos equipos segun sus lambdas (correlacion negativa
             entre equipos, como en los datos: -0.25); cada lambda se corrige
             por el dominio del partido (CORNERS_DOMINIO, validado fuera de muestra)
  tarjetas   Poisson por equipo (los datos no estan sobredispersos), con mas
             tarjetas si el partido acaba apretado: 4.33 con 0-1 goles de
             diferencia y 3.67 con 2+ (medido); analizar_jornada multiplica la media
             por el factor del arbitro designado (arbitros.py, 2026-11-15)
  Limites: corners independientes del marcador (correlacion medida -0.01);
  tarjetas = amarillas (bet365 puede contar la roja como 2: mira la regla de
  la linea antes de apostar). Los goleadores no salen de aqui: los calcula
  goleadores.py con el xG de cada jugador (en prueba desde 2026-11-15).

Uso:
  python simulador.py LaLiga "Valencia" "Real Madrid"
  python simulador.py LaLiga "Valencia" "Real Madrid" --cuotas cuotas.json
     cuotas.json: {"1": 9.7, "Mas 2.5": 1.42, "2 & Mas 2.5": 1.80, ...}
     (claves = nombres del catalogo; una combinada se escribe con " & ")
"""
import argparse
import json
import re
import sys
from pathlib import Path

from math import exp, log

import numpy as np
import pandas as pd
from scipy.optimize import root

import motor

DIR = Path(__file__).parent
N_DEF = 50_000
VAR_MEDIA_CORNERS = 1.16          # total de corners, dentro de liga-temporada
TARJ_APRETADO, TARJ_ABIERTO = 4.33, 3.67
# colchones de la skill (puntos de probabilidad) por tipo de mercado
COLCHON = {'1x2': 0.08, 'goles': 0.05, 'corners': 0.06, 'tarjetas': 0.06, 'remates_eq': 0.06, 'stats_eq': 0.06}
# Corners por equipo segun el dominio (experimento_corners.py, 2026-11-05): ajustado en
# 2025-26 (7.051 partidos) y probado sin reajustar en 2026-27 (2.188): mejora 9 de 10 lineas.
#   corners local     = perfil + a_l + b_l * (Elo local - Elo visitante) / 100
#   corners visitante = perfil + a_v + b_v * (Elo local - Elo visitante) / 100
CORNERS_DOMINIO = dict(a_l=0.489, b_l=0.506, a_v=-0.453, b_v=-0.373)
COLCHON_COMBINADA = 1.25          # combinadas: cuota minima = 1.25 x cuota justa (provisional)


# ------------------------------------------------------------------ ancla al mercado
_K = np.arange(11)
_I, _J = np.indices((11, 11))
_LOGFACT = np.cumsum(np.log(np.maximum(_K, 1)))


def _matriz(lh, la, cd):
    """Marcadores 0-10 con Poisson independientes y el empate reponderado por exp(cd)."""
    M = np.outer(np.exp(-lh + _K * log(lh) - _LOGFACT), np.exp(-la + _K * log(la) - _LOGFACT))
    M[_I == _J] *= exp(cd)
    return M / M.sum()


def _potencia(o):
    """Probabilidades sin margen (metodo de la potencia) de un mercado de N salidas."""
    q = 1 / np.asarray(o, float); lo, hi = .2, 5
    for _ in range(60):
        k = (lo + hi) / 2
        if (q ** k).sum() < 1: hi = k
        else: lo = k
    p = q ** ((lo + hi) / 2); return p / p.sum()


def anclar_al_mercado(R, o1, ox, o2, o_mas25, o_menos25):
    """Copia de R (motor.analizar) con los goles sacados de las cuotas del partido.

    Resuelve lh, la y el peso del empate para que la simulacion reproduzca las
    probabilidades sin margen de 1, X y Mas 2,5. No usa ningun parametro ajustado con
    resultados. Los corners y las tarjetas siguen saliendo de los perfiles del motor.
    Devuelve None si las cuotas no valen o no hay solucion (se simula con el motor)."""
    try:
        o = [float(x) for x in (o1, ox, o2, o_mas25, o_menos25)]
    except (TypeError, ValueError):
        return None
    if not all(np.isfinite(x) and x > 1 for x in o):
        return None
    p1, px, _ = _potencia(o[:3]); po = motor.sin_margen_2(o[3], o[4])

    def f(z):
        M = _matriz(exp(z[0]), exp(z[1]), z[2])
        return [M[_I > _J].sum() - p1, M[_I == _J].sum() - px, M[_I + _J > 2.5].sum() - po]
    try:
        r = root(f, [log(R['lh']), log(R['la']), motor.CAL_EMPATE], method='hybr')
    except (OverflowError, ValueError, FloatingPointError):
        return None
    if not r.success or np.abs(r.fun).max() > 1e-4:
        return None
    return dict(R, lh=exp(r.x[0]), la=exp(r.x[1]), cal_local=0.0, cal_empate=float(r.x[2]),
                ancla=dict(p1=float(p1), px=float(px), p_mas25=float(po), lh_motor=R['lh'], la_motor=R['la']))


# ------------------------------------------------------------------ simulacion
def simular(R, n=N_DEF, semilla=None):
    """Simula a partir de la salida de motor.analizar (o de anclar_al_mercado).
    Devuelve dict de arrays + pesos."""
    rng = np.random.default_rng(semilla)
    gl = rng.poisson(R['lh'], n); gv = rng.poisson(R['la'], n)
    # pesos para que el 1X2 coincida con el del motor calibrado (CAL_LOCAL / CAL_EMPATE)
    # o, si R viene anclado, con el del mercado (cal_local / cal_empate propios del partido)
    cal_l, cal_e = R.get('cal_local', motor.CAL_LOCAL), R.get('cal_empate', motor.CAL_EMPATE)
    w = np.where(gl > gv, np.exp(cal_l), np.where(gl == gv, np.exp(cal_e), 1.0))
    w = w / w.mean()

    # corners: total binomial negativa, reparto binomial por la proporcion de cada equipo
    lc = R['lc']
    if not np.isfinite(lc) or lc <= 0:              # liga sin corners en la base (p. ej. National League)
        print('  aviso: sin datos de corners para esta liga; se usa la media de la base (9.8)', file=sys.stderr)
        lc = 9.80
    sl, sv = R.get('sl'), R.get('sv')
    if sl and sv:
        lc_l = (sl['cor_f'] + sv['cor_c']) / 2; lc_v = (sv['cor_f'] + sl['cor_c']) / 2
        gap = (R['elo_l'] - R['elo_v']) / 100
        k = CORNERS_DOMINIO
        lc_l = max(lc_l + k['a_l'] + k['b_l'] * gap, 0.8); lc_v = max(lc_v + k['a_v'] + k['b_v'] * gap, 0.8)
        lc = lc_l + lc_v                              # el total tambien sale de los dos perfiles corregidos
        share = lc_l / lc
    else:
        share = 0.547                                 # media local de la base (5.36 / 9.80)
    alpha = max(VAR_MEDIA_CORNERS - 1, 1e-6) / lc     # var = mu + alpha mu^2
    nb_n = 1 / alpha
    cor = rng.negative_binomial(nb_n, nb_n / (nb_n + lc), n)
    cl = rng.binomial(cor, share); cv = cor - cl

    # tarjetas: mas si el partido queda apretado; se reescala para que la media sea la del motor
    lt = R['lt']
    if not np.isfinite(lt) or lt <= 0:
        print('  aviso: sin datos de tarjetas para esta liga; se usa la media de la base (4.1)', file=sys.stderr)
        lt = 4.10
    if sl and sv and sl.get('amar') and sv.get('amar'):
        sh_t = sl['amar'] / (sl['amar'] + sv['amar'])
    else:
        sh_t = 0.466                                  # la base: el visitante ve algo mas (2.19 vs 1.91)
    apretado = np.abs(gl - gv) <= 1
    f = np.where(apretado, TARJ_APRETADO, TARJ_ABIERTO)
    f = f / np.average(f, weights=w)
    tl = rng.poisson(lt * sh_t * f); tv = rng.poisson(lt * (1 - sh_t) * f)
    out = dict(gl=gl, gv=gv, cl=cl, cv=cv, tl=tl, tv=tv, w=w)
    # remates y remates a puerta de cada equipo (remates_equipo.py); independientes del marcador
    if R.get('rem'):
        import remates_equipo
        out.update(remates_equipo.simular(R['rem'], n, rng))
    # faltas, entradas, fueras de juego, paradas y centros de cada equipo (stats_equipo.py; en prueba)
    if R.get('stq'):
        import stats_equipo_modelo as sq
        for s, m in R['stq'].items():
            x = sq.simular(m, m, n, rng)
            out[f'sq_{s}_l'], out[f'sq_{s}_v'] = x['l'], x['v']
    return out


# ------------------------------------------------------------------ catalogo
# Remates del equipo / del partido: 'Remates más 24.5', 'Remates local menos 12.5',
# 'Remates a puerta visitante más 3.5'... Linea entera: si se iguala, nula.
_RE_REMATES = re.compile(r'^Remates( a puerta)?( local| visitante)? (más|menos) (\d+(?:\.\d)?)$')
# nombre del mercado en bet365 -> estadistica de FotMob (stats_equipo.py)
STATS_MERCADO = {'Faltas': 'faltas', 'Entradas': 'entradas', 'Fueras de juego': 'offsides',
                 'Paradas': 'atajadas', 'Centros': 'centros'}
_RE_STATS = re.compile(r'^(' + '|'.join(STATS_MERCADO) + r')( local| visitante)? (más|menos) (\d+(?:\.\d)?)$')


class Catalogo(dict):
    """dict de mercados que genera bajo demanda las lineas de remates de equipo."""
    def __init__(self, S, *a, **k):
        super().__init__(*a, **k); self._S = S

    def _remates(self, k):
        m = _RE_REMATES.match(k) if isinstance(k, str) else None
        if not m or 'rl' not in self._S:
            return None
        S = self._S; pu, lado, sentido, ln = m.group(1), (m.group(2) or '').strip(), m.group(3), float(m.group(4))
        a, b = (S['pl'], S['pv']) if pu else (S['rl'], S['rv'])
        x = a if lado == 'local' else b if lado == 'visitante' else a + b
        gana = x > ln if sentido == 'más' else x < ln
        return gana, (x == ln) if ln == int(ln) else None, 'remates_eq'

    def _stats(self, k):
        m = _RE_STATS.match(k) if isinstance(k, str) else None
        if not m:
            return None
        st = STATS_MERCADO[m.group(1)]
        if f'sq_{st}_l' not in self._S:
            return None
        lado, sentido, ln = (m.group(2) or '').strip(), m.group(3), float(m.group(4))
        a, b = self._S[f'sq_{st}_l'], self._S[f'sq_{st}_v']
        x = a if lado == 'local' else b if lado == 'visitante' else a + b
        gana = x > ln if sentido == 'más' else x < ln
        return gana, (x == ln) if ln == int(ln) else None, 'stats_eq'

    def __contains__(self, k):
        return super().__contains__(k) or self._remates(k) is not None or self._stats(k) is not None

    def __missing__(self, k):
        v = self._remates(k)
        if v is None:
            v = self._stats(k)
        if v is None:
            raise KeyError(k)
        self[k] = v
        return v


def catalogo(S):
    """{nombre: (array booleano de acierto, array de 'nula' o None, tipo)} con nombres tipo bet365."""
    gl, gv, cor, tar = S['gl'], S['gv'], S['cl'] + S['cv'], S['tl'] + S['tv']
    tot, d = gl + gv, gl - gv
    C = {'1': (d > 0, None, '1x2'), 'X': (d == 0, None, '1x2'), '2': (d < 0, None, '1x2'),
         '1X': (d >= 0, None, '1x2'), '12': (d != 0, None, '1x2'), 'X2': (d <= 0, None, '1x2'),
         'Sin empate 1': (d > 0, d == 0, '1x2'), 'Sin empate 2': (d < 0, d == 0, '1x2'),
         'Ambos marcan - Sí': ((gl > 0) & (gv > 0), None, 'goles'),
         'Ambos marcan - No': ((gl == 0) | (gv == 0), None, 'goles')}
    for ln in (0.5, 1.5, 2.5, 3.5, 4.5, 5.5):
        C[f'Más {ln}'] = (tot > ln, None, 'goles'); C[f'Menos {ln}'] = (tot < ln, None, 'goles')
    for ln in (0.5, 1.5, 2.5):
        C[f'Local más {ln}'] = (gl > ln, None, 'goles'); C[f'Visitante más {ln}'] = (gv > ln, None, 'goles')
    for h in (-2.5, -1.5, -0.5, 0.5, 1.5, 2.5):
        C[f'Hándicap local {h:+}'] = (d + h > 0, None, '1x2')
        C[f'Hándicap visitante {h:+}'] = (-d + h > 0, None, '1x2')
    for h in (-2, -1, 1, 2):                          # lineas enteras: empate con el handicap = nula
        C[f'Hándicap local {h:+}'] = (d + h > 0, d + h == 0, '1x2')
        C[f'Hándicap visitante {h:+}'] = (-d + h > 0, -d + h == 0, '1x2')
    for ln in (7.5, 8.5, 9.5, 10.5, 11.5, 12.5):
        C[f'Córners más {ln}'] = (cor > ln, None, 'corners'); C[f'Córners menos {ln}'] = (cor < ln, None, 'corners')
    for ln in (2.5, 3.5, 4.5, 5.5, 6.5, 7.5):
        C[f'Córners local más {ln}'] = (S['cl'] > ln, None, 'corners'); C[f'Córners local menos {ln}'] = (S['cl'] < ln, None, 'corners')
        C[f'Córners visitante más {ln}'] = (S['cv'] > ln, None, 'corners'); C[f'Córners visitante menos {ln}'] = (S['cv'] < ln, None, 'corners')
    for ln in (2.5, 3.5, 4.5, 5.5, 6.5):
        C[f'Tarjetas más {ln}'] = (tar > ln, None, 'tarjetas'); C[f'Tarjetas menos {ln}'] = (tar < ln, None, 'tarjetas')
    for ln in (0.5, 1.5, 2.5, 3.5):
        C[f'Tarjetas local más {ln}'] = (S['tl'] > ln, None, 'tarjetas'); C[f'Tarjetas local menos {ln}'] = (S['tl'] < ln, None, 'tarjetas')
        C[f'Tarjetas visitante más {ln}'] = (S['tv'] > ln, None, 'tarjetas'); C[f'Tarjetas visitante menos {ln}'] = (S['tv'] < ln, None, 'tarjetas')
    ambos = (S['tl'] > 0) & (S['tv'] > 0)
    C['Ambos reciben tarjetas - Sí'] = (ambos, None, 'tarjetas'); C['Ambos reciben tarjetas - No'] = (~ambos, None, 'tarjetas')
    return Catalogo(S, C)


def prob(S, C, nombre):
    """Probabilidad efectiva (las nulas se excluyen, como paga la casa) de una simple o combinada 'A & B'."""
    patas = [p.strip() for p in nombre.split('&')]
    w = S['w']
    gana = np.ones(len(w), bool); nula = np.zeros(len(w), bool)
    for p in patas:
        if p not in C:
            raise KeyError(f'mercado desconocido: {p!r}')
        g, nu, _ = C[p]
        gana &= g
        if nu is not None:
            nula |= nu
    pg = np.average(gana & ~nula, weights=w); pn = np.average(nula, weights=w)
    return pg / (1 - pn) if pn < 1 else np.nan, pg, pn


def prob_independiente(S, C, nombre):
    """Producto de las patas como si fueran independientes (lo que haria un calculo ingenuo)."""
    return float(np.prod([prob(S, C, p)[0] for p in nombre.split('&')]))


def cuota_minima(S, C, nombre):
    p = prob(S, C, nombre)[0]
    patas = [x.strip() for x in nombre.split('&')]
    if len(patas) > 1:
        return COLCHON_COMBINADA / p
    col = COLCHON[C[patas[0]][2]]
    return 1 / (p - col) if p > col else np.inf


def informe(R, S, local, visit, cuotas=None):
    C = catalogo(S)
    print(f'\n{local} - {visit} [{R["liga"]}]  ({len(S["w"]):,} simulaciones, fiabilidad: {R["fiabilidad"]})')
    print(f'  goles esperados {R["lh"]:.2f} - {R["la"]:.2f}   corners {R["lc"]:.1f}   tarjetas {R["lt"]:.1f}')
    filas = []
    for k in ('1', 'X', '2', '1X', 'X2', 'Más 1.5', 'Más 2.5', 'Menos 2.5', 'Más 3.5', 'Ambos marcan - Sí',
              'Ambos marcan - No', 'Hándicap local -1.5', 'Hándicap visitante +1.5', 'Córners más 8.5', 'Córners más 9.5',
              'Córners más 10.5', 'Tarjetas más 3.5', 'Tarjetas más 4.5', 'Tarjetas más 5.5'):
        p = prob(S, C, k)[0]
        filas.append((k, p, 1 / p, cuota_minima(S, C, k)))
    print(f'  {"mercado":28s} {"prob":>6s} {"justa":>6s} {"mínima":>7s}')
    for k, p, j, m in filas:
        print(f'  {k:28s} {p*100:5.1f}% {j:6.2f} {m:7.2f}')
    print('  combinadas tipicas (probabilidad simulada vs producto ingenuo de las patas):')
    for k in ('1 & Más 2.5', '2 & Más 2.5', '1 & Ambos marcan - Sí', 'Más 2.5 & Ambos marcan - Sí',
              'Menos 2.5 & Tarjetas más 4.5', 'X & Menos 2.5', 'Córners más 9.5 & Tarjetas más 4.5'):
        p = prob(S, C, k)[0]; pi = prob_independiente(S, C, k)
        print(f'  {k:38s} {p*100:5.1f}%  (ingenuo {pi*100:4.1f}%)  justa {1/p:6.2f}  mínima {cuota_minima(S, C, k):6.2f}')
    if cuotas:
        print('\n  Cuotas de bet365 recibidas:')
        for k, o in cuotas.items():
            p = prob(S, C, k)[0]; m = cuota_minima(S, C, k)
            ev = p * o - 1
            print(f'  {k:38s} cuota {o:6.2f}  prob {p*100:5.1f}%  EV {ev*100:+5.1f}%  mínima {m:6.2f}  '
                  f'{"PASA LA MÍNIMA" if o >= m else "no"}')
        print('  (EV con la probabilidad del modelo sin contraer: en simples aplica la contraccion hacia el '
              'mercado de la skill antes de decidir)')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('liga'); ap.add_argument('local'); ap.add_argument('visitante')
    ap.add_argument('--n', type=int, default=N_DEF)
    ap.add_argument('--cuotas')
    ap.add_argument('--semilla', type=int, default=1)
    a = ap.parse_args()
    df = motor.cargar(DIR / 'datos' / 'partidos.csv')
    B = motor.Base(df)
    R = motor.analizar(B, a.local, a.visitante, a.liga)
    S = simular(R, a.n, a.semilla)
    cuotas = json.loads(Path(a.cuotas).read_text(encoding='utf-8')) if a.cuotas else None
    informe(R, S, a.local, a.visitante, cuotas)


if __name__ == '__main__':
    main()

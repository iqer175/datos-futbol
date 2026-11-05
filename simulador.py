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
             CAL_LOCAL / CAL_EMPATE reponderando las simulaciones
  corners    total ~ binomial negativa (varianza/media 1.16, medida) repartida
             entre los dos equipos segun sus lambdas (correlacion negativa
             entre equipos, como en los datos: -0.25)
  tarjetas   Poisson por equipo (los datos no estan sobredispersos), con mas
             tarjetas si el partido acaba apretado: 4.33 con 0-1 goles de
             diferencia y 3.67 con 2+ (medido)
  Limites: corners independientes del marcador (correlacion medida -0.01);
  tarjetas = amarillas (bet365 puede contar la roja como 2: mira la regla de
  la linea antes de apostar); sin goleadores hasta que el modelo de jugadores
  este calibrado.

Uso:
  python simulador.py LaLiga "Valencia" "Real Madrid"
  python simulador.py LaLiga "Valencia" "Real Madrid" --cuotas cuotas.json
     cuotas.json: {"1": 9.7, "Mas 2.5": 1.42, "2 & Mas 2.5": 1.80, ...}
     (claves = nombres del catalogo; una combinada se escribe con " & ")
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import motor

DIR = Path(__file__).parent
N_DEF = 50_000
VAR_MEDIA_CORNERS = 1.16          # total de corners, dentro de liga-temporada
TARJ_APRETADO, TARJ_ABIERTO = 4.33, 3.67
# colchones de la skill (puntos de probabilidad) por tipo de mercado
COLCHON = {'1x2': 0.08, 'goles': 0.05, 'corners': 0.06, 'tarjetas': 0.06}
COLCHON_COMBINADA = 1.25          # combinadas: cuota minima = 1.25 x cuota justa (provisional)


# ------------------------------------------------------------------ simulacion
def simular(R, n=N_DEF, semilla=None):
    """Simula a partir de la salida de motor.analizar. Devuelve dict de arrays + pesos."""
    rng = np.random.default_rng(semilla)
    gl = rng.poisson(R['lh'], n); gv = rng.poisson(R['la'], n)
    # pesos para que el 1X2 coincida con el del motor calibrado (CAL_LOCAL / CAL_EMPATE)
    w = np.where(gl > gv, np.exp(motor.CAL_LOCAL), np.where(gl == gv, np.exp(motor.CAL_EMPATE), 1.0))
    w = w / w.mean()

    # corners: total binomial negativa, reparto binomial por la proporcion de cada equipo
    lc = R['lc']
    if not np.isfinite(lc) or lc <= 0:              # liga sin corners en la base (p. ej. National League)
        print('  aviso: sin datos de corners para esta liga; se usa la media de la base (9.8)', file=sys.stderr)
        lc = 9.80
    sl, sv = R.get('sl'), R.get('sv')
    if sl and sv:
        lc_l = (sl['cor_f'] + sv['cor_c']) / 2; lc_v = (sv['cor_f'] + sl['cor_c']) / 2
        share = lc_l / (lc_l + lc_v)
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
    return dict(gl=gl, gv=gv, cl=cl, cv=cv, tl=tl, tv=tv, w=w)


# ------------------------------------------------------------------ catalogo
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
    for ln in (2.5, 3.5, 4.5, 5.5, 6.5):
        C[f'Tarjetas más {ln}'] = (tar > ln, None, 'tarjetas'); C[f'Tarjetas menos {ln}'] = (tar < ln, None, 'tarjetas')
    return C


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

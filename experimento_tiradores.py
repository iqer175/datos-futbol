#!/usr/bin/env python3
"""
experimento_tiradores.py -- ¿se queda corto el modelo de remates de jugador con los que mas disparan?

Motivo (2026-11-12): en la prueba de backtest_jugadores.py, el 10 % de titulares con mas remates por 90'
recibe 71,5 % en 2+ remates y 50,1 % en 3+ frente a 75,6 % y 53,2 % reales (resultado Sustituto+).
El encogimiento hacia la media de la posicion (K_RITMO) parece tirar demasiado de ellos.

Modelo vigente (analizar_jornada.py): lambda = (ritmo encogido x min_esp + ritmo suplente x resto) x factor
del partido; binomial negativa con ALPHA_REMATES. Variantes, cada una con UN parametro por estadistica
(remates / a puerta), ajustado por maxima verosimilitud SOLO en la primera mitad de las fechas y
probado en la segunda:
  decil   lambda x c para los que estan en el 10 % de mas ritmo bruto previo (umbral de desarrollo)
  suave   lambda x (ritmo bruto / ritmo encogido) ^ beta, para todos (beta = 0 es el vigente)

Uso:  python experimento_tiradores.py
Salida: resultados/tiradores.txt
"""
from pathlib import Path

import numpy as np, pandas as pd
from scipy.optimize import minimize_scalar

import analizar_jornada as aj
import backtest_jugadores as bj
import backtest_sustituto as bs
import jugadores_modelo as jm

OUT = Path(__file__).parent / 'resultados'


def ll(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def cargar():
    J = jm.cargar().drop_duplicates(['espn_id', 'jugador_id'])
    S = bs.resultado_sustituto(J)
    T = bj.remates_equipo(bj.mercado(bj.tabla_equipos(J)))
    R = bj.predecir(bj.preparar(J, T)).merge(S, on=['espn_id', 'jugador_id'], how='left')
    R = R[R.f_mercado_tiros.notna() & (R.t_n >= aj.MUESTRA_MIN)].copy()
    for s in bj.STATS:
        r_pos = R[f'g_{s}'] / (R.g_min / 90)
        r_enc = (R[f'a_{s}'] + bj.K_RITMO * r_pos) / (R.a_min / 90 + bj.K_RITMO)
        R[f'bruto_{s}'] = R[f'a_{s}'] / (R.a_min / 90)
        R[f'ratio_{s}'] = (R[f'bruto_{s}'] / r_enc).clip(0.2, 5)
        rs = R.grupo.map(aj.R_SUSTITUTO[s]).fillna(aj.R_SUSTITUTO[s]['MED'])
        R[f'lam_{s}'] = R[f'lam_mercado_{s}'] + rs * np.clip(90 - R.min_esp, 0, None) / 90 * R[f'f_mercado_{s}']
        R[f'y_{s}'] = R[s] + R[f'sub_{s}'].fillna(0)
    return R


def perdida(R, s, mult):
    """log-loss medio de todas las lineas de la estadistica s con lambda x mult."""
    lam = R[f'lam_{s}'].values * mult
    return np.mean([ll(bj.p_nb(lam, k, aj.ALPHA_REMATES[s]), (R[f'y_{s}'].values >= k)).mean() for k in bj.STATS[s]])


def main():
    OUT.mkdir(exist_ok=True)
    R = cargar()
    corte = R.fecha.quantile(0.5)
    D, P = R[R.fecha < corte], R[R.fecha >= corte].copy()
    L = [f'Titulares con {aj.MUESTRA_MIN}+ titularidades y cuotas de mercado: {len(R):,}',
         f'Ajuste: {D.fecha.min().date()} .. {D.fecha.max().date()} ({len(D):,}) | '
         f'prueba: {P.fecha.min().date()} .. {P.fecha.max().date()} ({len(P):,}, {P.espn_id.nunique()} partidos)', '']
    par = {}
    for s in bj.STATS:
        u = D[f'bruto_{s}'].quantile(0.9)
        top_d = (D[f'bruto_{s}'] >= u).values
        c = minimize_scalar(lambda c: perdida(D, s, np.where(top_d, c, 1.0)), bounds=(0.8, 1.4), method='bounded').x
        b = minimize_scalar(lambda b: perdida(D, s, D[f'ratio_{s}'].values ** b), bounds=(-0.5, 1.0), method='bounded').x
        par[s] = dict(umbral=u, c=c, beta=b)
        L.append(f'{bj.NOMBRE[s]:9s} umbral del 10 % (remates/90 bruto) {u:.2f} | decil: c = {c:.3f} | suave: beta = {b:.3f}')
    L.append('')

    for s in bj.STATS:
        top = (P[f'bruto_{s}'] >= par[s]['umbral']).values
        mult = {'vigente': np.ones(len(P)), 'decil': np.where(top, par[s]['c'], 1.0),
                'suave': P[f'ratio_{s}'].values ** par[s]['beta']}
        L.append(f'=== {bj.NOMBRE[s]} (prueba) ===')
        L.append(f'{"linea":6s} {"grupo":7s} {"n":>5s} {"real":>6s} {"vigente":>8s} {"decil":>6s} {"suave":>6s} | '
                 f'decil - vigente [IC95]      suave - vigente [IC95]   (log-loss, negativo = mejor)')
        for k in bj.STATS[s]:
            y = (P[f'y_{s}'].values >= k).astype(float)
            p = {v: bj.p_nb(P[f'lam_{s}'].values * m, k, aj.ALPHA_REMATES[s]) for v, m in mult.items()}
            for g, msk in (('todos', np.ones(len(P), bool)), ('top 10%', top)):
                lv = ll(p['vigente'][msk], y[msk]); txt = []
                for v in ('decil', 'suave'):
                    d = ll(p[v][msk], y[msk]) - lv; se = d.std(ddof=1) / np.sqrt(len(d))
                    txt.append(f'{d.mean():+.4f} [{d.mean() - 1.96 * se:+.4f},{d.mean() + 1.96 * se:+.4f}]')
                L.append(f'{str(k) + "+":6s} {g:7s} {msk.sum():5d} {y[msk].mean() * 100:5.1f}% '
                         f'{p["vigente"][msk].mean() * 100:7.1f}% {p["decil"][msk].mean() * 100:5.1f}% '
                         f'{p["suave"][msk].mean() * 100:5.1f}% | ' + '   '.join(txt))
        L.append('')
    txt = '\n'.join(L); print(txt)
    (OUT / 'tiradores.txt').write_text(txt + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()

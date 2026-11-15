#!/usr/bin/env python3
"""
experimento_remates.py -- ¿mejora el motor si el GLM aprende de los remates y no solo de los goles?

El xG solo esta en el 5 % de la base; los remates y remates a puerta, en el 91 %.
Se construye un "xG de remates" por equipo y partido:
    xS = c_p · remates a puerta + c_f · remates fuera
con c_p y c_f ajustados SOLO con partidos anteriores a 2022-07 (antes de la evaluacion),
y el GLM del motor se ajusta al objetivo  y = alpha·goles + (1 - alpha)·xS
(partidos sin remates: y = goles).

Variantes sobre el banco de experimentos_motor.py (walk-forward semanal, recalibracion
en 2022-24, regla de 4/4 temporadas):
  alpha  peso de los goles en el objetivo (1 = motor actual)
  W      peso del GLM en el total de goles (hoy 0.35)
  V      peso del GLM en la supremacia (hoy 0: Elo puro)

Uso:  python experimento_remates.py
Salida: resultados/experimento_remates.txt
"""
import sys
from pathlib import Path

import numpy as np, pandas as pd

import motor
import experimentos_motor as em
from backtest_ancla import potencia

DIR = Path(__file__).parent
OUT = DIR / 'resultados'


def objetivo(df, alpha, cp, cf):
    gl, gv = df.gl.values.astype(float), df.gv.values.astype(float)
    hay = df[['tiros_l', 'tiros_v', 'tiros_p_l', 'tiros_p_v']].notna().all(axis=1).values
    xl = np.where(hay, cp * df.tiros_p_l + cf * (df.tiros_l - df.tiros_p_l).clip(lower=0), gl)
    xv = np.where(hay, cp * df.tiros_p_v + cf * (df.tiros_v - df.tiros_p_v).clip(lower=0), gv)
    return alpha * gl + (1 - alpha) * xl, alpha * gv + (1 - alpha) * xv


def glm_semanal(df, ev, y):
    """(lh, la) del GLM para cada partido evaluado, reajustado cada lunes."""
    lh = pd.Series(np.nan, index=ev.index); la = lh.copy()
    for t, g in ev.groupby('semana'):
        G = motor.ajustar_glm(df, t, y=y)
        if G is None:
            continue
        for ix, l, v, lg in zip(g.index, g.local, g.visitante, g.liga):
            if l in G['eqs'] and v in G['eqs'] and lg in G['lgs']:
                il, iv, ig = G['eqs'].get_loc(l), G['eqs'].get_loc(v), G['lgs'].get_loc(lg)
                lh[ix] = np.exp(G['base'][ig] + G['loc'][ig] + G['att'][il] - G['dfn'][iv])
                la[ix] = np.exp(G['base'][ig] + G['att'][iv] - G['dfn'][il])
    return lh.values, la.values


def lambdas(df, ev, gh, ga, W, V):
    lh = np.full(len(ev), np.nan); la = np.full(len(ev), np.nan)
    pos = {ix: k for k, ix in enumerate(ev.index)}
    for t, g in ev.groupby('semana'):
        co = motor.coeficientes(df, t)
        g = g[g.liga.isin(co)]
        C = pd.DataFrame(co).T.loc[g.liga]
        d = (g.elo_l_pre.values - g.elo_v_pre.values) / 100.0
        sup = C.a.values.astype(float) * d + C.b.values.astype(float)
        tot = C.bt.values.astype(float) + C['at'].values.astype(float) * np.abs(d)
        k = np.array([pos[i] for i in g.index])
        hay = ~np.isnan(gh[k])
        tot = np.where(hay, (1 - W) * tot + W * (gh[k] + ga[k]), tot)
        sup = np.where(hay, (1 - V) * sup + V * (gh[k] - ga[k]), sup)
        lh[k] = (tot + sup) / 2; la[k] = (tot - sup) / 2
    return lh, la


def main():
    OUT.mkdir(exist_ok=True)
    df = motor.cargar(DIR / 'datos' / 'partidos.csv').sort_values('fecha', kind='stable').reset_index(drop=True)
    ev = df[(df.fecha >= em.INI) & df.b365_1.notna()].copy()
    ev['semana'] = ev.fecha.dt.to_period('W-SUN').dt.start_time
    lineas = []
    def p(s=''):
        print(s); lineas.append(s); sys.stdout.flush()

    # coeficientes del xG de remates, solo con datos anteriores a la evaluacion
    h = df[(df.fecha < em.INI) & df.tiros_p_l.notna() & df.tiros_l.notna()]
    X = np.concatenate([np.c_[h.tiros_p_l, (h.tiros_l - h.tiros_p_l).clip(lower=0)],
                        np.c_[h.tiros_p_v, (h.tiros_v - h.tiros_p_v).clip(lower=0)]]).astype(float)
    yy = np.concatenate([h.gl, h.gv]).astype(float)
    cp, cf = np.linalg.lstsq(X, yy, rcond=None)[0]
    p(f'xG de remates = {cp:.3f}·a puerta + {cf:.3f}·fuera   (ajustado con {len(h):,} partidos anteriores a {em.INI.date()})')

    cache = {}
    def glm(alpha):
        if alpha not in cache:
            print(f'  GLM alpha={alpha}...', file=sys.stderr, flush=True)
            cache[alpha] = glm_semanal(df, ev, objetivo(df, alpha, cp, cf))
        return cache[alpha]

    def correr(alpha, W, V):
        gh, ga = glm(alpha)
        lh, la = lambdas(df, ev, gh, ga, W, V)
        return em.evaluar(ev, lh, la, 0.0)

    base = correr(1.0, motor.W_GLM_V5, 0.0)
    ok = base['ok']; e = ev[ok]
    # referencia: apertura de bet365 sin margen en los mismos partidos
    idx = np.select([e.res == 'H', e.res == 'D'], [0, 1], 2)
    q = np.array([potencia(o) for o in e[['b365_1', 'b365_x', 'b365_2']].values])
    l1 = em.ll3(q, idx)
    hay_ou = e.b365_over25.notna().values & e.b365_under25.notna().values
    po = np.array([motor.sin_margen_2(a, b) if h_ else np.nan
                   for a, b, h_ in zip(e.b365_over25, e.b365_under25, hay_ou)])
    lo = em.llb(np.nan_to_num(po, nan=.5), ((e.gl + e.gv) > 2.5).values)
    mk = []
    for k in range(4):
        m = ((e.fecha >= em.CORTES[k]) & (e.fecha < em.CORTES[k + 1])).values
        mk.append((l1[m].mean(), lo[m & hay_ou].mean()))

    p(f'Partidos evaluados: {ok.sum():,}   cortes: 22-23, 23-24, 24-25, 25-27')
    p('BASE (motor actual)   ' + '  '.join(f'1X2 {a:.4f} O/U {b:.4f}' for a, b in base['cortes']) + f'  cal={tuple(float(x) for x in base["cal"])}')
    p('bet365 apertura       ' + '  '.join(f'1X2 {a:.4f} O/U {b:.4f}' for a, b in mk))
    p('mercado - base        1X2 ' + ' '.join(f'{a - b:+.4f}' for (a, _), (b, _) in zip(mk, base['cortes']))
      + '          O/U ' + ' '.join(f'{a - b:+.4f}' for (_, a), (_, b) in zip(mk, base['cortes'])))
    p('\nDiferencias frente a la base (negativo = mejor). "4/4" = mejora en las 4 temporadas.')
    for alpha in (1.0, 0.5, 0.0):
        for W in (0.35, 0.6, 1.0):
            for V in (0.0, 0.3):
                if (alpha, W, V) == (1.0, motor.W_GLM_V5, 0.0):
                    continue
                R = correr(alpha, W, V)
                d1 = [a - b for (a, _), (b, _) in zip(R['cortes'], base['cortes'])]
                do = [a - b for (_, a), (_, b) in zip(R['cortes'], base['cortes'])]
                p(f'alpha={alpha:.1f} W={W:.2f} V={V:.1f}   1X2 {" ".join(f"{x:+.4f}" for x in d1)} ({sum(x < 0 for x in d1)}/4)   '
                  f'O/U {" ".join(f"{x:+.4f}" for x in do)} ({sum(x < 0 for x in do)}/4)   cal={tuple(float(x) for x in R["cal"])}')
    (OUT / 'experimento_remates.txt').write_text('\n'.join(lineas) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()

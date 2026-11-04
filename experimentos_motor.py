#!/usr/bin/env python3
"""
experimentos_motor.py -- banco de pruebas walk-forward para cambios del motor.

Cada variante se predice semana a semana (solo datos anteriores), se
recalibra con el bloque de desarrollo (2022-07..2024-06: offsets de 1X2 como
CAL_LOCAL/CAL_EMPATE y desplazamiento del total como CAL_TOTAL) y se evalua
por temporada frente a la variante base, recalibrada igual. Regla de las
notas de motor.py: un cambio entra solo si mejora el log-loss en las 4
temporadas, 1X2 y Mas/Menos por separado, y su parametro no salta entre cortes.

Variantes:
  half_life   vida media del GLM de goles (dias)
  rho         correccion Dixon-Coles de marcadores bajos (0 = Poisson)
  elo_k       K del Elo
  elo_reg     regresion de verano del Elo hacia la base de la liga
  elo_hfa     velocidad de aprendizaje de una ventaja local PROPIA por liga
              (0 = 65 puntos fijos para todas, como ahora)

Uso:  python experimentos_motor.py  [--solo half_life,rho,elo]
Salida: resultados/experimentos_motor.txt
"""
import argparse
import sys
from pathlib import Path

import numpy as np, pandas as pd
from scipy.optimize import minimize
from scipy.stats import poisson

import motor
import actualizar_datos as ad

DIR = Path(__file__).parent
OUT = DIR / 'resultados'
INI = pd.Timestamp('2022-07-01')
CORTES = pd.to_datetime(['2022-07-01', '2023-07-01', '2024-07-01', '2025-07-01', '2026-12-01'])
FIN_DEV = pd.Timestamp('2024-07-01')
BASE = dict(half_life=motor.HALF_LIFE, rho=0.0, elo_k=ad.K_BASE, elo_reg=ad.REGRESION_TEMPORADA, elo_hfa=0.0)


# ------------------------------------------------------------------ Elo variable
def elo_variante(df, k=20, reg=0.25, hfa_lr=0.0, hfa0=65):
    """Copia de actualizar_datos.calcular_elo con K, regresion y ventaja local
    por liga aprendida online: hfa[liga] += hfa_lr * K * (real - esperado)."""
    bases = {n: b for (n, _, _, b) in list(ad.LIGAS.values()) + list(ad.EXTRA.values())}
    elo, liga_eq, temp_eq, hfa = {}, {}, {}, {}
    pre_l, pre_v = np.empty(len(df)), np.empty(len(df))
    for i, f in enumerate(df[['liga', 'temporada', 'local', 'visitante', 'gl', 'gv']].itertuples(index=False)):
        base = bases.get(f.liga, 1500)
        for eq in (f.local, f.visitante):
            if eq not in elo:
                elo[eq] = base
            if temp_eq.get(eq) not in (None, f.temporada) or (liga_eq.get(eq) and liga_eq[eq] != f.liga):
                elo[eq] += (base - elo[eq]) * reg
            liga_eq[eq] = f.liga; temp_eq[eq] = f.temporada
        h = hfa.get(f.liga, hfa0)
        el, ev = elo[f.local], elo[f.visitante]
        pre_l[i], pre_v[i] = el, ev
        esp = 1 / (1 + 10 ** ((ev - el - h) / 400))
        gl, gv = int(f.gl), int(f.gv)
        real = 1.0 if gl > gv else (0.5 if gl == gv else 0.0)
        dif = abs(gl - gv)
        mult = 1.0 if dif <= 1 else (1.5 if dif == 2 else (2 + (dif - 2) / 8))
        c = k * mult * (real - esp)
        elo[f.local] = el + c; elo[f.visitante] = ev - c
        if hfa_lr:
            hfa[f.liga] = h + hfa_lr * k * (real - esp)
    return pre_l, pre_v


# ------------------------------------------------------------------ prediccion
def tot_glm_semanal(df, ev, half):
    """Total del GLM para cada partido evaluado, reajustado cada lunes."""
    tg = pd.Series(np.nan, index=ev.index)
    for t, g in ev.groupby('semana'):
        G = motor.ajustar_glm(df, t, half=half)
        tg.loc[g.index] = [motor.total_glm(G, l, v, lg) or np.nan
                           for l, v, lg in zip(g.local, g.visitante, g.liga)]
    return tg.values


def lambdas(df, ev, tg):
    """lh, la con la formula del motor (sin CAL_TOTAL: se recalibra despues)."""
    lh = np.full(len(ev), np.nan); la = np.full(len(ev), np.nan)
    pos = {ix: k for k, ix in enumerate(ev.index)}
    for t, g in ev.groupby('semana'):
        co = motor.coeficientes(df, t)
        g = g[g.liga.isin(co)]
        C = pd.DataFrame(co).T.loc[g.liga]
        d = (g.elo_l_pre.values - g.elo_v_pre.values) / 100.0
        sup = C.a.values * d + C.b.values
        tot_elo = C.bt.values + C['at'].values * np.abs(d)
        k = np.array([pos[i] for i in g.index])
        tgk = tg[k]
        tot = np.where(np.isnan(tgk), tot_elo, (1 - motor.W_GLM) * tot_elo + motor.W_GLM * tgk)
        lh[k] = (tot + sup) / 2; la[k] = (tot - sup) / 2
    return lh, la


def matriz(lh, la, rho):
    g = np.arange(11)
    M = poisson.pmf(g[None, :], lh[:, None])[:, :, None] * poisson.pmf(g[None, :], la[:, None])[:, None, :]
    if rho:
        M[:, 0, 0] *= np.maximum(1 - lh * la * rho, 0)
        M[:, 0, 1] *= np.maximum(1 + lh * rho, 0)
        M[:, 1, 0] *= np.maximum(1 + la * rho, 0)
        M[:, 1, 1] *= np.maximum(1 - rho, 0)
    return M / M.sum((1, 2), keepdims=True)


def probs(lh, la, rho, dtot=0.0):
    """1X2 (sin calibrar) y Mas 2,5 con el total desplazado dtot goles."""
    lh2 = np.maximum(0.15, lh + dtot / 2); la2 = np.maximum(0.15, la + dtot / 2)
    M = matriz(lh2, la2, rho)
    i, j = np.indices((11, 11))
    q = np.stack([(M * (i > j)).sum((1, 2)), (M * (i == j)).sum((1, 2)), (M * (i < j)).sum((1, 2))], 1)
    over = (M * ((i + j) > 2)).sum((1, 2))
    return q, over


# ------------------------------------------------------------------ evaluacion
def ll3(q, idx): return -np.log(np.clip(q[np.arange(len(q)), idx], 1e-12, 1))
def llb(p, y): p = np.clip(p, 1e-12, 1 - 1e-12); return -np.where(y, np.log(p), np.log(1 - p))


def offsets(q, a, b):
    z = q * np.exp([a, b, 0.0]); return z / z.sum(1, keepdims=True)


def evaluar(ev, lh, la, rho):
    ok = ~np.isnan(lh)
    e, lh, la = ev[ok], lh[ok], la[ok]
    idx = np.select([e.res == 'H', e.res == 'D'], [0, 1], 2)
    over = ((e.gl + e.gv) > 2.5).values
    dev = (e.fecha < FIN_DEV).values
    # desplazamiento del total ajustado en desarrollo (como CAL_TOTAL)
    f_ou = lambda x: llb(probs(lh[dev], la[dev], rho, x[0])[1], over[dev]).mean()
    dt = minimize(f_ou, [0.1], method='Nelder-Mead').x[0]
    q, po = probs(lh, la, rho, dt)
    f_12 = lambda x: ll3(offsets(q[dev], *x), idx[dev]).mean()
    a, b = minimize(f_12, [0.05, 0.13], method='Nelder-Mead').x
    q = offsets(q, a, b)
    l1, lo = ll3(q, idx), llb(po, over)
    por = []
    for k in range(4):
        m = ((e.fecha >= CORTES[k]) & (e.fecha < CORTES[k + 1])).values
        por.append((l1[m].mean(), lo[m].mean()))
    return dict(cal=(round(a, 3), round(b, 3), round(dt, 3)), cortes=por, ok=ok)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--solo', default='half_life,rho,elo')
    grupos = ap.parse_args().solo.split(',')
    OUT.mkdir(exist_ok=True)
    df = motor.cargar(DIR / 'datos' / 'partidos.csv').sort_values('fecha', kind='stable').reset_index(drop=True)
    ev = df[(df.fecha >= INI) & df.b365_1.notna()].copy()
    ev['semana'] = ev.fecha.dt.to_period('W-SUN').dt.start_time

    lineas = []
    def p(s=''):
        print(s); lineas.append(s); sys.stdout.flush()

    cache_tg = {}
    def tg_de(half):
        if half not in cache_tg:
            print(f'  GLM vida media {half}...', file=sys.stderr)
            cache_tg[half] = tot_glm_semanal(df, ev, half)
        return cache_tg[half]

    def correr(nombre, half=BASE['half_life'], rho=0.0, elo=None):
        d = df
        if elo is not None:
            d = df.copy()
            d['elo_l_pre'], d['elo_v_pre'] = elo_variante(df, *elo)
        e = ev.copy(); e['elo_l_pre'] = d.loc[ev.index, 'elo_l_pre']; e['elo_v_pre'] = d.loc[ev.index, 'elo_v_pre']
        lh, la = lambdas(d, e, tg_de(half))
        return evaluar(e, lh, la, rho)

    base = correr('base')
    p(f'Partidos evaluados: {base["ok"].sum():,}   cortes: 22-23, 23-24, 24-25, 25-27')
    p(f'BASE (motor v4)  cal={base["cal"]}  ' + '  '.join(f'1X2 {a:.4f} O/U {b:.4f}' for a, b in base['cortes']))
    p('Diferencias frente a la base (negativo = mejor). "4/4" = mejora en las 4 temporadas.\n')

    def fila(nombre, R):
        d1 = [a - b for (a, _), (b, _) in zip(R['cortes'], base['cortes'])]
        do = [a - b for (_, a), (_, b) in zip(R['cortes'], base['cortes'])]
        n1, no = sum(x < 0 for x in d1), sum(x < 0 for x in do)
        p(f'{nombre:28s} 1X2 {" ".join(f"{x:+.4f}" for x in d1)} ({n1}/4)   '
          f'O/U {" ".join(f"{x:+.4f}" for x in do)} ({no}/4)   cal={R["cal"]}')

    if 'half_life' in grupos:
        p('--- vida media del GLM (dias) ---')
        for h in (120, 270, 365, 450, 540):
            fila(f'half_life={h}', correr('hl', half=float(h)))
    if 'rho' in grupos:
        p('--- Dixon-Coles rho ---')
        for r in (-0.03, -0.06, -0.10, -0.15):
            fila(f'rho={r}', correr('rho', rho=r))
    if 'elo' in grupos:
        p('--- Elo: K ---')
        for k in (15, 25, 30):
            fila(f'elo_k={k}', correr('k', elo=(k, BASE['elo_reg'], 0.0)))
        p('--- Elo: regresion de verano ---')
        for rg in (0.15, 0.35):
            fila(f'elo_reg={rg}', correr('reg', elo=(BASE['elo_k'], rg, 0.0)))
        p('--- Elo: ventaja local propia por liga (aprendizaje online) ---')
        for lr in (0.02, 0.05, 0.10):
            fila(f'elo_hfa_lr={lr}', correr('hfa', elo=(BASE['elo_k'], BASE['elo_reg'], lr)))
    (OUT / 'experimentos_motor.txt').write_text('\n'.join(lineas), encoding='utf-8')


if __name__ == '__main__':
    main()

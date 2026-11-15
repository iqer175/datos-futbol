#!/usr/bin/env python3
"""
remates_equipo.py -- remates y remates a puerta esperados de cada equipo para el simulador.

Validado en experimento_remates_equipo.py (ajuste 2025-26, prueba 2026-27 con todo congelado;
resultados/remates_equipo.txt, 2026-11-11):
  - perfil: remates a favor del equipo y en contra del rival, cada partido dividido por la media
    de su liga en ese momento (ultimos VENTANA_LIGA partidos) y re-escalado a la media actual.
    En 2026-27 los datos vienen de ESPN y hay ~2,5 remates mas por partido que con la fuente
    anterior: medir en relativo evita arrastrar el nivel viejo.
  - dominio: media = perfil + a + b * (Elo local - Elo visitante)/100, por separado local/visitante.
  - simulacion (simulador.simular): binomial negativa con dispersion ALPHA, correlacion RHO entre
    los dos equipos (copula gaussiana) y a puerta ~ binomial(remates, precision del equipo).
"""
import numpy as np
import pandas as pd

import motor

VENTANA_LIGA = 120
COEF = dict(alpha=0.0489, rho=-0.220,
            ta_l=1.282, tb_l=1.009, ta_v=-1.220, tb_v=-0.753,
            pa_l=0.439, pb_l=0.437, pa_v=-0.405, pb_v=-0.281)
MIN_PARTIDOS = 3


def _tabla(B, liga):
    """Partidos de la liga con remates, vistos desde cada equipo y relativos a la media de la liga."""
    cache = B.__dict__.setdefault('_rem_eq', {})
    if liga in cache:
        return cache[liga]
    df = B.df
    g = df[(df.liga == liga) & df.tiros_l.notna() & df.tiros_v.notna()
           & df.tiros_p_l.notna() & df.tiros_p_v.notna()].sort_values('fecha')
    if len(g) < 50:
        cache[liga] = None
        return None
    mt = ((g.tiros_l + g.tiros_v) / 2).rolling(VENTANA_LIGA, min_periods=50).mean().shift()
    mp = ((g.tiros_p_l + g.tiros_p_v) / 2).rolling(VENTANA_LIGA, min_periods=50).mean().shift()
    L = pd.concat([
        pd.DataFrame(dict(eq=g.local, fecha=g.fecha, tf=g.tiros_l / mt, tc=g.tiros_v / mt,
                          pf=g.tiros_p_l / mp, pc=g.tiros_p_v / mp)),
        pd.DataFrame(dict(eq=g.visitante, fecha=g.fecha, tf=g.tiros_v / mt, tc=g.tiros_l / mt,
                          pf=g.tiros_p_v / mp, pc=g.tiros_p_l / mp))]).dropna().sort_values('fecha')
    cache[liga] = (g, {eq: x for eq, x in L.groupby('eq')})
    return cache[liga]


def _nivel(g, h):
    x = g[g.fecha < h].tail(VENTANA_LIGA)
    return float(((x.tiros_l + x.tiros_v) / 2).mean()), float(((x.tiros_p_l + x.tiros_p_v) / 2).mean())


def _perfil(idx, eq, h):
    x = idx.get(eq)
    if x is None:
        return None
    x = x[x.fecha < h].tail(motor.MAX_N)
    if len(x) < MIN_PARTIDOS:
        return None
    w = 0.5 ** ((h - x.fecha).dt.days.values / motor.HALF_LIFE)
    v = x[['tf', 'tc', 'pf', 'pc']].values
    return ((w[:, None] * v).sum(0) + motor.K_SHRINK) / (w.sum() + motor.K_SHRINK)   # encogido hacia 1


def medias(B, local, visit, liga, elo_l, elo_v, hasta=None):
    """dict(ml, mv, ql, qv, n) con remates y a puerta esperados, o None si no hay datos."""
    t = _tabla(B, liga)
    if t is None:
        return None
    g, idx = t
    h = pd.Timestamp(hasta) if hasta else B.hasta
    a, b = _perfil(idx, local, h), _perfil(idx, visit, h)
    if a is None or b is None:
        return None
    nt, npu = _nivel(g, h)
    gap = (elo_l - elo_v) / 100
    k = COEF
    ml = max(nt * (a[0] + b[1]) / 2 + k['ta_l'] + k['tb_l'] * gap, 3.0)
    mv = max(nt * (b[0] + a[1]) / 2 + k['ta_v'] + k['tb_v'] * gap, 3.0)
    ql = max(npu * (a[2] + b[3]) / 2 + k['pa_l'] + k['pb_l'] * gap, 0.8)
    qv = max(npu * (b[2] + a[3]) / 2 + k['pa_v'] + k['pb_v'] * gap, 0.8)
    return dict(ml=ml, mv=mv, ql=min(ql, 0.9 * ml), qv=min(qv, 0.9 * mv), nivel_liga=nt)


def simular(rem, n, rng):
    """Remates (rl, rv) y a puerta (pl, pv) de n partidos simulados."""
    from scipy.stats import nbinom, norm
    z1 = rng.standard_normal(n)
    z2 = COEF['rho'] * z1 + np.sqrt(1 - COEF['rho'] ** 2) * rng.standard_normal(n)
    nb = 1 / COEF['alpha']
    out = {}
    for lado, z, mt, mp in (('l', z1, rem['ml'], rem['ql']), ('v', z2, rem['mv'], rem['qv'])):
        u = np.clip(norm.cdf(z), 1e-12, 1 - 1e-12)
        t = nbinom.ppf(u, nb, nb / (nb + mt)).astype(np.int64)
        out['r' + lado] = t
        out['p' + lado] = rng.binomial(t, mp / mt)
    return out

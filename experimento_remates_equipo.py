#!/usr/bin/env python3
"""
experimento_remates_equipo.py -- remates y remates a puerta de cada equipo (y del partido).

Modelo (mismo esquema que los corners, experimento_corners.py):
  perfil   = (remates a favor del equipo + remates en contra del rival) / 2, medias ponderadas en la
             liga (vida media HALF_LIFE, ultimos MAX_N, encogidas K_SHRINK hacia la media de la liga),
             con datos ANTERIORES a cada partido
  dominio  = media = perfil + a + b * (Elo local - Elo visitante)/100   (por separado local/visitante)
  remates  ~ binomial negativa (var = mu + alpha mu^2), alpha medido en el ajuste
  a puerta ~ binomial(remates, precision) con precision = media a puerta / media de remates del equipo
  partido  = suma de los dos equipos, con correlacion negativa entre ellos (copula gaussiana rho,
             medida en el ajuste: cuando uno domina, el otro remata menos)

Protocolo: ajuste (a, b, alpha) en 2025-26; prueba en 2026-27 con todo congelado. Se compara
log-loss frente a dos referencias: media de la liga, y perfil sin dominio.

Uso:  python experimento_remates_equipo.py
Salida: resultados/remates_equipo.txt y los coeficientes para simulador.py
"""
from pathlib import Path

import numpy as np
import pandas as pd

import motor

DIR = Path(__file__).parent
N_MC = 6000
VENTANA_LIGA = 120      # media de la liga: ultimos 120 partidos (el nivel de remates cambia entre temporadas)
RNG = np.random.default_rng(7)


def perfiles(df):
    """Para cada partido: perfiles previos de remates/a puerta a favor y en contra de los dos equipos."""
    df = df[df.tiros_l.notna() & df.tiros_v.notna() & df.tiros_p_l.notna() & df.tiros_p_v.notna()].copy()
    df = df.sort_values('fecha').reset_index(drop=True)
    L = pd.concat([
        pd.DataFrame(dict(i=df.index, eq=df.local, liga=df.liga, fecha=df.fecha,
                          tf=df.tiros_l, tc=df.tiros_v, pf=df.tiros_p_l, pc=df.tiros_p_v)),
        pd.DataFrame(dict(i=df.index, eq=df.visitante, liga=df.liga, fecha=df.fecha,
                          tf=df.tiros_v, tc=df.tiros_l, pf=df.tiros_p_v, pc=df.tiros_p_l))])
    # medias de liga (por equipo y partido) con datos anteriores: media expansiva por fecha
    liga_media = {}
    for lg, g in df.groupby('liga'):
        g = g.sort_values('fecha')
        t = ((g.tiros_l + g.tiros_v) / 2).rolling(VENTANA_LIGA, min_periods=50).mean().shift()
        p = ((g.tiros_p_l + g.tiros_p_v) / 2).rolling(VENTANA_LIGA, min_periods=50).mean().shift()
        liga_media[lg] = pd.DataFrame({'t': t.values, 'p': p.values}, index=g.index)
    LM = pd.concat(liga_media.values())
    out = {}
    for (eq, lg), g in L.groupby(['eq', 'liga']):
        g = g.sort_values('fecha')
        f = g.fecha.values; v = g[['tf', 'tc', 'pf', 'pc']].values
        # cada partido, relativo a la media de su liga en ese momento (el nivel general cambia: 2026-27)
        bl = np.c_[LM.loc[g.i, 't'].values, LM.loc[g.i, 't'].values, LM.loc[g.i, 'p'].values, LM.loc[g.i, 'p'].values]
        rel = v / bl
        for k in range(len(g)):
            i = g.i.iloc[k]
            bt, bp = LM.loc[i, 't'], LM.loc[i, 'p']
            if not np.isfinite(bt):
                continue
            ini = max(0, k - motor.MAX_N)
            w = 0.5 ** ((f[k] - f[ini:k]).astype('timedelta64[D]').astype(float) / motor.HALF_LIFE)
            x = rel[ini:k]; ok = np.isfinite(x).all(1)
            base = np.array([bt, bt, bp, bp])
            prof = base * ((w[ok, None] * x[ok]).sum(0) + motor.K_SHRINK) / (w[ok].sum() + motor.K_SHRINK)
            out[(i, eq)] = (*prof, k, bt, bp)
    filas = []
    for r in df.itertuples():
        a, b = out.get((r.Index, r.local)), out.get((r.Index, r.visitante))
        if a is None or b is None:
            continue
        filas.append(dict(fecha=r.fecha, liga=r.liga, gap=(r.elo_l_pre - r.elo_v_pre) / 100,
                          n_l=a[4], n_v=b[4],
                          pl=(a[0] + b[1]) / 2, pv=(b[0] + a[1]) / 2,          # remates esperados (perfil)
                          ql=(a[2] + b[3]) / 2, qv=(b[2] + a[3]) / 2,          # a puerta esperados (perfil)
                          liga_t=a[5], liga_p=a[6],
                          rl=r.tiros_l, rv=r.tiros_v, sl=r.tiros_p_l, sv=r.tiros_p_v))
    return pd.DataFrame(filas)


def alpha_nb(y, mu):
    return max(float(np.mean((y - mu) ** 2 - mu) / np.mean(mu ** 2)), 1e-4)


def sim_par(ml, mv, ql, qv, alpha, rho):
    """Remates (NB con copula gaussiana rho entre equipos) y a puerta (binomial): arrays (n, N_MC)."""
    from scipy.stats import nbinom, norm
    z1 = RNG.standard_normal((len(ml), N_MC))
    z2 = rho * z1 + np.sqrt(1 - rho ** 2) * RNG.standard_normal(z1.shape)
    n = 1 / alpha
    out = []
    for z, mt, mp in ((z1, ml, ql), (z2, mv, qv)):
        u = np.clip(norm.cdf(z), 1e-9, 1 - 1e-9)
        t = nbinom.ppf(u, n, (n / (n + mt))[:, None]).astype(int)
        s = RNG.binomial(t, np.clip(mp / mt, 0.05, 0.95)[:, None])
        out.append((t, s))
    return out


def llb(p, y):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return float(-np.mean(np.where(y, np.log(p), np.log(1 - p))))


def main():
    df = motor.cargar(DIR / 'datos' / 'partidos.csv')
    P = perfiles(df)
    P = P[(P.n_l >= 3) & (P.n_v >= 3)]
    A = P[(P.fecha >= '2025-08-01') & (P.fecha < '2026-07-01')]
    T = P[P.fecha >= '2026-07-01'].copy()
    co = {}
    for lado in ('l', 'v'):
        co[f'ta_{lado}'], co[f'tb_{lado}'] = np.polyfit(A.gap, A[f'r{lado}'] - A[f'p{lado}'], 1)[::-1]
        co[f'pa_{lado}'], co[f'pb_{lado}'] = np.polyfit(A.gap, A[f's{lado}'] - A[f'q{lado}'], 1)[::-1]
    def medias(X, dom=True):
        if not dom:
            return X.pl.values, X.pv.values, X.ql.values, X.qv.values
        return (np.maximum(X.pl + co['ta_l'] + co['tb_l'] * X.gap, 3).values,
                np.maximum(X.pv + co['ta_v'] + co['tb_v'] * X.gap, 3).values,
                np.maximum(X.ql + co['pa_l'] + co['pb_l'] * X.gap, 0.8).values,
                np.maximum(X.qv + co['pa_v'] + co['pb_v'] * X.gap, 0.8).values)
    ml, mv, _, _ = medias(A)
    alpha = alpha_nb(np.r_[A.rl.values, A.rv.values], np.r_[ml, mv])
    rho = float(np.corrcoef(A.rl.values - ml, A.rv.values - mv)[0, 1])
    L = [f'Ajuste 2025-26 ({len(A):,} partidos): alpha NB remates = {alpha:.4f}, rho entre equipos = {rho:+.3f}',
         '  remates  local  real-perfil = {ta_l:+.3f} {tb_l:+.3f}·gap   visitante {ta_v:+.3f} {tb_v:+.3f}·gap'.format(**co),
         '  a puerta local  real-perfil = {pa_l:+.3f} {pb_l:+.3f}·gap   visitante {pa_v:+.3f} {pb_v:+.3f}·gap'.format(**co),
         f'Prueba 2026-27 ({len(T):,} partidos), coeficientes congelados. LL = log-loss (menor es mejor).', '']

    modelos = {
        'liga': (T.liga_t.values * 1.08, T.liga_t.values * 0.92, T.liga_p.values * 1.08, T.liga_p.values * 0.92),
        'perfil': medias(T, dom=False),
        'perfil+dominio': medias(T, dom=True),
    }
    sims = {k: tuple(sim_par(m[0], m[1], m[2], m[3], alpha, rho if k == 'perfil+dominio' else 0.0))
            for k, m in modelos.items()}
    lineas = [('remates local', 'rl', [8.5, 10.5, 12.5, 14.5, 16.5]),
              ('remates visitante', 'rv', [6.5, 8.5, 10.5, 12.5, 14.5]),
              ('a puerta local', 'sl', [2.5, 3.5, 4.5, 5.5, 6.5]),
              ('a puerta visitante', 'sv', [1.5, 2.5, 3.5, 4.5, 5.5]),
              ('remates partido', 'rt', [19.5, 21.5, 23.5, 25.5, 27.5]),
              ('a puerta partido', 'st', [6.5, 7.5, 8.5, 9.5, 10.5])]
    T['rt'] = T.rl + T.rv; T['st'] = T.sl + T.sv
    cab = f'{"mercado":28s} {"real":>6s}' + ''.join(f' {k[:14]:>14s}' for k in modelos) + ''.join(f' {"LL " + k[:11]:>14s}' for k in modelos)
    L.append(cab)
    tot = {k: [] for k in modelos}
    for nom, col, lns in lineas:
        for ln in lns:
            y = T[col].values > ln
            fila = f'{nom + " más " + str(ln):28s} {y.mean()*100:5.1f}%'
            lls = []
            for k, ((tl, sl), (tv, sv)) in sims.items():
                x = {'rl': tl, 'rv': tv, 'sl': sl, 'sv': sv, 'rt': tl + tv, 'st': sl + sv}[col]
                p = (x > ln).mean(1)
                fila += f' {p.mean()*100:13.1f}%'
                lls.append(llb(p, y)); tot[k].append(lls[-1])
            fila += ''.join(f' {v:14.4f}' for v in lls)
            L.append(fila)
    L += ['', 'Log-loss medio de todas las líneas: ' + ', '.join(f'{k} {np.mean(v):.4f}' for k, v in tot.items())]
    # calibracion por decil del modelo final en remates por equipo (líneas 10.5 y 4.5 a puerta)
    (tl, sl), (tv, sv) = sims['perfil+dominio']
    for nom, x, y, ln in (('remates local 10.5', tl, T.rl.values, 10.5), ('a puerta visitante 3.5', sv, T.sv.values, 3.5)):
        p = (x > ln).mean(1); q = pd.qcut(p, 5, duplicates='drop')
        g = pd.DataFrame({'p': p, 'y': y > ln, 'q': q}).groupby('q', observed=True).agg(n=('y', 'size'), pred=('p', 'mean'), real=('y', 'mean'))
        L += ['', f'Calibración por quintiles, {nom}:', (g.assign(pred=g.pred * 100, real=g.real * 100)).round(1).to_string()]
    L += ['', 'Coeficientes para remates_equipo.py:',
          f'VENTANA_LIGA = {VENTANA_LIGA}; COEF = dict(alpha={alpha:.4f}, rho={rho:.3f}, '
          + ', '.join(f'{k}={v:.3f}' for k, v in co.items()) + ')']
    txt = '\n'.join(L); print(txt)
    (DIR / 'resultados').mkdir(exist_ok=True)
    (DIR / 'resultados' / 'remates_equipo.txt').write_text(txt + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()

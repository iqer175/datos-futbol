#!/usr/bin/env python3
"""
backtest_clv.py -- ¿el motor anticipa el movimiento de la linea?

Walk-forward semanal sobre las ligas con cuota previa de bet365 (las 22 de
football-data con estadisticas; las EXTRA no traen apertura y quedan fuera).

Para cada semana t (lunes):
  - coeficientes por liga y GLM de goles ajustados SOLO con partidos < t
  - Elo: elo_*_pre del propio partido (solo usa partidos anteriores)
  - probabilidad del motor = la misma formula que motor.analizar()

Precio de entrada : b365_1/x/2  (cuota previa de bet365 que trae football-data)
Referencia        : avgc_1/x/2  (cierre medio del mercado) sin margen, metodo potencia
CLV de una apuesta: cuota_entrada * p_cierre_sin_margen - 1

Comparacion clave: el CLV de TODAS las apuestas posibles a la cuota previa de
bet365 es negativo (margen de bet365 frente al cierre medio). El motor solo
aporta algo si las apuestas que elige tienen un CLV claramente MEJOR que esa
linea base, y si el CLV medio es > 0 con intervalo al 95 % que excluya el 0.

Bloques:
  calentamiento  2021-07 .. 2022-06  (Elo recien sembrado, no se evalua)
  desarrollo     2022-07 .. 2024-06  (aqui se eligen umbral y peso w)
  reservado      2024-07 .. hoy      (evaluacion con todo congelado)
  Aviso: CAL_LOCAL/CAL_EMPATE/CAL_TOTAL de motor.py se midieron sobre
  2025-08..2026-09, que cae dentro del reservado. Por eso se reportan las dos
  versiones (calibrada y sin calibrar).

Uso:
    python backtest_clv.py
Salida en resultados/:
    backtest_predicciones.csv   una fila por partido con todas las probabilidades
    backtest_resumen.txt        tablas impresas
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
from scipy.stats import poisson

import motor

DIR = Path(__file__).parent
OUT = DIR / 'resultados'
INI_EVAL = pd.Timestamp('2022-07-01')
INI_RESERVADO = pd.Timestamp('2024-07-01')
UMBRALES = [0.0, 0.03, 0.05, 0.08, 0.12]
SEMILLA = 7


# ------------------------------------------------------------- sin margen
def potencia(odds):
    """Quita el margen con el metodo de potencia: sum (1/o)^k = 1. Vectorizado."""
    inv = 1.0 / odds
    lo, hi = np.full(len(inv), 0.2), np.full(len(inv), 5.0)
    for _ in range(60):
        k = (lo + hi) / 2
        s = (inv ** k[:, None]).sum(1)
        hi = np.where(s < 1, k, hi)      # suma < 1 -> k demasiado grande
        lo = np.where(s >= 1, k, lo)
    p = inv ** ((lo + hi) / 2)[:, None]
    return p / p.sum(1, keepdims=True)


def multiplicativo(odds):
    inv = 1.0 / odds
    return inv / inv.sum(1, keepdims=True)


# ------------------------------------------------------------- motor vectorizado
def probs_1x2(lh, la, calibrar):
    g = np.arange(11)
    ph = poisson.pmf(g[None, :], lh[:, None])
    pa = poisson.pmf(g[None, :], la[:, None])
    M = ph[:, :, None] * pa[:, None, :]
    M /= M.sum((1, 2), keepdims=True)
    i, j = np.indices((11, 11))
    p1 = (M * (i > j)).sum((1, 2)); px = (M * (i == j)).sum((1, 2)); p2 = (M * (i < j)).sum((1, 2))
    q = np.stack([p1, px, p2], 1)
    if calibrar:
        q = q * np.exp([motor.CAL_LOCAL, motor.CAL_EMPATE, 0.0])
        q /= q.sum(1, keepdims=True)
    return q


def predecir(df):
    """Probabilidades del motor partido a partido, sin mirar el futuro."""
    ev = df[(df.fecha >= INI_EVAL) & df.b365_1.notna() & df.b365_x.notna() & df.b365_2.notna()
            & df.avgc_1.notna() & df.avgc_x.notna() & df.avgc_2.notna()].copy()
    ev['semana'] = ev.fecha.dt.to_period('W-SUN').dt.start_time
    trozos = []
    semanas = sorted(ev.semana.unique())
    for n, t in enumerate(semanas, 1):
        g = ev[ev.semana == t].copy()
        co = motor.coeficientes(df, t)
        G = motor.ajustar_glm(df, t)
        g = g[g.liga.isin(co)]
        if not len(g):
            continue
        C = pd.DataFrame(co).T.loc[g.liga]
        d = (g.elo_l_pre.values - g.elo_v_pre.values) / 100.0
        sup = C.a.values * d + C.b.values
        tot_elo = C.bt.values + C['at'].values * np.abs(d)
        tg = np.array([motor.total_glm(G, l, v, lg) or np.nan
                       for l, v, lg in zip(g.local, g.visitante, g.liga)])
        tot = np.where(np.isnan(tg), tot_elo, (1 - motor.W_GLM) * tot_elo + motor.W_GLM * tg)
        for cal, suf in ((True, 'cal'), (False, 'raw')):
            tt = tot + (motor.CAL_TOTAL if cal else 0.0)
            lh = np.maximum(0.15, (tt + sup) / 2); la = np.maximum(0.15, (tt - sup) / 2)
            q = probs_1x2(lh, la, cal)
            g[f'pm1_{suf}'], g[f'pmx_{suf}'], g[f'pm2_{suf}'] = q.T
            g[f'pmo_{suf}'] = 1 - poisson.cdf(2, lh + la)     # Mas 2,5
        trozos.append(g)
        if n % 25 == 0:
            print(f'  semana {n}/{len(semanas)}  {t.date()}', file=sys.stderr)
    r = pd.concat(trozos)
    po = potencia(r[['b365_1', 'b365_x', 'b365_2']].values)
    pc = potencia(r[['avgc_1', 'avgc_x', 'avgc_2']].values)
    pcm = multiplicativo(r[['avgc_1', 'avgc_x', 'avgc_2']].values)
    for k, s in enumerate('1x2'):
        r[f'pa{s}'] = po[:, k]      # apertura bet365 sin margen
        r[f'pc{s}'] = pc[:, k]      # cierre medio sin margen (potencia)
        r[f'pcm{s}'] = pcm[:, k]    # cierre medio sin margen (multiplicativo)
    r['bloque'] = np.where(r.fecha >= INI_RESERVADO, 'reservado', 'desarrollo')
    return r


# ------------------------------------------------------------- metricas
def logloss(r, pref):
    p = np.select([r.res == 'H', r.res == 'D'], [r[f'{pref}1'], r[f'{pref}x']], r[f'{pref}2'])
    return float(-np.log(np.clip(p, 1e-9, 1)).mean())


def mezcla(r, w, suf):
    """p ∝ p_motor^w · p_apertura^(1-w): lo que se sabe en el momento de apostar."""
    m = np.stack([r[f'pm1_{suf}'], r[f'pmx_{suf}'], r[f'pm2_{suf}']], 1)
    a = np.stack([r.pa1, r.pax, r.pa2], 1)
    q = m ** w * a ** (1 - w)
    return q / q.sum(1, keepdims=True)


def ll_mat(q, res):
    idx = np.select([res == 'H', res == 'D'], [0, 1], 2)
    return float(-np.log(q[np.arange(len(q)), idx]).mean())


def apuestas(r, q, umbral):
    """Una fila por (partido, resultado) donde p·cuota_previa - 1 > umbral."""
    filas = []
    for k, (s, code) in enumerate(zip('1x2', 'HDA')):
        o = r[f'b365_{s}'].values
        edge = q[:, k] * o - 1
        sel = edge > umbral
        if not sel.any():
            continue
        x = r.loc[sel, ['fecha', 'semana', 'liga', 'local', 'visitante', 'res', 'bloque']].copy()
        x['sel'] = s; x['cuota'] = o[sel]; x['edge'] = edge[sel]
        x['clv'] = o[sel] * r[f'pc{s}'].values[sel] - 1
        x['clv_mult'] = o[sel] * r[f'pcm{s}'].values[sel] - 1
        x['gana'] = (r.res.values[sel] == code)
        x['beneficio'] = np.where(x.gana, x.cuota - 1, -1.0)
        filas.append(x)
    return pd.concat(filas) if filas else pd.DataFrame()


def ic_semanas(b, col, n=2000):
    """IC 95 % de la media por bootstrap de semanas completas."""
    rng = np.random.default_rng(SEMILLA)
    g = b.groupby('semana')[col].agg(['sum', 'size'])
    s, c = g['sum'].values, g['size'].values
    k = rng.integers(0, len(g), (n, len(g)))
    m = s[k].sum(1) / c[k].sum(1)
    return np.percentile(m, [2.5, 97.5])


def base_clv(r):
    """CLV de apostar TODO a la cuota previa de bet365: la linea base a batir."""
    v = [r[f'b365_{s}'] * r[f'pc{s}'] - 1 for s in '1x2']
    return float(pd.concat(v).mean())


def resumen_apuestas(b, r):
    if not len(b):
        return 'sin apuestas'
    lo, hi = ic_semanas(b, 'clv')
    return (f"n={len(b):5d}  CLV {b.clv.mean()*100:+.2f}% [{lo*100:+.2f}, {hi*100:+.2f}]  "
            f"CLV>0 {(b.clv > 0).mean()*100:4.1f}%  ROI {b.beneficio.mean()*100:+.1f}%  "
            f"(base todas {base_clv(r)*100:+.2f}%)")


def main():
    OUT.mkdir(exist_ok=True)
    df = motor.cargar(DIR / 'datos' / 'partidos.csv')
    print('Prediciendo walk-forward...', file=sys.stderr)
    r = predecir(df)
    r.to_csv(OUT / 'backtest_predicciones.csv', index=False)

    lineas = []
    def p(s=''):
        print(s); lineas.append(s)

    p(f'Partidos evaluados: {len(r):,}  ({r.fecha.min().date()} a {r.fecha.max().date()})')
    p(r.groupby('bloque').size().to_string())

    # ---- 1. log-loss -----------------------------------------------------
    p('\n=== 1. LOG-LOSS 1X2 (menor es mejor) ===')
    for bl, g in r.groupby('bloque'):
        p(f'[{bl}]  motor_cal {ll_mat(g[["pm1_cal","pmx_cal","pm2_cal"]].values, g.res.values):.4f}'
          f'  motor_raw {ll_mat(g[["pm1_raw","pmx_raw","pm2_raw"]].values, g.res.values):.4f}'
          f'  apertura_b365 {logloss(g, "pa"):.4f}  cierre_medio {logloss(g, "pc"):.4f}')

    # ---- 2. ¿aporta el motor algo sobre la apertura? peso w -------------
    p('\n=== 2. MEZCLA motor + apertura bet365: p ∝ motor^w · apertura^(1-w) ===')
    dev, res_ = r[r.bloque == 'desarrollo'], r[r.bloque == 'reservado']
    ws = np.round(np.arange(0, 0.61, 0.05), 2)
    mejor = {}
    for suf in ('cal', 'raw'):
        lld = [ll_mat(mezcla(dev, w, suf), dev.res.values) for w in ws]
        llr = [ll_mat(mezcla(res_, w, suf), res_.res.values) for w in ws]
        wb = ws[int(np.argmin(lld))]; mejor[suf] = wb
        p(f'motor_{suf}: w optimo en desarrollo = {wb}')
        p('  w     ' + ' '.join(f'{w:6.2f}' for w in ws))
        p('  dev   ' + ' '.join(f'{x:.4f}' for x in lld))
        p('  res   ' + ' '.join(f'{x:.4f}' for x in llr))
    p('(w=0 es la apertura sola. Si el minimo esta en w=0, el motor no aporta informacion.)')

    # ---- 3. apuestas y CLV ----------------------------------------------
    for etiqueta, qfun in (
            ('motor_cal puro', lambda g: g[['pm1_cal', 'pmx_cal', 'pm2_cal']].values),
            (f'mezcla w={mejor["cal"]} (motor_cal)', lambda g: mezcla(g, mejor['cal'], 'cal')),
            ('motor_raw puro', lambda g: g[['pm1_raw', 'pmx_raw', 'pm2_raw']].values)):
        p(f'\n=== 3. APUESTAS: {etiqueta}  (valor = p·cuota_previa_b365 - 1 > umbral) ===')
        for bl, g in r.groupby('bloque'):
            p(f'[{bl}]')
            q = qfun(g)
            for u in UMBRALES:
                p(f'  umbral {u*100:4.0f}%  ' + resumen_apuestas(apuestas(g, q, u), g))

    # ---- 4. desglose del reservado, umbral 5 % -----------------------------
    # Si w optimo es 0 la mezcla no apuesta nada: se desglosa el motor puro.
    u = 0.05
    if mejor['cal'] > 0:
        q, etiqueta = mezcla(res_, mejor['cal'], 'cal'), f'mezcla w={mejor["cal"]}'
    else:
        q, etiqueta = res_[['pm1_cal', 'pmx_cal', 'pm2_cal']].values, 'motor_cal puro'
    b = apuestas(res_, q, u)
    if len(b):
        p(f'\n=== 4. RESERVADO por liga  ({etiqueta}, umbral {u*100:.0f}%) ===')
        p('clv_menos_base > 0 significa que el motor elige apuestas mejores que apostar al azar en esa liga')
        base_liga = {lg: base_clv(g) for lg, g in res_.groupby('liga')}
        t = b.groupby('liga').agg(n=('clv', 'size'), clv=('clv', 'mean'),
                                  clv_pos=('clv', lambda x: (x > 0).mean()), roi=('beneficio', 'mean'))
        t['base'] = pd.Series(base_liga)
        t['clv_menos_base'] = t.clv - t.base
        p((t.sort_values('clv', ascending=False) * [1, 100, 100, 100, 100, 100]).round(2).to_string())
        p('\n--- por seleccion ---')
        p((b.groupby('sel').agg(n=('clv', 'size'), clv=('clv', 'mean'), roi=('beneficio', 'mean'))
           * [1, 100, 100]).round(2).to_string())
        p('\n--- por tramo de cuota ---')
        b['tramo'] = pd.cut(b.cuota, [1, 1.6, 2.2, 3.0, 4.5, 50])
        p((b.groupby('tramo', observed=True).agg(n=('clv', 'size'), clv=('clv', 'mean'),
                                                 roi=('beneficio', 'mean'))
           * [1, 100, 100]).round(2).to_string())
        p(f'\nCLV con cierre sin margen multiplicativo (comprobacion): {b.clv_mult.mean()*100:+.2f}%')

    # ---- 5. ¿anticipa el motor el movimiento de la linea? ----------------
    # Pendiente de (cierre - apertura) sobre (motor - apertura), en puntos de
    # probabilidad. > 0: cuando el motor discrepa, la linea se mueve hacia el.
    # 1 seria anticipar todo el movimiento; 0, no anticipar nada.
    p('\n=== 5. MOVIMIENTO DE LINEA: pendiente de (cierre-apertura) sobre (motor-apertura) ===')
    filas = []
    for (bl, lg), g in r.groupby(['bloque', 'liga']):
        x = np.concatenate([g[f'pm{s}_cal'] - g[f'pa{s}'] for s in '12'])
        y = np.concatenate([g[f'pc{s}'] - g[f'pa{s}'] for s in '12'])
        b1 = np.polyfit(x, y, 1)[0]
        se = np.sqrt(np.sum((y - b1 * x - (y - b1 * x).mean())**2) / (len(x) - 2) / np.sum((x - x.mean())**2))
        filas.append(dict(bloque=bl, liga=lg, n=len(g), pendiente=b1, t=b1 / se,
                          mov_medio_pts=np.abs(y).mean() * 100))
    m5 = pd.DataFrame(filas).pivot(index='liga', columns='bloque', values=['pendiente', 't'])
    m5.columns = [f'{a}_{b[:3]}' for a, b in m5.columns]
    p(m5.sort_values('pendiente_res', ascending=False).round(3).to_string())
    x = np.concatenate([r[f'pm{s}_cal'] - r[f'pa{s}'] for s in '12'])
    y = np.concatenate([r[f'pc{s}'] - r[f'pa{s}'] for s in '12'])
    p(f'GLOBAL pendiente {np.polyfit(x, y, 1)[0]:+.3f}   correlacion {np.corrcoef(x, y)[0,1]:+.3f}')

    # ---- 6. Mas/Menos 2,5 ------------------------------------------------
    # Entrada: b365_over25/under25 previas. Referencias: cierre medio y, desde
    # 2024-25, cierre de Betfair Exchange (el mas eficiente de los datos).
    cols = ['b365_over25', 'b365_under25', 'avgc_over25', 'avgc_under25']
    if all(c in r for c in cols):
        o = r[r[cols].notna().all(1) & (r[cols] > 1.01).all(1)].copy()
        o['over'] = (o.gl + o.gv) > 2.5
        pa = potencia(o[['b365_over25', 'b365_under25']].values)[:, 0]
        pc = potencia(o[['avgc_over25', 'avgc_under25']].values)[:, 0]
        hay_bf = o[['bfec_over25', 'bfec_under25']].notna().all(1).values & (o[['bfec_over25', 'bfec_under25']] > 1.01).all(1).values
        pb = np.full(len(o), np.nan)
        pb[hay_bf] = potencia(o.loc[hay_bf, ['bfec_over25', 'bfec_under25']].values)[:, 0]
        def llb(pv, y): return float(-np.mean(np.where(y, np.log(pv), np.log(1 - pv))))
        p('\n=== 6. MAS/MENOS 2,5 ===')
        for bl in ('desarrollo', 'reservado'):
            s = (o.bloque == bl).values; y = o.over.values[s]
            p(f'[{bl}] log-loss  motor_cal {llb(o.pmo_cal.values[s], y):.4f}  motor_raw {llb(o.pmo_raw.values[s], y):.4f}'
              f'  apertura_b365 {llb(pa[s], y):.4f}  cierre_medio {llb(pc[s], y):.4f}')
            for w in (0.1, 0.2, 0.3):
                lm = lambda a: np.log(a / (1 - a))
                pw = 1 / (1 + np.exp(-(w * lm(o.pmo_cal.values[s]) + (1 - w) * lm(pa[s]))))
                p(f'    mezcla w={w}: {llb(pw, y):.4f}')
        for u in (0.03, 0.05, 0.08):
            for bl in ('desarrollo', 'reservado'):
                s = (o.bloque == bl).values
                filas = []
                for lado, pm, oc, pcl, pbl, gana in (
                        ('over', o.pmo_cal.values, o.b365_over25.values, pc, pb, o.over.values),
                        ('under', 1 - o.pmo_cal.values, o.b365_under25.values, 1 - pc, 1 - pb, ~o.over.values)):
                    sel = s & (pm * oc - 1 > u)
                    filas.append(pd.DataFrame(dict(semana=o.semana.values[sel], lado=lado,
                        clv=oc[sel] * pcl[sel] - 1, clv_bf=oc[sel] * pbl[sel] - 1,
                        ben=np.where(gana[sel], oc[sel] - 1, -1.0))))
                b = pd.concat(filas)
                if len(b) < 20:
                    continue
                lo, hi = ic_semanas(b, 'clv')
                bf = b.dropna(subset=['clv_bf'])
                txt_bf = f'  CLV_betfair {bf.clv_bf.mean()*100:+.2f}% (n={len(bf)})' if len(bf) >= 20 else ''
                base = np.concatenate([o.b365_over25.values[s] * pc[s] - 1, o.b365_under25.values[s] * (1 - pc[s]) - 1]).mean()
                p(f'  umbral {u*100:.0f}% [{bl}] n={len(b):5d}  CLV {b.clv.mean()*100:+.2f}% [{lo*100:+.2f},{hi*100:+.2f}]'
                  f'{txt_bf}  ROI {b.ben.mean()*100:+.1f}%  (base todas {base*100:+.2f}%)  '
                  f'over {int((b.lado == "over").sum())} / under {int((b.lado == "under").sum())}')

    (OUT / 'backtest_resumen.txt').write_text('\n'.join(lineas), encoding='utf-8')
    print(f'\nEscrito en {OUT}/', file=sys.stderr)


if __name__ == '__main__':
    main()

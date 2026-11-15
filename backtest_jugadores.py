#!/usr/bin/env python3
"""
backtest_jugadores.py -- ¿predice mejor los remates un modelo de jugadores nuevo que el actual?

Para cada TITULAR de campo (sin porteros) y solo con partidos anteriores al suyo, se calcula
la probabilidad de "N o mas remates" y "N o mas remates a puerta" con cinco variantes:

  actual    el de analizar_jornada.Jugadores.evaluar: media de sus titularidades x lo que
            concede el rival, binomial negativa con SU varianza, y el MINIMO entre eso y su
            frecuencia real
  sin_min   lo mismo sin tomar el minimo con la frecuencia real
  ritmo     ritmo por 90' (todas sus apariciones) encogido hacia la media de su posicion
            (K_RITMO partidos virtuales) x minutos esperados como titular x lo que concede
            el rival (encogido) x campo; binomial negativa con dispersion comun
  equipo    como 'ritmo', pero el factor del partido sale de una regresion de Poisson de los
            remates del EQUIPO (su media, lo que concede el rival, campo), reajustada cada semana
  mercado   como 'equipo', anadiendo a la regresion la supremacia y el total de goles que
            implican las cuotas de apertura de bet365 (simulador.anclar_al_mercado)

No hay historico de cuotas de remates: esto mide CALIBRACION y log-loss, no valor.
Los datos de jugadores empiezan en 2026-07: desarrollo hasta INI_TEST (dispersion comun),
prueba despues. La regresion de equipo solo usa partidos anteriores a cada semana.

Uso:  python backtest_jugadores.py
Salida: resultados/jugadores_backtest.txt y resultados/jugadores_backtest.csv
"""
from pathlib import Path

import numpy as np, pandas as pd
from scipy.optimize import minimize
from scipy.stats import nbinom

import jugadores_modelo as jm
import simulador as sm

DIR = Path(__file__).parent
OUT = DIR / 'resultados'
INI_TEST = pd.Timestamp('2026-10-01')
K_RITMO, K_MIN, K_EQ = 5.0, 2.0, 3.0
MUESTRA_MIN = 6
STATS = {'tiros': (1, 2, 3, 4), 'tiros_puerta': (1, 2)}
NOMBRE = {'tiros': 'remates', 'tiros_puerta': 'a puerta'}
VARIANTES = ['actual', 'sin_min', 'ritmo', 'equipo', 'mercado']


def previo(df, claves, cols):
    """Suma de 'cols' en las filas ANTERIORES del mismo grupo (df ordenado por fecha)."""
    g = df.groupby(claves, sort=False)[cols]
    return g.cumsum() - df[cols]


def previo_por_fecha(df, claves, cols):
    """Suma de 'cols' del grupo en FECHAS estrictamente anteriores, alineada con df."""
    a = df.groupby(claves + ['fecha'])[cols].sum().sort_index()
    c = a.groupby(level=list(range(len(claves)))).cumsum() - a
    return df[claves + ['fecha']].merge(c.reset_index(), on=claves + ['fecha'], how='left')[cols].set_index(df.index)


def p_nb(lam, k, alpha):
    lam = np.maximum(np.asarray(lam, float), 1e-9); n = 1 / np.asarray(alpha, float)
    return 1 - nbinom.cdf(k - 1, n, n / (n + lam))


def ll(p, y):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    return -np.where(y, np.log(p), np.log(1 - p))


def poisson_reg(X, y):
    def f(b):
        e = X @ b; m = np.exp(np.clip(e, -5, 5))
        return np.sum(m - y * e), X.T @ (m - y)
    b0 = np.zeros(X.shape[1]); b0[0] = np.log(max(y.mean(), 1e-6))
    return minimize(f, b0, jac=True, method='L-BFGS-B').x


# ---------------------------------------------------------------- equipos y mercado
def tabla_equipos(J):
    T = J.groupby(['espn_id', 'fecha', 'liga', 'equipo_id', 'equipo', 'equipo_id_r', 'lado'], as_index=False)[
        ['tiros', 'tiros_puerta']].sum()
    c = T[['espn_id', 'equipo_id', 'tiros', 'tiros_puerta']].rename(
        columns={'equipo_id': 'equipo_id_r', 'tiros': 'tiros_c', 'tiros_puerta': 'tiros_puerta_c'})
    T = T.merge(c, on=['espn_id', 'equipo_id_r']).sort_values('fecha', kind='stable').reset_index(drop=True)
    T['uno'] = 1.0
    cols = ['tiros', 'tiros_puerta', 'tiros_c', 'tiros_puerta_c', 'uno']
    P = previo(T, ['equipo_id'], cols).add_prefix('eq_')
    L = previo_por_fecha(T, ['liga'], ['tiros', 'tiros_puerta', 'uno']).add_prefix('lg_')
    H = previo_por_fecha(T, ['lado'], ['tiros', 'tiros_puerta', 'uno']).add_prefix('ld_')
    G = previo_por_fecha(T.assign(todo=1), ['todo'], ['tiros', 'tiros_puerta', 'uno']).add_prefix('gl_')
    T = pd.concat([T, P, L, H, G], axis=1)
    for s in STATS:
        lg = (T[f'lg_{s}'] / T.lg_uno).fillna(T[s].mean())
        T[f'lg_m_{s}'] = lg
        T[f'af_{s}'] = (T[f'eq_{s}'] + K_EQ * lg) / (T.eq_uno + K_EQ) / lg            # a favor, encogido, relativo
        T[f'ec_{s}'] = (T[f'eq_{s}_c'] + K_EQ * lg) / (T.eq_uno + K_EQ) / lg          # en contra, encogido
        T[f'ec_bruto_{s}'] = np.where(T.eq_uno > 0, T[f'eq_{s}_c'] / T.eq_uno.replace(0, np.nan) / lg, 1.0)
        T[f'campo_{s}'] = ((T[f'ld_{s}'] / T.ld_uno) / (T[f'gl_{s}'] / T.gl_uno)).fillna(1.0)
    r = T[['espn_id', 'equipo_id'] + [f'{a}_{s}' for a in ('ec', 'ec_bruto') for s in STATS]].rename(
        columns={'equipo_id': 'equipo_id_r', **{f'{a}_{s}': f'riv_{a}_{s}' for a in ('ec', 'ec_bruto') for s in STATS}})
    return T.merge(r, on=['espn_id', 'equipo_id_r'])


def mercado(T):
    """Supremacia y total de goles del mercado (apertura bet365) para cada equipo-partido."""
    mapa = pd.read_csv(DIR / 'datos' / 'mapa_nombres.csv')
    mapa = mapa[mapa.fuente == 'espn'].set_index(['liga', 'nombre_src']).nombre_fd
    T['fd'] = [mapa.get((l, e)) for l, e in zip(T.liga, T.equipo)]
    h = T[T.lado == 'home'][['espn_id', 'fd']].rename(columns={'fd': 'local'})
    a = T[T.lado == 'away'][['espn_id', 'fd']].rename(columns={'fd': 'visitante'})
    T = T.merge(h, on='espn_id', how='left').merge(a, on='espn_id', how='left')
    P = pd.read_csv(DIR / 'datos' / 'partidos.csv', low_memory=False,
                    usecols=['liga', 'fecha', 'local', 'visitante', 'b365_1', 'b365_x', 'b365_2', 'b365_over25', 'b365_under25'])
    P['fecha_fd'] = pd.to_datetime(P.fecha); P = P[P.fecha_fd >= '2026-07-01'].drop(columns='fecha')
    filas = []
    for x in P.dropna().itertuples():
        R = sm.anclar_al_mercado(dict(lh=1.5, la=1.2), x.b365_1, x.b365_x, x.b365_2, x.b365_over25, x.b365_under25)
        if R:
            filas.append(dict(liga=x.liga, local=x.local, visitante=x.visitante, fecha_fd=x.fecha_fd,
                              sup_local=R['lh'] - R['la'], tot=R['lh'] + R['la']))
    M = pd.DataFrame(filas)
    T = T.merge(M, on=['liga', 'local', 'visitante'], how='left')
    lejos = (T.fecha - T.fecha_fd).abs() > pd.Timedelta(days=2)
    T.loc[lejos, ['sup_local', 'tot']] = np.nan
    T = T.sort_values(['espn_id', 'equipo_id', 'sup_local']).drop_duplicates(['espn_id', 'equipo_id'])
    T['sup'] = np.where(T.lado == 'home', T.sup_local, -T.sup_local)
    return T.sort_values('fecha', kind='stable').reset_index(drop=True)


def remates_equipo(T):
    """Remates esperados del equipo relativos a su media, con y sin mercado (walk-forward semanal)."""
    T['semana'] = T.fecha.dt.to_period('W-SUN').dt.start_time
    for s in STATS:
        T[f'f_equipo_{s}'] = np.nan; T[f'f_mercado_{s}'] = np.nan
        lf, lc = np.log(T[f'af_{s}']), np.log(T[f'riv_ec_{s}'])
        casa = (T.lado == 'home').astype(float)
        X1 = np.c_[np.ones(len(T)), lf, lc, casa]
        X2 = np.c_[X1, T.sup.fillna(0), T.tot.fillna(T.tot.mean()) - T.tot.mean()]
        off = np.log(T[f'lg_m_{s}'].values)                 # la media de la liga entra como desplazamiento
        y = T[s].values / np.exp(off)
        hay = T.sup.notna().values
        for t in sorted(T.semana.unique()):
            ent = (T.fecha < t).values; act = (T.semana == t).values
            if ent.sum() < 300:
                continue
            b1 = poisson_reg(X1[ent], y[ent])
            T.loc[act, f'f_equipo_{s}'] = np.exp(X1[act] @ b1) / T.loc[act, f'af_{s}']
            if (ent & hay).sum() >= 300:
                b2 = poisson_reg(X2[ent & hay], y[ent & hay])
                T.loc[act & hay, f'f_mercado_{s}'] = np.exp(X2[act & hay] @ b2) / T.loc[act & hay, f'af_{s}']
                T.attrs[f'coef_{s}'] = b2
    return T


def ajustar_equipo(T, s):
    """La regresion de remates del equipo con TODOS los partidos de T (para produccion:
    analizar_jornada.Jugadores). b1 = sin mercado, b2 = con supremacia y total del mercado."""
    casa = (T.lado == 'home').astype(float)
    tm = float(T.tot.mean())
    X1 = np.c_[np.ones(len(T)), np.log(T[f'af_{s}']), np.log(T[f'riv_ec_{s}']), casa]
    X2 = np.c_[X1, T.sup.fillna(0), T.tot.fillna(tm) - tm]
    y = T[s].values / T[f'lg_m_{s}'].values
    hay = T.sup.notna().values
    return dict(b1=poisson_reg(X1, y), b2=poisson_reg(X2[hay], y[hay]), tot_medio=tm)


# ---------------------------------------------------------------- jugadores
def preparar(J, T):
    J = J.sort_values('fecha', kind='stable').reset_index(drop=True)
    jugo = (J.minutos > 0).astype(float); tit = J.titular.astype(float)
    A = pd.DataFrame({'a_min': J.minutos * jugo, 'a_tiros': J.tiros * jugo, 'a_tiros_puerta': J.tiros_puerta * jugo,
                      't_n': tit, 't_min': J.minutos * tit})
    for s, lineas in STATS.items():
        A[f't_{s}'] = J[s] * tit; A[f't2_{s}'] = J[s] ** 2 * tit
        for k in lineas:
            A[f't_{s}_{k}'] = (J[s] >= k) * tit
    J = pd.concat([J, previo(pd.concat([J[['jugador_id']], A], axis=1), ['jugador_id'], list(A.columns))], axis=1)
    # medias por posicion (todas las ligas) en fechas anteriores
    Gp = pd.DataFrame({'grupo': J.grupo, 'fecha': J.fecha, 'g_min': J.minutos * jugo, 'g_tiros': J.tiros * jugo,
                       'g_tiros_puerta': J.tiros_puerta * jugo, 'g_tn': tit, 'g_tmin': J.minutos * tit})
    J = pd.concat([J, previo_por_fecha(Gp, ['grupo'], ['g_min', 'g_tiros', 'g_tiros_puerta', 'g_tn', 'g_tmin'])], axis=1)
    cols = ['espn_id', 'equipo_id'] + [c for c in T.columns if c.startswith(('riv_', 'campo_', 'f_equipo_', 'f_mercado_'))]
    return J.merge(T[cols], on=['espn_id', 'equipo_id'], how='left')


def predecir(J):
    R = J[J.titular & (J.grupo != 'POR') & (J.t_n >= 1) & (J.g_min > 0)].copy()
    n = R.t_n
    m_pos = (R.g_tmin / R.g_tn).fillna(80)
    R['min_esp'] = (R.t_min + K_MIN * m_pos) / (n + K_MIN)
    for s, lineas in STATS.items():
        # --- modelo actual
        m = R[f't_{s}'] / n
        var = R[f't2_{s}'] / n - m ** 2
        mu = m * R[f'riv_ec_bruto_{s}']
        a = np.maximum((np.maximum(var, m * 1.05) - m) / np.maximum(m, .01) ** 2, 0.01)
        # --- modelo nuevo
        r_pos = R[f'g_{s}'] / (R.g_min / 90)
        r90 = (R[f'a_{s}'] + K_RITMO * r_pos) / (R.a_min / 90 + K_RITMO)
        base = r90 * R.min_esp / 90
        R[f'lam_ritmo_{s}'] = base * R[f'riv_ec_{s}'] * R[f'campo_{s}']
        R[f'lam_equipo_{s}'] = base * R[f'f_equipo_{s}']
        R[f'lam_mercado_{s}'] = base * R[f'f_mercado_{s}']
        dev = (R.fecha < INI_TEST).values
        for v in ('ritmo', 'equipo', 'mercado'):
            lam = R[f'lam_{v}_{s}'].values; ok = dev & np.isfinite(lam)
            R.attrs[f'alpha_{v}_{s}'] = jm.alpha_nb(np.maximum(lam[ok], 1e-9), R[s].values[ok].astype(int))
        for k in lineas:
            pm = np.where(mu > 0, p_nb(mu, k, a), 0.0)
            R[f'p_sin_min_{s}_{k}'] = pm
            R[f'p_actual_{s}_{k}'] = np.minimum(pm, R[f't_{s}_{k}'] / n)
            for v in ('ritmo', 'equipo', 'mercado'):
                R[f'p_{v}_{s}_{k}'] = p_nb(R[f'lam_{v}_{s}'], k, R.attrs[f'alpha_{v}_{s}'])
            R[f'y_{s}_{k}'] = R[s] >= k
    return R


def main():
    OUT.mkdir(exist_ok=True)
    J = jm.cargar().drop_duplicates(['espn_id', 'jugador_id'])
    T = remates_equipo(mercado(tabla_equipos(J)))
    R = predecir(preparar(J, T))
    tst = R[(R.fecha >= INI_TEST) & R.f_mercado_tiros.notna() & R.f_equipo_tiros.notna()].copy()
    tst.to_csv(OUT / 'jugadores_backtest.csv', index=False)
    L = [f'Titulares de campo con al menos una titularidad previa: {len(R):,} ({R.fecha.min().date()} a {R.fecha.max().date()})',
         f'Prueba desde {INI_TEST.date()} con cuotas de mercado: {len(tst):,} filas, {tst.espn_id.nunique()} partidos '
         f'(cobertura de mercado en equipos-partido: {T.sup.notna().mean()*100:.0f} %)',
         'Dispersion comun (desarrollo): ' + ', '.join(f'{v}/{NOMBRE[s]} {R.attrs[f"alpha_{v}_{s}"]:.3f}'
                                                       for s in STATS for v in ('ritmo', 'mercado')),
         'Regresion de remates del equipo con mercado [cte, log a favor, log rival concede, casa, supremacia, total]: '
         + ' '.join(f'{x:+.3f}' for x in T.attrs['coef_tiros']),
         f'Minutos de los titulares en prueba: esperados {tst.min_esp.mean():.1f}, reales {tst.minutos.mean():.1f}', '']
    for nombre, d in (('TODOS los titulares', tst), (f'Con {MUESTRA_MIN}+ titularidades (lo que hoy se puede apostar)', tst[tst.t_n >= MUESTRA_MIN])):
        L += [f'=== {nombre}: {len(d):,} filas ===',
              f'{"linea":14s} {"real":>6s} | ' + ' '.join(f'{v:>8s}' for v in VARIANTES) + '  (media predicha)  | '
              + ' '.join(f'{v:>8s}' for v in VARIANTES) + '  (log-loss)  | mercado - actual [IC95]']
        for s, lineas in STATS.items():
            for k in lineas:
                y = d[f'y_{s}_{k}'].values
                P = {v: d[f'p_{v}_{s}_{k}'].values for v in VARIANTES}
                LLs = {v: ll(P[v], y) for v in VARIANTES}
                dd = LLs['mercado'] - LLs['actual']; se = dd.std(ddof=1) / np.sqrt(len(dd))
                L.append(f'{NOMBRE[s] + " " + str(k) + "+":14s} {y.mean()*100:5.1f}% | '
                         + ' '.join(f'{P[v].mean()*100:7.1f}%' for v in VARIANTES) + '                    | '
                         + ' '.join(f'{LLs[v].mean():8.4f}' for v in VARIANTES)
                         + f'              | {dd.mean():+.4f} [{dd.mean()-1.96*se:+.4f},{dd.mean()+1.96*se:+.4f}]')
        L.append('')
    # zona de apuesta: lo que el modelo actual marcaria con probabilidad alta
    d = tst[tst.t_n >= MUESTRA_MIN]
    L += ['=== Zona de apuesta: titulares con 6+ titularidades donde el modelo ACTUAL da 55 % o mas ===',
          f'{"linea":14s} {"n":>6s} {"real":>7s} {"actual":>8s} {"mercado":>8s}   y donde el NUEVO (mercado) da 55 % o mas: {"n":>6s} {"real":>7s} {"nuevo":>8s}']
    for s, lineas in STATS.items():
        for k in lineas:
            y = d[f'y_{s}_{k}'].values; pa = d[f'p_actual_{s}_{k}'].values; pn = d[f'p_mercado_{s}_{k}'].values
            za, zn = pa >= .55, pn >= .55
            if za.sum() < 30:
                continue
            L.append(f'{NOMBRE[s] + " " + str(k) + "+":14s} {za.sum():6d} {y[za].mean()*100:6.1f}% {pa[za].mean()*100:7.1f}% {pn[za].mean()*100:7.1f}%'
                     + ' ' * 44 + (f'{zn.sum():6d} {y[zn].mean()*100:6.1f}% {pn[zn].mean()*100:7.1f}%' if zn.sum() >= 30 else '   (menos de 30)'))
    L += ['', 'Calibracion por tramos de probabilidad, todas las lineas juntas, 6+ titularidades (predicha → real, n):']
    for v in ('actual', 'mercado'):
        p = np.concatenate([d[f'p_{v}_{s}_{k}'].values for s in STATS for k in STATS[s]])
        y = np.concatenate([d[f'y_{s}_{k}'].values for s in STATS for k in STATS[s]])
        b = pd.cut(p, [0, .2, .4, .5, .6, .7, .8, 1.0001], right=False)
        c = pd.DataFrame(dict(p=p, y=y, b=b)).groupby('b', observed=True).agg(n=('y', 'size'), pred=('p', 'mean'), real=('y', 'mean'))
        L.append(f'  {v:8s} ' + ' | '.join(f'{r.pred*100:.0f}→{r.real*100:.0f} ({r.n})' for r in c.itertuples()))
    txt = '\n'.join(L); print(txt)
    (OUT / 'jugadores_backtest.txt').write_text(txt + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()

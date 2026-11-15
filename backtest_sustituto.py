#!/usr/bin/env python3
"""
backtest_sustituto.py -- ¿cuanto cambia el modelo de remates si se tiene en cuenta el Sustituto+ de bet365?

En bet365 (remates y remates a puerta de jugador) rige "Sustituto+": si el titular es cambiado,
suman los remates de quien entra por el. La apuesta cubre el PUESTO los 90 minutos, no al jugador.
El modelo vigente ('mercado' de backtest_jugadores.py) predice solo los remates del jugador:
ritmo por 90' x minutos esperados x factor del partido.

Este script:
  1. Construye el resultado Sustituto+ de cada titular: sus remates + los del suplente que entro
     por el. ESPN no dice quien entro por quien: dentro de cada equipo-partido se emparejan los
     titulares cambiados (de menos a mas minutos) con los suplentes (de mas a menos minutos).
     En cambios simultaneos el emparejamiento puede cruzar jugadores; para la calibracion media
     da igual, para un jugador concreto no.
  2. Variante 'sust': lambda = ritmo_jugador x min_esp/90 + ritmo_suplente x (90 - min_esp)/90,
     las dos partes por el mismo factor del partido. ritmo_suplente = remates por 90' de los
     suplentes que entraron por titulares de su misma posicion (solo periodo de desarrollo).
     Dispersion comun reajustada en desarrollo contra el resultado Sustituto+.
  3. Compara 'mercado' (vigente) y 'sust' contra el resultado Sustituto+ (lo que paga bet365).

Uso:  python backtest_sustituto.py
Salida: resultados/sustituto_backtest.txt
"""
from pathlib import Path

import numpy as np, pandas as pd

import backtest_jugadores as bj
import jugadores_modelo as jm

OUT = Path(__file__).parent / 'resultados'


def resultado_sustituto(J):
    """Devuelve DataFrame (espn_id, jugador_id, sub_min, sub_tiros, sub_tiros_puerta) para titulares cambiados."""
    J = J.copy()
    filas = []
    for (_, _), g in J.groupby(['espn_id', 'equipo_id'], sort=False):
        out = g[g.titular & (g.salio == 'True')].sort_values('minutos', kind='stable')
        inn = g[(g.entro == 'True') & ~g.titular].sort_values('minutos', ascending=False, kind='stable')
        for (_, a), (_, b) in zip(out.iterrows(), inn.iterrows()):
            filas.append((a.espn_id, a.jugador_id, b.minutos, b.tiros, b.tiros_puerta, b.grupo))
    return pd.DataFrame(filas, columns=['espn_id', 'jugador_id', 'sub_min', 'sub_tiros', 'sub_tiros_puerta', 'sub_grupo'])


def main():
    OUT.mkdir(exist_ok=True)
    J = jm.cargar().drop_duplicates(['espn_id', 'jugador_id'])
    S = resultado_sustituto(J)
    T = bj.remates_equipo(bj.mercado(bj.tabla_equipos(J)))
    R = bj.predecir(bj.preparar(J, T))
    R = R.merge(S, on=['espn_id', 'jugador_id'], how='left')
    R['cambiado'] = R.sub_min.notna()
    dev = (R.fecha < bj.INI_TEST).values
    L = [f'Titulares evaluados: {len(R):,}; cambiados con suplente emparejado: {R.cambiado.mean()*100:.1f} %',
         f'Minutos medios del suplente emparejado: {R.sub_min.mean():.1f}', '']
    L.append('Ritmo por 90\' de los suplentes que entran por un titular de cada posicion (desarrollo):')
    for s in bj.STATS:
        R[f'ys_{s}'] = R[s] + R[f'sub_{s}'].fillna(0)
        d = R[dev & R.cambiado]
        rs = (d.groupby('grupo')[f'sub_{s}'].sum() / (d.groupby('grupo').sub_min.sum() / 90))
        r_glob = d[f'sub_{s}'].sum() / (d.sub_min.sum() / 90)
        R[f'r_sub_{s}'] = R.grupo.map(rs).fillna(r_glob)
        L.append(f'  {bj.NOMBRE[s]:9s} ' + ', '.join(f'{k} {v:.2f}' for k, v in rs.items()) + f'  (todos {r_glob:.2f})')
        resto = np.clip(90 - R.min_esp, 0, None) / 90
        R[f'lam_sust_{s}'] = R[f'lam_mercado_{s}'] + R[f'r_sub_{s}'] * resto * R[f'f_mercado_{s}']
        lam = R[f'lam_sust_{s}'].values; ok = dev & np.isfinite(lam)
        R.attrs[f'alpha_sust_{s}'] = jm.alpha_nb(np.maximum(lam[ok], 1e-9), R[f'ys_{s}'].values[ok].astype(int))
        for k in bj.STATS[s]:
            R[f'p_sust_{s}_{k}'] = bj.p_nb(R[f'lam_sust_{s}'], k, R.attrs[f'alpha_sust_{s}'])
            R[f'yS_{s}_{k}'] = R[f'ys_{s}'] >= k
    L.append('Dispersion (sust): ' + ', '.join(f'{bj.NOMBRE[s]} {R.attrs[f"alpha_sust_{s}"]:.3f}' for s in bj.STATS))
    L.append('')

    tst = R[(R.fecha >= bj.INI_TEST) & R.f_mercado_tiros.notna() & R.f_equipo_tiros.notna()]
    for nombre, d in (('TODOS los titulares', tst), (f'Con {bj.MUESTRA_MIN}+ titularidades', tst[tst.t_n >= bj.MUESTRA_MIN])):
        L += [f'=== Prueba (desde {bj.INI_TEST.date()}), {nombre}: {len(d):,} filas ===',
              f'{"linea":13s} {"real jug":>8s} {"real S+":>8s} | {"vigente":>8s} {"sust":>8s} (media) | '
              f'{"vigente":>8s} {"sust":>8s} (log-loss vs S+) | sust - vigente [IC95]']
        for s, lineas in bj.STATS.items():
            for k in lineas:
                y = d[f'yS_{s}_{k}'].values; pv = d[f'p_mercado_{s}_{k}'].values; ps = d[f'p_sust_{s}_{k}'].values
                lv, ls = bj.ll(pv, y), bj.ll(ps, y); dd = ls - lv; se = dd.std(ddof=1) / np.sqrt(len(dd))
                L.append(f'{bj.NOMBRE[s] + " " + str(k) + "+":13s} {d[f"y_{s}_{k}"].mean()*100:7.1f}% {y.mean()*100:7.1f}% | '
                         f'{pv.mean()*100:7.1f}% {ps.mean()*100:7.1f}%         | {lv.mean():8.4f} {ls.mean():8.4f}                  | '
                         f'{dd.mean():+.4f} [{dd.mean()-1.96*se:+.4f},{dd.mean()+1.96*se:+.4f}]')
        L.append('')
    d = tst[tst.t_n >= bj.MUESTRA_MIN]
    L.append(f'Calibracion contra el resultado Sustituto+, todas las lineas, {bj.MUESTRA_MIN}+ titularidades (predicha → real, n):')
    for v in ('mercado', 'sust'):
        p = np.concatenate([d[f'p_{v}_{s}_{k}'].values for s in bj.STATS for k in bj.STATS[s]])
        y = np.concatenate([d[f'yS_{s}_{k}'].values for s in bj.STATS for k in bj.STATS[s]])
        b = pd.cut(p, [0, .2, .4, .5, .6, .7, .8, 1.0001], right=False)
        c = pd.DataFrame(dict(p=p, y=y, b=b)).groupby('b', observed=True).agg(n=('y', 'size'), pred=('p', 'mean'), real=('y', 'mean'))
        L.append(f'  {("vigente" if v == "mercado" else v):8s} ' + ' | '.join(f'{r.pred*100:.0f}→{r.real*100:.0f} ({r.n})' for r in c.itertuples()))
    # por liga: Hypermotion
    L += ['', 'Por liga, remates 3+, 6+ titularidades (real jugador / real S+ / vigente / sust, n):']
    for liga, g in d.groupby('liga'):
        if len(g) >= 40:
            L.append(f'  {liga:28s} {g.y_tiros_3.mean()*100:5.1f}% {g.yS_tiros_3.mean()*100:5.1f}% '
                     f'{g.p_mercado_tiros_3.mean()*100:5.1f}% {g.p_sust_tiros_3.mean()*100:5.1f}%  ({len(g)})')
    txt = '\n'.join(L); print(txt)
    (OUT / 'sustituto_backtest.txt').write_text(txt + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()

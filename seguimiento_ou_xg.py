#!/usr/bin/env python3
"""
seguimiento_ou_xg.py -- evaluacion fuera de muestra de la variante CONGELADA
de Mas/Menos 2,5 con xG (motor.OU_XG_*), solo con partidos posteriores a
motor.OU_XG_FECHA_CONGELACION.

CRITERIOS FIJADOS ANTES DE VER LOS DATOS (2026-11-04). No se cambian despues.

  Fase 1 -- informacion (log-loss), con >= 1.000 partidos nuevos:
    PASA   si log-loss(mezcla) < log-loss(apertura bet365) y el IC95 de la
           diferencia (bootstrap por semanas) queda entero por debajo de 0.
    FALLA  si la diferencia media es >= 0.
    Si no ocurre ninguna de las dos, se amplia hasta 2.000 partidos y se aplica
    la misma regla; si sigue sin decidirse, se considera FALLA.
  Fase 2 -- valor (CLV), solo si pasa la fase 1, con >= 300 apuestas:
    apuesta cuando p_mezcla · cuota_previa_b365 - 1 > 0 (Mas o Menos);
    PASA si el CLV medio frente al cierre de Betfair sin margen es > 0 y su
    IC95 excluye el 0. Solo entonces se plantea dinero real, con stake plano.
    Aviso medido el 2026-11-04 sobre la muestra de desarrollo: el margen de
    bet365 en O/U es ~5,5 % y la mezcla apenas se separa de la apertura, asi que
    solo ~1,4 % de los partidos dan apuesta (13 de 962). Llegar a 300 puede
    llevar mas de una temporada: la fase 1 decide si el xG informa; la fase 2,
    si esa informacion basta para batir el margen de bet365.
  Diagnostico (no decide): pendiente del movimiento de linea
    (cierre Betfair - apertura) sobre (mezcla - apertura). > 0 = la linea se
    mueve hacia la mezcla.

Prediccion walk-forward: cada semana se reconstruye motor.Base con los datos
anteriores al lunes de esa semana y se llama a motor.analizar, es decir, se
evalua el codigo real del motor, no una copia.

Uso:  python seguimiento_ou_xg.py
Salida: resultados/seguimiento_ou_xg.csv (una fila por partido) y
        resultados/seguimiento_ou_xg.txt (estado frente a los criterios)
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd

import motor

DIR = Path(__file__).parent
OUT = DIR / 'resultados'
FASE1_MIN, FASE1_MAX = 1000, 2000
FASE2_MIN, UMBRAL_VALOR = 300, 0.0
SEMILLA = 11


def ic_semanas(x, semanas, n=3000):
    rng = np.random.default_rng(SEMILLA)
    g = pd.DataFrame(dict(x=x, s=semanas)).groupby('s').x.agg(['sum', 'size'])
    s, c = g['sum'].values, g['size'].values
    k = rng.integers(0, len(g), (n, len(g)))
    return np.percentile(s[k].sum(1) / c[k].sum(1), [2.5, 97.5])


def llb(p, y):
    p = np.clip(p, 1e-9, 1 - 1e-9)
    return -np.where(y, np.log(p), np.log(1 - p))


def predecir(df, desde):
    ev = df[(df.fecha >= desde) & df.b365_over25.notna() & df.b365_under25.notna()
            & (df.b365_over25 > 1.01) & (df.b365_under25 > 1.01)].copy()
    ev['semana'] = ev.fecha.dt.to_period('W-SUN').dt.start_time
    filas = []
    for t in sorted(ev.semana.unique()):
        t = pd.Timestamp(t)
        B = motor.Base(df[df.fecha < t], hasta=t)
        for x in ev[ev.semana == t].itertuples(index=False):
            if x.liga not in B.co or x.liga not in B.ligas_xg:
                continue
            try:
                R = motor.analizar(B, x.local, x.visitante, x.liga, hasta=t)
            except SystemExit:
                continue
            po = R['over25_xg_motor']
            if po is None:
                continue
            filas.append(dict(
                fecha=x.fecha.date(), semana=t.date(), liga=x.liga, local=x.local, visitante=x.visitante,
                goles=x.gl + x.gv, over=(x.gl + x.gv) > 2.5,
                p_motor_xg=po, p_motor_actual=R['m']['over2.5'],
                p_apertura=motor.sin_margen_2(x.b365_over25, x.b365_under25),
                p_mezcla=motor.ou_xg_congelado(po, x.b365_over25, x.b365_under25),
                b365_over25=x.b365_over25, b365_under25=x.b365_under25,
                p_cierre_bf=(motor.sin_margen_2(x.bfec_over25, x.bfec_under25)
                             if pd.notna(x.bfec_over25) and pd.notna(x.bfec_under25)
                             and x.bfec_over25 > 1.01 and x.bfec_under25 > 1.01 else np.nan)))
    return pd.DataFrame(filas)


def main(desde=None):
    OUT.mkdir(exist_ok=True)
    desde = pd.Timestamp(desde or motor.OU_XG_FECHA_CONGELACION)
    df = motor.cargar(DIR / 'datos' / 'partidos.csv')
    r = predecir(df, desde)
    lineas = []
    def p(s=''):
        print(s); lineas.append(s)

    p(f'Variante congelada O/U xG  (alpha={motor.OU_XG_ALPHA}, w_motor={motor.OU_XG_W_MOTOR}, '
      f'congelada {motor.OU_XG_FECHA_CONGELACION})')
    p(f'Partidos evaluados desde {desde.date()}: {len(r)}')
    if not len(r):
        p('Aun no hay partidos nuevos con resultado. ESTADO: EN ESPERA (fase 1).')
        (OUT / 'seguimiento_ou_xg.txt').write_text('\n'.join(lineas), encoding='utf-8')
        return r
    r.to_csv(OUT / 'seguimiento_ou_xg.csv', index=False)

    y, sem = r.over.values, r.semana.astype(str).values
    l_mez, l_ap = llb(r.p_mezcla.values, y), llb(r.p_apertura.values, y)
    l_mx = llb(r.p_motor_xg.values, y)
    dif = l_mez - l_ap; lo, hi = ic_semanas(dif, sem)
    p(f'log-loss  mezcla {l_mez.mean():.4f}   apertura {l_ap.mean():.4f}   motor_xg solo {l_mx.mean():.4f}')
    p(f'diferencia mezcla - apertura {dif.mean():+.4f}  IC95 [{lo:+.4f}, {hi:+.4f}]')
    mv = r.dropna(subset=['p_cierre_bf'])
    if len(mv) >= 50:
        x = (mv.p_mezcla - mv.p_apertura).values; yy = (mv.p_cierre_bf - mv.p_apertura).values
        p(f'diagnostico: pendiente del movimiento de linea hacia la mezcla {np.polyfit(x, yy, 1)[0]:+.2f} '
          f'(correlacion {np.corrcoef(x, yy)[0,1]:+.3f}, n={len(mv)})')

    n = len(r)
    if n < FASE1_MIN:
        estado = f'EN ESPERA (fase 1): faltan {FASE1_MIN - n} partidos para decidir'
    elif hi < 0:
        estado = 'FASE 1 SUPERADA'
    elif dif.mean() >= 0:
        estado = 'DESCARTADA: la mezcla no mejora a la apertura'
    elif n < FASE1_MAX:
        estado = f'INDECISA (fase 1): se amplia hasta {FASE1_MAX} partidos (faltan {FASE1_MAX - n})'
    else:
        estado = 'DESCARTADA: sin mejora clara con 2.000 partidos'

    # fase 2: CLV frente al cierre de Betfair
    pm = r.p_mezcla.values
    over_ok = pm * r.b365_over25 - 1 > UMBRAL_VALOR
    under_ok = (1 - pm) * r.b365_under25 - 1 > UMBRAL_VALOR
    b = pd.concat([
        pd.DataFrame(dict(semana=sem[over_ok], lado='over', cuota=r.b365_over25[over_ok],
                          clv=r.b365_over25[over_ok] * r.p_cierre_bf[over_ok] - 1,
                          ben=np.where(y[over_ok], r.b365_over25[over_ok] - 1, -1.0))),
        pd.DataFrame(dict(semana=sem[under_ok], lado='under', cuota=r.b365_under25[under_ok],
                          clv=r.b365_under25[under_ok] * (1 - r.p_cierre_bf[under_ok]) - 1,
                          ben=np.where(~y[under_ok], r.b365_under25[under_ok] - 1, -1.0)))]).dropna(subset=['clv'])
    if len(b) >= 20:
        clo, chi = ic_semanas(b.clv.values, b.semana.values)
        p(f'apuestas hipoteticas (valor > {UMBRAL_VALOR*100:.0f} %): n={len(b)}  CLV vs Betfair '
          f'{b.clv.mean()*100:+.2f}% [{clo*100:+.2f}, {chi*100:+.2f}]  ROI {b.ben.mean()*100:+.1f}%  '
          f'(over {int((b.lado == "over").sum())} / under {int((b.lado == "under").sum())})')
        if estado == 'FASE 1 SUPERADA':
            if len(b) < FASE2_MIN:
                estado += f'; fase 2 EN ESPERA: faltan {FASE2_MIN - len(b)} apuestas'
            elif clo > 0:
                estado += '; FASE 2 SUPERADA: CLV positivo. Valorar stake plano pequeno'
            else:
                estado += '; FASE 2 NO SUPERADA: sin CLV positivo, no apostar'
    else:
        p(f'apuestas hipoteticas (valor > {UMBRAL_VALOR*100:.0f} %): {len(b)}, demasiado pocas')
    p(f'ESTADO: {estado}')
    (OUT / 'seguimiento_ou_xg.txt').write_text('\n'.join(lineas), encoding='utf-8')
    return r


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else None)

#!/usr/bin/env python3
"""
calcular_clv.py -- CLV y resultado de cada apuesta de la bitacora.

bitacora.csv (una fila por apuesta, nombres de equipo como en partidos.csv):
  id             identificador libre (p. ej. 2026-11-08-01)
  fecha_apuesta  momento en que se tomo la cuota, ISO (2026-11-06T18:30)
  liga           como en partidos.csv (LaLiga, Super Lig, ...)
  fecha_partido  AAAA-MM-DD
  local, visitante
  mercado        1x2 | ou25
  seleccion      1 | x | 2 | over | under
  cuota          cuota decimal tomada
  casa           bet365 | betfair | ...   (betfair: se aplica comision)
  stake          unidades apostadas
  p_modelo       probabilidad propia en el momento de apostar (opcional)
  nota           texto libre

CLV = cuota_efectiva x p_cierre_sin_margen - 1, con el cierre de Betfair
Exchange (bfec_*) si existe y el cierre medio (avgc_*) si no. Sin margen por
el metodo de potencia. En Betfair, cuota_efectiva = 1 + (cuota - 1)(1 - comision).

Uso:  python calcular_clv.py [--comision 0.05]
Salida: resultados/bitacora_clv.csv y resultados/bitacora_clv.txt
"""
import argparse
from pathlib import Path
import numpy as np, pandas as pd

DIR = Path(__file__).parent
BITACORA = DIR / 'bitacora.csv'
OUT = DIR / 'resultados'
COLUMNAS = ['id', 'fecha_apuesta', 'liga', 'fecha_partido', 'local', 'visitante', 'mercado',
            'seleccion', 'cuota', 'casa', 'stake', 'p_modelo', 'nota']
CIERRES = {   # mercado -> (columnas Betfair cierre, columnas media cierre, selecciones)
    '1x2':  (['bfec_1', 'bfec_x', 'bfec_2'], ['avgc_1', 'avgc_x', 'avgc_2'], ['1', 'x', '2']),
    'ou25': (['bfec_over25', 'bfec_under25'], ['avgc_over25', 'avgc_under25'], ['over', 'under']),
}


def sin_margen(odds):
    inv = 1.0 / np.asarray(odds, float)
    lo, hi = 0.2, 5.0
    for _ in range(60):
        k = (lo + hi) / 2
        if (inv ** k).sum() < 1: hi = k
        else: lo = k
    p = inv ** ((lo + hi) / 2)
    return p / p.sum()


def cierre(fila, mercado, sel):
    bf, med, sels = CIERRES[mercado]
    k = sels.index(sel)
    for cols, nombre in ((bf, 'betfair'), (med, 'media')):
        if all(c in fila and pd.notna(fila[c]) and fila[c] > 1.01 for c in cols):
            return sin_margen([fila[c] for c in cols])[k], nombre, fila[cols[k]]
    return np.nan, None, np.nan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--comision', type=float, default=0.05)
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    if not BITACORA.exists():
        pd.DataFrame(columns=COLUMNAS).to_csv(BITACORA, index=False)
    b = pd.read_csv(BITACORA, dtype={'seleccion': str})
    lineas = []
    def p(s=''):
        print(s); lineas.append(s)
    if not len(b):
        p('Bitacora vacia: anade una fila por apuesta en bitacora.csv.')
        (OUT / 'bitacora_clv.txt').write_text('\n'.join(lineas), encoding='utf-8')
        return

    df = pd.read_csv(DIR / 'datos' / 'partidos.csv', low_memory=False)
    df['fecha'] = pd.to_datetime(df.fecha)
    equipos = {lg: set(g.local) | set(g.visitante) for lg, g in df.groupby('liga')}
    filas = []
    for x in b.itertuples(index=False):
        r = dict(x._asdict())
        r['seleccion'] = str(r['seleccion']).lower().strip()
        f = pd.Timestamp(r['fecha_partido'])
        m = df[(df.liga == r['liga']) & (df.local == r['local']) & (df.visitante == r['visitante'])
               & (df.fecha >= f - pd.Timedelta(days=1)) & (df.fecha <= f + pd.Timedelta(days=1))]
        betfair = str(r['casa']).lower().startswith('betfair')
        r['cuota_efectiva'] = 1 + (r['cuota'] - 1) * (1 - a.comision) if betfair else r['cuota']
        if not len(m):
            faltan = [e for e in (r['local'], r['visitante']) if e not in equipos.get(r['liga'], set())]
            r['estado'] = (f'NOMBRE: {", ".join(faltan)} no existe en {r["liga"]}' if faltan
                           else 'pendiente (aun no esta en partidos.csv)')
            filas.append(r); continue
        fila = m.iloc[0]
        pc, ref, oc = cierre(fila, r['mercado'], r['seleccion'])
        r.update(p_cierre=pc, ref_cierre=ref, cuota_cierre=oc,
                 clv=r['cuota_efectiva'] * pc - 1 if pd.notna(pc) else np.nan,
                 goles=f"{int(fila.gl)}-{int(fila.gv)}")
        gana = {'1': fila.gl > fila.gv, 'x': fila.gl == fila.gv, '2': fila.gl < fila.gv,
                'over': fila.gl + fila.gv > 2.5, 'under': fila.gl + fila.gv < 2.5}[r['seleccion']]
        r['gana'] = bool(gana)
        r['beneficio'] = r['stake'] * (r['cuota_efectiva'] - 1) if gana else -r['stake']
        r['estado'] = 'liquidada'
        filas.append(r)
    R = pd.DataFrame(filas)
    R.to_csv(OUT / 'bitacora_clv.csv', index=False)

    L = R[R.estado == 'liquidada']
    p(f'Apuestas en bitacora: {len(R)}  liquidadas: {len(L)}  '
      f'pendientes: {(R.estado.str.startswith("pendiente")).sum()}')
    for e in R[R.estado.str.startswith('NOMBRE')].estado.unique():
        p(f'  REVISAR {e}')
    v = L.dropna(subset=['clv'])
    if len(v):
        rng = np.random.default_rng(3)
        bs = [rng.choice(v.clv.values, len(v)).mean() for _ in range(3000)]
        lo, hi = np.percentile(bs, [2.5, 97.5])
        p(f'CLV medio {v.clv.mean()*100:+.2f}%  IC95 [{lo*100:+.2f}, {hi*100:+.2f}]  '
          f'CLV>0 {(v.clv > 0).mean()*100:.0f}%  (cierre Betfair en {(v.ref_cierre == "betfair").mean()*100:.0f}%)')
        p(f'ROI {L.beneficio.sum() / L.stake.sum()*100:+.1f}%  beneficio {L.beneficio.sum():+.2f} u  '
          f'stake total {L.stake.sum():.2f} u')
        if len(v) < 200:
            p(f'Muestra corta ({len(v)} apuestas): no sacar conclusiones antes de 200-300.')
        for col in ('casa', 'mercado', 'liga'):
            t = v.groupby(col).agg(n=('clv', 'size'), clv=('clv', 'mean'), roi=('beneficio', 'sum'),
                                   stake=('stake', 'sum'))
            t['clv'] *= 100; t['roi'] = t.roi / t.stake * 100
            p(f'\n--- por {col} ---'); p(t.drop(columns='stake').round(2).to_string())
    (OUT / 'bitacora_clv.txt').write_text('\n'.join(lineas), encoding='utf-8')


if __name__ == '__main__':
    main()

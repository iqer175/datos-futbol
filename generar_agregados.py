#!/usr/bin/env python3
"""
generar_agregados.py  --  anadir al repo iqer175/datos-futbol

Lee datos/partidos.csv y escribe archivos pequenos (<45 KB) que un asistente
puede leer directamente desde raw.githubusercontent.com sin descargar los 4 MB.

Ejecutar al final de actualizar_datos.py:
    python generar_agregados.py

Salida:
    datos/agregados/equipos_<liga>.csv   perfil ponderado por equipo (~4 KB)
    datos/agregados/ligas.csv            medias por liga y temporada (~6 KB)
    datos/agregados/arbitros.csv         Premier y Championship (~1 KB)
    datos/h2h/<liga>.csv                 enfrentamientos directos (~20-45 KB)
    datos/cierres/<liga>.csv             cuotas de cierre temporada actual (~2-23 KB)
    datos/indice.json                    rutas, tamanos y fecha de corte
"""
import pandas as pd, numpy as np, os, json, warnings
from datetime import datetime, timezone
warnings.filterwarnings('ignore')

# --- parametros del modelo (deben coincidir con los de la skill) -------------
HALF_LIFE = 180.0   # dias; un partido de hace 6 meses pesa la mitad que uno de hoy
K_SHRINK  = 6.0     # partidos virtuales de media de liga: frena las muestras cortas
MAX_N     = 40      # tope de partidos por equipo
TEMPS_ACT = ('2026', '2026-27')   # actualizar cada verano

BASE  = os.path.dirname(os.path.abspath(__file__))
DATOS = os.path.join(BASE, 'datos')
SLUG = {'LaLiga':'laliga','LaLiga Hypermotion':'laliga2','Premier League':'premier',
        'Championship':'championship','Serie A':'seriea','Serie B':'serieb',
        'Bundesliga':'bundesliga','Ligue 1':'ligue1','Eredivisie':'eredivisie',
        'Liga Portugal':'portugal','Brasileirao':'brasil','Primera Division':'argentina'}

def main():
    df = pd.read_csv(os.path.join(DATOS, 'partidos.csv'), low_memory=False)
    df['fecha'] = pd.to_datetime(df['fecha'])
    hoy = df.fecha.max() + pd.Timedelta(days=1)
    for sub in ('agregados', 'h2h', 'cierres'):
        os.makedirs(os.path.join(DATOS, sub), exist_ok=True)

    # ---- 1. perfil ponderado por equipo, UNA LIGA POR ARCHIVO ----------------
    # Clave: se filtra por la liga en la que juega ahora, para no mezclar
    # categorias en equipos recien ascendidos o descendidos.
    for liga, g in df.groupby('liga'):
        ref = g.tail(600)
        L = {'gfc': ref.gl.mean(), 'gff': ref.gv.mean(),
             'corc': ref.corners_l.mean(), 'corf': ref.corners_v.mean(),
             'amc': ref.amar_l.mean(), 'amf': ref.amar_v.mean()}
        filas = []
        for eq in pd.unique(pd.concat([g.local, g.visitante])):
            m = g[(g.local == eq) | (g.visitante == eq)].sort_values('fecha').tail(MAX_N)
            if len(m) < 5:
                continue
            w = 0.5 ** (((hoy - m.fecha).dt.days.values) / HALF_LIFE)
            loc = (m.local == eq).values

            def P(serie_l, serie_v, casa, base):
                sel = loc if casa else ~loc
                if sel.sum() == 0 or pd.isna(base):
                    return '' if pd.isna(base) else round(float(base), 3)
                x = np.nan_to_num(np.where(loc, serie_l, serie_v)[sel], nan=base)
                ww = w[sel]
                return round(float((np.sum(ww * x) + K_SHRINK * base) / (np.sum(ww) + K_SHRINK)), 3)

            u = m.tail(6)
            filas.append(dict(
                equipo=eq, liga=liga, n=len(m),
                gf_casa=P(m.gl.values, m.gv.values, 1, L['gfc']),
                gc_casa=P(m.gv.values, m.gl.values, 1, L['gff']),
                gf_fuera=P(m.gl.values, m.gv.values, 0, L['gff']),
                gc_fuera=P(m.gv.values, m.gl.values, 0, L['gfc']),
                cor_f_casa=P(m.corners_l.values, m.corners_v.values, 1, L['corc']),
                cor_c_casa=P(m.corners_v.values, m.corners_l.values, 1, L['corf']),
                cor_f_fuera=P(m.corners_l.values, m.corners_v.values, 0, L['corf']),
                cor_c_fuera=P(m.corners_v.values, m.corners_l.values, 0, L['corc']),
                amar_casa=P(m.amar_l.values, m.amar_v.values, 1, L['amc']),
                amar_fuera=P(m.amar_l.values, m.amar_v.values, 0, L['amf']),
                ult6_gf=round(float(np.where(u.local == eq, u.gl, u.gv).mean()), 2),
                ult6_gc=round(float(np.where(u.local == eq, u.gv, u.gl).mean()), 2),
                ultimo=str(m.fecha.max().date())))
        if filas:
            pd.DataFrame(filas).to_csv(
                os.path.join(DATOS, 'agregados', f'equipos_{SLUG[liga]}.csv'), index=False)

    # ---- 2. medias por liga y temporada -------------------------------------
    lf = []
    for (liga, temp), g in df.groupby(['liga', 'temporada']):
        t = g.gl + g.gv
        lf.append(dict(liga=liga, temporada=temp, n=len(g),
            goles=round(t.mean(), 2), over15=round((t > 1.5).mean(), 3),
            over25=round((t > 2.5).mean(), 3), over35=round((t > 3.5).mean(), 3),
            btts=round(((g.gl > 0) & (g.gv > 0)).mean(), 3),
            gf_casa=round(g.gl.mean(), 2), gf_fuera=round(g.gv.mean(), 2),
            local_gana=round((g.res == 'H').mean(), 3), empate=round((g.res == 'D').mean(), 3),
            corners=round((g.corners_l + g.corners_v).mean(), 2) if g.corners_l.notna().any() else '',
            amarillas=round((g.amar_l + g.amar_v).mean(), 2) if g.amar_l.notna().any() else ''))
    pd.DataFrame(lf).to_csv(os.path.join(DATOS, 'agregados', 'ligas.csv'), index=False)

    # ---- 3. arbitros (solo Premier y Championship traen el dato) ------------
    ar = df[df.arbitro.notna()]
    if len(ar):
        a = ar.groupby('arbitro').agg(n=('fecha', 'size'), al=('amar_l', 'mean'),
                                      av=('amar_v', 'mean'), rl=('rojas_l', 'mean'),
                                      rv=('rojas_v', 'mean')).reset_index()
        a = a[a.n >= 15]
        a['amarillas_tot'] = (a.al + a.av).round(2)
        a['rojas_tot'] = (a.rl + a.rv).round(3)
        a[['arbitro', 'n', 'amarillas_tot', 'rojas_tot']].to_csv(
            os.path.join(DATOS, 'agregados', 'arbitros.csv'), index=False)

    # ---- 4. head to head por liga ------------------------------------------
    for liga, g in df.groupby('liga'):
        g = g.copy()
        g['par'] = [' | '.join(sorted([l, v])) for l, v in zip(g.local, g.visitante)]
        rows = []
        for par, h in g.groupby('par'):
            if len(h) < 2:
                continue
            a, b = par.split(' | ')
            t = h.gl + h.gv
            rows.append(dict(eq_a=a, eq_b=b, n=len(h), goles=round(t.mean(), 2),
                over25=round((t > 2.5).mean(), 2),
                btts=round(((h.gl > 0) & (h.gv > 0)).mean(), 2),
                ultimos=';'.join(f"{r.local[:3]}{int(r.gl)}-{int(r.gv)}{r.visitante[:3]}"
                                 for _, r in h.sort_values('fecha').tail(5).iterrows())))
        if rows:
            pd.DataFrame(rows).to_csv(os.path.join(DATOS, 'h2h', f'{SLUG[liga]}.csv'), index=False)

    # ---- 5. cuotas de cierre de la temporada en curso (para el CLV) ---------
    act = df[df.temporada.isin(TEMPS_ACT)]
    for liga, g in act.groupby('liga'):
        g[['fecha', 'local', 'visitante', 'gl', 'gv', 'b365c_1', 'b365c_x', 'b365c_2',
           'avgc_1', 'avgc_x', 'avgc_2', 'b365c_over25', 'b365c_under25']].to_csv(
            os.path.join(DATOS, 'cierres', f'{SLUG[liga]}.csv'), index=False)

    # ---- 6. indice con rutas y tamanos --------------------------------------
    idx = {'generado': datetime.now(timezone.utc).isoformat(timespec='seconds'),
           'ultimo_partido': str(df.fecha.max().date()),
           'parametros': {'half_life_dias': HALF_LIFE, 'k_shrink': K_SHRINK, 'max_n': MAX_N},
           'slugs': SLUG, 'archivos': {}}
    for sub in ('agregados', 'h2h', 'cierres'):
        d = os.path.join(DATOS, sub)
        for f in sorted(os.listdir(d)):
            idx['archivos'][f'{sub}/{f}'] = round(os.path.getsize(os.path.join(d, f)) / 1024, 1)
    grandes = {k: v for k, v in idx['archivos'].items() if v > 45}
    idx['aviso'] = ('todos los archivos por debajo de 45 KB' if not grandes
                    else f'archivos grandes, partir: {grandes}')
    with open(os.path.join(DATOS, 'indice.json'), 'w') as fh:
        json.dump(idx, fh, indent=2, ensure_ascii=False)
    print(json.dumps(idx['archivos'], indent=2))
    print(idx['aviso'])

if __name__ == '__main__':
    main()

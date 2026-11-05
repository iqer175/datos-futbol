#!/usr/bin/env python3
"""
informe_laliga.py -- informe semanal de LaLiga (se genera los jueves).

Para cada partido de LaLiga de los proximos dias (calendario sacado de las
cuotas capturadas, porque football-data no publica partidos futuros):
  - motor: 1X2 y Mas 2,5, fiabilidad del Elo
  - mercado: Pinnacle y Betfair Exchange sin margen (ultima captura) y como se
    ha movido Pinnacle desde la primera captura de ese partido
  - mejor cuota disponible entre las casas capturadas
  - variante O/U con xG en prueba (solo informativa)

IMPORTANTE, medido en los backtests: el motor NO tiene ventaja demostrada sobre
el mercado (peso optimo 0 en 1X2). Una discrepancia grande motor-mercado no es
una apuesta: es una senal para buscar informacion (bajas, alineaciones) que
explique quien tiene razon. Por eso el informe no recomienda apuestas.

Uso:  python informe_laliga.py [--liga LaLiga] [--dias 8]
Salida: informes/<slug>_AAAA-MM-DD.md
"""
import argparse
from datetime import datetime, timezone
from pathlib import Path

import numpy as np, pandas as pd

import motor
import emparejar_nombres as en
import espn

DIR = Path(__file__).parent
DIR_INF = DIR / 'informes'
UMBRAL_DISCREPANCIA = 0.08      # 8 puntos de probabilidad
REFERENCIAS = ['pinnacle', 'betfair_ex_eu']


def sin_margen(odds):
    inv = 1.0 / np.asarray(odds, float)
    lo, hi = 0.2, 5.0
    for _ in range(60):
        k = (lo + hi) / 2
        if (inv ** k).sum() < 1: hi = k
        else: lo = k
    p = inv ** ((lo + hi) / 2)
    return p / p.sum()


def probs_casa(g, casa, mercado, sels):
    x = g[(g.casa == casa) & (g.mercado == mercado)].set_index('seleccion').cuota
    if not all(s in x.index for s in sels):
        return None
    return sin_margen([x[s] for s in sels])


DIAS = ['lun', 'mar', 'mié', 'jue', 'vie', 'sáb', 'dom']


def fecha_es(t):
    return f'{DIAS[t.weekday()]} {t.day:02d} {t.strftime("%H:%M")}'


def pct(p):
    return '—' if p is None or (isinstance(p, float) and np.isnan(p)) else f'{p*100:.0f}'


def leer(ruta, n=6):
    try:
        return [l for l in Path(ruta).read_text(encoding='utf-8').splitlines() if l.strip()][:n]
    except OSError:
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--liga', default='LaLiga')
    ap.add_argument('--dias', type=int, default=8)
    a = ap.parse_args()
    ahora = pd.Timestamp(datetime.now(timezone.utc))

    df = motor.cargar(DIR / 'datos' / 'partidos.csv')
    cap = en.cargar_capturas()
    if len(cap):
        en.emparejar(cap, df)                   # actualiza el mapa con equipos nuevos
        cap = en.traducir(cap)
        cap = cap[(cap.liga == a.liga) & cap.local.notna() & cap.visitante.notna()].copy()
        cap['inicio'] = pd.to_datetime(cap.inicio_utc, utc=True)
        cap['t'] = pd.to_datetime(cap.capturado_utc, utc=True)
        cap = cap[(cap.inicio > ahora) & (cap.inicio <= ahora + pd.Timedelta(days=a.dias))]
    else:
        cap = pd.DataFrame(columns=['local', 'visitante', 'inicio', 't', 'casa', 'mercado', 'seleccion', 'cuota'])

    # calendario: ESPN; si no responde, el de las cuotas capturadas
    cal = espn.calendario([a.liga], ahora.to_pydatetime(), a.dias)
    if len(cal):
        en.emparejar(cal, df)
        cal = en.traducir(cal)
        cal['inicio'] = pd.to_datetime(cal.inicio_utc, utc=True)
        cal = cal[(cal.inicio > ahora) & cal.local.notna() & cal.visitante.notna()]
        partidos = cal[['local', 'visitante', 'inicio']]
        fuente_cal = 'ESPN'
    else:
        partidos = cap.groupby(['local', 'visitante'], as_index=False).inicio.first()
        fuente_cal = 'cuotas capturadas (ESPN no respondió)'
    if not len(partidos):
        raise SystemExit(f'No hay partidos de {a.liga} en los proximos {a.dias} dias.')

    B = motor.Base(df)
    filas = []
    for p_ in partidos.itertuples(index=False):
        loc, vis = p_.local, p_.visitante
        g = cap[(cap.local == loc) & (cap.visitante == vis)]
        ult = g[g.t == g.t.max()] if len(g) else g
        pri = g[g.t == g.t.min()] if len(g) else g
        r = dict(local=loc, visitante=vis, inicio=p_.inicio, capturas=g.t.nunique() if len(g) else 0)
        try:
            R = motor.analizar(B, loc, vis, a.liga)
            m = R['m']
            r.update(m1=m['1'], mx=m['X'], m2=m['2'], mo=m['over2.5'], fiab=R['fiabilidad'],
                     xg_motor=R.get('over25_xg_motor'))
        except SystemExit as e:
            r.update(m1=np.nan, mx=np.nan, m2=np.nan, mo=np.nan, fiab=f'sin motor: {e}', xg_motor=None)
        ref = None
        for casa in REFERENCIAS:
            p = probs_casa(ult, casa, '1x2', ['1', 'x', '2'])
            po = probs_casa(ult, casa, 'ou25', ['over', 'under'])
            if p is not None:
                r[f'{casa}_1x2'] = p; r[f'{casa}_o'] = po[0] if po is not None else np.nan
                ref = ref or casa
        r['ref'] = ref
        # O/U: Pinnacle solo publica su linea principal y Betfair no da totales en
        # The Odds API; si falta Pinnacle 2,5 se usa el consenso sin margen del resto
        if np.isnan(r.get('pinnacle_o', np.nan)):
            ps = [probs_casa(ult, c, 'ou25', ['over', 'under']) for c in ult.casa.unique()
                  if not c.startswith('betfair')]
            ps = [q[0] for q in ps if q is not None]
            r['ou_ref'], r['ou_fuente'] = (float(np.mean(ps)), f'consenso {len(ps)} casas') if ps else (np.nan, '')
        else:
            r['ou_ref'], r['ou_fuente'] = r['pinnacle_o'], 'Pinnacle'
        # movimiento de Pinnacle desde la primera captura
        p0 = probs_casa(pri, 'pinnacle', '1x2', ['1', 'x', '2'])
        p1 = r.get('pinnacle_1x2')
        r['mov_local'] = (p1[0] - p0[0]) if p0 is not None and p1 is not None and g.t.nunique() > 1 else np.nan
        # mejor cuota por seleccion (y en que casa)
        for mk, sels in (('1x2', ['1', 'x', '2']), ('ou25', ['over', 'under'])):
            for s in sels:
                x = ult[(ult.mercado == mk) & (ult.seleccion == s) & ~ult.casa.str.startswith('betfair')]
                r[f'max_{s}'] = (x.cuota.max(), x.loc[x.cuota.idxmax(), 'casa']) if len(x) else (np.nan, '')
        # variante xG: se mezcla con la mejor referencia de O/U (no hay bet365 capturado)
        po_ref = r['ou_ref']
        if r['xg_motor'] is not None and po_ref is not None and not np.isnan(po_ref):
            lg = lambda q: np.log(q / (1 - q))
            z = motor.OU_XG_W_MOTOR * lg(r['xg_motor']) + (1 - motor.OU_XG_W_MOTOR) * lg(po_ref)
            r['xg_mezcla'] = 1 / (1 + np.exp(-z))
        filas.append(r)
    T = pd.DataFrame(filas).sort_values('inicio')

    hoy = ahora.strftime('%Y-%m-%d')
    L = [f'# Informe {a.liga} — {hoy}', '',
         f'Generado {ahora.strftime("%Y-%m-%d %H:%M")} UTC con datos hasta {df.fecha.max().date()} '
         f'y cuotas capturadas hasta {cap.t.max().strftime("%Y-%m-%d %H:%M") if len(cap) else "—"} UTC. '
         f'Calendario: {fuente_cal}. '
         f'{len(T)} partidos en los próximos {a.dias} días.', '',
         '> **El motor no tiene ventaja demostrada sobre el mercado** (backtest 2022-2026: peso '
         'óptimo 0 frente a la apertura de bet365). Este informe no recomienda apuestas. Una '
         f'discrepancia de más de {UMBRAL_DISCREPANCIA*100:.0f} puntos motor-mercado es una señal '
         'para buscar información (bajas, alineaciones), no un valor.', '',
         '## 1X2 (probabilidades en %)', '',
         '| Partido | Hora (UTC) | Motor 1-X-2 | Pinnacle 1-X-2 | Betfair 1-X-2 | Mov. Pinnacle local | Mejor cuota 1 / X / 2 | Fiabilidad |',
         '|---|---|---|---|---|---|---|---|']
    avisos = []
    for r in T.itertuples():
        d = r._asdict()
        pin, bf = d.get('pinnacle_1x2'), d.get('betfair_ex_eu_1x2')
        mot = (r.m1, r.mx, r.m2)
        f3 = lambda p: '—' if p is None or (isinstance(p, float)) else ' - '.join(pct(v) for v in p)
        mov = '—' if np.isnan(r.mov_local) else f'{r.mov_local*100:+.1f}'
        mejor = ' / '.join(f'{d[f"max_{s}"][0]:.2f}' if not np.isnan(d[f"max_{s}"][0]) else '—' for s in '1x2')
        L.append(f'| {r.local} - {r.visitante} | {fecha_es(r.inicio)} | '
                 f'{" - ".join(pct(v) for v in mot)} | {f3(pin)} | {f3(bf)} | {mov} | {mejor} | {r.fiab} |')
        refp = pin if pin is not None else bf
        if refp is not None and not np.isnan(r.m1):
            dif = np.array(mot) - np.array(refp)
            k = int(np.argmax(np.abs(dif)))
            if abs(dif[k]) > UMBRAL_DISCREPANCIA:
                avisos.append(f'- **{r.local} - {r.visitante}**: el motor da {pct(mot[k])} % a '
                              f'"{"1X2"[k]}" y el mercado {pct(refp[k])} % ({dif[k]*100:+.0f} puntos).')
    L += ['', '## Más/Menos 2,5 (probabilidad de Más, %)', '',
          '| Partido | Motor | Variante xG (en prueba) | Mercado | Fuente del mercado | Mejor Más / Menos |',
          '|---|---|---|---|---|---|']
    for r in T.itertuples():
        d = r._asdict()
        xm = d.get('xg_mezcla')
        mo, mu = d['max_over'][0], d['max_under'][0]
        L.append(f'| {r.local} - {r.visitante} | {pct(r.mo)} | {pct(xm) if xm is not None else "—"} | '
                 f'{pct(d.get("ou_ref"))} | {d.get("ou_fuente") or "—"} | '
                 f'{"—" if np.isnan(mo) else f"{mo:.2f}"} / {"—" if np.isnan(mu) else f"{mu:.2f}"} |')
    L += ['', 'La variante xG mezcla el motor con el mercado capturado (no hay cuota de bet365), '
          'así que no es exactamente la variante congelada, que usa la apertura de bet365.', '',
          '## Discrepancias para investigar', '']
    L += avisos or ['Ninguna por encima del umbral.']
    L += ['', '## Alineaciones', '', 'Las alineaciones se publican ~1 h antes de cada partido. El aviso de '
          'titulares habituales ausentes se actualiza solo en `informes/alertas_alineaciones.md` '
          '(workflow alineaciones.yml, cada 20 minutos).']
    L += ['', '## Estado del seguimiento y de la bitácora', '', '```']
    L += leer(DIR / 'resultados' / 'seguimiento_ou_xg.txt') or ['(sin seguimiento todavía)']
    L += [''] + (leer(DIR / 'resultados' / 'bitacora_clv.txt', 4) or ['(bitácora sin calcular)'])
    L += ['```', '', 'Las cuotas de bet365 no se capturan de forma automática: antes de apostar, '
          'compruébalas en bet365 y apunta la apuesta en `bitacora.csv`.']

    DIR_INF.mkdir(exist_ok=True)
    ruta = DIR_INF / f'{en.norm(a.liga).replace(" ", "")}_{hoy}.md'
    ruta.write_text('\n'.join(L) + '\n', encoding='utf-8')
    print(f'Informe escrito en {ruta}  ({len(T)} partidos)')


if __name__ == '__main__':
    main()

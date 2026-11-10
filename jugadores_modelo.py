#!/usr/bin/env python3
"""
jugadores_modelo.py -- modelo de goleador y tiros por jugador (en pruebas).

Para un jugador TITULAR (estos mercados se apostarian con la alineacion ya
publicada), con datos solo anteriores al partido:

  ritmo por 90'  = (suma de sus ultimos N_J partidos + K partidos virtuales de la
                    media de su posicion) / (minutos/90 + K)      [tiros, a puerta, goles]
  minutos        = media de sus minutos cuando es titular (ultimas 10 titularidades)
  factor partido = tiros que concede el rival / media de la liga, x factor campo
  lambda         = ritmo x minutos/90 x factor partido
  goles          = mitad su ritmo de goles, mitad su ritmo a puerta x conversion
                   de su posicion (los goles solos son demasiado ruidosos)

Probabilidades con Poisson o binomial negativa (tiros sobredispersos):
  P(marca), P(tiros >= 1), P(tiros >= 2), P(a puerta >= 1)

Sin cuotas de bet365 de estos mercados no se puede medir el valor: este script
solo mide la CALIBRACION (cuando dice 30 %, ocurre un 30 %?) frente a una
referencia simple (media de la posicion x minutos esperados).

Uso:  python jugadores_modelo.py
Salida: resultados/jugadores_calibracion.txt
"""
from pathlib import Path

import numpy as np, pandas as pd
from scipy.stats import nbinom, poisson

DIR = Path(__file__).parent
JUG = DIR / 'datos' / 'alineaciones' / 'jugadores.csv'
OUT = DIR / 'resultados'
N_J, N_EQ, K_RITMO, K_GOL, K_EQ = 20, 10, 5.0, 15.0, 3.0
INI_TEST = pd.Timestamp('2026-07-01')


def grupo(pos):
    p = str(pos).upper()
    if p == 'G':
        return 'POR'
    if p.startswith(('CD', 'LB', 'RB', 'D', 'LWB', 'RWB', 'CB', 'SW')):
        return 'DEF'
    if p.startswith(('F', 'CF', 'LF', 'RF', 'ST', 'S')) and p != 'SUB':
        return 'DEL'
    return 'MED'


def cargar():
    j = pd.read_csv(JUG, dtype=str)
    j['fecha'] = pd.to_datetime(j.fecha)
    for c in ('minutos', 'tiros', 'tiros_puerta', 'goles'):
        j[c] = pd.to_numeric(j[c], errors='coerce')
    j['titular'] = j.titular == 'True'
    j = j[j.minutos.notna()].copy()
    # posicion habitual sin 'SUB' -> grupo
    pos = j[j.posicion.notna() & (j.posicion != 'SUB')].groupby('jugador_id').posicion.agg(
        lambda x: x.value_counts().index[0])
    j['grupo'] = j.jugador_id.map(pos).map(grupo).fillna('MED')
    # rival y campo de cada fila
    eqs = j.groupby(['espn_id', 'equipo_id']).agg(lado=('lado', 'first')).reset_index()
    riv = eqs.merge(eqs, on='espn_id', suffixes=('', '_r'))
    riv = riv[riv.equipo_id != riv.equipo_id_r][['espn_id', 'equipo_id', 'equipo_id_r']]
    j = j.merge(riv, on=['espn_id', 'equipo_id'], how='left')
    return j.sort_values('fecha').reset_index(drop=True)


def totales_equipo(j):
    t = j.groupby(['espn_id', 'fecha', 'equipo_id', 'equipo_id_r', 'lado']).agg(
        tiros=('tiros', 'sum'), goles=('goles', 'sum')).reset_index()
    c = t[['espn_id', 'equipo_id', 'tiros', 'goles']].rename(
        columns={'equipo_id': 'equipo_id_r', 'tiros': 'tiros_c', 'goles': 'goles_c'})
    return t.merge(c, on=['espn_id', 'equipo_id_r'])


def predecir(j, T, objetivo, dispersion=None):
    """Lambdas para las filas 'objetivo' (titulares) usando solo partidos anteriores."""
    filas = []
    for fecha, g in objetivo.groupby('fecha'):
        h = j[j.fecha < fecha]
        th = T[T.fecha < fecha]
        if h.espn_id.nunique() < 30:
            continue
        # medias de liga por posicion (por 90') y conversion de goles por tiro a puerta
        m90 = h[h.minutos > 0].groupby('grupo')[['tiros', 'tiros_puerta', 'goles', 'minutos']].sum()
        r_pos = m90[['tiros', 'tiros_puerta', 'goles']].div(m90.minutos / 90, axis=0)
        conv = (m90.goles / m90.tiros_puerta.replace(0, np.nan)).fillna(0.3)
        liga_tiros = th.tiros.mean(); liga_goles = th.goles.mean()
        loc_f = th[th.lado == 'home'].tiros.mean() / liga_tiros
        vis_f = th[th.lado == 'away'].tiros.mean() / liga_tiros
        for x in g.itertuples():
            ph = h[(h.jugador_id == x.jugador_id) & (h.minutos > 0)].tail(N_J)
            mins = ph.minutos.sum() / 90
            pr = r_pos.loc[x.grupo] if x.grupo in r_pos.index else r_pos.mean()
            r_t = (ph.tiros.sum() + K_RITMO * pr.tiros) / (mins + K_RITMO)
            r_p = (ph.tiros_puerta.sum() + K_RITMO * pr.tiros_puerta) / (mins + K_RITMO)
            r_g = (ph.goles.sum() + K_GOL * pr.goles) / (mins + K_GOL)
            tit = h[(h.jugador_id == x.jugador_id) & h.titular].tail(10)
            m_esp = tit.minutos.mean() if len(tit) else {'POR': 90, 'DEF': 84, 'MED': 76, 'DEL': 72}[x.grupo]
            # rival: tiros y goles concedidos (encogidos hacia la liga)
            rc = th[th.equipo_id == x.equipo_id_r].tail(N_EQ)
            f_tiros = ((rc.tiros_c.sum() + K_EQ * liga_tiros) / (len(rc) + K_EQ)) / liga_tiros
            f_goles = ((rc.goles_c.sum() + K_EQ * liga_goles) / (len(rc) + K_EQ)) / liga_goles
            f_campo = loc_f if x.lado == 'home' else vis_f
            u = m_esp / 90
            lam_t = r_t * u * f_tiros * f_campo
            lam_p = r_p * u * f_tiros * f_campo
            lam_g = (0.5 * r_g + 0.5 * r_p * conv.get(x.grupo, 0.3)) * u * f_goles * f_campo
            base_t = pr.tiros * u; base_p = pr.tiros_puerta * u; base_g = pr.goles * u
            filas.append(dict(espn_id=x.espn_id, fecha=fecha, jugador=x.jugador, grupo=x.grupo,
                              min_esp=m_esp, minutos=x.minutos, lam_t=lam_t, lam_p=lam_p, lam_g=lam_g,
                              base_t=base_t, base_p=base_p, base_g=base_g,
                              tiros=x.tiros, puerta=x.tiros_puerta, goles=x.goles))
    return pd.DataFrame(filas)


def p_al_menos(lam, k, alpha=None):
    """P(X >= k) con Poisson (alpha None) o binomial negativa de varianza lam + alpha·lam^2."""
    lam = np.asarray(lam, float)
    if not alpha:
        return 1 - poisson.cdf(k - 1, lam)
    n = 1 / alpha; p = n / (n + lam)
    return 1 - nbinom.cdf(k - 1, n, p)


def llb(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6); return float(-np.mean(np.where(y, np.log(p), np.log(1 - p))))


def alpha_nb(lam, y):
    """Dispersion de la binomial negativa por maxima verosimilitud."""
    from scipy.optimize import minimize_scalar
    def nll(a):
        n = 1 / a; p = n / (n + lam)
        return -nbinom.logpmf(y, n, p).sum()
    return minimize_scalar(nll, bounds=(1e-3, 3), method='bounded').x


def main():
    OUT.mkdir(exist_ok=True)
    j = cargar()
    T = totales_equipo(j)
    obj = j[j.titular & (j.fecha >= pd.Timestamp('2025-11-01'))]
    R = predecir(j, T, obj)
    dev = R[R.fecha < INI_TEST]; tst = R[R.fecha >= INI_TEST]
    a_t = alpha_nb(dev.lam_t.values, dev.tiros.values.astype(int))
    a_p = alpha_nb(dev.lam_p.values, dev.puerta.values.astype(int))
    L = [f'Titulares evaluados: {len(R):,} (desarrollo {len(dev):,} hasta jun-2026, prueba {len(tst):,} desde jul-2026)',
         f'Dispersion binomial negativa (ajustada en desarrollo): tiros {a_t:.3f}, a puerta {a_p:.3f}', '']
    mercados = [('Marca (goles >= 1)', 'lam_g', 'base_g', 'goles', 1, None),
                ('Tiros >= 1', 'lam_t', 'base_t', 'tiros', 1, a_t),
                ('Tiros >= 2', 'lam_t', 'base_t', 'tiros', 2, a_t),
                ('Tiros >= 3', 'lam_t', 'base_t', 'tiros', 3, a_t),
                ('A puerta >= 1', 'lam_p', 'base_p', 'puerta', 1, a_p),
                ('A puerta >= 2', 'lam_p', 'base_p', 'puerta', 2, a_p)]
    for nombre, lam, base, col, k, a in mercados:
        L.append(f'=== {nombre} ===')
        for bl, d in (('desarrollo', dev), ('prueba', tst)):
            y = d[col].values >= k
            pm = p_al_menos(d[lam], k, a); pb = p_al_menos(d[base], k, a)
            pp = p_al_menos(d[lam], k, None)
            L.append(f'  [{bl:10s}] log-loss modelo {llb(pm, y):.4f}  referencia {llb(pb, y):.4f}  '
                     f'(Poisson {llb(pp, y):.4f})   media predicha {pm.mean()*100:.1f} %  real {y.mean()*100:.1f} %')
        y = tst[col].values >= k; pm = p_al_menos(tst[lam], k, a)
        tr = pd.qcut(pm, 5, duplicates='drop')
        c = pd.DataFrame(dict(p=pm, y=y, tr=tr)).groupby('tr', observed=True).agg(n=('y', 'size'), pred=('p', 'mean'), real=('y', 'mean'))
        L.append('  calibracion en prueba por quintiles: ' + ' | '.join(
            f'{r.pred*100:.0f}%→{r.real*100:.0f}% (n={r.n})' for r in c.itertuples()))
        L.append('')
    # por posicion en prueba (marca)
    y = tst.goles.values >= 1; pm = p_al_menos(tst.lam_g, 1)
    t = pd.DataFrame(dict(g=tst.grupo, p=pm, y=y)).groupby('g').agg(n=('y', 'size'), pred=('p', 'mean'), real=('y', 'mean'))
    L.append('Marca por posicion (prueba): ' + ' | '.join(f'{g}: {r.pred*100:.0f}% vs {r.real*100:.0f}% (n={r.n})' for g, r in t.iterrows()))
    L.append(f'Minutos esperados vs reales (prueba): {tst.min_esp.mean():.1f} vs {tst.minutos.mean():.1f}')
    txt = '\n'.join(L)
    print(txt)
    (OUT / 'jugadores_calibracion.txt').write_text(txt + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()

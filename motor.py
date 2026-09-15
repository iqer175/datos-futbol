"""
motor.py -- motor de pronostico v3.  Sustituye a modelo.py.

QUE CAMBIA Y POR QUE
--------------------
v1 sacaba los goles esperados cruzando las medias de goles de cada equipo
(ataque de uno contra defensa del otro). Eso se rompe en cuanto un equipo cambia
de categoria: sus partidos en la liga nueva son pocos, el shrinkage lo aplasta
contra la media de liga y el motor devuelve "equipo medio" disfrazado de
pronostico. Ocurre en 7 de cada 10 partidos a principio de temporada.

v2 separa las dos preguntas y las resuelve con la fuente que aguanta cada una:

  SUPREMACIA (quien gana y por cuanto)  <- Elo, al 100%.
      El Elo es global y continuo: no se resetea al subir o bajar de categoria,
      y esta disponible en el 100% de los partidos de la base. Medido en
      backtest: mejora el log-loss de 1X2 frente a v1 en -0.015 global,
      -0.025 en muestras medias y -0.046 en muestras rotas.

  TOTAL DE GOLES  <- 65% linea base de liga ajustada por Elo + 35% GLM de Poisson
      con efectos de equipo (ataque y defensa estimados a la vez, corrigiendo
      por la calidad del rival y compartidos entre categorias). Medido: -0.003
      global y -0.007 en muestras rotas frente a Elo solo.

  CORNERS Y TARJETAS <- medias ponderadas por recencia, como en v1. No encontre
      nada mejor; se acompanan del peso efectivo para poder descartarlas.

LO QUE PROBE Y NO FUNCIONO (documentado para no repetirlo)
  - Convertir los partidos de la otra categoria con factores medidos
    (Championship->Premier: ataque x0.68, defensa x1.81). Sin ganancia medible
    una vez el Elo lleva la supremacia: 1.0637 -> 1.0673 en muestra rota.
  - Usar el GLM tambien para la supremacia: peor que el Elo en todos los grupos
    (1.0800 vs 1.0427 en muestra rota), porque el ridge encoge al recien
    ascendido hacia la media, que es el mismo fallo de v1.

QUE CAMBIA EN v3 (2026-09-15)
  Dos recalibraciones medidas sobre los 5.160 partidos del backtest y validadas
  fuera de muestra en cuatro cortes temporales distintos:
    - 1X2: el motor daba +3.5 puntos de mas al visitante. Se corrige multiplicando
      local y empate y renormalizando. Mejora 0.0015-0.0029 de log-loss en los 4 cortes.
    - Goles: el motor predecia 2.8 puntos menos de Mas 2.5 de los que ocurren.
      Se corrige sumando 0.12 al total. Mejora 0.0018-0.0027 en los 4 cortes.
  El sesgo del visitante importa mas de lo que sugiere el log-loss: 3.5 puntos son
  casi la mitad del colchon de 8 que exige la skill, asi que una parte del "valor"
  que el motor encontraba en visitantes era este sesgo y no una ineficiencia real.

LO QUE SE PROBO EN v3 Y SE DESCARTO
  - Correccion del Elo por forma reciente. La hipotesis era que el Elo tarda en bajar
    a los equipos que se hunden. MEDIDO SOBRE 50.060 observaciones equipo-partido:
    el residuo del Elo NO persiste, REVIERTE (coef -0.042, t=-3.1). Un equipo que ha
    rendido por debajo de su Elo tiende a rendir por ENCIMA despues. El mercado, en
    cambio, no tiene ese sesgo (coef -0.002, t=-0.1). La hipotesis era falsa y venia
    de mirar cinco equipos que acababan de perder, que es tautologico.
  - Temperatura sobre el Over (encoger hacia 50%). Mejora en tres cortes pero EMPEORA
    en el mas reciente, y su parametro se mueve de 0.71 a 0.55 entre cortes. Sobreajuste.

LIMITE QUE NO SE ARREGLA CON MODELADO
  El mercado sigue ganando en todos los grupos (1X2: 0.9970 vs 1.0139 en muestra
  sana; 1.0117 vs 1.0427 en muestra rota). Este motor es una referencia
  calibrada, no una fuente de ventaja. La ventaja sale de informacion que el
  mercado no ha digerido, no de la discrepancia del motor.
"""
import numpy as np, pandas as pd
from math import exp, factorial
from scipy.optimize import minimize

HALF_LIFE = 180.0
RIDGE     = 3.0
VENTANA   = 1460      # dias de historico para el GLM
W_GLM     = 0.35      # peso del GLM en el total (0.65 se lo lleva la linea Elo)

# --- calibracion v3, medida sobre 5.160 partidos de backtest (2025-08 a 2026-09) ---
# El motor v2 tenia dos sesgos sistematicos frente al resultado real:
#   1X2  : daba +3.5 puntos de mas al visitante, -1.8 al local y -1.7 al empate.
#   goles: predecia 2.8 puntos MENOS de Mas 2.5 de los que ocurren.
# Las dos correcciones se ajustaron solo con datos anteriores al corte y se
# validaron despues; mejoran el log-loss en los CUATRO cortes temporales probados.
CAL_TOTAL  = 0.12     # goles que se suman al total (equivale a +0.115 en el logit del Over)
CAL_LOCAL  = 0.1966   # multiplica la probabilidad de local por exp(), luego se renormaliza
CAL_EMPATE = 0.1972   # idem para el empate
K_SHRINK  = 6.0
MAX_N     = 40


# ---------------------------------------------------------------- carga y coefs
def cargar(path):
    df = pd.read_csv(path, low_memory=False)
    df['fecha'] = pd.to_datetime(df['fecha'])
    return df


def coeficientes(df, hasta=None):
    """a = goles de supremacia por 100 puntos de Elo; b = localia; bt/at = total."""
    d = df[df.fecha < hasta] if hasta is not None else df
    out = {}
    for liga, g in d.groupby('liga'):
        if len(g) < 200: continue
        x = (g.elo_l_pre - g.elo_v_pre).values / 100.0
        a, b = np.polyfit(x, (g.gl - g.gv).values, 1)
        at, bt = np.polyfit(np.abs(x), (g.gl + g.gv).values, 1)
        out[liga] = dict(a=a, b=b, at=at, bt=bt,
                         cor=(g.corners_l + g.corners_v).mean(),
                         amar=(g.amar_l + g.amar_v).mean())
    return out


# ---------------------------------------------------------------------- GLM
def ajustar_glm(df, hasta, ridge=RIDGE, ventana=VENTANA, half=HALF_LIFE):
    d = df[(df.fecha < hasta) & (df.fecha >= hasta - pd.Timedelta(days=ventana))]
    if len(d) < 500: return None
    eqs = pd.Index(sorted(set(d.local) | set(d.visitante)))
    lgs = pd.Index(sorted(d.liga.unique()))
    il, iv = eqs.get_indexer(d.local.values), eqs.get_indexer(d.visitante.values)
    ig = lgs.get_indexer(d.liga.values)
    w = 0.5 ** ((hasta - d.fecha).dt.days.values / half)
    gl, gv = d.gl.values.astype(float), d.gv.values.astype(float)
    ne, nl = len(eqs), len(lgs)

    def obj(p):
        att, dfn = p[:ne], p[ne:2*ne]
        base, loc = p[2*ne:2*ne+nl], p[2*ne+nl:]
        eh = base[ig] + loc[ig] + att[il] - dfn[iv]
        ea = base[ig] + att[iv] - dfn[il]
        mh, ma = np.exp(np.clip(eh, -3, 2.5)), np.exp(np.clip(ea, -3, 2.5))
        nll = np.sum(w*(mh - gl*eh)) + np.sum(w*(ma - gv*ea)) + ridge*(att@att + dfn@dfn)
        rh, ra = w*(mh - gl), w*(ma - gv)
        return nll, np.concatenate([
            np.bincount(il, rh, ne) + np.bincount(iv, ra, ne) + 2*ridge*att,
            -(np.bincount(iv, rh, ne) + np.bincount(il, ra, ne)) + 2*ridge*dfn,
            np.bincount(ig, rh, nl) + np.bincount(ig, ra, nl),
            np.bincount(ig, rh, nl)])

    p0 = np.zeros(2*ne + 2*nl); p0[2*ne:2*ne+nl] = np.log(1.3)
    r = minimize(obj, p0, jac=True, method='L-BFGS-B', options=dict(maxiter=400))
    return dict(att=r.x[:ne], dfn=r.x[ne:2*ne], base=r.x[2*ne:2*ne+nl],
                loc=r.x[2*ne+nl:], eqs=eqs, lgs=lgs)


def total_glm(G, local, visit, liga):
    if G is None or local not in G['eqs'] or visit not in G['eqs'] or liga not in G['lgs']:
        return None
    il, iv, ig = G['eqs'].get_loc(local), G['eqs'].get_loc(visit), G['lgs'].get_loc(liga)
    lh = exp(G['base'][ig] + G['loc'][ig] + G['att'][il] - G['dfn'][iv])
    la = exp(G['base'][ig] + G['att'][iv] - G['dfn'][il])
    return lh + la


# ------------------------------------------------------------- perfil auxiliar
class Base:
    def __init__(self, df, hasta=None):
        self.df = df
        self.hasta = pd.Timestamp(hasta) if hasta is not None else df.fecha.max() + pd.Timedelta(days=1)
        self.co = coeficientes(df, self.hasta)
        L = pd.concat([
            df.assign(eq=df.local, cf=df.corners_l, cc=df.corners_v, am=df.amar_l, elo=df.elo_l_pre),
            df.assign(eq=df.visitante, cf=df.corners_v, cc=df.corners_l, am=df.amar_v, elo=df.elo_v_pre),
        ])[['eq','liga','pais','fecha','cf','cc','am','elo']].sort_values(['eq','fecha'])
        self.idx = {eq: g for eq, g in L.groupby('eq')}
        self.glm = ajustar_glm(df, self.hasta)

    def elo(self, eq, hasta=None):
        """Devuelve (elo, dias desde el ultimo partido, partidos totales en la base).
        n_base importa: un equipo con pocos partidos EN TODA LA BASE tiene un Elo
        de inicializacion, no medido. Arezzo llega de Serie C con 3 partidos: su
        1417 es el valor semilla, no una fuerza estimada."""
        h = hasta or self.hasta; g = self.idx.get(eq)
        if g is None: return np.nan, np.nan, 0
        p = g[g.fecha < h]
        if not len(p): return np.nan, np.nan, 0
        return float(p.elo.iloc[-1]), int((h - p.fecha.iloc[-1]).days), len(p)

    def secundarios(self, eq, liga, hasta=None):
        """Corners y tarjetas: medias ponderadas en la liga objetivo + peso efectivo."""
        h = hasta or self.hasta; C = self.co[liga]; g = self.idx.get(eq)
        if g is None: return None
        m = g[(g.fecha < h) & (g.liga == liga)].tail(MAX_N)
        if not len(m): return None
        w = 0.5 ** ((h - m.fecha).dt.days.values / HALF_LIFE)
        def p(x, base):
            x = np.nan_to_num(x, nan=base)
            return (np.sum(w*x) + K_SHRINK*base) / (np.sum(w) + K_SHRINK)
        return dict(cor_f=p(m.cf.values, C['cor']/2), cor_c=p(m.cc.values, C['cor']/2),
                    amar=p(m.am.values, C['amar']/2), peso=float(w.sum()),
                    n=len(m), desde=m.fecha.min().date())


# ------------------------------------------------------------------- mercados
def pois(k, lam): return exp(-lam) * lam**k / factorial(k)

def mercados(lh, la, lc, lt, calibrar=True):
    M = np.outer([pois(i, lh) for i in range(11)], [pois(j, la) for j in range(11)]); M /= M.sum()
    i, j = np.indices(M.shape); tot = i + j
    p1, px, p2 = M[i>j].sum(), float(np.trace(M)), M[i<j].sum()
    if calibrar:   # recalibracion 1X2 medida en backtest: ver CAL_LOCAL / CAL_EMPATE
        q = np.array([p1*exp(CAL_LOCAL), px*exp(CAL_EMPATE), p2]); q /= q.sum()
        p1, px, p2 = q
    r = {'1': p1, 'X': px, '2': p2}
    for ln in (0.5,1.5,2.5,3.5,4.5): r[f'over{ln}'] = M[tot>ln].sum()
    r['btts'] = M[1:,1:].sum()
    r['gana_l_2plus'] = M[(i-j)>=2].sum(); r['gana_v_2plus'] = M[(j-i)>=2].sum()
    for ln in (7.5,8.5,9.5,10.5,11.5): r[f'cor_over{ln}'] = sum(pois(k,lc) for k in range(int(ln)+1,45))
    for ln in (2.5,3.5,4.5,5.5): r[f'tar_over{ln}'] = sum(pois(k,lt) for k in range(int(ln)+1,30))
    return r


def analizar(B, local, visit, liga, hasta=None):
    h = pd.Timestamp(hasta) if hasta else B.hasta
    C = B.co[liga]
    el, gap_l, nb_l = B.elo(local, h); ev, gap_v, nb_v = B.elo(visit, h)
    if np.isnan(el) or np.isnan(ev):
        raise SystemExit(f"Sin Elo para {local if np.isnan(el) else visit}")
    d = (el - ev) / 100.0

    sup = C['a'] * d + C['b']                 # supremacia: Elo puro
    tot_elo = C['bt'] + C['at'] * abs(d)      # linea base de total
    tg = total_glm(B.glm, local, visit, liga)
    tot = tot_elo if tg is None else (1 - W_GLM) * tot_elo + W_GLM * tg
    tot += CAL_TOTAL      # correccion de calibracion: el motor se quedaba corto en goles

    lh = max(0.15, (tot + sup) / 2); la = max(0.15, (tot - sup) / 2)

    sl = B.secundarios(local, liga, h); sv = B.secundarios(visit, liga, h)
    if sl and sv:
        lc = (sl['cor_f'] + sv['cor_c'] + sv['cor_f'] + sl['cor_c']) / 2
        lt = sl['amar'] + sv['amar']
        peso2 = min(sl['peso'], sv['peso'])
    else:
        lc, lt, peso2 = C['cor'], C['amar'], 0.0

    # fiabilidad: el Elo obsoleto es mas ruidoso (err_abs 1.44 vs 1.21 en backtest)
    gap = max(gap_l, gap_v); nb = min(nb_l, nb_v)
    if nb < 15:
        fiab = f'baja: Elo sin asentar ({nb} partidos en toda la base)'
    elif gap > 200:
        fiab = f'baja: Elo obsoleto ({gap} dias sin jugar en la base)'
    elif gap > 30 or nb < 30:
        fiab = 'media'
    else:
        fiab = 'alta'

    return dict(liga=liga, elo_l=el, elo_v=ev, gap_l=gap_l, gap_v=gap_v,
                nb_l=nb_l, nb_v=nb_v, fiabilidad=fiab,
                lh=lh, la=la, lc=lc, lt=lt, sup=sup, tot=tot, tot_elo=tot_elo, tot_glm=tg,
                peso_secundarios=peso2, sl=sl, sv=sv, m=mercados(lh, la, lc, lt))


def informe(R, local, visit):
    m = R['m']
    print(f"\n{local} vs {visit}  [{R['liga']}]")
    print(f"  Elo {R['elo_l']:.0f} ({R['nb_l']} part.) - {R['elo_v']:.0f} ({R['nb_v']} part.)   fiabilidad: {R['fiabilidad']}")
    print(f"  supremacia {R['sup']:+.2f}   total {R['tot']:.2f}  (Elo {R['tot_elo']:.2f} / GLM {R['tot_glm'] if R['tot_glm'] is None else round(R['tot_glm'],2)})")
    print(f"  xG {R['lh']:.2f} - {R['la']:.2f}   corners {R['lc']:.1f}  tarjetas {R['lt']:.1f}  (peso secundarios {R['peso_secundarios']:.1f})")
    print(f"  1X2  {m['1']*100:.1f} / {m['X']*100:.1f} / {m['2']*100:.1f}")
    print(f"  O1.5 {m['over1.5']*100:.0f}  O2.5 {m['over2.5']*100:.0f}  O3.5 {m['over3.5']*100:.0f}  BTTS {m['btts']*100:.0f}")
    print(f"  cuotas justas: 1={1/m['1']:.2f} X={1/m['X']:.2f} 2={1/m['2']:.2f} O2.5={1/m['over2.5']:.2f} U2.5={1/(1-m['over2.5']):.2f}")

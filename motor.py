"""
motor.py -- motor de pronostico v6.  Sustituye a modelo.py.

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

QUE CAMBIA EN v4 (2026-11-05)
  Recalibracion del 1X2 medida en walk-forward (backtest_clv.py): la correccion v3 del
  local era excesiva y daba ~3 puntos de mas al local. Ver CAL_LOCAL / CAL_EMPATE.
  El aparente "rebaje de favoritos" frente al mercado NO es un sesgo: calibrado sobre si
  mismo, el motor acierta con los favoritos; parece tibio solo en los partidos que el
  mercado ve muy claros, porque es un estimador mas ruidoso. Estirarlo empeora.

QUE CAMBIA EN v5 (2026-11-05) -- experimentos_motor.py, regla de 4/4 temporadas
  Entra: K del Elo 20 -> 15 (y recalibracion de CAL_LOCAL/CAL_EMPATE).
  Descartado: vida media del GLM 120-540 (efecto < 0.0005), Dixon-Coles rho (empeora:
  la correccion de empates ya lo cubre), regresion estival 0.05-0.35 combinada con K=15,
  ventaja local propia por liga en el Elo (efecto nulo), y CAL_TOTAL 0.044 en vez de 0.12
  (gana 2 temporadas y pierde 2: el nivel de goles cambia de un ano a otro).

QUE CAMBIA EN v6 (2026-11-10) -- experimento_remates.py, regla de 4/4 temporadas
  El GLM deja de aprender solo de los goles: su objetivo es mitad goles, mitad un "xG de
  remates" (REM_CP por remate a puerta + REM_CF por remate fuera, coeficientes ajustados
  solo con partidos anteriores a 2022-07). Los remates estan en el 91 % de la base; el xG,
  en el 5 %. Con ese GLM sube su peso en el total (W_GLM 0.35 -> 0.60) y entra por primera
  vez en la supremacia (V_GLM 0.30; el resto sigue siendo Elo). Medido sobre 27.525
  partidos: mejora las 4 temporadas en 1X2 (-0.0001 a -0.0023) y en Mas/Menos (-0.0011 a
  -0.0017). Elegida entre 17 combinaciones: la cifra esta algo favorecida por la busqueda.
  Con el GLM de goles (v2) la supremacia mixta NO mejoraba (2/4): la ganancia es de los remates.
  El mercado sigue por delante (1X2 0.0125-0.018, Mas/Menos 0.0055-0.008).

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
W_GLM     = 0.60      # peso del GLM en el total (el resto se lo lleva la linea Elo)
V_GLM     = 0.30      # peso del GLM en la supremacia (el resto, Elo)
W_GLM_V5  = 0.35      # peso del GLM de goles en el motor v5: lo usan los backtests antiguos
# objetivo del GLM (v6): REM_ALPHA·goles + (1 - REM_ALPHA)·(REM_CP·a puerta + REM_CF·fuera)
REM_ALPHA = 0.5
REM_CP, REM_CF = 0.331, -0.015    # goles por remate a puerta / fuera, ajustados con datos < 2022-07

# --- calibracion v3, medida sobre 5.160 partidos de backtest (2025-08 a 2026-09) ---
# El motor v2 tenia dos sesgos sistematicos frente al resultado real:
#   1X2  : daba +3.5 puntos de mas al visitante, -1.8 al local y -1.7 al empate.
#   goles: predecia 2.8 puntos MENOS de Mas 2.5 de los que ocurren.
# Las dos correcciones se ajustaron solo con datos anteriores al corte y se
# validaron despues; mejoran el log-loss en los CUATRO cortes temporales probados.
CAL_TOTAL  = 0.12     # goles que se suman al total (equivale a +0.115 en el logit del Over)
# v4 (2026-11-05): CAL_LOCAL 0.1966 -> 0.050 y CAL_EMPATE 0.1972 -> 0.133. Medido en
# walk-forward con backtest_clv.py (33.042 partidos, 2022-07..2026-11): la v3 daba ~3 puntos
# de MAS al local en las 4 temporadas y en 21 de 22 ligas; el fallo real era falta de empate
# (-2 puntos) y exceso de visitante (+2). Ajustado solo con 2022-24 y mejora el log-loss en
# las 4 temporadas (-0.0008 a -0.0026); reajustado por temporada sale 0.05-0.09 / 0.133-0.138.
# Probado y descartado: temperatura sobre las probabilidades (T=0.93), empeora la mas reciente.
# v5 (2026-11-05): con el Elo a K=15 (actualizar_datos.K_BASE) se recalibran a 0.061 / 0.177
# (ajuste 2022-24). Frente a v4 mejora el log-loss 1X2 en las 4 temporadas (-0.0003 a -0.0010).
# v6 (2026-11-10): con el GLM de remates y la supremacia mixta se recalibran a 0.055 / 0.128
# (ajuste 2022-24, experimento_remates.py). Comprobado por el camino real (Base + analizar)
# frente al v5 de produccion en 27.433 partidos: 1X2 -0.0019 -0.0001 -0.0010 -0.0010 y
# Mas/Menos -0.0022 -0.0016 -0.0024 -0.0017, mejora en las 4 temporadas
# (resultados/motor_v6_verificacion.txt). CAL_TOTAL se queda en 0.12: con 0.072 o 0.155 tambien
# mejora 4/4 en Mas/Menos, pero 0.12 es el mas parejo entre temporadas y no toca la variante
# congelada de xG, que lo usa.
CAL_LOCAL  = 0.055    # multiplica la probabilidad de local por exp(), luego se renormaliza
CAL_EMPATE = 0.128    # idem para el empate
K_SHRINK  = 6.0
MAX_N     = 40

# --- variante CONGELADA Mas/Menos 2,5 con xG (2026-11-04) ---------------------
# Elegida en backtest_xg.py sobre 1.161 partidos (2026-09-07..2026-11-02), la
# mejor de 45 variantes: el resultado esta sesgado a favor por la busqueda.
# NO TOCAR estos valores: se evaluan solo con partidos posteriores a la fecha
# de congelacion (seguimiento_ou_xg.py). Si no pasan, se descartan, no se reajustan.
#   total O/U = GLM ajustado SOLO a xG (alpha=0) + CAL_TOTAL, sin linea Elo
#   p_final   = logit-mezcla: 0.2·motor + 0.8·apertura bet365 sin margen (potencia)
# Solo vale en ligas con xG en la temporada (ver ligas_con_xg).
OU_XG_FECHA_CONGELACION = '2026-11-04'
OU_XG_ALPHA   = 0.0       # peso de los goles en el objetivo del GLM (0 = solo xG)
OU_XG_W_MOTOR = 0.2       # peso del motor en la mezcla con la apertura de bet365
OU_XG_HALF_LIFE = 180.0   # vida media del GLM xG, fijada aparte para que cambiar HALF_LIFE no la toque


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
def objetivo_xg(df, alpha):
    """Objetivo mixto alpha·goles + (1-alpha)·xG; en partidos sin xG, goles."""
    gl, gv = df.gl.values.astype(float), df.gv.values.astype(float)
    if 'xg_l' not in df:
        return gl, gv
    hay = (df.xg_l.notna() & df.xg_v.notna()).values
    xl = np.where(hay, df.xg_l.values, gl); xv = np.where(hay, df.xg_v.values, gv)
    return alpha*gl + (1-alpha)*xl, alpha*gv + (1-alpha)*xv


def objetivo_remates(df, alpha=None):
    """Objetivo del GLM v6: alpha·goles + (1-alpha)·xG de remates; sin remates, goles."""
    alpha = REM_ALPHA if alpha is None else alpha
    gl, gv = df.gl.values.astype(float), df.gv.values.astype(float)
    cols = ['tiros_l', 'tiros_v', 'tiros_p_l', 'tiros_p_v']
    if not all(c in df for c in cols):
        return gl, gv
    hay = df[cols].notna().all(axis=1).values
    xl = np.where(hay, REM_CP * df.tiros_p_l + REM_CF * (df.tiros_l - df.tiros_p_l).clip(lower=0), gl)
    xv = np.where(hay, REM_CP * df.tiros_p_v + REM_CF * (df.tiros_v - df.tiros_p_v).clip(lower=0), gv)
    return alpha * gl + (1 - alpha) * xl, alpha * gv + (1 - alpha) * xv


def ligas_con_xg(df, hasta, minimo=0.8, dias=60):
    """Ligas donde al menos el 80 % de los partidos de los ultimos 60 dias trae xG.
    (Con 120 dias, en septiembre entraban partidos de mayo sin xG y se perdian ligas.)"""
    if 'xg_l' not in df:
        return set()
    d = df[(df.fecha < hasta) & (df.fecha >= hasta - pd.Timedelta(days=dias))]
    c = d.groupby('liga').xg_l.apply(lambda s: s.notna().mean())
    return set(c[c >= minimo].index)


def ajustar_glm(df, hasta, ridge=RIDGE, ventana=VENTANA, half=HALF_LIFE, y=None):
    """y = (y_local, y_visitante) alineados con df; por defecto, los goles."""
    m = ((df.fecha < hasta) & (df.fecha >= hasta - pd.Timedelta(days=ventana))).values
    d = df[m]
    if len(d) < 500: return None
    eqs = pd.Index(sorted(set(d.local) | set(d.visitante)))
    lgs = pd.Index(sorted(d.liga.unique()))
    il, iv = eqs.get_indexer(d.local.values), eqs.get_indexer(d.visitante.values)
    ig = lgs.get_indexer(d.liga.values)
    w = 0.5 ** ((hasta - d.fecha).dt.days.values / half)
    if y is None:
        gl, gv = d.gl.values.astype(float), d.gv.values.astype(float)
    else:
        gl, gv = np.asarray(y[0], float)[m], np.asarray(y[1], float)[m]
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


def lambdas_glm(G, local, visit, liga):
    """(goles esperados del local, del visitante) segun el GLM, o None si no los conoce."""
    if G is None or local not in G['eqs'] or visit not in G['eqs'] or liga not in G['lgs']:
        return None
    il, iv, ig = G['eqs'].get_loc(local), G['eqs'].get_loc(visit), G['lgs'].get_loc(liga)
    lh = exp(G['base'][ig] + G['loc'][ig] + G['att'][il] - G['dfn'][iv])
    la = exp(G['base'][ig] + G['att'][iv] - G['dfn'][il])
    return lh, la


def total_glm(G, local, visit, liga):
    g = lambdas_glm(G, local, visit, liga)
    return None if g is None else g[0] + g[1]


# ------------------------------------------------------------- perfil auxiliar
class Base:
    def __init__(self, df, hasta=None):
        self.df = df
        self.hasta = pd.Timestamp(hasta) if hasta is not None else df.fecha.max() + pd.Timedelta(days=1)
        self.co = coeficientes(df, self.hasta)
        # Elo DESPUES de cada partido: el ultimo valor antes de 'hasta' es la fuerza
        # vigente. Con elo_*_pre el motor iba un partido por detras (mediana 9.5 puntos).
        # Si el CSV es antiguo y no trae las columnas post, se cae al pre.
        el_l = df.elo_l_post if 'elo_l_post' in df else df.elo_l_pre
        el_v = df.elo_v_post if 'elo_v_post' in df else df.elo_v_pre
        L = pd.concat([
            df.assign(eq=df.local, cf=df.corners_l, cc=df.corners_v, am=df.amar_l, elo=el_l),
            df.assign(eq=df.visitante, cf=df.corners_v, cc=df.corners_l, am=df.amar_v, elo=el_v),
        ])[['eq','liga','pais','fecha','cf','cc','am','elo']].sort_values(['eq','fecha'])
        self.idx = {eq: g for eq, g in L.groupby('eq')}
        self.glm = ajustar_glm(df, self.hasta, y=objetivo_remates(df))    # v6: goles + remates
        # GLM de la variante congelada O/U con xG
        self.ligas_xg = ligas_con_xg(df, self.hasta)
        self.glm_xg = (ajustar_glm(df, self.hasta, half=OU_XG_HALF_LIFE, y=objetivo_xg(df, OU_XG_ALPHA))
                       if self.ligas_xg else None)

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

    sup_elo = C['a'] * d + C['b']             # supremacia segun el Elo
    tot_elo = C['bt'] + C['at'] * abs(d)      # linea base de total
    g = lambdas_glm(B.glm, local, visit, liga)
    tg, sg = (None, None) if g is None else (g[0] + g[1], g[0] - g[1])
    tot = tot_elo if tg is None else (1 - W_GLM) * tot_elo + W_GLM * tg
    sup = sup_elo if sg is None else (1 - V_GLM) * sup_elo + V_GLM * sg
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

    # variante congelada O/U con xG: solo el Mas 2,5 del motor, antes de mezclar
    # con el mercado (eso lo hace ou_xg_congelado, que necesita la cuota de bet365)
    tg_xg = total_glm(getattr(B, 'glm_xg', None), local, visit, liga) \
        if liga in getattr(B, 'ligas_xg', set()) else None
    over_xg = None if tg_xg is None else float(1 - sum(pois(k, tg_xg + CAL_TOTAL) for k in range(3)))

    return dict(liga=liga, elo_l=el, elo_v=ev, gap_l=gap_l, gap_v=gap_v,
                nb_l=nb_l, nb_v=nb_v, fiabilidad=fiab,
                lh=lh, la=la, lc=lc, lt=lt, sup=sup, sup_elo=sup_elo, sup_glm=sg,
                tot=tot, tot_elo=tot_elo, tot_glm=tg,
                tot_glm_xg=tg_xg, over25_xg_motor=over_xg,
                peso_secundarios=peso2, sl=sl, sv=sv, m=mercados(lh, la, lc, lt))


def sin_margen_2(o_a, o_b):
    """Probabilidad sin margen del primer lado de un mercado de dos (metodo potencia)."""
    lo, hi = 0.2, 5.0
    for _ in range(60):
        k = (lo + hi) / 2
        if (1/o_a)**k + (1/o_b)**k < 1: hi = k
        else: lo = k
    k = (lo + hi) / 2
    a, b = (1/o_a)**k, (1/o_b)**k
    return a / (a + b)


def ou_xg_congelado(over_motor, cuota_over_b365, cuota_under_b365):
    """Probabilidad final de Mas 2,5 de la variante congelada.
    over_motor: R['over25_xg_motor']. Devuelve None si no aplica a esa liga."""
    if over_motor is None:
        return None
    pa = sin_margen_2(cuota_over_b365, cuota_under_b365)
    lg = lambda p: np.log(p / (1 - p))
    z = OU_XG_W_MOTOR * lg(over_motor) + (1 - OU_XG_W_MOTOR) * lg(pa)
    return float(1 / (1 + np.exp(-z)))


def informe(R, local, visit):
    m = R['m']
    print(f"\n{local} vs {visit}  [{R['liga']}]")
    print(f"  Elo {R['elo_l']:.0f} ({R['nb_l']} part.) - {R['elo_v']:.0f} ({R['nb_v']} part.)   fiabilidad: {R['fiabilidad']}")
    print(f"  supremacia {R['sup']:+.2f}   total {R['tot']:.2f}  (Elo {R['tot_elo']:.2f} / GLM {R['tot_glm'] if R['tot_glm'] is None else round(R['tot_glm'],2)})")
    print(f"  xG {R['lh']:.2f} - {R['la']:.2f}   corners {R['lc']:.1f}  tarjetas {R['lt']:.1f}  (peso secundarios {R['peso_secundarios']:.1f})")
    print(f"  1X2  {m['1']*100:.1f} / {m['X']*100:.1f} / {m['2']*100:.1f}")
    print(f"  O1.5 {m['over1.5']*100:.0f}  O2.5 {m['over2.5']*100:.0f}  O3.5 {m['over3.5']*100:.0f}  BTTS {m['btts']*100:.0f}")
    print(f"  cuotas justas: 1={1/m['1']:.2f} X={1/m['X']:.2f} 2={1/m['2']:.2f} O2.5={1/m['over2.5']:.2f} U2.5={1/(1-m['over2.5']):.2f}")
    if R.get('over25_xg_motor') is not None:
        print(f"  [EN PRUEBA desde {OU_XG_FECHA_CONGELACION}] O2.5 motor-xG {R['over25_xg_motor']*100:.0f} "
              f"(total xG {R['tot_glm_xg']:.2f}); mezclar con ou_xg_congelado() y la cuota de bet365. "
              f"No apostar con ella hasta que pase seguimiento_ou_xg.py")

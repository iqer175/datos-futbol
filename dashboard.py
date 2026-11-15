#!/usr/bin/env python3
"""
dashboard.py -- panel local para analizar jornadas con el modelo actual.

  streamlit run dashboard.py        (o doble clic en dashboard.bat)

Tres pestañas:
  - Solo modelo: calendario de ESPN + motor.analizar para cada partido (sin cuotas,
    sin agentes, al momento). Probabilidades y cuota justa.
  - Historial: cualquier carpeta ligas/<slug>/jornadas/<fecha>/ ya analizada
    (recomendaciones, evaluacion.csv, contexto, comparación de casas, lecciones).
  - Análisis completo: lanza `claude -p` sin ventana con los agentes del proyecto
    (recolector-cuotas -> contexto -> analista-contexto -> simulador-apuestas
    [-> comparar casas]). Necesita Chrome abierto con la extensión de Claude.
    El registro de cada ejecución queda en dashboard_runs/.

Solo lee ficheros; lo único que escribe son los registros de dashboard_runs/ (y lo que
escriban los agentes en el análisis completo, como siempre).
"""
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

DIR = Path(__file__).parent
sys.path.insert(0, str(DIR))
import ligas  # noqa: E402

RUNS = DIR / 'dashboard_runs'

st.set_page_config(page_title='Jornadas · datos-futbol', page_icon='⚽', layout='wide')


# ------------------------------------------------------------------ utilidades
def leer_json(p):
    try:
        return json.loads(Path(p).read_text(encoding='utf-8'))
    except Exception:
        return None


def pct(x, d=1):
    return None if x is None or pd.isna(x) else round(100 * float(x), d)


def cuota_justa(p):
    return None if not p else round(1 / p, 2)


def jornadas_existentes():
    filas = []
    for slug_dir in sorted((DIR / 'ligas').glob('*/jornadas/*')):
        if not slug_dir.is_dir():
            continue
        f = {n: (slug_dir / n).exists() for n in
             ('cuotas.json', 'contexto.json', 'evaluacion.csv', 'recomendaciones.json', 'comparacion_casas.json')}
        if not any(f.values()):
            continue
        rec = leer_json(slug_dir / 'recomendaciones.json') if f['recomendaciones.json'] else None
        filas.append({'slug': slug_dir.parent.parent.name, 'fecha': slug_dir.name, 'ruta': slug_dir,
                      'n_rec': len(rec) if isinstance(rec, list) else None, **f})
    return filas


NOMBRE_DE_SLUG = {v[0]: k for k, v in ligas.LIGAS.items()}


# ------------------------------------------------------------------ solo modelo
@st.cache_resource(show_spinner='Cargando datos y motor…')
def base_motor():
    import motor
    D = motor.cargar(DIR / 'datos' / 'partidos.csv')
    return motor, D, motor.Base(D)


@st.cache_data(ttl=1800, show_spinner='Leyendo calendario de ESPN…')
def calendario(liga, dias):
    import espn
    cal = espn.calendario([liga], datetime.now(timezone.utc), dias)
    cal = cal[cal.estado == 'STATUS_SCHEDULED']
    mapa = pd.read_csv(DIR / 'datos' / 'mapa_nombres.csv')
    m = {r.nombre_src: r.nombre_fd for r in mapa[(mapa.fuente == 'espn') & (mapa.liga == liga)].itertuples()}
    return cal.assign(local=cal.local_src.map(m), visitante=cal.visitante_src.map(m)).sort_values('inicio_utc')


def modelo_partido(liga, loc, vis):
    import analizar_jornada as aj
    motor, D, B = base_motor()
    R = motor.analizar(B, loc, vis, liga)
    m = R['m']
    DL = D[D.liga == liga]
    fl, dl = aj._forma(DL, loc)
    fv, dv = aj._forma(DL, vis)
    return {'R': R, 'm': m, 'forma_l': fl, 'forma_v': fv, 'ult_l': dl, 'ult_v': dv, 'h2h': aj._h2h(DL, loc, vis)}


def pestaña_modelo():
    c1, c2, c3 = st.columns([2, 1, 1])
    liga = c1.selectbox('Liga', list(ligas.LIGAS), key='m_liga')
    dias = c2.number_input('Días', 1, 14, 7, key='m_dias')
    c3.write(''); c3.write('')
    if c3.button('Calcular', type='primary', width='stretch'):
        st.session_state['m_calc'] = (liga, int(dias))
    if st.session_state.get('m_calc') != (liga, int(dias)):
        st.info('Elige liga y pulsa **Calcular**. Sin cuotas: son las probabilidades del motor (Elo + GLM), '
                'sin ancla al mercado ni noticias.')
        return
    try:
        cal = calendario(liga, int(dias))
    except Exception as e:
        st.error(f'No se pudo leer el calendario de ESPN: {e}'); return
    if not len(cal):
        st.warning('Sin partidos programados en ese plazo.'); return
    falta = cal[cal.local.isna() | cal.visitante.isna()]
    if len(falta):
        st.warning('Equipos sin emparejar en datos/mapa_nombres.csv: '
                   + ', '.join(sorted(set(falta.local_src) | set(falta.visitante_src))))
    filas, detalles = [], []
    for r in cal.dropna(subset=['local', 'visitante']).itertuples():
        try:
            x = modelo_partido(liga, r.local, r.visitante)
        except BaseException as e:  # motor lanza SystemExit si no conoce un equipo
            filas.append({'Inicio UTC': str(r.inicio_utc)[:16], 'Partido': f'{r.local} - {r.visitante}',
                          'Fiabilidad': f'sin modelo ({e})'}); continue
        R, m = x['R'], x['m']
        filas.append({'Inicio UTC': str(r.inicio_utc)[:16], 'Partido': f'{r.local} - {r.visitante}',
                      '1 %': pct(m['1']), 'X %': pct(m['X']), '2 %': pct(m['2']),
                      'Más 2.5 %': pct(m['over2.5']), 'Ambos %': pct(m['btts']),
                      'xG local': round(R['lh'], 2), 'xG visit.': round(R['la'], 2),
                      'Córners': None if R.get('lc') is None else round(R['lc'], 1),
                      'Tarjetas': None if R.get('lt') is None else round(R['lt'], 1),
                      'Fiabilidad': R['fiabilidad']})
        detalles.append((r, x))
    df = pd.DataFrame(filas)
    st.subheader(f'{liga}: {len(df)} partidos')
    st.dataframe(df, hide_index=True, width='stretch',
                 column_config={c: st.column_config.ProgressColumn(c, min_value=0, max_value=100, format='%.1f')
                                for c in ('1 %', 'X %', '2 %', 'Más 2.5 %', 'Ambos %')})
    st.caption('Cuota justa = 1 / probabilidad. Para que haya valor, bet365 tiene que pagar claramente más '
               '(el simulador pide +3 puntos sobre la necesaria).')
    for r, x in detalles:
        R, m = x['R'], x['m']
        with st.expander(f'{r.local} - {r.visitante}  ·  {R["fiabilidad"]}'):
            a, b, c, d = st.columns(4)
            a.metric(f'1 ({r.local})', f'{pct(m["1"])} %', f'justa {cuota_justa(m["1"])}', delta_color='off')
            b.metric('X', f'{pct(m["X"])} %', f'justa {cuota_justa(m["X"])}', delta_color='off')
            c.metric(f'2 ({r.visitante})', f'{pct(m["2"])} %', f'justa {cuota_justa(m["2"])}', delta_color='off')
            d.metric('Más 2.5', f'{pct(m["over2.5"])} %', f'justa {cuota_justa(m["over2.5"])}', delta_color='off')
            st.write(f'**Elo** {R["elo_l"]:.0f} - {R["elo_v"]:.0f} · **goles esperados** {R["lh"]:.2f} - {R["la"]:.2f} '
                     f'(total {R["tot"]:.2f})')
            g1, g2 = st.columns(2)
            g1.markdown(f'**{r.local}**: {x["forma_l"]}'); g1.text('\n'.join(x['ult_l']))
            g2.markdown(f'**{r.visitante}**: {x["forma_v"]}'); g2.text('\n'.join(x['ult_v']))
            st.caption(x['h2h'])


# ------------------------------------------------------------------ historial
def colorear_filas(fila):
    """Verde fuerte = limpia (pasa las reglas del simulador); verde suave = margen >= 0 sin ser limpia."""
    if fila.get('limpia') is True or fila.get('limpia') == 'True':
        estilo = 'background-color: rgba(34,160,90,.35)'
    elif pd.notna(fila.get('margen')) and fila['margen'] >= 0:
        estilo = 'background-color: rgba(34,160,90,.12)'
    else:
        estilo = ''
    return [estilo] * len(fila)


def ver_jornada(ruta: Path):
    slug, fecha = ruta.parent.parent.name, ruta.name
    liga = NOMBRE_DE_SLUG.get(slug, slug)
    cu = leer_json(ruta / 'cuotas.json') or {}
    cx = leer_json(ruta / 'contexto.json') or {}
    rec = leer_json(ruta / 'recomendaciones.json')
    st.header(f'{liga} · {fecha}')
    m1, m2, m3, m4 = st.columns(4)
    m1.metric('Partidos con cuotas', len(cu.get('partidos', [])))
    m2.metric('Cuotas leídas (UTC)', (cu.get('leido_utc') or '—')[5:16].replace('T', ' '))
    m3.metric('Noticias', cx.get('noticias', '—'))
    m4.metric('Recomendadas', '—' if rec is None else len(rec))

    t_rec, t_eval, t_part, t_casas, t_txt = st.tabs(
        ['Recomendadas', 'Todas las apuestas', 'Partidos', 'Comparar casas', 'Informe y lecciones'])

    with t_rec:
        if rec is None:
            st.info('Aún no hay recomendaciones.json (falta el simulador).')
        elif not rec:
            st.success('El simulador no recomendó ninguna apuesta en esta jornada.')
        else:
            df = pd.DataFrame([r.get('data', r) for r in rec])
            cols = [c for c in ('fecha', 'local', 'visitante', 'mercado', 'seleccion', 'cuota', 'prob',
                                'prob_apuesta', 'stake', 'confianza', 'casa', 'estado') if c in df]
            st.dataframe(df[cols], hide_index=True, width='stretch')
            for r in rec:
                d = r.get('data', r)
                with st.expander(f'{d.get("local")} - {d.get("visitante")}: {d.get("seleccion")} @ {d.get("cuota")}'):
                    st.markdown(f'**Tesis:** {d.get("tesis", "")}')
                    st.markdown(f'**Nota:** {d.get("nota", "")}')

    with t_eval:
        p = ruta / 'evaluacion.csv'
        if not p.exists():
            st.info('Aún no hay evaluacion.csv (falta `analizar_jornada.py simular`).')
        else:
            ev = pd.read_csv(p)
            f1, f2, f3, f4 = st.columns(4)
            part = f1.multiselect('Partido', sorted(ev.partido.unique()))
            tipo = f2.multiselect('Tipo', sorted(ev.tipo.dropna().unique()))
            solo = f3.checkbox('Solo limpias', False)
            mmin = f4.slider('Margen mínimo (pts)', -20.0, 10.0, -20.0, 0.5)
            x = ev
            if part: x = x[x.partido.isin(part)]
            if tipo: x = x[x.tipo.isin(tipo)]
            if solo and 'limpia' in x: x = x[x.limpia == True]  # noqa: E712
            x = x[x.margen * 100 >= mmin].sort_values('margen', ascending=False)
            vista = x[[c for c in ('partido', 'tipo', 'apuesta', 'cuota', 'prob', 'necesaria', 'margen',
                                    'prob_apuesta', 'p_titular', 'titularidades', 'goles', 'limpia', 'aviso') if c in x]]
            st.caption(f'{len(x)} de {len(ev)} apuestas · margen = prob − necesaria · verde fuerte = limpia '
                       '(pasa las reglas del simulador, que miran prob_apuesta y titularidad); verde suave = margen ≥ 0 '
                       'pero no limpia')
            st.dataframe(vista.style.apply(colorear_filas, axis=1)
                         .format({'prob': '{:.1%}', 'necesaria': '{:.1%}', 'margen': '{:+.1%}',
                                  'prob_apuesta': '{:.1%}', 'cuota': '{:.2f}'}, na_rep=''),
                         hide_index=True, width='stretch', height=520)

    with t_part:
        if not cx.get('partidos'):
            st.info('Aún no hay contexto.json.')
        cuotas = {(p['local'], p['visitante']): p for p in cu.get('partidos', [])}
        for P in cx.get('partidos', []):
            mot = P.get('motor') or {}
            titulo = f'{P["local"]} - {P["visitante"]}  ·  {P.get("inicio_utc", "")[:16].replace("T", " ")}'
            with st.expander(titulo):
                if P.get('resumen'):
                    st.markdown(f'**Resumen:** {P["resumen"]}')
                if mot:
                    mk = (cuotas.get((P['local'], P['visitante'])) or {}).get('mercados', {})
                    filas = []
                    for nom, k in (('1', 'p1'), ('X', 'px'), ('2', 'p2'), ('Más 2.5', 'over25'), ('Ambos marcan - Sí', 'btts')):
                        q = mk.get(nom)
                        filas.append({'Mercado': nom, 'Motor %': pct(mot.get(k)), 'Cuota bet365': q,
                                      'Implícita %': None if not q else round(100 / q, 1)})
                    st.dataframe(pd.DataFrame(filas), hide_index=True)
                    rem = (f' · remates esperados {mot["remates_l"]} - {mot["remates_v"]} '
                           f'(a puerta {mot["a_puerta_l"]} - {mot["a_puerta_v"]})') if mot.get('remates_l') else ''
                    st.caption(f'Elo {mot.get("elo_l")} - {mot.get("elo_v")} · xG {mot.get("lh")} - {mot.get("la")}{rem} · '
                               f'fiabilidad {mot.get("fiabilidad")}. La implícita lleva el margen de la casa.')
                g1, g2 = st.columns(2)
                g1.markdown(f'**{P["local"]}**: {P.get("forma_local", "")}')
                g2.markdown(f'**{P["visitante"]}**: {P.get("forma_visitante", "")}')
                def baja(b):
                    return b if isinstance(b, str) else f'{b.get("jugador", "?")} ({b.get("motivo", "")})'
                if P.get('bajas_local') or P.get('bajas_visitante'):
                    g1.markdown('Bajas: ' + ('; '.join(map(baja, P.get('bajas_local') or [])) or '—'))
                    g2.markdown('Bajas: ' + ('; '.join(map(baja, P.get('bajas_visitante') or [])) or '—'))
                st.caption(P.get('h2h', ''))
                if P.get('jugadores'):
                    tp = P.get('titular_prob', {})
                    jf = [{'Jugador': n, 'Equipo': j.get('equipo'), 'Titular prob': tp.get(n),
                           'Tit. últ. 3': j.get('titular_ultimos_3'), 'Titularidades': j.get('titularidades'),
                           'Min. medios': j.get('minutos_medios'),
                           'Remates últ. 5': ' '.join(map(str, j.get('remates_ultimos_5') or [])),
                           'A puerta últ. 5': ' '.join(map(str, j.get('a_puerta_ultimos_5') or [])),
                           'Nota': j.get('nota', '')} for n, j in P['jugadores'].items()]
                    st.dataframe(pd.DataFrame(jf), hide_index=True, width='stretch')
                for a in P.get('alertas', []):
                    st.warning(a, icon='⚠️')

    with t_casas:
        cc = leer_json(ruta / 'comparacion_casas.json')
        md = ruta / 'comparacion_casas.md'
        if md.exists():
            st.markdown(md.read_text(encoding='utf-8'))
        elif cc is not None:
            st.json(cc)
        else:
            st.info('No se han comparado casas en esta jornada.')

    with t_txt:
        for n in ('recomendaciones.md', 'lecciones.md'):
            p = ruta / n
            if p.exists():
                with st.expander(n, expanded=(n == 'recomendaciones.md')):
                    st.markdown(p.read_text(encoding='utf-8'))


def pestaña_historial():
    js = jornadas_existentes()
    if not js:
        st.info('Aún no hay jornadas analizadas en ligas/*/jornadas/.'); return
    js.sort(key=lambda r: r['fecha'], reverse=True)
    resumen = pd.DataFrame([{'Fecha': j['fecha'], 'Liga': NOMBRE_DE_SLUG.get(j['slug'], j['slug']),
                             'Cuotas': j['cuotas.json'], 'Contexto': j['contexto.json'],
                             'Simulada': j['evaluacion.csv'], 'Recomendadas': j['n_rec'],
                             'Casas': j['comparacion_casas.json']} for j in js])
    st.dataframe(resumen, hide_index=True, width='stretch')
    etiquetas = [f'{j["fecha"]} · {NOMBRE_DE_SLUG.get(j["slug"], j["slug"])}' for j in js]
    pre = st.session_state.pop('abrir_jornada', None)
    idx = next((i for i, j in enumerate(js) if str(j['ruta']) == pre), 0)
    i = st.selectbox('Abrir jornada', range(len(js)), index=idx, format_func=lambda k: etiquetas[k])
    ver_jornada(js[i]['ruta'])


# ------------------------------------------------------------------ análisis completo
def prompt_completo(liga, fecha, comparar, notas):
    p = (f'Analiza la jornada de {liga}' + (f' que empieza el {fecha}' if fecha else ' (la próxima)') + '. '
         'Sigue el flujo de CLAUDE.md al pie de la letra: '
         '1) `python analizar_jornada.py calendario --liga "' + liga + '"` para la carpeta y los partidos; '
         '2) agente recolector-cuotas (bet365 en Chrome, solo lectura) -> cuotas.json; '
         '3) `python analizar_jornada.py contexto --dir <carpeta>`; '
         '4) agente analista-contexto (noticias y titular_prob); '
         '5) agente simulador-apuestas -> recomendaciones.json/.md (registra sus recomendadas en la bitácora como siempre). ')
    if comparar:
        p += ('6) Comparar casas: `python comparar_casas.py candidatas --dir <carpeta>`; si hay candidatas, '
              'recolector-cuotas en modo comparación con cada casa de la ficha (una a una) y después '
              '`python comparar_casas.py evaluar --dir <carpeta>`. No registres en la bitácora las que solo '
              'salen limpias en otra casa: eso lo decide el usuario. ')
    p += ('Esta ejecución la lanza el dashboard sin nadie mirando: no preguntes nada; si bet365 pide inicio de '
          'sesión, captcha o aceptar algo, para y explícalo en el resumen final. No cambies reglas, estados de la '
          'bitácora ni nada fuera de la carpeta de la jornada. Termina con un resumen corto en español: carpeta, '
          'partidos leídos, recomendadas y avisos.')
    if notas.strip():
        p += ' Indicaciones del usuario: ' + notas.strip()
    return p


def lanzar(liga, fecha, comparar, notas):
    exe = shutil.which('claude')
    if not exe:
        st.error('No encuentro el comando `claude` en el PATH.'); return
    RUNS.mkdir(exist_ok=True)
    rid = datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + ligas.LIGAS[liga][0]
    log = RUNS / f'{rid}.jsonl'
    cmd = [exe, '-p', prompt_completo(liga, fecha, comparar, notas), '--chrome',
           '--permission-mode', 'bypassPermissions', '--output-format', 'stream-json', '--verbose', '--forward-subagent-text']
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0
    with open(log, 'w', encoding='utf-8') as f:
        pr = subprocess.Popen(cmd, cwd=DIR, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                              creationflags=flags)
    meta = {'id': rid, 'liga': liga, 'fecha': fecha, 'comparar': comparar, 'pid': pr.pid,
            'inicio': datetime.now().isoformat(timespec='seconds'), 'log': log.name}
    (RUNS / f'{rid}.json').write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding='utf-8')
    st.session_state['run_activo'] = rid


def vivo(pid):
    if os.name == 'nt':
        r = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/NH'], capture_output=True, text=True)
        return str(pid) in r.stdout
    try:
        os.kill(pid, 0); return True
    except OSError:
        return False


def leer_eventos(log: Path):
    """stream-json de claude -p -> lista de (tipo, texto) legibles."""
    ev, final, coste = [], None, None
    if not log.exists():
        return ev, final, coste
    for linea in log.read_text(encoding='utf-8', errors='replace').splitlines():
        try:
            o = json.loads(linea)
        except Exception:
            if linea.strip():
                ev.append(('texto', linea))
            continue
        t = o.get('type')
        if t == 'assistant':
            sub = ' (subagente)' if o.get('parent_tool_use_id') else ''
            for c in o.get('message', {}).get('content', []):
                if c.get('type') == 'text' and c.get('text', '').strip():
                    ev.append(('texto' + sub, c['text'].strip()))
                elif c.get('type') == 'tool_use':
                    i = c.get('input', {})
                    desc = i.get('description') or i.get('command') or i.get('subagent_type') or i.get('url') or ''
                    ev.append(('herramienta' + sub, f'{c.get("name")}: {str(desc)[:200]}'))
        elif t == 'result':
            final = o.get('result') or o.get('subtype')
            coste = o.get('total_cost_usd')
    return ev, final, coste


def ver_run(meta):
    log = RUNS / meta['log']
    activo = vivo(meta['pid'])
    ev, final, coste = leer_eventos(log)
    est = '🟢 en marcha' if activo and final is None else ('✅ terminado' if final else '⏹️ parado')
    st.markdown(f'**{meta["liga"]}** · {meta.get("fecha") or "próxima"} · lanzado {meta["inicio"]} · {est}'
                + (f' · coste {coste:.2f} $' if coste else ''))
    if final:
        st.success(final)
    pasos = [e for e in ev if e[0].startswith('herramienta')]
    st.caption(f'{len(pasos)} acciones hasta ahora')
    for tipo, txt in ev[-40:][::-1]:
        if tipo.startswith('herramienta'):
            st.text(('   ↳ ' if 'subagente' in tipo else '• ') + txt)
        else:
            st.markdown(('> ' if 'subagente' in tipo else '') + txt[:1500])
    if activo and final is None:
        if st.button('Parar análisis', key=f'stop_{meta["id"]}'):
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(meta['pid']), '/T', '/F'], capture_output=True)
            else:
                os.kill(meta['pid'], 15)
            st.rerun()
    carpeta = None
    for tipo, txt in ev:
        if 'ligas/' in txt and '/jornadas/' in txt:
            import re
            mm = re.search(r'ligas/[\w-]+/jornadas/\d{4}-\d{2}-\d{2}', txt.replace('\\', '/'))
            if mm and (DIR / mm.group(0)).exists():
                carpeta = DIR / mm.group(0)
    if carpeta and st.button(f'Ver la jornada ({carpeta.relative_to(DIR).as_posix()}) en Historial',
                             key=f'ver_{meta["id"]}'):
        st.session_state['abrir_jornada'] = str(carpeta)
        st.info('Abre la pestaña **Historial**: ya está seleccionada.')


@st.fragment(run_every=4)
def monitor():
    rid = st.session_state.get('run_activo')
    metas = sorted(RUNS.glob('*.json'), reverse=True) if RUNS.exists() else []
    if not metas:
        st.caption('Sin ejecuciones todavía.'); return
    opciones = [leer_json(m) for m in metas]
    opciones = [o for o in opciones if o]
    ids = [o['id'] for o in opciones]
    i = ids.index(rid) if rid in ids else 0
    sel = st.selectbox('Ejecución', range(len(opciones)), index=i,
                       format_func=lambda k: f'{opciones[k]["inicio"]} · {opciones[k]["liga"]}')
    st.session_state['run_activo'] = ids[sel]
    ver_run(opciones[sel])


def pestaña_completo():
    st.markdown('Lanza el pipeline entero con Claude sin ventana: **cuotas de bet365 → contexto → noticias → '
                'simulador** (y, si quieres, comparación de casas). Tarda varios minutos y gasta uso de Claude.')
    st.warning('Antes: Chrome abierto con la extensión de Claude conectada y bet365 cargado. '
               'Las ligas van de una en una (comparten la pestaña de Chrome).', icon='🧭')
    with st.form('lanzar'):
        c1, c2 = st.columns([2, 1])
        liga = c1.selectbox('Liga', list(ligas.LIGAS))
        fecha = c2.date_input('Primer día de la jornada (vacío = la próxima)', value=None)
        comparar = st.checkbox('Comparar casas después (winamax, bwin, codere, sportium)', False)
        notas = st.text_input('Indicaciones extra (opcional)', placeholder='p. ej. solo partidos del sábado')
        ok = st.form_submit_button('Lanzar análisis completo', type='primary')
    if ok:
        activos = [m for m in (leer_json(p) for p in RUNS.glob('*.json')) if m and vivo(m['pid'])] if RUNS.exists() else []
        if activos:
            st.error(f'Ya hay un análisis en marcha ({activos[0]["liga"]}). Espera o páralo: comparten Chrome.')
        else:
            lanzar(liga, fecha.isoformat() if fecha else '', comparar, notas)
            st.success('Lanzado. El progreso sale abajo y se actualiza solo.')
    st.divider()
    monitor()


# ------------------------------------------------------------------ página
st.title('⚽ Jornadas')
t1, t2, t3 = st.tabs(['Historial', 'Solo modelo', 'Análisis completo'])
with t1:
    pestaña_historial()
with t2:
    pestaña_modelo()
with t3:
    pestaña_completo()

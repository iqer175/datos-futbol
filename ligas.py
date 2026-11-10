#!/usr/bin/env python3
"""
ligas.py -- registro unico de las ligas activas y de sus carpetas.

Desde 2026-11-10 el repo trabaja solo con las 18 ligas que tienen historico
(football-data) Y datos de jugadores (ESPN). Cada una tiene su carpeta:

  ligas/<slug>/ficha.json                    lo propio de la liga (direccion de bet365,
                                             codigos, nombres que no casan, notas)
  ligas/<slug>/jornadas/<fecha>/             cuotas.json, contexto.json, evaluacion.csv,
                                             recomendaciones.json/.md, lecciones.md, bruto/

Los datos siguen en un unico datos/partidos.csv y datos/alineaciones/jugadores.csv
(el motor estima el Elo y los goles con todas las ligas a la vez).

Uso:
  python ligas.py                 lista las ligas activas y lo que le falta a cada ficha
  python ligas.py fichas          crea las carpetas y fichas que falten y refresca los
                                  campos automaticos (no toca bet365_url, nombres ni notas)
  python ligas.py ficha "LaLiga"  imprime la ficha de una liga (nombre o slug)
  python ligas.py anotar "LaLiga" --bet365 <direccion>
                                  guarda en la ficha la direccion de la liga en bet365
"""
import json
import sys
import unicodedata
from pathlib import Path

DIR = Path(__file__).parent
DIR_LIGAS = DIR / 'ligas'

# nombre en partidos.csv -> (slug de carpeta, pais, codigo ESPN, slug de datos/agregados, estadisticas de partido)
# 'estadisticas' = football-data trae tiros, corners y tarjetas. Argentina no:
# alli solo valen goles y mercados de jugador.
LIGAS = {
    'LaLiga':               ('laliga', 'España', 'esp.1', 'laliga', True),
    'LaLiga Hypermotion':   ('laliga-hypermotion', 'España', 'esp.2', 'laliga2', True),
    'Premier League':       ('premier-league', 'Inglaterra', 'eng.1', 'premier', True),
    'Championship':         ('championship', 'Inglaterra', 'eng.2', 'championship', True),
    'League One':           ('league-one', 'Inglaterra', 'eng.3', 'leagueone', True),
    'League Two':           ('league-two', 'Inglaterra', 'eng.4', 'leaguetwo', True),
    'Serie A':              ('serie-a', 'Italia', 'ita.1', 'seriea', True),
    'Serie B':              ('serie-b', 'Italia', 'ita.2', 'serieb', True),
    'Bundesliga':           ('bundesliga', 'Alemania', 'ger.1', 'bundesliga', True),
    '2. Bundesliga':        ('2-bundesliga', 'Alemania', 'ger.2', 'bundesliga2', True),
    'Ligue 1':              ('ligue-1', 'Francia', 'fra.1', 'ligue1', True),
    'Ligue 2':              ('ligue-2', 'Francia', 'fra.2', 'ligue2', True),
    'Eredivisie':           ('eredivisie', 'Holanda', 'ned.1', 'eredivisie', True),
    'Liga Portugal':        ('liga-portugal', 'Portugal', 'por.1', 'portugal', True),
    'Scottish Premiership': ('scottish-premiership', 'Escocia', 'sco.1', 'escocia', True),
    'Jupiler Pro League':   ('jupiler-pro-league', 'Bélgica', 'bel.1', 'belgica', True),
    'Super Lig':            ('super-lig', 'Turquía', 'tur.1', 'turquia', True),
    'Primera Division':     ('primera-division-argentina', 'Argentina', 'arg.1', 'argentina', False),
}

# direcciones de bet365 ya comprobadas; el resto las anota el recolector la primera vez
BET365 = {
    'Premier League': 'https://www.bet365.es/#/AC/B1/C1/D1002/E91422157/G40/',
    'Serie A': 'https://www.bet365.es/#/AC/B1/C1/D1002/E92269709/G40/',
    'Bundesliga': 'https://www.bet365.es/#/AC/B1/C1/D1002/E135680139/G40/',
    'Ligue 1': 'https://www.bet365.es/#/AC/B1/C1/D1002/E135119473/G40/',
}

# campos que rellena una persona o la sesion principal: 'fichas' nunca los pisa
MANUALES = ('bet365_url', 'nombres_bet365', 'notas')


def _nz(s):
    return unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode().lower().strip()


def resolver(liga):
    """Nombre canonico (el de partidos.csv) a partir del nombre o del slug. Error claro si no esta activa."""
    k = _nz(liga)
    for nombre, v in LIGAS.items():
        if k in (_nz(nombre), v[0]):
            return nombre
    raise SystemExit(f'Liga no activa: {liga!r}. Activas: {", ".join(LIGAS)}. '
                     'Las retiradas el 2026-11-10 estan en actualizar_datos.py (LIGAS_RETIRADAS).')


def slug(liga):
    return LIGAS[resolver(liga)][0]


def carpeta(liga):
    return DIR_LIGAS / slug(liga)


def carpeta_jornada(liga, fecha):
    """ligas/<slug>/jornadas/<AAAA-MM-DD>"""
    return carpeta(liga) / 'jornadas' / str(fecha)[:10]


def liga_de_carpeta(ruta):
    """Liga a la que pertenece una carpeta de jornada (ligas/<slug>/jornadas/<fecha>)."""
    partes = Path(ruta).resolve().parts
    if 'ligas' in partes:
        i = len(partes) - 1 - partes[::-1].index('ligas')
        if i + 1 < len(partes):
            return resolver(partes[i + 1])
    return None


def ficha(liga):
    f = carpeta(liga) / 'ficha.json'
    return json.loads(f.read_text(encoding='utf-8')) if f.exists() else {}


def ficha_base(nombre):
    sl, pais, espn, datos, est = LIGAS[nombre]
    return {
        'liga': nombre, 'slug': sl, 'pais': pais, 'espn': espn,
        'estadisticas_partido': est,
        'mercados': (['goles', 'corners', 'tarjetas', 'jugadores'] if est else ['goles', 'jugadores']),
        'datos': {'perfil_equipos': f'datos/agregados/equipos_{datos}.csv',
                  'h2h': f'datos/h2h/{datos}.csv', 'cierres': f'datos/cierres/{datos}.csv'},
        'bet365_url': BET365.get(nombre, ''),
        'nombres_bet365': {},
        'notas': [],
    }


def crear_fichas():
    for nombre in LIGAS:
        c = carpeta(nombre); (c / 'jornadas').mkdir(parents=True, exist_ok=True)
        (c / 'jornadas' / '.gitkeep').touch()
        nueva, vieja = ficha_base(nombre), ficha(nombre)
        for k in MANUALES:
            if vieja.get(k):
                nueva[k] = vieja[k]
        (c / 'ficha.json').write_text(json.dumps(nueva, indent=2, ensure_ascii=False) + '\n',
                                      encoding='utf-8', newline='\n')
    print(f'{len(LIGAS)} fichas en {DIR_LIGAS.relative_to(DIR).as_posix()}/')


def listar():
    print(f'{"liga":22s} {"carpeta":38s} {"bet365":7s} jornadas')
    for nombre in LIGAS:
        f, c = ficha(nombre), carpeta(nombre)
        n = len([d for d in (c / 'jornadas').glob('*') if d.is_dir()]) if c.exists() else 0
        print(f'{nombre:22s} {c.relative_to(DIR).as_posix():38s} {"sí" if f.get("bet365_url") else "falta":7s} {n}')


if __name__ == '__main__':
    a = sys.argv[1:]
    if not a:
        listar()
    elif a[0] == 'fichas':
        crear_fichas()
    elif a[0] == 'ficha' and len(a) > 1:
        print(json.dumps(ficha(a[1]) or ficha_base(resolver(a[1])), indent=2, ensure_ascii=False))
    elif a[0] == 'anotar' and len(a) == 4 and a[2] == '--bet365':
        if not a[3].startswith('https://www.bet365.'):
            raise SystemExit('La direccion tiene que empezar por https://www.bet365.')
        f = ficha(a[1]) or ficha_base(resolver(a[1]))
        f['bet365_url'] = a[3]
        (carpeta(a[1]) / 'ficha.json').write_text(json.dumps(f, indent=2, ensure_ascii=False) + '\n',
                                                  encoding='utf-8', newline='\n')
        print(f'{f["liga"]}: bet365_url = {a[3]}')
    else:
        raise SystemExit(__doc__)

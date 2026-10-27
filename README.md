# Datos de fútbol automatizados

Descarga 12 ligas europeas de football-data.co.uk (más Brasil y Argentina
para Libertadores), las consolida, calcula ratings Elo y genera agregados
ligeros. Se actualiza sola los lunes, martes y jueves mediante GitHub Actions.

## Qué genera

| Fichero | Contenido |
|---|---|
| `datos/partidos.csv` | Un partido por fila: resultado, tiros, córners, tarjetas, árbitro, cuotas bet365 de apertura y cierre, media de mercado, y el Elo de ambos equipos antes (`elo_*_pre`) y después (`elo_*_post`) del partido |
| `datos/ratings.csv` | Elo actual por equipo + goles a favor y en contra en casa y fuera de los últimos 20 partidos |
| `datos/resumen.json` | Fecha de actualización, conteos y cobertura por liga |
| `datos/agregados/` | Perfil ponderado por equipo y liga, medias por liga y temporada, árbitros (generado por `generar_agregados.py`) |
| `datos/h2h/` | Enfrentamientos directos entre equipos activos de cada liga |
| `datos/cierres/` | Cuotas de cierre de la temporada en curso, para medir el CLV |
| `datos/indice.json` | Rutas, tamaños y parámetros de los agregados |

`motor.py` es el motor de pronóstico (Elo + GLM de Poisson); no lo ejecuta el
workflow, se usa desde la skill.

## Montaje (una sola vez, ~10 minutos)

1. **Crea un repositorio en GitHub.** Público es más simple; si lo haces
   privado, los ficheros no serán accesibles por URL directa y habrá que usar
   otra vía.

2. **Sube estos archivos** manteniendo la estructura:

   ```
   actualizar_datos.py
   generar_agregados.py
   motor.py
   requirements.txt
   .github/workflows/actualizar.yml
   datos/                    (vacía, se rellena sola)
   ```

3. **Da permiso de escritura a Actions.** En el repo:
   Settings → Actions → General → Workflow permissions →
   marca *Read and write permissions* → Save.
   Sin esto el workflow no puede guardar los datos.

4. **Lánzalo a mano la primera vez.** Pestaña Actions → *Actualizar datos de
   fútbol* → *Run workflow*. Tarda 2-4 minutos. Al terminar deben aparecer los
   tres ficheros en `datos/`.

5. **Pásale a Claude tu usuario y nombre de repo.** A partir de ahí lee los
   datos directamente.

Después de esto no tienes que hacer nada más. El cron corre solo.

## Uso local (opcional)

```bash
pip install -r requirements.txt
python actualizar_datos.py --temporadas 6
python actualizar_datos.py --sin-extra      # sin Brasil ni Argentina
```

## Ligas incluidas

Premier League, Championship, LaLiga, Segunda División, Serie A, Serie B,
Bundesliga, 2. Bundesliga, Ligue 1, Ligue 2, Eredivisie y Liga Portugal, más
Brasileirão y Primera argentina.

## Notas

- **Elo entre ligas.** Los equipos de ligas distintas no se enfrentan, así que
  el Elo por sí solo no puede compararlos. El diccionario `LIGAS` del script
  incluye un `elo_base` por competición que actúa como prior de fuerza
  relativa. Es lo que permite valorar un cruce de Champions. Los valores
  iniciales son una estimación razonable; ajústalos conforme acumules
  resultados europeos reales.

- **Árbitros.** La fuente solo trae el árbitro en Premier y Championship.

- **Cuotas de Pinnacle.** Desde el 23/07/2025 la fuente advierte de que las
  cuotas de Pinnacle van desactualizadas y ya no entran en el cálculo de la
  media de mercado. Por eso el script no las importa. Como referencia de cierre
  usa `avgc_1`, `avgc_x`, `avgc_2`.

- **Brasil y Argentina** vienen del apartado *Extra Leagues*, que solo trae
  resultado final y cuotas de cierre. Sin tiros, córners ni tarjetas. Sirven
  para ratings, no para modelar mercados secundarios.

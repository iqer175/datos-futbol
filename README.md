# Datos de fútbol automatizados

Descarga las 10 ligas seleccionadas de football-data.co.uk (más Brasil y
Argentina para Libertadores), las consolida y calcula ratings Elo. Se actualiza
solo los lunes y jueves mediante GitHub Actions.

## Qué genera

| Fichero | Contenido |
|---|---|
| `datos/partidos.csv` | Un partido por fila: resultado, tiros, córners, tarjetas, árbitro, cuotas bet365 de apertura y cierre, media de mercado, y el Elo previo de ambos equipos |
| `datos/ratings.csv` | Elo actual por equipo + goles a favor y en contra en casa y fuera de los últimos 20 partidos |
| `datos/resumen.json` | Fecha de actualización, conteos y cobertura por liga |

## Montaje (una sola vez, ~10 minutos)

1. **Crea un repositorio en GitHub.** Público es más simple; si lo haces
   privado, los ficheros no serán accesibles por URL directa y habrá que usar
   otra vía.

2. **Sube estos archivos** manteniendo la estructura:

   ```
   actualizar_datos.py
   requirements.txt
   .gitignore
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

Championship, Segunda División, Premier League, LaLiga, Serie A, Serie B,
Bundesliga, Ligue 1, Eredivisie, Liga Portugal, más Brasileirão y Primera
argentina.

## Notas

- **Elo entre ligas.** Los equipos de ligas distintas no se enfrentan, así que
  el Elo por sí solo no puede compararlos. El diccionario `LIGAS` del script
  incluye un `elo_base` por competición que actúa como prior de fuerza
  relativa. Es lo que permite valorar un cruce de Champions. Los valores
  iniciales son una estimación razonable; ajústalos conforme acumules
  resultados europeos reales.

- **Cuotas de Pinnacle.** Desde el 23/07/2025 la fuente advierte de que las
  cuotas de Pinnacle van desactualizadas y ya no entran en el cálculo de la
  media de mercado. Por eso el script no las importa. Como referencia de cierre
  usa `avgc_1`, `avgc_x`, `avgc_2`.

- **Brasil y Argentina** vienen del apartado *Extra Leagues*, que solo trae
  resultado final y cuotas de cierre. Sin tiros, córners ni tarjetas. Sirven
  para ratings, no para modelar mercados secundarios.

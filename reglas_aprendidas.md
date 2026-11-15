# Reglas aprendidas

Reglas vigentes para los agentes de `.claude/agents/`. Los tres leen este fichero al empezar y,
si algo de aquí choca con su propio `.md` o con la skill `pronostico-futbol`, manda esto.

Cómo se cambia:
- Solo en la revisión semanal (`revision_semanal.py` → `notas/revisiones/<fecha>.md`) y con el
  visto bueno del usuario.
- Reglas de modelo o de mercado: hace falta muestra suficiente (al menos 30 apuestas resueltas del
  tipo afectado). Con menos, la propuesta se apunta en la revisión y se espera.
- Fallos operativos (direcciones de bet365, nombres que no cuadran, tablas mal leídas): se
  corrigen en cuanto se detectan, sin esperar muestra.
- Cada regla lleva fecha, evidencia y de dónde sale. Las que se retiran se mueven a "Retiradas".

## Vigentes

### Mercados
- **1X2 y empate no válido: no se recomiendan.** El motor no tiene ventaja en el 1X2 (peso óptimo
  w=0 en el backtest de CLV contra el cierre de Betfair). Desde 2026-11, backtests de CLV.
- **Unders, "Ambos marcan - No" y "goles 2-4" en partidos SIN ancla (`goles` = `motor`): como mucho
  uno por jornada y solo si mantienen margen con w=0.20.** Con los goles del motor, el simulador ve
  menos goles de los reales (Over 2.5: 52,8% simulado frente a 56,2% real). Desde 2026-11,
  `validar_simulador.py`. **En partidos anclados (`goles` = `ancla`) la restricción solo se aplica
  a "Ambos marcan - No"** (también dentro de combinadas): unders y "goles 2-4" se tratan como
  cualquier otro mercado, porque el nivel de goles es el del mercado (Más 1.5: 75,6% simulado
  frente a 75,8% real; Más 3.5: 30,3% frente a 29,8%, `backtest_ancla.py`). Desde 2026-11-10.
- **Goles del simulador anclados al mercado.** Si el partido trae cuotas de 1, X, 2, Más 2.5 y
  Menos 2.5, `analizar_jornada.py simular` saca los goles esperados de esas cuotas sin margen y no
  del motor (columna `goles` = `ancla` en `evaluacion.csv`; `motor` si faltan cuotas). En 1X2 y
  Más/Menos 2.5 la probabilidad es entonces la del mercado: nunca hay margen propio ahí. Anclado,
  el aviso de sesgo solo queda en "Ambos marcan - No" (el ancla ve 52,2% de ambos marcan frente a
  54,0% real). `backtest_ancla.py`: mejora los 13 mercados derivados de goles en 4/4 temporadas
  (21.002 partidos, 2023-07..2026-11; córners y tarjetas no cambian). Desde 2026-11-10.
- **Remates y remates a puerta de equipo y del partido (en prueba desde 2026-11-11).** Salen de
  `remates_equipo.py`: perfil de cada equipo relativo a la media reciente de su liga (120 partidos),
  corrección por dominio de Elo, binomial negativa con correlación -0,22 entre equipos. Fuera de
  muestra en 2026-27 mejora el log-loss frente a la media de liga en 30/30 líneas y frente al
  perfil sin dominio en 27/30 (las 3 que no, por poco, son totales del partido); por equipo queda a 1-2 puntos de lo real, pero el total del partido sale ~3 puntos corto,
  así que **los "Menos" del partido no se recomiendan** (aviso de sesgo). Las líneas enteras con
  "Exactamente" no se copian. `experimento_remates_equipo.py`, `resultados/remates_equipo.txt`.
- **Corners por equipo: solo con el reparto corregido por dominio** (`CORNERS_DOMINIO` en
  `simulador.py`). Validado fuera de muestra en 2026-27: mejora 9 de 10 líneas. Desde 2026-11-05.

### Jugadores
- **Solo si es titular.** Probabilidad de titularidad < 0.8 → descartar o dejar condicionada a la
  alineación oficial. bet365 anula si no juega, pero paga como perdida si entra pocos minutos.
- **Muestra mínima: 6 titularidades** esta temporada.
- **Probabilidad = la del modelo de remates de `analizar_jornada.py`** (ritmo por 90' encogido
  hacia su posición × minutos esperados × remates esperados de su equipo en ese partido). La
  frecuencia real del jugador va en el aviso solo como información: ya no se toma el mínimo.
  `backtest_jugadores.py` (869 partidos, oct-nov 2026): mejora el log-loss en las 5 líneas
  (0,015-0,043) y, donde el modelo anterior daba 55% o más, este se pasaba 4-8 puntos en remates
  2+, 3+ y a puerta 1+. Desde 2026-11-10.
  Desde 2026-11-11 cuenta el **Sustituto+** de bet365: los minutos que no juega el titular suman el
  ritmo de un suplente de su posición (`R_SUSTITUTO`). Sube sobre todo a los que suelen ser
  cambiados pronto (`backtest_sustituto.py`).
  Desde 2026-11-12, en **remates** (no a puerta) y con 6+ titularidades, los que más disparan se
  encogen menos: λ × (ritmo bruto / ritmo encogido)^0,148 (`BETA_TIRADORES`). Sube 1-3 puntos al
  10 % que más dispara y baja un poco a los que disparan menos que su posición (`experimento_tiradores.py`).
- **Sospecha del mercado: si el modelo supera a la cuota en más de 25 puntos, descartar** salvo que
  el contexto lo explique. Caso: Füllkrug 2+ a puerta al 75% con cuota 3.25 (2026-11-05).

### Gestión
- **Margen mínimo: prob >= necesaria + 3 puntos. Cuota >= 1.50** (criterio del usuario).
- **Como mucho 2 apuestas por partido y 1 por jugador**; si dos dependen de lo mismo, una sola.
- **Stake plano 0,5u** en fase de prueba.
- **Lista "con valor" para que elija el usuario (desde 2026-11-12):** además de las recomendadas,
  `recomendaciones.md` enseña siempre todas las apuestas con valor esperado >= +0,1 % y cuota >= 1.50
  (columna `con_valor` de `evaluacion.csv`), con el motivo por el que no son recomendadas. No se
  registran solas: las que el usuario elija las registra la sesión principal con `nota` "elegida por el
  usuario (con valor, no recomendada)", para poder medirlas aparte en la revisión.
- **Registro automático:** `simulador-apuestas` registra en la bitácora sus recomendadas (solo crea,
  nunca modifica ni borra). Estados y cuotas tomadas: sesión principal con visto bueno. Desde 2026-11-06.

### Operativas
- bet365: la búsqueda por dirección con `#` dejó de funcionar; se entra por la página de la liga y
  se pulsa cada partido. La dirección de cada liga está en `ligas/<slug>/ficha.json`
  (`bet365_url`); el recolector la anota con `python ligas.py anotar` la primera vez.
- bet365 (desde 2026-11-11): el recolector abre también la pestaña "Remates" de cada partido y
  despliega con "Mostrar más" las tablas de remates de jugador (todos los jugadores de los dos
  equipos, no solo los 6 de Populares) y lee los remates de equipo y del partido. Son los únicos
  clics permitidos además de abrir el partido.
- bet365: en el texto de la página las tablas de remates van por columnas y una casilla vacía
  desaparece. Si una columna tiene menos cuotas que jugadores, la tabla se lee fila a fila
  (`read_page` o zoom); solo si así tampoco cuadra se descarta la tabla.
- Solo se analizan las 18 ligas de `ligas.py`, que son las que tienen histórico y jugadores en
  ESPN. Las otras 16 se retiraron el 2026-11-10 (ESPN da remates a cero o no las cubre).
- Los tres agentes son únicos y valen para todas las ligas; lo propio de cada liga va en su ficha.
- El contexto va en dos tiempos: `analizar_jornada.py contexto` pone los datos propios y
  `analista-contexto` (Haiku, en prueba) solo las noticias. Si `contexto.json` trae
  `"noticias": "pendiente"` o `"parciales"`, las apuestas de jugador quedan condicionadas a la
  alineación oficial.
- Primera División argentina: sin córners ni tarjetas en la fuente; solo goles y jugadores.

- Comparar casas (desde 2026-11-11): las candidatas de cada jornada (`comparar_casas.py candidatas`)
  se buscan en Winamax, bwin, Codere y Sportium con el recolector en "modo comparación" y se apuesta
  donde la cuota sea más alta. Las reglas de apuesta limpia no cambian (cuota >= 1.50, margen >= 3
  puntos). En remates de jugador, una casa sin Sustituto+ (o sin confirmar) usa la probabilidad sin
  Sustituto+. bet365 sigue siendo la casa de referencia del ancla y del CLV.

## Retiradas
(ninguna todavía)

## Registro de cambios
| fecha | cambio | evidencia | aprobado |
|---|---|---|---|
| 2026-11-05 | Versión inicial con las reglas usadas hasta ahora | backtests y validaciones de 2026-11 | sí |
| 2026-11-06 | El agente de Opus registra sus recomendadas en la bitácora | petición del usuario | sí |
| 2026-11-10 | Reorganización: 18 ligas activas, carpeta y ficha por liga, contexto de datos por script y analista en Haiku (en prueba) | petición del usuario; solo cambian reglas operativas, ninguna de modelo | sí |
| 2026-11-10 | Goles del simulador anclados a las cuotas de 1X2 y Más/Menos 2.5 del partido (motor de respaldo) | `backtest_ancla.py`: 21.002 partidos, mejora 13/13 mercados derivados de goles en 4/4 temporadas | sí |
| 2026-11-10 | La restricción de unders y "goles 2-4" se limita a partidos sin ancla; con ancla solo queda en "Ambos marcan - No" | `backtest_ancla.py`: anclado, Más 1.5 75,6% vs 75,8% real y Más 3.5 30,3% vs 29,8%; ambos marcan 52,2% vs 54,0% | sí |
| 2026-11-10 | Motor v6: el GLM aprende de goles y remates a puerta, pesa 0,60 en el total y 0,30 en la supremacía; CAL_LOCAL 0,055 y CAL_EMPATE 0,128. No cambia ninguna regla de los agentes | `experimento_remates.py` y `resultados/motor_v6_verificacion.txt`: 27.433 partidos, mejora 4/4 temporadas en 1X2 y Más/Menos frente al v5 | sí |
| 2026-11-10 | Jugadores: la probabilidad sale del modelo nuevo de remates (ritmo encogido, minutos, remates del equipo con mercado) y se retira la regla del mínimo con la frecuencia real | `backtest_jugadores.py`: 9.349 titulares con 6+ titularidades, log-loss mejor en 5/5 líneas; el anterior se pasaba 4-8 puntos en la zona de 55% o más | sí |
| 2026-11-11 | Recolector: las tablas de remates con columnas incompletas se leen fila a fila (`read_page` o zoom) en vez de descartarse | Hypermotion 2026-11-13: las 10 tablas se descartaron por columnas 1+ u 8+/9+ incompletas (en el Oviedo-Girona, 1+ a 7+ cuadraban); petición del usuario | sí |
| 2026-11-11 | Jugadores: el modelo de remates incluye el Sustituto+ de bet365 (minutos no jugados con el ritmo de un suplente de su posición) y la dispersión pasa a 0,117 remates / 0,127 a puerta | `backtest_sustituto.py`: 9.349 titulares con 6+ titularidades contra el resultado que paga bet365, log-loss mejor en 6/6 líneas (todas con IC95 por debajo de 0); el modelo anterior se quedaba corto (75→81 %), el nuevo 75→73 % | sí |
| 2026-11-11 | Comparar casas: las candidatas se buscan en Winamax, bwin, Codere y Sportium y se toma la mejor cuota; en remates sin Sustituto+ se usa la probabilidad sin Sustituto+ | petición del usuario; operativa, no cambia ninguna regla de modelo | sí |
| 2026-11-11 | Remates y remates a puerta de equipo y del partido como mercado nuevo (en prueba, stake 0,5u); los "Menos" del partido no se recomiendan | `experimento_remates_equipo.py`: ajuste 2025-26 (6.221 partidos), prueba 2026-27 (1.849) con todo congelado; log-loss 0,554 frente a 0,570 del perfil y 0,583 de la media de liga; mejor que la media de liga en 30/30 líneas y que el perfil en 27/30; el total del partido ~3 pts corto | sí (petición del usuario) |
| 2026-11-11 | Recolector: pestaña "Remates" y "Mostrar más" para leer todos los jugadores y los remates de equipo | petición del usuario; operativa | sí |
| 2026-11-12 | Jugadores: en remates (con Sustituto+ y 6+ titularidades), λ × (ritmo bruto / ritmo encogido)^0,148; a puerta y sin Sustituto+ no cambian | `experimento_tiradores.py`: ajuste 4.265 titulares (09-05..10-23), prueba 5.999 (10-24..11-09); log-loss mejor en 4/4 líneas de remates con IC95 < 0; top 10 % en 2+: 71,7 → 72,7 % (real 75,4 %). La variante "decil" (×1,073) se descartó por pasarse en 3+ y 4+ | sí |
| 2026-11-12 | Lista "con valor" (valor esperado >= +0,1 %, cuota >= 1.50) en cada `recomendaciones.md` para que elija el usuario; no se registra sola | petición del usuario; las recomendadas y sus reglas no cambian | sí |

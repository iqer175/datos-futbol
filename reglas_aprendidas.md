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
- **Unders, "Ambos marcan - No" y "goles 2-4": como mucho uno por jornada y solo si mantienen
  margen con w=0.20.** El simulador ve menos goles de los reales (Over 2.5: 52,8% simulado frente a
  56,2% real). Desde 2026-11, `validar_simulador.py`.
- **Corners por equipo: solo con el reparto corregido por dominio** (`CORNERS_DOMINIO` en
  `simulador.py`). Validado fuera de muestra en 2026-27: mejora 9 de 10 líneas. Desde 2026-11-05.

### Jugadores
- **Solo si es titular.** Probabilidad de titularidad < 0.8 → descartar o dejar condicionada a la
  alineación oficial. bet365 anula si no juega, pero paga como perdida si entra pocos minutos.
- **Muestra mínima: 6 titularidades** esta temporada.
- **Probabilidad = mínimo entre el modelo ajustado al rival y la frecuencia real del jugador.**
- **Sospecha del mercado: si el modelo supera a la cuota en más de 25 puntos, descartar** salvo que
  el contexto lo explique. Caso: Füllkrug 2+ a puerta al 75% con cuota 3.25 (2026-11-05).

### Gestión
- **Margen mínimo: prob >= necesaria + 3 puntos. Cuota >= 1.50** (criterio del usuario).
- **Como mucho 2 apuestas por partido y 1 por jugador**; si dos dependen de lo mismo, una sola.
- **Stake plano 0,5u** en fase de prueba.
- **Registro automático:** `simulador-apuestas` registra en la bitácora sus recomendadas (solo crea,
  nunca modifica ni borra). Estados y cuotas tomadas: sesión principal con visto bueno. Desde 2026-11-06.

### Operativas
- bet365: la búsqueda por dirección con `#` dejó de funcionar; se entra por la página de la liga y
  se pulsa cada partido. La dirección de cada liga está en `ligas/<slug>/ficha.json`
  (`bet365_url`); el recolector la anota con `python ligas.py anotar` la primera vez.
- bet365: las tablas de remates a veces vienen descolocadas (una fila con menos cuotas que
  jugadores). Se descarta la tabla entera.
- Solo se analizan las 18 ligas de `ligas.py`, que son las que tienen histórico y jugadores en
  ESPN. Las otras 16 se retiraron el 2026-11-10 (ESPN da remates a cero o no las cubre).
- Los tres agentes son únicos y valen para todas las ligas; lo propio de cada liga va en su ficha.
- El contexto va en dos tiempos: `analizar_jornada.py contexto` pone los datos propios y
  `analista-contexto` (Haiku, en prueba) solo las noticias. Si `contexto.json` trae
  `"noticias": "pendiente"` o `"parciales"`, las apuestas de jugador quedan condicionadas a la
  alineación oficial.
- Primera División argentina: sin córners ni tarjetas en la fuente; solo goles y jugadores.

## Retiradas
(ninguna todavía)

## Registro de cambios
| fecha | cambio | evidencia | aprobado |
|---|---|---|---|
| 2026-11-05 | Versión inicial con las reglas usadas hasta ahora | backtests y validaciones de 2026-11 | sí |
| 2026-11-06 | El agente de Opus registra sus recomendadas en la bitácora | petición del usuario | sí |
| 2026-11-10 | Reorganización: 18 ligas activas, carpeta y ficha por liga, contexto de datos por script y analista en Haiku (en prueba) | petición del usuario; solo cambian reglas operativas, ninguna de modelo | sí |

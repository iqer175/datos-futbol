# LaLiga Hypermotion, jornada del 2026-11-13 al 2026-11-16: recomendaciones

Simulado el 2026-11-11 13:56 UTC. Cuotas leídas el 2026-11-11 hacia las 13:28 UTC (bet365). 10 partidos, 567 apuestas evaluadas (220 de equipo y 347 de remates de jugador). Tenerife-Ceuta no está en bet365 y no se analiza.
Goles: los 10 partidos tienen `ancla` (los goles salen de las cuotas de 1X2 y Más/Menos 2.5 sin margen).
Noticias: **parciales**. No hay alineaciones probables verificadas en ningún partido. Jose Corpas (Albacete) está sancionado (titular_prob 0). El motor del Eldense tiene fiabilidad baja (13 partidos). Las fechas y horas son las de bet365.
No hay cuotas de córners ni de tarjetas en `cuotas.json`, así que esos mercados no se evalúan.

## Recomendadas

**Ninguna.** Ninguna de las 567 apuestas tiene prob >= necesaria + 3 puntos. El mejor margen de la jornada es de -1,1 puntos (Menos 1.5 en el Leganés-Burgos y "1 & Ambos marcan - No" en el Almería-Andorra).

## Mejor apuesta de cada partido (todas por debajo del umbral)

| Partido | Inicio UTC | Mejor de equipo (cuota, prob, necesaria, margen) | Mejor de jugador (cuota, prob, necesaria, margen) |
|---|---|---|---|
| Leganés - Burgos | 13-nov 19:30 | Menos 1.5 (3.00, 32%, 33%, -1,1) | Gharbi 6+ remates (15.0, 5%, 7%, -1,7) |
| Valladolid - Castellón | 14-nov 13:00 | 2 & Ambos marcan - Sí (4.33, 22%, 23%, -1,4) | O. Camara 8+ (21.0, 3%, 5%, -2,2), titularidad 10% |
| Real Sociedad B - Sporting | 14-nov 15:15 | 2 & Ambos marcan - Sí (6.00, 15%, 17%, -1,5) | Astiazarán 8+ (21.0, 3%, 5%, -2,1), titularidad 10% |
| Almería - Andorra | 14-nov 17:30 | 1 & Ambos marcan - No (3.40, 28%, 29%, -1,1) | De la Fuente 9+ (21.0, 1%, 5%, -3,4) |
| Mallorca - Córdoba | 14-nov 17:30 | 2 & Ambos marcan - No (9.00, 10%, 11%, -1,3) | Álex Sala 8+ (17.0, 2%, 6%, -3,7) |
| Oviedo - Girona | 14-nov 20:00 | 2 & Ambos marcan - No (5.50, 17%, 18%, -1,5) | Abel Ruiz 7+ (21.0, 1%, 5%, -4,0) |
| Granada - Las Palmas | 15-nov 15:15 | 1 & Ambos marcan - No (3.00, 31%, 33%, -2,0) | Arnaiz 9+ (21.0, 1%, 5%, -4,2) |
| Eibar - Cádiz | 15-nov 17:30 | Más 4.5 (5.50, 16%, 18%, -1,8) | Martón 9+ (21.0, 1%, 5%, -3,7), muestra corta |
| Eldense - Sabadell | 15-nov 17:30 | 2 & Ambos marcan - No (5.00, 18%, 20%, -1,6) | Gioacchini 6+ (15.0, 5%, 7%, -2,1), muestra corta |
| Albacete - Celta B | 16-nov 19:30 | X & Ambos marcan - No (15.0, 5%, 7%, -2,0) | Corpas (sancionado) |

## Descartadas con probabilidad alta

| Partido | Apuesta | Cuota | Prob | Necesaria | Motivo |
|---|---|---|---|---|---|
| Eldense - Sabadell | Menos 2.5 | 1.53 | 62% | 65% | Sin margen (-3,2); además es under con el motor del Eldense en fiabilidad baja |
| Valladolid, Eibar, Granada, Sociedad B, Mallorca, Albacete, Almería, Oviedo | goles 2-4 | 1.53 | 61-62% | 65% | Sin margen (-3,3 a -4,4) en todos |
| Albacete - Celta B / Almería - Andorra | Más 2.5 | 1.57 | 61% | 64% | Prob anclada al mercado: no hay margen propio posible |
| Valladolid - Castellón | Ambos marcan - Sí | 1.57 | 60% | 64% | Sin margen (-3,6) |
| Leganés - Burgos | Sin empate 1 | 1.53 | 61% | 65% | 1X2/empate no válido: descartado por regla |
| Albacete, Eibar, Almería, Mallorca | 1 (local) | 1.57-1.70 | 57-61% | 59-64% | 1X2: descartado por regla y sin margen |
| Valladolid - Castellón | Ousmane Camara 3+ remates | 1.50 | 49% | 67% | -17 puntos; titularidad 10% según el analista (su frecuencia real es 71%, pero decide el modelo) |
| Real Sociedad B - Sporting | Lander Astiazarán 3+ remates | 1.50 | 50% | 67% | -17 puntos; titularidad 10% |
| Leganés - Burgos | Ismaël Gharbi 3+ remates | 2.10 | 38% | 48% | -10 puntos; es el jugador mejor situado (12 titularidades, 0,9) |
| Leganés - Burgos | Álex Sancris 3+ remates | 1.53 | 33% | 65% | -33 puntos |

## Remates de jugador: el mercado va muy por encima
Con los titulares de 6+ titularidades, en 3+ remates el modelo da 34% de media, la frecuencia real 35% y bet365 pide 60%. En 4+: 20%, 18% y 49%. Las 1+ se pagan a 1.01-1.07. Ningún remate de jugador se acerca al umbral, y esto no parece un fallo de los datos: los remates por equipo de ESPN en Hypermotion (13,1) cuadran con los de football-data (13,1).

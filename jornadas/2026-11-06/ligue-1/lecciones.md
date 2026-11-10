## recolector-cuotas

- bet365 (texto de la página): las celdas vacías desaparecen al extraer el texto, así que las filas `N+` con menos valores que jugadores son frecuentes. De 18 tablas de jugadores (remates a puerta y remates) en 9 partidos, 16 se descartaron por filas incompletas (7+, 8+, 9+ o 4+ con 1 a 3 valores). Propuesta: confirmar con captura de pantalla antes de descartar, para no perder tablas válidas.
- bet365 muestra "No disp." en algunas celdas de jugador (Le Havre - Lorient, Mohamed Bamba y Andy Logbo): el número de valores de la fila no cuadra con los jugadores listados.
- Le Havre - Lorient se abrió en español ("Francia - Ligue 1", "Resultado final", "Remates a puerta") y el resto en inglés. El copiado de mercados tiene que aceptar las dos etiquetas.
- Hora de inicio: PSG - Troyes aparece en bet365 a las 20:45 (CET, 19:45 UTC), pero el calendario de football-data da 20:05 UTC. Se guardó el dato del calendario; revisar la fuente de horas.
- La "Hora del Servidor" del pie no muestra fecha. Se tomó 2026-11-06 de la fecha de hoy indicada en la petición.
- Dirección de la liga `E135119473` (Ligue 1) funcionó tal cual en esta jornada, con los 9 partidos de la jornada en la lista.
- Los ficheros de `bruto/` son transcripciones resumidas, no copias literales del texto de la página (faltan líneas en blanco y algún texto repetido).

## analista-contexto

- Web sin utilidad para 2026-27: 5 busquedas (2026-11-06) devolvieron solo previas de temporadas anteriores; no hay bajas, sanciones ni onces verificados en ningun partido. La pagina "J11 principaux absents" de ligue1.com no estaba indexada.
- Hora PSG - Troyes: cuotas.json trae 20:05Z (ESPN/football-data) y bet365 20:45 de Francia = 19:45Z; sport-flash y sportytrader parecen dar tambien 19:45Z. Sin confirmacion de la LFP; el calendario de ESPN puede llevar una hora provisional. Revisar que fuente manda.
- Nombre distinto: bet365 "Odsonne Edouard" frente a ESPN "Odsonne Édouard" (con tilde); normalizar tildes al cruzar jugadores.
- Lens: Abdallah Sima (5 titularidades hasta 2026-09-18, 0 apariciones en las 4 jornadas siguientes) y Odsonne Edouard (0 en las 2 ultimas) siguen en la tabla de remates a puerta de bet365 aunque probablemente no juegan; filtrar jugadores sin apariciones en los ultimos 3 partidos del equipo.
- Angers - Nice: Jansson (0 titularidades en 9) y Djibirin (0 en 9) tienen linea de remates en bet365 siendo suplentes habituales; Kadile (Lens) 1 titularidad en 6.
- Le Mans (recien ascendido, 43 partidos en la base): secundarios con peso 7.7 y n=9; corners y tarjetas del motor poco fiables.

## simulador-apuestas

- Unders en desequilibrio grande: PSG-Troyes Menos 3.5 a 2.20 (nec. 45.5%) frente a un cubo de la base de 55.0% (Elo local +250/+320, n=462) y Lens-Marseille Menos 3.5 a 1.66 (nec. 60.2%) frente a 70.4% (Ligue 1, Elo 0/+30, n=216): el aviso "el simulador ve menos goles" es global, pero aquí la base da incluso más unders que el simulador; medir el sesgo del simulador por banda de línea (3.5/4.5) y de desequilibrio antes de aplicar la contracción w=0.20 a todos los unders por igual.
- Lens-Marseille Más 2.5 a 1.50 (66.7% implícito) frente a un 49.5% del cubo de Ligue 1 con Elo 0/+30 (n=216): mercado y base separados 17 pts en el mismo partido; candidato a revisar si bet365 infla los Overs de partidos grandes en Ligue 1.
- La regla "máx. 1 under por jornada" dejó fuera 3 unders con margen >= 3 pts (PSG Menos 4.5 +8.7, PSG Menos 2.5 +7.4, Lens Menos 3.5 +3.8); los tres de PSG son la misma tesis, pero Lens no: conviene que la revisión mida si la regla debe ser "1 por tesis" en vez de "1 por jornada".
- La columna `limpia` marcó Brest-Lyon Más 2.5 con margen 3.03 pts y EV +2.0% (w=0.35); un umbral de EV >= +3% con p_apuesta, como pide la skill, la habría filtrado.
- `evaluacion.csv` calcula p_apuesta contra 1/cuota (con margen), no contra la probabilidad sin margen por método potencia que pide la skill: infravalora el EV de los unders/overs y no es el mismo método que se usará en el CLV.
- La tabla de remates a puerta de Lens incluye a Sima (sin jugar desde 2026-09-18) y Edouard (ausente en las 2 últimas): el simulador los evalúa igual; filtrar jugadores sin minutos en los 3 últimos partidos del equipo antes de simular.
- PSG-Troyes sigue con inicio_utc 20:05Z en cuotas.json pese a que bet365 y dos webs dan 19:45Z: la hora del recolector debería salir de bet365, no de football-data.

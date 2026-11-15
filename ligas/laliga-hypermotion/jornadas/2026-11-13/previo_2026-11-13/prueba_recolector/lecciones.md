## recolector-cuotas (prueba LaLiga Hypermotion, 2026-11-13)

- Tras pulsar "Ver más" la página se desplaza y la posición de los elementos cambia: un clic en coordenadas hecho a continuación cayó sobre una cuota (2.20, Miguel De la Fuente Más 4.5, Almería-Andorra) y añadió una selección a la hoja de apuesta. Se quitó con su aspa, sin apostar. Propuesta: hacer `find` de nuevo antes de cada clic en "Ver más" o en la flecha de sección, y comprobar con captura que el objetivo es el botón y no una casilla.
- La flecha de "Remates a puerta" despliega solo un índice A-Z de jugadores, no una tabla de cuotas (Valladolid-Castellón). En Almería no se reabrió por el clic accidental anterior.
- Una lectura de la pestaña Remates se reinició sola (la tabla volvió a 6 jugadores y la sección a puerta se plegó). El encabezado pasó de "Iniciar sesión" a un saldo "0,00€" con avatar, sin intervención del recolector. Revisar si es un cambio de sesión de bet365.
- En la columna 1+ bet365 muestra un candado en vez de cuota cuando la línea no se ofrece (Almería: Leo Baptistao, Jon Morcillo, Miguel De la Fuente). `get_page_text` no deja hueco para ellos: la columna queda con 48 valores de 51 jugadores. Con candados la alineación se debe leer fila a fila.
- Las columnas 4+ y superiores tienen menos valores que jugadores en los dos partidos (Valladolid 4+: 47 de 59; Almería 4+: 43 de 51). No se copiaron; faltó la lectura fila a fila por zoom (solo se verificaron visualmente las primeras 11 filas de Valladolid y las 6 de Almería).
- La tabla "Remates a puerta" no se pudo leer en ninguno de los dos partidos (solo índice A-Z o lista de nombres sin cuotas en el texto).
- En ninguno de los dos partidos aparecieron remates de equipo o del partido (Remates local/visitante, Remates más/menos del partido) en la pestaña Remates. No se copió ninguno.
- Mezcla de idiomas: en Populares los mercados salen en inglés ("Full Time Result", "Both Teams to Score", "Shots") y en la pestaña Remates en español. La pestaña "Remates" de Populares se llama "Shots" en el DOM.
- Nombre en bet365 sin entrada en `nombres_bet365` de la ficha: "Real Valladolid" del calendario aparece como "Valladolid" en la página (Valladolid-Castellón). Almería, Andorra y Castellón ya están en la ficha y casaron bien.
- El partido Almería-Andorra empieza a las 18:30 CET en bet365 (17:30Z), coincide con el calendario.

## recolector-cuotas
- Tablas de remates descartadas en 10 de 10 partidos leídos: en cada una hay una fila con menos cuotas que jugadores (la fila 1+ en Sociedad B, Almería, Mallorca, Eibar, Granada y Albacete; la fila 8+ en Leganés, Valladolid, Oviedo y Eldense) y en varias aparece un jugador "No disp.". Las filas de las demás líneas sí cuadran en varias tablas (Eldense 1+ a 7+, Oviedo 1+ a 7+): puede valer la pena que la regla mire si la fila corta es la del jugador sin cuota, o que se lea la tabla desde la captura.
- Tenerife - Ceuta (dom 15 nov, 13:00 CET) no aparece en la lista de la liga en bet365 a las 14:31 CET (la lista tiene 10 partidos). Revisar si se ha retirado o si bet365 lo publica más tarde.
- Varios partidos (Sociedad B - Sp Gijon, Oviedo - Girona, Eibar - Cadiz) se abrieron con la página en inglés. Las cifras son las mismas, pero los nombres de mercado cambian (Full Time Result, Both Teams to Score, Shots).
- Los nombres de equipos de la ficha (`nombres_bet365`) casaron con todos los partidos de la lista. No hay nombres nuevos que apuntar.

## recolector-cuotas (relectura de remates, 2026-11-11 14:40 CET)
- Las 10 tablas de Remates se leyeron fila a fila con zoom (read_page no estaba en mis herramientas). Al desplazar la tabla a la derecha salen las columnas altas; el texto de `get_page_text` coincidió con la captura en todas las celdas. Las casillas sin cuota muestran un candado, y en 8+/9+ es lo que las deja vacías.
- Casillas vacías en 1+ de Sociedad B (Arkaitz Mariezkurrena), Almería (Leo Baptistao, Jon Morcillo), Mallorca (Buksa, Zito Luvumbo, Liso), Granada (Jorge Pascual), Eibar (Jon Bautista) y Albacete (Samuel Obeng): en todas ellas el candado sale solo en la primera columna.
- Hay un "No disp." bajo el nombre de algunos jugadores (Axel Perez en Valladolid, Job Ochieng en Sociedad B, Ibrahima Kone en Almería, Abdon Prats en Mallorca, Agustin Coscia en Eldense, Antonio Puertas y Fabio Garcia-Moreno en Albacete). Sus cuotas sí aparecen en la tabla y se copiaron.
- Cada tabla de Remates muestra 6 jugadores; hay un "Ver más" que no pulsé (solo se permiten clicks en equipos). Puede haber más jugadores ocultos.
- No aparece "Remates a puerta" en el Populares de ninguno de los 10 partidos.
- Tenerife - Ceuta sigue sin aparecer en la lista de la liga a las 14:47 CET.

## analista-contexto

- Búsquedas web para Hypermotion J13-14 (2026-11-11): casi todo devuelve temporadas 2024-25 o 2025-26 o partidos de Primera. Solo dieron dato de 2026 FutbolTotal (sancionados jornada 13, publicado 2026-11-06) y las páginas de partido de LaLiga y FutbolTotal (fecha y hora). No hubo alineación probable ni parte de lesiones de ningún partido de esta jornada.
- FutbolTotal (sancionados J13) fue la fuente útil: dio 9 sanciones con motivo, y confirmó a Corpas (Albacete, 3 partidos), que coincide con "No disp." en bet365. Propuesta: añadirla a la skill como fuente de sanciones de Hypermotion.
- FutbolFantasy `/laliga-hypermotion/lesionados` devuelve 404; no sirve para Hypermotion.
- Fechas y horas contradictorias entre fuentes en 5 de 10 partidos (Leganés-Burgos, Almería-Andorra, Mallorca-Córdoba, Oviedo-Girona, Eldense-Sabadell). Propuesta: que `cuotas.json` o el contexto marque la hora de la fuente de bet365 y que el recolector lo compare con la página de LaLiga.
- Sanciones de la lista J13 (publicada 2026-11-06) incluyen a jugadores de Leganés que jugaron el 7-nov según el contexto. No queda claro si esa lista es de la jornada 12 o de la 13, así que no se sabe con certeza si cumplen el próximo partido. Propuesta: que la fuente de sanciones indique la jornada exacta.
- 9 jugadores con cuota no tienen titular_prob del script (sin datos en ESPN). El simulador trata la probabilidad vacía como "sin dato": no pone aviso de titularidad dudosa. Les puse 0,1 a mano, por el patrón "ninguno de 3". Propuesta: que `analizar_jornada.py contexto` ponga 0,1 por defecto a los jugadores sin historial en ESPN, o que el simulador avise de los vacíos.
- Job Ochieng (Sociedad B) tiene una lesión citada en un preview de El Goldigital sobre Sociedad B-Ceuta, sin fecha verificada. Queda en 0,1, pero conviene que el recolector o el analista tengan una fecha de noticia para no dejarlo en "sin verificar".
- Elgoldigital cita a Balda y Carbonell como sancionados en la J29 de 2025-26 (no de esta temporada): cuidado con las listas de sanciones antiguas en el buscador.

## simulador-apuestas
- Hypermotion 2026-11-13: 0 de 567 apuestas con margen. El máximo es -1,1 puntos, y en los partidos anclados el margen de equipo está siempre entre -1 y -5, así que los mercados de goles anclados no dan valor por construcción (solo pueden darlo los derivados que el ancla reparte distinto, como las combinadas).
- Remates de jugador en Hypermotion: con 6+ titularidades, en 3+ el modelo da 34%, la frecuencia real 35% y bet365 60%; en 4+, 20%, 18% y 49%. Las 1+ van a 1.01-1.07. Los remates por equipo de ESPN cuadran con football-data (13,1 y 13,1 por partido en 2026-27). bet365 cuenta otra cosa (¿bloqueados o Sustituto+?) o tiene un margen enorme. Convendría medir el resultado real de estas líneas antes de seguir leyendo 347 cuotas por jornada.
- El analista dejó en 0,1-0,3 la titularidad de 9 jugadores con 6+ titularidades (Astiazarán 9, O. Camara 7, D. González 10, Latasa, Jacobo, Morata 6, Abel Ruiz 8, Gorka Carrera 8) sin una noticia concreta. Si eso viene del patrón "ninguno de 3", es demasiado agresivo para delanteros habituales. Esta vez no cambió nada, porque igualmente no había margen.
- No hay córners ni tarjetas en el `cuotas.json`, aunque la ficha los incluye en `mercados`. Son justo los mercados donde el simulador tiene señal propia en partidos anclados: el recolector debería leerlos.

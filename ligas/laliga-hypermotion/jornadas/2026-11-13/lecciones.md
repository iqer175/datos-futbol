## recolector-cuotas

- 19:53 CET: apareció en bet365 un aviso "Alerta de actividad" (tiempo de sesión, botones "Seguir conectado" y "Salir") al volver a la lista de la liga. No pulsé nada; cerré la pestaña. Eldense - Sabadell y Albacete - Celta B quedaron sin leer.
- Remates de partido (totales, "Remates más/menos X.5" del partido): no aparecen ni en la pestaña Remates ni en Crear apuesta de ninguno de los 9 partidos leídos. Nada copiado.
- Selectores de Crear apuesta: en Eibar - Cádiz, al elegir el equipo en la sección Remates cambió también el selector de Remates a puerta (y al revés). Hay que comprobar los dos encabezados tras cada cambio.
- Eibar - Cádiz: la tabla de remates de Eibar no quedó verificada (transcripción dudosa, con un jugador de Cádiz que aparecía también en la lista de Eibar). Se descartó; solo se copió la tabla a puerta de Eibar y las dos de Cádiz.
- Avisos de empate del script dejados tal cual: Valladolid (a puerta|4: Hugo Calvo, Marcos Andre, Victor Fernandez; remates|4: Alejandro Galdeano, Carlos Clerc, Ivan Garriel), Sociedad B (remates|4: Alex Lebarbier, Benat Isasa, Jon Garro, Peru Larranaga), Oviedo (remates|5: Aritz Aldasoro, Daniel Villahermosa), Tenerife (remates|4: Arick Betancourt, Joaquin Gonzalez).
- Carpeta bruto/: los ficheros Populares completos solo existen para Leganes-Burgos y Valladolid-Castellon. Para los demás, el texto de Populares no se guardó en bruto/ (las cuotas de mercado sí están en cuotas.json).

## analista-contexto
- Búsqueda web sin noticias de la última semana para las 9 jornadas de Hypermotion: no hubo bajas ni alineaciones verificables; los resultados eran de temporadas anteriores o sin fecha. Las noticias quedaron "parciales".
- Fechas en conflicto: el contexto (ESPN) pone Leganés-Burgos en 13 19:30Z (confirmado por laliga.com), pero varios partidos de la jornada 14 aparecen en LaLiga o Futboltotal como domingo 15 (Valladolid-Castellón, Almería-Andorra, Tenerife-Ceuta, Granada-Las Palmas, Eibar-Cádiz) o sábado 14 (Mallorca-Córdoba según Futboltotal). Conviene que el script contraste la fecha con una segunda fuente.
- `espn.calendario(['laliga-hypermotion'], ...)` devolvió un DataFrame vacío a las 18:56Z del 13 de noviembre; no se pudo usar la alineación oficial de ESPN.
- Artículos sin fecha que mencionan lesiones de Franquesa (cruzado), Sancris (isquiotibiales), Karrikaburu, D. González, Lencina y Appin: no se pudieron fechar. Hacen falta filtros de fecha en la búsqueda o fuentes del club.

## simulador-apuestas
- Con las plantillas completas (3.282 filas), 384 apuestas salen "con valor", pero casi todas son suplentes o muestras cortas (< 6 titularidades o p_titular < 0.8): la lista "con valor" se vuelve inmanejable; convendría que solo incluyera jugadores con 6+ titularidades y p_titular >= 0.8, o separarlos en otra tabla.
- Varias recomendadas tienen el modelo muy por encima de su frecuencia real: Zárate 1+ remates 61% vs 38% real, Orozco 2+ 55% vs 33%, Vencedor 1+ 60% vs 33%, Gharbi 2+ 71% vs 42% (0 de 5 en las últimas). Son defensas o jugadores de poco remate que el encogimiento hacia la posición sube; vale la pena medir en la revisión si en ese grupo (modelo - frecuencia > 15 pts) el modelo sigue calibrado.
- Christian Gomis (Tenerife): bet365 ofrece remates desde 3+ (1.80) y a puerta desde 2+ (3.25) a un jugador con [2,0,0,1,1] remates en sus 5 últimos; Enric Gallego, remates 4+ a 1.57 con máximo de 3 en sus 5 últimos. O es una lectura desplazada una línea en parsear_remates.py, o el nombre se cruza con otro jugador: revisar la tabla de Tenerife en bruto/.
- No hubo ninguna cuota de remates de equipo/partido ni de córners con margen: el mercado de remates de equipo (en prueba) no se pudo probar en Hypermotion esta jornada.
- La simulación tardó unos 6 minutos con las plantillas completas (antes era bastante menos); con un partido a 30 minutos del inicio el margen para registrar fue justo.

## recolector-cuotas (lectura suelta de Eldense - Sabadell y Albacete - Celta B)
- El calendario imprimió la carpeta `jornadas/2026-11-14` para esta jornada, pero la carpeta real es `2026-11-13`. Conviene que `analizar_jornada.py calendario` use la carpeta que existe.
- En Albacete - Celta B bet365 sirvió la pestaña Populares en inglés (Full Time Result, Both Teams to Score, Draw No Bet, Alternative Total Goals) y luego cambió al español dentro de la misma sesión. Nombres en inglés: "Albacete Balompie" y "Celta Fortuna" (la ficha ya cubre "RC Celta Fortuna" = "Celta B").
- Ni en Populares, ni en Crear apuesta, ni en la pestaña Remates de ninguno de los dos partidos había líneas de remates de equipo ni del partido (solo tablas de jugador). No se copió ninguna "Remates local/visitante" ni "Remates más/menos" de partido.
- El selector de equipo de las tablas de remates de Crear apuesta (que cambia el equipo de la tabla, y cada tabla tiene el suyo) no lo encontraba `find` con el nombre del equipo en frase larga. Con el nombre suelto ("Eldense", "Sabadell", "Albacete Balompié") sí aparece. Cada sección (Remates a puerta y Remates) necesita su propio cambio de equipo.
- Pulsar la cabecera de una sección ya desplegada la pliega (y la pestaña Crear apuesta al volver a entrar pliega todo): comprobar el estado con `find` antes de pulsar otra vez.
- Para abrir Eldense - Sabadell desde la lista, el nombre del equipo no salía en `find` (estaba fuera de pantalla); se pulsó con coordenadas en la lista de la liga (fuera de la página del partido), comprobando después que se abría el partido correcto.
## recolector-cuotas (modo comparación, winamax)
- La página de Segunda División (sports/1/32/37) se queda en esqueletos y no lista partidos; se llega a cada partido con el buscador de la pestaña Calendario (calendar).
- Real Valladolid - Castellón y Almería - FC Andorra: sin mercados de remates ni remates a puerta de jugador (solo goles, goleadores, hándicap, combinadas). Almería - Andorra: 'Más de 2,5' solo aparece dentro de una combinada con Doble oportunidad (2,15), no como mercado suelto: no copiado.
- Buscador 'Mallorca' y 'Orozco' sin resultados de partido; no se pudo abrir Mallorca - Córdoba.


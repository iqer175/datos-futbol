## recolector-cuotas

- bet365 no tenía dirección para League 2: anotada `https://www.bet365.es/#/AC/B1/C1/D1002/G40/J1/Q1/F%5E2001/` (listado de Reino Unido, donde aparece "Inglaterra - League 2"). No es una página propia de la liga: conviene confirmarla o buscar la dirección propia de la liga.
- La pestaña Remates (aparece como "Shots") da "No se puede mostrar este contenido" en Exeter - Cheltenham y en Fleetwood Town - Tranmere.
- 8 de 10 partidos (Bristol Rvs, Colchester, Crewe, Port Vale, Rochdale, Salford, Shrewsbury, Swindon) no tienen ninguna tabla de remates en Crear apuesta ni pestaña Remates: solo Exeter y Fleetwood tienen tablas de remates a puerta.
- En Exeter y en Fleetwood, "Total - Remates" del partido sale con las mismas líneas y cuotas (19.5 a 29.5: Más 1.10, 1.22, 1.44, 1.83, 2.50, 3.75; Menos 6.50, 4.00, 2.62, 1.83, 1.50, 1.25). Puede ser dato genérico de bet365: no se copió. Revisar.
- Fleetwood: el selector de equipo (de Fleetwood Town a Tranmere) no cambió la tabla de remates a puerta; el visitante no se pudo leer.
- En Exeter no encontré la sección de remates de jugador ("Remates" sin "a puerta") con find; solo se leyó "Remates a puerta".
- Las tablas de remates a puerta de Crear apuesta no llevan la etiqueta SUSTITUTO+. Sí la llevan "Jugador - Anotará", "Jugador - Tarjeta" y "Goalscorers" (Populares).
- Córners: en todos los partidos, las líneas son enteras (9, 10, 11) con Exactly/Over/Under: no se copia ninguna. Los córners por equipo no se leyeron: la sección "Córners" de Crear apuesta no está entre los clics permitidos.
- Tarjetas: "Ambos equipos recibirán tarjetas" (Sí/No) copiado con clave nueva "Ambos reciben tarjetas - Sí/No" (Exeter y Fleetwood). Exeter tiene tres columnas y la fila No trae dos valores: se copió la primera columna.
- Clic accidental en la cabecera "Inglaterra - League 2" de la lista: plegó la sección. Se volvió a desplegar. No se pulsó ninguna cuota.
- Al empezar pulsé una pestaña ya abierta (chrome 1842737824) en vez de la mía; queda en bet365 (AS/B1). Mi pestaña de trabajo es la 1842737825.

## analista-contexto (League Two 2026-11-14)
- Las búsquedas generales en la web devolvieron casi solo temporadas anteriores (2019-2025). Lo útil salió de las webs oficiales de los clubes (sección de noticias, con fecha en cada entrada): exetercityfc.co.uk y fleetwoodtownfc.com. Conviene empezar por ahí en League Two.
- Fleetwood (12 nov 2026): el club confirma "suspensions this weekend" sin nombrar a ningún jugador. Sin nombre no se puede ajustar ningún titular; sería útil que la ficha o el recolector pidiera la lista de sancionados a 11v11 o al club.
- starting11.com/injuries/league-two y injuriesandsuspensions.com (página de League Two) no traen fechas por jugador: no sirven para verificar bajas por sí solas.
- Exeter: las selecciones (sub-21 Irlanda y Escocia, Kenia) sacaron a tres jugadores de cuota (Cummins 0.9, Sutherland 0.1, Harper 0.1). El script no sabe de convocatorias: mirar la lista de selecciones de la semana antes de dar titularidades de 0.9 a jugadores jóvenes.
- ESPN llega solo hasta 2026-11-03 y la jornada es el 14 nov: el script deja titularidades de hace una jornada. Revisar si la ficha debería marcar esta liga como de datos desfasados.
- Ocho de diez partidos sin noticias verificadas y sin jugadores con cuota: la frase "sin noticias" aparece como alerta, no como baja.

## simulador-apuestas (League Two 2026-11-14)
- `con_valor`/`limpia` se calculan con la prob con Sustituto+ aunque la tabla de bet365 no lo lleve: de 26 "con valor" solo ~10 siguen con valor sin Sustituto+ (Scully 1+: 56,1% -> 43,9%). Convendría que el recolector marque `sustituto_plus: false` por partido y que el script use entonces `prob_sin_sustituto` en limpia, margen y valor.
- Coughlan 2+ a puerta pasa de 10,3 pts de margen (limpia) a 3,1 pts sin Sustituto+: la única recomendada de la jornada vive justo en el límite por esa diferencia.
- "Ambos reciben tarjetas - Sí/No" sale como mercado desconocido en el simulador (4 avisos): o se modela con las tarjetas por equipo o el recolector deja de copiarlo.
- 8 de 10 partidos sin mercados de jugador ni remates de equipo y con goles anclados: en League Two el simulador no tiene dónde encontrar margen fuera de jugadores; 0 apuestas de equipo por encima de la necesaria en 220 evaluadas.
- Reece Cole (Exeter) sale a 61% en 1+ a puerta a 1.83 pero con titular_prob 0.1 sin baja escrita en el contexto: conviene que el analista diga el motivo cuando baja a un jugador con 8 titularidades.

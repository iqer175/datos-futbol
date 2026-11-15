## analista-contexto

- Eldense y Sabadell: ninguna fuente dio bajas, dudas ni alineación probable. Alicanteplaza, El Gol Digital, futboltotal, starting11 y la ficha de LaLiga no traían contenido útil. Hace falta una fuente de bajas para Segunda que tenga parte médico o convocatoria con fecha.
- Hora del partido Eldense-Sabadell: futboltotal da 11:00 CET, otra web 20:00 y starting11 17:30 GMT. Se usó la ficha de LaLiga (17:30 Canarias = 17:30 UTC). Propuesta: fijar LaLiga como fuente de calendario en la ficha de la liga.
- rccelta.es (parte médico con fecha de actualización) es la fuente más fiable de la búsqueda, pero solo cubre el Celta. El Albacete no tiene un equivalente localizado.
- En `jugadores` aparecen nombres sin datos en ESPN que no tienen `titular_prob` (Eldense: Albert Orriols, Alejandro Ibarrondo, Chavez Fidel, Genar Fornes, Lluis Blanch y otros; Albacete: Agustin Medina, Antonio Puertas, Germain Milla y otros). Conviene que el script avise si esos nombres no tienen cuota o que no los lista.
- Alerta del script "Elo sin asentar" en los dos partidos: el motor queda en fiabilidad baja; el 1X2 y el total del motor deben tomarse con el peso reducido.

## simulador-apuestas

- Las 13 apuestas de jugador con prob >= 60% y margen alto (Macho 83%, Bernabeu 80%, Jordi Martín 80%, Ribeiro 80%) caen todas por muestra corta (1-5 titularidades) y p_titular 0.1: en partidos con equipos recién ascendidos casi toda la señal está en suplentes sin muestra.
- Macho y Bernabeu remates 1+ superan la necesaria en 25-26 pts con p_titular 0.1: el modelo calcula prob con 76-77 min esperados aunque el contexto dé 10% de titularidad; convendría que el aviso de sospecha salte junto a "titularidad dudosa" o que la prob se muestre ponderada por p_titular.
- El Sustituto+ aporta 10-13 pts en las dos recomendadas (Rastrojo 56→66%, Aguado 54→67%): sin él ninguna pasaría; las dos dependen por completo de que bet365 lo aplique en esas líneas.
- Aguado a puerta 1+: modelo 18% frente a 31% de la cuota (-12 pts) mientras su remates 1+ sale +3 pts; el mercado ve más calidad de remate que el modelo en un lateral con 0 a puerta en 7 titularidades.
- Con fiabilidad baja (Elo semilla de Sabadell y Celta B) y w=0.20, el EV de las dos recomendadas baja a +1.0/+1.3 %: la regla de w=0.20 solo aplica a unders, pero quizá debería aplicarse a todo partido con fiabilidad baja.
- En `cuotas.json` de estos dos partidos no hay remates de equipo ni córners, aunque la ficha los permite: revisar si el recolector abrió la pestaña "Remates" completa.

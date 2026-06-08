# PROYECTO: Lista de leads de restaurantes para venta de helado (CABA)

> Pegá este archivo al inicio de la próxima sesión para que Claude retome el proyecto sin empezar de cero.

---

## 1. Qué estamos haciendo
Construir y enriquecer una base de **restaurantes en CABA** para que mi hermano les venda **helado** (para postre en carta), por **mail, WhatsApp o visita física**. El vendedor que tiene también entrega y vende.

## 2. El producto / cliente
- Helado **industrial**, calidad **media / media-alta**.
- Pocos sabores (**3 o 4**).
- Pensado como **postre en el menú** de restaurantes.
- Sin problema de privacidad de datos (el usuario lo aclaró explícitamente).

## 3. A quién apuntamos (targeting)
**SÍ — restaurantes con postre como parte de la experiencia:**
- Italianos / trattorias, bistró, cocina de autor o contemporánea, parrillas premium, parrillas y bodegones de volumen, hotelería / restó de hotel.

**NO — excluir siempre:**
- Fast food (McDonald's, Mostaza, etc.), bares, cervecerías / brewpubs, rotiserías y take-away, cafés y panaderías.

**Señal de buen lead (priorizar):**
- Tienen **ÁREA DE EVENTOS con mail propio** (compra profesionalizada) → mejor interlocutor.
- **Dueño / decisor identificable** o marca **multi-local** (si entrás, escala).

**Señal de lead difícil / descartar:**
- Ya **fabrican su propio helado** o tienen carta de postres propia fuerte (panna cotta, etc.).

## 4. Geografía
- **Foco actual:** Palermo (Soho, Hollywood, Botánico) y Recoleta / Barrio Norte / Balvanera.
- **Para escalar:** Villa Crespo, Belgrano, Núñez, Caballito, Colegiales, San Telmo, Microcentro.

## 5. Fuentes de enriquecimiento y qué da cada una
| Fuente | Qué aporta |
|---|---|
| **Google Maps / Places** | Columna vertebral: nombre, dirección, barrio, tipo, rating, reseñas, teléfono, web. |
| **Instagram** | Canal principal. Handle + bio (a veces mail/WhatsApp) + botón de contacto (WhatsApp). |
| **Facebook** | Suele exponer MÁS que IG: mail, teléfono, botón "Enviar WhatsApp". |
| **Web propia / Linktree / wa.me** | Mejor fuente de mail (info@, reservas@, eventos@) y link directo de WhatsApp. |
| **TripAdvisor** | Teléfono, rango de precio, link a web, distinciones (Travellers' Choice). NO da mail/WhatsApp. Sirve para validar. |
| **Guía Michelin** | Distinciones (estrella, Bib Gourmand, recomendado) = perfil de calidad. |
| **Prensa gastronómica** (Cronista, Perfil, La Nación, Time Out) | Muchas veces publican WhatsApp, IG y nombre del dueño. Muy productiva. |
| **Agregadores** (RestaurantGuru, Sluurpy, Carta.menu, Foursquare) | Repiten teléfono / IG / horarios. |
| **Plataformas de reserva** (Meitre, Woki, OpenTable) | Confirman el local y a veces el canal. |

## 6. Regla de contacto (importante)
- Si el **WhatsApp está publicado** → ponerlo.
- Si solo está **detrás del botón de IG** → dejar el **link directo al perfil de IG** con la nota "abrir y tocar contacto" (no se puede scrapear el número).
- Bodegones tradicionales sin redes → **teléfono / visita**.
- Tel con formato **+54 9 11...** = celular = WhatsApp casi seguro.

## 7. Tamaño del restaurante (mesas / cubiertos) — LIMITACIÓN CONOCIDA
**Google Places NO expone capacidad ni cantidad de mesas/cubiertos.** No hay forma directa. Aproximaciones disponibles, en orden de utilidad:
1. **Capacidad de eventos** = el mejor dato cuando existe. Los locales con área de eventos publican su máximo (ej.: Tomate Soho 140 / Rosedal 150 invitados). Buscar en su web/IG "eventos".
2. **Cantidad de reseñas de Google** = proxy de *volumen de tráfico histórico*, NO de mesas (ej.: La Cabrera 23.380 mueve muchísimo más que CHIC 145). Útil para ordenar por tamaño relativo.
3. **Prensa** = a veces menciona m² o capacidad (ej.: Hierro "400 m²"). Aparece de casualidad, no para todos.
4. Si se necesita el dato real → estimar por fotos o **llamar al local**.
**Conclusión operativa:** no prometer "cantidad de cubiertos". Usar # reseñas como tamaño relativo y capacidad de eventos donde exista.

**Columna fija de la planilla: "Capacidad eventos (comensales)".** Existe desde v4. Cómo se llena:
- Con el **nº de comensales/invitados** cuando el local lo publica (web/IG, sección "eventos"). Celda en **celeste**.
- Si hace eventos pero **no publica el número** → "Sí (eventos) — nº a confirmar". Celda en **amarillo** (dato a completar por web/IG o llamada).
- Si **no hace eventos / no hay dato** → "—".
- OJO: capacidad de eventos ≠ cubiertos del salón normal (suele ser el máximo al privatizar). Es el mejor proxy de tamaño *publicado*, no el aforo exacto.

## 8. METODOLOGÍA DE GENERACIÓN DE LA BASE (leer antes de "correr de nuevo")
**El tamaño de la muestra lo fija Claude, NO Google.** La muestra inicial de ~21 salió de **5 búsquedas temáticas × 5-8 resultados c/u**, filtrada (se sacaron fast food, bares, take-away). No es un techo de Google.

Hechos técnicos del tool `places_search`:
- Devuelve **hasta 10 resultados por consulta**.
- El **volumen total** = (nº de barrios) × (nº de subrubros) × hasta 10, menos duplicados.
- Con la grilla completa, Capital da fácil **150-300 leads**.

### NO es determinístico
- Places ordena por **relevancia / proximidad / popularidad**, y eso **cambia con el tiempo y entre llamadas**.
- Los "pesos pesados" reaparecen casi siempre (Don Julio, La Cabrera, Tomate); **los del borde de cada lista entran o salen**.
- **No confiar** en "correr la misma query y esperar el mismo resultado". No es una base fija.

### Cómo hacerlo reproducible y completo: BARRIDO SISTEMÁTICO
En vez de repetir una búsqueda, **barrer una grilla** y deduplicar:
1. Definir grilla **barrio × subrubro**.
2. Una consulta por cada combinación, `max_results = 10`, query tipo: `"[subrubro] en [barrio] Buenos Aires"`.
3. Juntar todo y **DEDUPLICAR por `place_id`** (fallback: nombre + dirección).
4. Aplicar el filtro de exclusión (sección 3) y clasificar Fit.
5. **Antes de correr, mostrarle al usuario la grilla de combinaciones** para que valide.

**Grilla sugerida:**
- *Barrios:* Palermo Soho, Palermo Hollywood, Palermo Botánico/Chico, Recoleta, Barrio Norte, Balvanera. (Escalar: Villa Crespo, Belgrano, Núñez, Caballito, Colegiales, San Telmo.)
- *Subrubros:* restaurantes de autor, italianos/trattorias, parrillas, parrillas premium, bistró, español/tapas, bodegones, contemporáneo/fusión, restó de hotel.
- Ej.: 6 barrios × 9 subrubros = 54 consultas × ~10 = hasta ~540 crudos → tras dedup y filtro, base sólida de varios cientos.

## 9. Estructura de la planilla (columnas)
Nombre · Barrio · Tipo · Google (nota/reseñas) · Precio · Teléfono · WhatsApp · Instagram (link) · Facebook · Sitio web/menú · Email · Reservas (plataforma) · Decisor/señal B2B · **Capacidad eventos (comensales)** · ¿Helado propio? · TripAdvisor/Guías · Fit · Notas.
Fit: **Alto** (verde) / **Medio** (amarillo) / **Bajo** (rosa) / **Muy bajo** (rojo = competencia).

## 10. Intel competitiva ya descubierta
- **El Preferido de Palermo** y **Don Julio** (ambos del grupo de **Pablo Rivero**) **fabrican su propio helado artesanal** (sambayón, dulce de leche, pistacho). No son clientes, pero marcan el **estándar de helado de la alta gama** porteña → conocimiento de mercado.
- **Raggio Osteria, La Alacena y La Cabrera** ya tienen **panna cotta / carta de postres propia** → leads más difíciles.

## 11. Estado actual (al cierre de esta sesión)
- **21 leads** de Palermo/Recoleta cargados y enriquecidos en `leads_restaurantes_CABA_v4.xlsx` (incluye columna Capacidad eventos).
- Enriquecidos a fondo (IG, WhatsApp, web, mail donde existe): los 10 de fit Alto + La Cabrera, Reencuentro, Viejo Palermo Grill, La Escondida, Museo Evita, Bilbao.
- Sin IG (contacto por teléfono/visita): Cervantes, La Martona, bodegón de Ayacucho.
- **Mejores leads hasta ahora:** Tomate (mail de eventos), Bilbao (mail de marketing), Museo Evita (mail directo), Il Matterello / Gianni's / Andante / Raggio / CHIC (WhatsApp directo).
- **Esta muestra fue para VALIDAR el método**, no es la base completa. El siguiente paso natural es el barrido sistemático (sección 8).

## 12. Próximos pasos sugeridos
1. **Barrido sistemático** (sección 8): mostrar grilla → correr → deduplicar → base completa de Palermo + Recoleta.
2. **Escalar** a más barrios (sección 4).
3. **Completar** los sin-IG por visita/teléfono y **confirmar** el handle de IG de La Alacena.
4. Armar la **secuencia de outreach** (mail / WhatsApp / visita) y un mensaje tipo por canal.
5. Definir un **mínimo de datos** por lead antes de pasárselo al vendedor (ej.: nombre + zona + WhatsApp o IG + fit).

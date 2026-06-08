# PROYECTO EUROCREM — Documento maestro
> Pegá este archivo al inicio de cada sesión. Contiene TODO el contexto del proyecto.
> Última actualización: 02/06/2026

---

## 1. Qué estamos haciendo
Construir, enriquecer y automatizar una base de **restaurantes en CABA** para que el hermano del usuario (fábrica **Eurocrem**) les venda **helado** (para postre en carta), por **mail, WhatsApp o visita física**. Tiene un vendedor que también entrega.

**Segmentos:**
- **HOY:** Restaurantes únicamente.
- **FUTURO (no ahora):** Hoteles y casas/salones de eventos como segmentos separados.

---

## 2. El producto
- Helado **industrial**, calidad **media / media-alta**.
- Pocos sabores (**3 o 4**).
- Pensado como **postre en el menú**.
- Sin problema de privacidad de datos.

---

## 3. Targeting
**SÍ:** Italianos / trattorias, bistró, autor, contemporáneo, parrillas premium, parrillas y bodegones de volumen, hotelería / restó de hotel.

**NO — excluir siempre:** Fast food, bares, cervecerías, rotiserías, take-away, cafés, panaderías. **Parrilla AL PASO** (sin mesa ni carta de postres) → excluir. Distinto de parrilla-restaurante (Don Julio, La Cabrera, Hierro) que SÍ aplica.

**Bodegones:** NO se descartan por categoría. Se evalúan por ticket y si hay momento de postre.

**Señal de buen lead:** Área de EVENTOS con mail propio, dueño/decisor identificable, marca multi-local.

**Señal de lead difícil:** Ya fabrican su propio helado o tienen carta de postres propia fuerte.

---

## 4. Geografía
- **Foco actual:** Palermo (Soho, Hollywood, Botánico) y Recoleta / Barrio Norte / Balvanera.
- **Escalar:** Villa Crespo, Belgrano, Núñez, Caballito, Colegiales, San Telmo, Microcentro.

---

## 5. Fuentes de enriquecimiento
| Fuente | Qué aporta |
|---|---|
| **Google Maps / Places API** | Backbone: nombre, dirección, barrio, tipo, rating, reseñas, teléfono, web, place_id. |
| **Instagram** | Canal principal. Handle + bio + botón de contacto (WhatsApp). |
| **Facebook** | Suele exponer MÁS que IG: mail, teléfono, botón WhatsApp. |
| **Web / Linktree / wa.me** | Mejor fuente de mail y link directo WhatsApp. |
| **TripAdvisor** | Teléfono, precio, web, distinciones. NO da mail/WhatsApp. Solo validación. |
| **Michelin** | Distinciones (estrella, Bib Gourmand) = perfil de calidad. |
| **Prensa gastronómica** | Publica WhatsApp, IG y nombre del dueño. Muy productiva. |
| **Agregadores** (RestaurantGuru, Sluurpy) | Repiten teléfono / IG / horarios. |
| **Plataformas de reserva** (Meitre, Woki) | Confirman local y canal. |

---

## 6. Regla de contacto
- WhatsApp **publicado** → número plano.
- Solo **detrás del botón de IG** → link directo al perfil ("abrir y tocar contacto"). NO se puede scrapear.
- Sin redes → teléfono / visita.
- Tel **+54 9 11...** = celular = WhatsApp casi seguro.
- **Claude NO puede operar Instagram logueado.** No pedir credenciales.

---

## 7. Tamaño del restaurante (limitación conocida)
Places NO expone capacidad. Aproximaciones: (1) Capacidad de eventos cuando la publican, (2) Cantidad de reseñas como proxy de volumen, (3) Prensa (m² ocasional), (4) Fotos o llamar.

**Columna "Capacidad eventos"** en planilla: número publicado (celeste), "Sí — a confirmar" (amarillo), "—" si no hace eventos.

---

## 8. Metodología de generación — leer SIEMPRE antes de correr de nuevo

### Universo real
- AGC CABA: ~7.442 restaurantes-cantinas registrados.
- Tras filtro de targeting: universo accionable ~1.500–2.500.

### El tamaño lo fija Claude, NO Google
- Places devuelve hasta 10 por consulta.
- Volumen total = barrios × subrubros × 10, menos dedup.
- NO es determinístico — Places varía entre llamadas. No correr la misma query esperando lo mismo.

### Barrido sistemático (reproducible)
1. Grilla barrio × subrubro.
2. Una consulta por combinación, max_results=10.
3. DEDUPLICAR por place_id.
4. Filtrar y asignar Fit.
5. Mostrar grilla al usuario antes de correr.

**Grilla sugerida:** 6 barrios × 9 subrubros = 54 consultas → varios cientos tras dedup/filtro.

### API vs Scraper
Para ~2.000 leads filtrados: **alcanza con la API de Places que usa Claude**. Scraper (Outscraper, Apify) solo vale para bajar los ~7.000 totales sin Claude. No es el caso.

---

## 9. Estructura de la planilla (desde v8)
**place_id** · Segmento · Nombre · Barrio · Tipo · Google (nota/reseñas) · Precio · Teléfono · Origen contacto · WhatsApp · Instagram · (link IG) · Facebook · Link wa.me · Sitio web · Email · Reservas · Decisor/B2B · Capacidad eventos · ¿Helado propio? · TripAdvisor/Guías · Fit · Notas · Link wa.me · Estado WhatsApp · Estado mail · Opt-in · Fecha de alta.

**Segmento:** Restaurante / Hotel / Evento.
**Fit:** Alto (verde) / Medio (amarillo) / Bajo (rosa) / Muy bajo (rojo = competencia).
**Estado por canal:** No contactado / Contactado / Respondió / Baja.
**Origen contacto:** Maps/web (automático, protegido) / IG-manual / FB-manual (candado, NUNCA pisar) / pendiente.

---

## 10. Regla de merge — NO sobrescribir enriquecimiento manual (CRÍTICO)
La corrida quincenal NUNCA reemplaza la planilla. Hace merge por place_id.

| Campos ACTUALIZABLES (Maps refresca) | Campos PROTEGIDOS (nunca pisar si tienen contenido) |
|---|---|
| Rating, nº reseñas, abierto/cerrado, horarios, precio | WhatsApp, email, notas, Estado WA, Estado mail, opt-in, link wa.me, Origen contacto = IG-manual/FB-manual |

**Regla:** escribir solo si la celda está vacía. Backup con fecha antes de cada corrida.

Para enriquecer a mano: abrís IG/FB, copiás el número en WhatsApp, escribís "IG-manual" en Origen contacto → ese es el candado.

---

## 11. Objetivo final — automatización de envíos

### Canal mail (simple)
- Texto libre desde el primer mensaje. Sin aprobación de nadie.
- n8n: nodo Gmail/SMTP.
- Requisito único: dominio propio + SPF/DKIM/DMARC configurado una vez.

### Canal WhatsApp — doble canal SIMULTÁNEO (decisión acordada)
Si el lead tiene mail Y WhatsApp → recibe los dos a la vez.
- **App WhatsApp Business (la que tiene el hermano):** solo para envío manual/semi-manual. Lista de difusión solo llega a quienes te tengan agendado.
- **WhatsApp Business Cloud API (para escalar):** número DEDICADO + plantilla aprobada por Meta + proveedor (Wati, 360dialog). n8n orquesta todo.

### Proceso Meta — WhatsApp Business Cloud API
**Capa 1:** empresa verificada en Meta Business (WABA + número dedicado ≠ personal del hermano).
**Capa 2:** plantillas pre-aprobadas (categoría Marketing). Variables {{1}} = nombre del local. Meta revisa en minutos/horas.
**Capa 3:** calificación de calidad del número (verde/amarillo/rojo). Límites: 250 → 1.000 → 10.000 conversaciones/día.
**Ventana 24h:** cuando el restaurante responde, se puede chatear texto libre por 24h → ahí sigue el humano.

### n8n — flujo de nodos (Camino B activo hoy)
```
Webhook GET → Leer Sheets → Procesar todos los leads → Generar HTML → Mostrar página
```
URL activa: https://primary-production-5c81.up.railway.app/webhook/eurocrem-whatsapp

**Lógica de envío (futura — Camino A):**
```
Para cada lead (Estado WA/mail = No contactado):
  ¿tiene email?    → enviar MAIL
  ¿tiene WhatsApp? → enviar PLANTILLA WA
  → registrar Estado WA/mail = Contactado + fecha
```

---

## 12. Intel competitiva
- **El Preferido de Palermo** y **Don Julio** (grupo Pablo Rivero): fabrican helado propio + kiosco. Fit Muy bajo. Marcan el estándar de la alta gama.
- **Raggio, La Alacena, La Cabrera:** ya tienen panna cotta / carta de postres propia → leads más difíciles.

---

## 13. Estado actual al 02/06/2026

### Planilla
- 21 leads Palermo/Recoleta en `leads_restaurantes_CABA_v8.xlsx`.
- Columnas: place_id, Segmento, Origen contacto, Estado WhatsApp, Estado mail, Link wa.me (todos clicleables).
- place_id verificados por API: Il Matterello, Tomate, Trattoria Olivetti, Gianni's, Andante.

### Google Sheet de prueba
- Ubicación: Mi Drive > Eurocrem > Eurocrem_leads
- Sheet ID: `1MzUe7EtmB9qfntYY29EK6YDijywGEZpffnT1SMSRW8o`
- Hoja: `leads_helados_CABA`
- Columnas: place_id | Nombre | Tipo | WhatsApp | Estado_WA | Estado_mail | Fecha_de_alta
- Datos: 3 leads de prueba (Il Matterello, Andante, Tomate)

### n8n
- Railway URL: https://primary-production-5c81.up.railway.app
- Webhook activo: https://primary-production-5c81.up.railway.app/webhook/eurocrem-whatsapp
- Workflow v3 activo — genera página HTML con botón "Abrir WhatsApp" por cada lead
- **BUG PENDIENTE:** el nodo "Leer leads" solo trae 1 fila de 3. Causa probable: caracteres invisibles en headers del Sheet o formato de fechas distinto entre filas.

### MCP n8n conectado en Claude Desktop
- Node.js instalado: C:/Program Files/nodejs/
- Config Claude Desktop: `"command": "C:/Program Files/nodejs/npx.cmd"`
- MCP server: https://primary-production-5c81.up.railway.app/mcp-server/http
- Estado: **CONECTADO Y AUTORIZADO** (Allow dado en sesión de hoy)

---

## 14. Archivos del proyecto
| Archivo | Contenido |
|---|---|
| `leads_restaurantes_CABA_v8.xlsx` | Planilla principal — 21 leads enriquecidos |
| `PROYECTO_EUROCREM_MASTER.md` | Este archivo — contexto completo |
| `FLUJO_completo_prospeccion.md` | Flujo funcional + WhatsApp Cloud API + Email + n8n |
| `PLANTILLAS_mensajes_outreach.md` | Plantillas WA (×3) y mail listas |
| `n8n_workflow_whatsapp_v3.json` | Workflow activo en Railway |
| `SRS_Sesion_20260602.md` | Resumen sesión 02/06/2026 |

---

## 15. Próximos pasos en orden
1. **Arreglar bug** — nodo Sheets solo lee 1 fila (usar MCP desde Claude para corregir directo en n8n).
2. **Agregar nodo "Marcar Contactado"** de vuelta al workflow v3.
3. **Probar flujo completo** con números reales de 3-4 restaurantes.
4. **Barrido sistemático** para base completa Palermo/Recoleta (sección 8).
5. **Escalar** a más barrios y subrubros.
6. **Camino A** (futuro): WhatsApp Business Cloud API para envío 100% automático.
7. **FUTURO:** incorporar segmentos Hoteles y Eventos.

---

## 16. Notas técnicas clave para Claude
- El MCP de n8n YA ESTÁ CONECTADO — puedo leer y modificar workflows directamente sin que el usuario toque nada.
- El bug del Sheet (1 fila de 3) se resuelve desde el MCP en la próxima sesión.
- El workflow usa Webhook GET, no trigger manual — se activa abriendo la URL en el browser.
- Los emojis en mensajes de WhatsApp causan encoding raro — usar texto plano sin emojis.
- Los números de WhatsApp en el Sheet pueden tener cualquier formato — el código los normaliza con `replace(/[^\d]/g, '')`.

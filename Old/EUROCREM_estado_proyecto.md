# EUROCREM — Estado del Proyecto
**Fecha:** 04/06/2026  
**Objetivo:** Sistema de enriquecimiento y outreach B2B para helados Sergio — restaurantes CABA

---

## ARQUITECTURA GENERAL

```
Batch (Places API)
      ↓
Supabase tabla leads
      ↓
enrich_web.py  →  sitios web propios
enrich_ig.py   →  Instagram (pendiente)
enrich_fb.py   →  Facebook (pendiente)
      ↓
Outreach WhatsApp + Email (pendiente)
```

---

## LO QUE ESTÁ HECHO

### Base de datos — Supabase
- Tabla `leads` operativa
- Tabla `mensajes_wzap` operativa (FK lead_id)
- Columnas activas: `id, place_id, segmento, fit, nombre, direccion, barrio, google_nota, google_resenas, precio, horarios, abierto, telefono, whatsapp, email, sitio_web, instagram, link_ig, facebook, link_wame, notas, origen_contacto, enriquecido, fecha_alta, fecha_actualizacion`
- Columnas eliminadas: `canal_preferido, tipo, reservas, decisor, capacidad_eventos, helado_propio, guias, estado_wa, estado_mail, opt_in, apto_difusion`

### eurocrem_batch.py v2.0
- Trae leads desde Google Places API por grilla barrio × tipo de cocina
- Deduplicación por `place_id`
- Grilla expandida: 9 barrios × 8 tipos = 72 queries → ~200 leads únicos
- Barrios: Palermo, Recoleta, Belgrano, Villa Crespo, Colegiales, San Telmo, Almagro, Núñez, Caballito
- Tipos: italiano, parrilla, trattoria/osteria, autor, bistro, bodegón, eventos, mediterráneo

### eurocrem_enrich_web.py v1.9
Script de enriquecimiento para sitios web propios. Procesa leads con `origen_contacto = pendiente` que tengan `sitio_web` con dominio propio (no IG, no FB).

**Qué extrae:**
- WhatsApp: links `wa.me`, `api.whatsapp.com/send`, JSON de plugins WordPress, URL encoding (`%2B`), entidades HTML (`&lt;`)
- Email: `mailto:`, JSON-LD, texto visible. Gmail NO está en blacklist
- Instagram: todos los candidatos rankeados por similitud con el nombre del restaurante
- Facebook: primer link válido encontrado

**Tecnología:**
- httpx (fetch principal, sin headers custom para compatibilidad con Wix)
- BeautifulSoup + lxml (parsing HTML)
- Playwright (fallback solo cuando faltan AMBOS WhatsApp y email)
- Supabase Python SDK

**Lógica de páginas internas:**
- Detecta hasta 5 páginas relevantes por keywords: contacto, reservas, reservar, delivery, nosotros, menú, carta, locales, etc.
- Visita solo páginas del mismo dominio

**Notas de estado en campo `notas`:**
- `"sin contacto web"` — procesado, no encontró nada
- `"sin whatsapp web"` — encontró email pero no WhatsApp
- `"sin email web"` — encontró WhatsApp pero no email
- `"2do email: xxx"` — email secundario encontrado
- `"web timeout/bloqueado/etc"` — sitio no accesible

**Reporte:** genera `eurocrem_debug_report.txt` al finalizar con leads clasificados

**Propietario de campos:** enrich_web es dueño absoluto de `whatsapp, link_wame, email, notas, instagram, link_ig, facebook, origen_contacto, enriquecido`. Siempre pisa en cada corrida.

**Resultados en 29 leads de prueba:**
- Completos (wzap + email): 5
- Parciales (wzap o email): 7 (incluye Hierro resuelto en v2.0)
- Sin contacto web: 3
- Saltados (IG/FB como sitio web): 10
- Sin sitio web: 4

---

## APRENDIZAJES TÉCNICOS CLAVE

| Problema | Solución aplicada |
|---|---|
| Wix devuelve HTML reducido con headers custom | Sin headers en httpx — Wix da HTML completo al UA default |
| WhatsApp en JSON de plugin WordPress | Regex `"telephone":"NUMERO"` en atributos data-* |
| WhatsApp con URL encoding `%2B541...` | `urllib.parse.unquote()` antes del regex |
| WhatsApp con entidades HTML `&lt;+549...` | `.replace("&#038;","&")` antes del regex |
| WhatsApp con Wix en atributo `data-testid` con entidades | RE_WA_API acepta `+` opcional antes de dígitos |
| Sitios JS-only (2KB de HTML) | Playwright como fallback |
| IG incorrecto (marca hermana) | Score por tokens del nombre vs tokens del handle |
| Gmail en blacklist | Eliminado — muchos restaurantes usan Gmail |

---

## LO QUE FALTA HACER

### PRIORIDAD ALTA

#### 1. Expandir batch a ~200 leads y correr enrich
- Correr `eurocrem_batch.py` v2.0 contra los 9 barrios
- Correr `eurocrem_enrich_web.py` v1.9 contra todos los pendientes
- Analizar `eurocrem_debug_report.txt` para identificar patrones de falla

#### 2. eurocrem_enrich_ig.py (no empezado)

**Qué hace:**
- Procesa leads cuyo `sitio_web` contiene `instagram.com`
- Extrae el handle del `sitio_web` → popula `instagram` y `link_ig`
- Llama a Apify con la lista de handles para obtener la bio completa de cada perfil
- De la bio extrae: email (si está publicado), WhatsApp (número o link wa.me), website real

**Herramienta: Apify — Instagram Profile Scraper**
- Costo: ~$2 por 500 perfiles (casi gratis para el volumen actual)
- No requiere login propio — usa proxies de Apify
- Input: lista de handles. Output: JSON con bio, email, external_url, etc.
- Integración: API REST de Apify desde Python

**Limitaciones conocidas:**
- Email en bio: ~20% de los perfiles lo publican
- WhatsApp en bio: más común en CABA (~30-40%), aparece como texto o link wa.me
- No extrae lo que está en DMs ni en Stories

**Campos que popula:** `instagram, link_ig, email, whatsapp, link_wame, notas`

**Lógica de notas:**
- Si bio tiene email → guardarlo
- Si bio tiene wa.me o número cerca de "whatsapp" → guardar como whatsapp
- Si no encontró nada → `notas = "sin contacto IG"`

---

#### 3. eurocrem_enrich_fb.py (no empezado)

**Qué hace:**
- Procesa leads cuyo `sitio_web` contiene `facebook.com`
- Extrae el path del `sitio_web` → popula campo `facebook`
- Intenta fetchear la página de FB para extraer teléfono, email y WhatsApp si están en "Información"

**Herramienta: httpx directo (sin herramienta paga)**
- FB bloquea scraping agresivo pero permite GET de páginas públicas con UA de browser
- La sección "Información" de una página de FB suele tener teléfono y a veces WhatsApp
- Playwright como fallback si el contenido carga con JS

**Limitaciones conocidas:**
- FB es el canal más bloqueado — tasa de éxito esperada baja (~15-20%)
- Muchos restaurantes tienen FB desactualizado
- Lo que está en FB generalmente también está en el sitio web o en IG

**Campos que popula:** `facebook, email, whatsapp, link_wame, notas`

**Lógica de notas:**
- Si no encontró nada → `notas = "sin contacto FB"`

### PRIORIDAD MEDIA

#### 4. ~~Mejorar detección de sitios con splash page~~ ✓ RESUELTO en v2.0
- Cuando home < 3000 bytes, el script prueba automáticamente `/qr/`, `/inicio/`, `/home/`, `/bienvenida/`
- Hierro: home de 2024 bytes → `/qr/` encontrada con 3551 bytes → WhatsApp `+54 9 11 2486-8061` extraído ✓

#### 5. Sitios con URL acortada (ej: La Baita — acortar.link)
- El script ya resuelve redirects de acortadores conocidos
- Verificar que funciona en producción

#### 6. Linktree como sitio web (ej: Buenos Aires Grill)
- El script fetchea Linktree pero puede no encontrar WhatsApp/email
- Evaluar resultados con 200 leads

#### 7. Mejorar el counter de stats en el resumen final
- Actualmente siempre muestra `sin_datos: N` porque el contador no se actualiza correctamente

### PRIORIDAD BAJA

#### 8. Outreach WhatsApp
- WhatsApp Business Cloud API con número dedicado
- n8n para orquestación
- Templates de mensajes ya escritos en `PLANTILLAS_mensajes_outreach.md`
- El sistema solo envía el primer mensaje — el vendedor maneja la conversación

#### 9. Outreach Email
- Canal simultáneo con WhatsApp (no escalonado)
- Configurar dominio y servidor de envío

#### 10. App web para el hermano
- Interface para ver leads, marcar contactados, registrar respuestas
- Stack: Supabase + (por definir)

---

## ARCHIVOS DEL PROYECTO

| Archivo | Versión | Estado |
|---|---|---|
| `eurocrem_batch.py` | v2.0 | ✓ Operativo |
| `eurocrem_enrich_web.py` | v2.0 | ✓ Operativo |
| `eurocrem_enrich_ig.py` | — | ✗ No empezado |
| `eurocrem_enrich_fb.py` | — | ✗ No empezado |
| `INSTRUCCIONES_PROYECTO_leads_helados.md` | — | Documentación inicial |
| `FLUJO_completo_prospeccion.md` | — | Flujo funcional |
| `PLANTILLAS_mensajes_outreach.md` | — | Templates outreach |

---

## STACK TÉCNICO

| Componente | Tecnología |
|---|---|
| Base de datos | Supabase (PostgreSQL) |
| Scraping web | httpx + BeautifulSoup + Playwright |
| Discovery leads | Google Places API (via Claude places_search) |
| Enriquecimiento IG | Apify (pendiente) |
| Outreach WhatsApp | WhatsApp Business Cloud API (pendiente) |
| Orquestación | n8n en Railway (pendiente para outreach) |
| Backend app web | Por definir |


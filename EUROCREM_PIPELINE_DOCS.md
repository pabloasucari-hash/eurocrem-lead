# EUROCREM — Documentación del Pipeline
**Última actualización:** 09/06/2026

---

## Índice

1. [Arquitectura general](#1-arquitectura-general)
2. [Base de datos — tabla `leads`](#2-base-de-datos--tabla-leads)
3. [Script 1: Batch de captura (`eurocrem_batch_v2.3.py`)](#3-script-1-batch-de-captura)
4. [Script 2: Enrich Leve — primer pase (`eurocrem_enrich_v5.16.py full`)](#4-script-2-enrich-leve)
5. [Script 3: Enrich Fuerte — segundo pase (modos sinwa / sincontacto)](#5-script-3-enrich-fuerte)
6. [Script 4: vdrmota batch (`eurocrem_vdrmota_batch_v1.py`)](#6-script-4-vdrmota-batch)
7. [Flujo completo — cuándo usar qué](#7-flujo-completo)
8. [Actores Apify en uso](#8-actores-apify-en-uso)
9. [Reglas de protección de datos](#9-reglas-de-protección-de-datos)
10. [Estado actual y próximos pasos](#10-estado-actual-y-próximos-pasos)

---

## 1. Arquitectura general

```
[Google Places API]
       ↓
[eurocrem_batch_v2.3.py]  ←  Captura restaurantes por barrio + tipo
       ↓
  Supabase leads (enriquecido=False, origen='pendiente')
       ↓
[eurocrem_enrich_v5.16.py full]  ← ENRICH LEVE (primer pase Apify)
       ↓
  Supabase leads (enriquecido=True, origen=ig-auto/fb-auto/web-auto)
       ↓
[eurocrem_enrich_v5.16.py sinwa/sincontacto]  ← ENRICH FUERTE (segundo pase)
       ↓
[eurocrem_vdrmota_batch_v1.py]  ← ENRICH WEB (sitios propios, manual)
       ↓
  Leads con whatsapp / email completos
       ↓
[index.html — App interactiva]  ← Visualización y edición manual
```

---

## 2. Base de datos — tabla `leads`

### Campos clave

| Campo | Tipo | Descripción |
|---|---|---|
| `id` | uuid | PK |
| `nombre` | text | Nombre del restaurante |
| `barrio` | text | Barrio en Buenos Aires |
| `direccion` | text | Dirección completa |
| `whatsapp` | text | Número normalizado (+549...) |
| `email` | text | Email principal |
| `email_2` | text | Email secundario |
| `instagram` | text | Handle IG (@...) |
| `facebook` | text | URL Facebook |
| `link_wame` | text | Link wa.me completo |
| `sitio_web` | text | URL del sitio web |
| `telefono` | text | Teléfono sin validar |
| `origen_contacto` | text | Cómo se obtuvo el contacto (ver tabla abajo) |
| `enriquecido` | bool | True = ya pasó por Apify |
| `lat` / `lng` | float | Coordenadas geográficas |
| `google_nota` | float | Rating Google |
| `google_resenas` | int | N° reseñas |
| `photo_ref` | text | Referencia foto Google Places |
| `notas` | text | Notas internas + alternativos encontrados |

### Valores de `origen_contacto`

| Valor | Significado |
|---|---|
| `pendiente` | Recién capturado por batch, no enriquecido |
| `ig-auto` | WA/email encontrado vía scraping de Instagram |
| `fb-auto` | WA/email encontrado vía scraping de Facebook |
| `web-auto` | WA/email encontrado vía scraping del sitio web |
| `telefono-auto` | Solo teléfono encontrado (sin WA ni email) |
| `manual` | Cargado a mano (NUNCA se pisa) |
| `IG-manual` | Lead cargado manualmente desde IG (NUNCA se pisa) |
| `FB-manual` | Lead cargado manualmente desde FB (NUNCA se pisa) |

---

## 3. Script 1: Batch de captura

**Archivo:** `eurocrem_batch_v2.3.py`
**Cuándo usarlo:** Para agregar nuevos restaurantes a la base.

### Qué hace

1. Recorre una grilla de **barrios × tipos de cocina** (14 barrios × 8 tipos = 112 combinaciones).
2. Llama a **Google Places API** (Text Search) con cada combinación.
3. Por cada resultado:
   - Si el `place_id` ya existe en Supabase → actualiza solo campos de Maps (nota, reseñas, precio, horarios, lat/lng, photo_ref). **No toca WA ni email.**
   - Si es nuevo → inserta fila completa con `enriquecido=False`, `origen_contacto='pendiente'`.
4. Descarta resultados de tipos no deseados (bar, café, fast food, etc.).

### Cómo correr

```bash
python eurocrem_batch_v2.3.py
```

Sin parámetros — corre la grilla completa. Puede tardar 20–30 min por el throttling de Places API.

### Resultado esperado

- Nuevos leads en Supabase con campos de Google Maps completos.
- Campos de contacto vacíos (`whatsapp=null`, `email=null`).
- `enriquecido=False`, `origen_contacto='pendiente'`.

### Parámetros de configuración (en el script)

| Variable | Valor actual | Descripción |
|---|---|---|
| `max_results` | 60 | Máx. resultados por query (3 páginas Places API) |
| `BARRIOS` | 14 barrios | Palermo, Recoleta, Belgrano, etc. |
| `TIPOS_COCINA` | 8 tipos | Italiano, parrilla, bodegón, etc. |

---

## 4. Script 2: Enrich Leve

**Archivo:** `eurocrem_enrich_v5.16.py`
**Modo:** `full` (default)
**Cuándo usarlo:** Después de correr el batch, para procesar los nuevos leads.

### Filtro de entrada

Procesa únicamente leads que cumplan **todos** estos criterios:
- `enriquecido = False` (nunca pasaron por Apify)
- `whatsapp IS NULL` **O** `email IS NULL` (les falta algo)
- `origen_contacto NOT IN ('manual', 'IG-manual', 'FB-manual')` (no están protegidos)

### Qué hace — flujo detallado

#### Paso 1: Clasificación de leads

```
leads filtrados
    ├── leads con sitio_web = instagram.com  → batch IG
    ├── leads con sitio_web = facebook.com   → batch FB
    └── leads con otro sitio_web / sin web   → procesamiento web individual
```

#### Paso 2a: Batch Instagram (todos juntos en 1 llamada Apify)

Actor: `apify/instagram-profile-scraper`

Por cada perfil devuelto:
- Extrae bio, external_url, links en bio
- Busca wa.me en bio → guarda como `whatsapp`
- Busca email en bio
- Si `external_url` es un sitio scrappeable (no reservas, no CDN) → lo scrapea en busca de WA/email
- Si `external_url` es Linktree/bio.link → llama `parsear_linktree()` para seguir los links
- Si `external_url` es cualquier otra URL → [**v5.16**] igualmente intenta buscar wa.me genérico
- Guarda `origen_contacto = 'ig-auto'`

#### Paso 2b: Batch Facebook (todos juntos en 1 llamada Apify)

Actor: `apify/facebook-pages-scraper`

Por cada página devuelta:
- [**v5.16 FIX**] Lee directamente `item["phone"]` y `item["email"]` (campos directos del actor)
- Si `phone` tiene código de país (+) → lo intenta normalizar como WA argentino
- Si `email` directo válido → lo guarda con prioridad sobre lo scrapeado en HTML
- También scrapea el HTML de la página FB como fallback
- Guarda `origen_contacto = 'fb-auto'`

#### Paso 2c: Procesamiento web individual (paralelo, 4 workers)

Para cada lead con sitio web propio o sin web:

```
¿Tiene sitio_web scrappeable?
    ├── SÍ → fetch_html() → buscar WA/email en HTML
    │          ├── Encontró algo → guardar
    │          └── No encontró → probar subpáginas (/contacto, /reservas, etc.)
    │
    └── NO / no scrapeable → solo tiene teléfono de Google Places
               └── normalizar_whatsapp(telefono) → si es argentino, guardar como WA
```

#### Paso 3: Guardar resultados

`_guardar_resultado()` — reglas de escritura:
- **NUNCA pisa** campos que ya tienen valor (WA, email, IG, FB, sitio_web)
- Si encuentra un valor alternativo al que ya hay → lo guarda en `notas` ("WA alt: ...")
- Actualiza `enriquecido = True` y `fecha_actualizacion`
- Actualiza `origen_contacto` según qué actor encontró los datos

### Cómo correr

```bash
# Procesar todos los leads pendientes (modo por defecto):
python eurocrem_enrich_v5.16.py full

# Procesar IDs específicos:
python eurocrem_enrich_v5.16.py ids abc123,def456

# Solo generar reporte sin procesar:
python eurocrem_enrich_v5.16.py reporte
```

### Resultado esperado

- Leads procesados con `enriquecido=True`.
- Campos de contacto completados donde fue posible.
- Los que no encontraron nada quedan con `enriquecido=True` pero `whatsapp=null`, `email=null`.

---

## 5. Script 3: Enrich Fuerte

**Archivo:** `eurocrem_enrich_v5.16.py`
**Modos:** `sinwa` y/o `sincontacto`
**Cuándo usarlo:** Después del Enrich Leve, para reintentar en leads que quedaron sin datos.

### Diferencia con Enrich Leve

| | Enrich Leve (`full`) | Enrich Fuerte (`sinwa`/`sincontacto`) |
|---|---|---|
| **Filtro** | `enriquecido=False` | Leads YA procesados que todavía no tienen WA/email |
| **Cuándo** | Primera vez sobre leads nuevos | Segunda pasada sobre leads que no dieron resultado |
| **Actors** | IG + FB + web scraping | IG + FB + web scraping + **GMaps como último recurso** |
| **Uso típico** | Semanal, tras batch | Mensual, revisión de leads difíciles |

### Modo `sinwa` — leads sin WhatsApp

Procesa leads que **ya fueron procesados** (`origen_contacto != 'pendiente'`) y siguen sin WA.

```bash
# Llamada interna — no disponible como CLI directo en v5.16
# Ejecutar desde Python:
from eurocrem_enrich_v5 import run_sin_wa
run_sin_wa()
```

Aplica el mismo flujo de IG/FB/web que el Enrich Leve pero sin el filtro de `enriquecido=False`.

### Modo `sincontacto` — leads sin absolutamente nada

Procesa leads que tienen `enriquecido=True` y **ni WA ni email** — los más difíciles.
También procesa leads `pendiente` que no tienen web/IG/FB (stuck sin fuente de datos).

Último recurso: usa `compass/crawler-google-places` para scrapear la ficha de Google Maps del lugar. A veces Google Maps tiene un teléfono o web que no estaba en los datos originales.

```bash
# Llamada interna:
from eurocrem_enrich_v5 import run_sin_contacto
run_sin_contacto()
```

### Modo `todos` — fuerza re-procesamiento completo

Re-corre el enrich en absolutamente todos los leads, incluyendo los que ya tienen WA y email. Útil para actualizar datos cuando hay una mejora grande en el pipeline (ej: al pasar de v5.15 a v5.16).

> ⚠️ **Nota:** `_guardar_resultado()` nunca pisa campos existentes, así que correr `todos` sobre un lead completo no borra nada.

```bash
# Llamada interna:
from eurocrem_enrich_v5 import run_todos
run_todos()
```

---

## 6. Script 4: vdrmota batch

**Archivo:** `eurocrem_vdrmota_batch_v1.py`
**Cuándo usarlo:** Complemento puntual para leads con sitio web propio que les falta email o WA.

### Qué hace — en detalle

1. Consulta Supabase: leads con `sitio_web` que sea un dominio propio (excluye IG, FB, Linktree, beacons.ai, atom.bio).
2. Filtra los que no tienen `whatsapp` **O** no tienen `email`.
3. Por cada URL, llama al actor `vdrmota/contact-info-scraper` via Apify API.
4. El actor crawlea el sitio (hasta depth 2, máx. 15 páginas) buscando emails, teléfonos y links wa.me.
5. Parsea los resultados y actualiza Supabase.

### Qué campos toca (y qué NO toca)

**SÍ puede escribir:**
- `whatsapp` — solo si estaba vacío
- `email` — solo si estaba vacío
- `telefono` — solo si estaba vacío

**NUNCA toca:**
- `nombre`, `barrio`, `direccion`, `instagram`, `facebook`, `sitio_web`, `notas`, `origen_contacto`, `enriquecido`, `lat`, `lng`, `google_nota`, `precio`, `fit`, ni ningún otro campo

Es un script **conservador**: solo completa, nunca sobreescribe.

### Limitaciones conocidas

- **No sirve para páginas JS-rendered** (beacons.ai, atom.bio, taplink): ya las excluye por dominio.
- **Linktree** está excluido porque el actor crawlea todo el dominio linktr.ee y mezcla resultados de otros usuarios.
- **No encuentra WA** en la mayoría de sitios web — los restaurantes ponen el WA en sus redes sociales, no en el sitio. El valor real de este script está en encontrar **emails** nuevos.
- Costo Apify: ~$0.002 por página scrapeada (sin browser), ~$0.01 con browser.

### Cómo correr

```bash
# Primero exportar el token de Apify:
export APIFY_API_TOKEN=apify_api_xxxxxxxxxxxx

# Ver qué procesaría sin gastar créditos:
python eurocrem_vdrmota_batch_v1.py --dry-run

# Correr en todos los candidatos:
python eurocrem_vdrmota_batch_v1.py

# Limitar a 20 leads (para probar):
python eurocrem_vdrmota_batch_v1.py --limit 20

# Usar browser mode (más lento y caro, para sitios que requieren JS):
python eurocrem_vdrmota_batch_v1.py --browser
```

---

## 7. Flujo completo

### Cuándo usar cada script

```
┌─────────────────────────────────────────────────────────────────┐
│  ¿Quiero agregar restaurantes nuevos a la base?                 │
│  → eurocrem_batch_v2.3.py                                       │
│     Resultado: leads nuevos con enriquecido=False               │
└─────────────────────────────────────────────────────────────────┘
           ↓
┌─────────────────────────────────────────────────────────────────┐
│  ¿Quiero procesar todos los leads pendientes por primera vez?   │
│  → python eurocrem_enrich_v5.16.py full   (ENRICH LEVE)        │
│     Toca: leads con enriquecido=False                           │
│     Resultado: enriquecido=True, origen actualizado             │
└─────────────────────────────────────────────────────────────────┘
           ↓
┌─────────────────────────────────────────────────────────────────┐
│  ¿Quedaron leads sin WA después del full?                       │
│  → run_sin_wa()   (ENRICH FUERTE - parcial)                    │
│     Toca: leads ya procesados que siguen sin WA                 │
└─────────────────────────────────────────────────────────────────┘
           ↓
┌─────────────────────────────────────────────────────────────────┐
│  ¿Hay leads con enriquecido=True pero sin ningún contacto?      │
│  → run_sin_contacto()   (ENRICH FUERTE - total)                │
│     Toca: leads enriquecidos sin WA NI email                    │
│     Último recurso: GMaps scraper                               │
└─────────────────────────────────────────────────────────────────┘
           ↓
┌─────────────────────────────────────────────────────────────────┐
│  ¿Leads con sitio web propio todavía sin email o WA?            │
│  → eurocrem_vdrmota_batch_v1.py   (ENRICH WEB)                 │
│     Toca: solo whatsapp, email, telefono si estaban vacíos      │
└─────────────────────────────────────────────────────────────────┘
           ↓
┌─────────────────────────────────────────────────────────────────┐
│  Completar manualmente desde index.html                         │
│  (Los leads restantes son los más difíciles de automatizar)     │
└─────────────────────────────────────────────────────────────────┘
```

### Frecuencia sugerida

| Script | Frecuencia | Duración aprox. |
|---|---|---|
| `eurocrem_batch_v2.3.py` | Cada 2–4 semanas | 20–30 min |
| `enrich full` | Tras cada batch | 30–60 min |
| `enrich sinwa` | Mensual | 15–30 min |
| `enrich sincontacto` | Mensual | 10–20 min |
| `vdrmota batch` | A demanda | Variable |

---

## 8. Actores Apify en uso

| Actor | Para qué se usa | Costo aprox. |
|---|---|---|
| `apify/instagram-profile-scraper` | Scraping de perfiles IG (bio, links) | ~$0.001/perfil |
| `apify/facebook-pages-scraper` | Scraping de páginas FB (phone, email directos) | ~$0.001/página |
| `compass/crawler-google-places` | Scraping de fichas GMaps (último recurso) | Variable |
| `vdrmota/contact-info-scraper` | Crawleo de sitios web propios (email/WA) | ~$0.002/página |

### Actor descartados / en evaluación

| Actor | Estado | Razón |
|---|---|---|
| `delicious_zebu/contact-info-scraper` | Descartado para Linktree | Crawlea todo el dominio, mezcla usuarios |
| `apify/rag-web-browser` | Desactivado | Costo alto, ROI incierto |

---

## 9. Reglas de protección de datos

Estas reglas aplican en todos los scripts:

1. **`origen_contacto IN ('manual', 'IG-manual', 'FB-manual')`** → el lead nunca se toca en enrich automático.

2. **`_guardar_resultado()` nunca pisa campos existentes** — si un lead ya tiene `whatsapp`, ningún script lo sobreescribe. Si encuentra un WA alternativo, lo guarda en `notas`.

3. **vdrmota batch** solo escribe en campos que son `null` — no toca campos con valor.

4. **Batch de captura** solo actualiza campos de Google Maps en leads existentes (nota, reseñas, lat/lng) — nunca toca los de contacto.

---

## 10. Estado actual y próximos pasos

### Estado actual (09/06/2026)

| Métrica | Valor |
|---|---|
| Total leads | ~657 |
| Con WhatsApp | ~452 (69%) |
| Con Email | ~330+ (50%+) |
| Sin ningún contacto | ~104 (16%) |
| Sin WhatsApp | 205 |
| Pendientes (no enriquecidos) | ~50 |

### Próximos pasos sugeridos

#### Corto plazo

- [ ] **Correr Enrich Leve** sobre los ~50 leads `pendiente` que quedaron sin procesar.
- [ ] **Deploy del fix de UI** — copiar `index-efa8f914.html` como `index.html` y correr `deploy.bat` para que el topnav se vea bien en todos los browsers.
- [ ] **Probar vdrmota batch** con `--dry-run` para ver cuántos leads candidatos hay, luego correr con `--limit 30` para validar calidad de resultados.

#### Mediano plazo

- [ ] **Páginas JS-rendered** (beacons.ai, atom.bio, taplink): requieren un actor Apify con browser completo. Evaluar `browserless/chrome` o `apify/web-scraper` para estos ~5–10 leads específicos.
- [ ] **Enrich Fuerte** mensual — correr `run_sin_wa()` y `run_sin_contacto()` para reintentar los leads más difíciles.
- [ ] **Expandir grilla batch** — agregar barrios: Flores, Villa del Parque, Devoto, Paternal.
- [ ] **Automatizar con scheduler** — `eurocrem_enrich_v5.16.py` ya tiene la estructura para correr programado. Evaluar Railway o GitHub Actions para ejecución semanal.

#### Largo plazo

- [ ] **Campaña de contacto** — con los leads que tienen WA, armar flujo de outreach desde la app.
- [ ] **Métricas de conversión** — agregar campo `estado_contacto` en la tabla para trackear (contactado / interesado / cliente).
- [ ] **Ampliar cobertura geográfica** — CABA casi cubierta, evaluar GBA (San Isidro, Vicente López, Tigre).

# EUROCREM — Nueva Estrategia de Enriquecimiento de Leads

## Problema Original
Google Custom Search JSON API no acepta clientes nuevos (según documentación oficial 2026-2027). Nuestro enfoque inicial de usar solo Google Search falló por esta restricción.

## Solución: Pipeline de 3 Pasos

### Paso 1: Google Places API ✅ (YA FUNCIONA)
**Herramienta:** Claude `places_search` tool  
**Output:**
- Nombre
- Dirección
- Teléfono
- Sitio web
- Rating
- Categoría
- place_id (para deduplicación)

**Status:** Funcionando correctamente — 29 leads ya capturados

---

### Paso 2: Serper.dev (SÍ FUNCIONA - API de Google Legal)
**Herramienta:** Serper API (2.500 búsquedas gratis/mes)  
**Búsquedas:**
```
"[Nombre Restaurante]" Instagram Buenos Aires
"[Nombre Restaurante]" WhatsApp contacto
"[Nombre Restaurante]" email reservas
"[Nombre Restaurante]" Facebook
```

**Output esperado:**
- URLs a Instagram
- URLs a Facebook  
- Números de WhatsApp
- Emails de contacto

---

### Paso 3: Crawler Propio (Opcional pero recomendado)

**¿Qué es un Crawler Propio?**
Un bot automatizado que visita sitios web, extrae datos y los organiza. Es como si alguien visitara manualmente cada restaurante, Instagram y Facebook — pero lo hace un programa en segundos.

**Ejemplo:**
```
1. Visita www.ilmatterello.com.ar
   → Busca patrones: "email@", "whatsapp", teléfono
   → Extrae: contact@ilmatterello.com.ar, +5491234567
   
2. Visita instagram.com/ilmatterello_palermo
   → Lee la bio
   → Extrae: email de contacto, WhatsApp, link de reservas
   
3. Visita facebook.com/ilmatterello
   → Busca info de contacto
   → Extrae: teléfono, email, horarios
```

**Herramienta:** BeautifulSoup + requests (Python)  
**Costo:** 💰 GRATIS — son librerías open source

**¿Por qué es gratis?**
BeautifulSoup y requests son librerías de Python open source. No cobran nada. Solo necesitás:
- Python (gratis)
- BeautifulSoup (gratis)
- requests (gratis)
- Tu servidor/máquina para ejecutarlo (lo que ya tenés)

**Targets:**
- Sitio web (extraer email, WhatsApp, formulario)
- Perfil Instagram (extraer email de bio, contactos)
- Facebook (extraer info de contacto)

**Output esperado:**
- Email directo
- WhatsApp directo
- Enlaces a redes sociales
- Información de reservas
- Horarios de atención

---

## Recuperación Esperada (Según IA Consultora)
| Campo | Tasa de Recuperación |
|-------|----------------------|
| WhatsApp | 60-80% |
| Instagram | 80-95% |
| Email | 50-70% |
| Facebook | 70-85% |

---

## Cambios en el Script

### Antes (Roto)
```
eurocrem_batch.py → Places API → Supabase
eurocrem_enrich.py → Google Custom Search (403 bloqueado) + sitio web + Linktree
```

### Después (Funcional)
```
eurocrem_batch.py → Places API → Supabase ✅
eurocrem_enrich_v3.py → 
  - Paso 1: Sitio web + Linktree (actual) ✅
  - Paso 2: Serper API → Buscar en Google legalmente ✅
  - Paso 3: Crawler opcional para sitios web encontrados (futuro)
```

---

## Implementación Inmediata

### 1. Registrar en Serper.dev
- URL: [serper.dev](https://serper.dev)
- Login con Google
- Obtener API key (gratis, 2.500/mes)

### 2. Actualizar `eurocrem_enrich.py`
- Remover función `google_search()` que usa Google Custom Search
- Agregar función `serper_search()` que usa Serper API
- Mantener todo lo demás igual (sitio web, Linktree, etc.)

### 3. Variables de entorno
```
SERPER_API_KEY=<tu_key_de_serper>
GOOGLE_API_KEY=<ya_existe>
SUPABASE_URL=<ya_existe>
SUPABASE_KEY=<ya_existe>
```

---

## Preguntas Pendientes
- ¿Implementamos Paso 3 (Crawler) ahora o después?
- ¿Prio: completar Paso 2 primero con 29 leads?

---

## Ranking de Soluciones (según IA consultora)
1. ⭐⭐⭐⭐⭐⭐ Google Places + Serper (nuestra arquitectura)
2. ⭐⭐⭐⭐⭐ Serper.dev solo
3. ⭐⭐⭐⭐ SerpAPI
4. ⭐⭐⭐ Bing Search API
5. ⭐ Scraping Google directo
6. ⭐⭐ DuckDuckGo scraping

---

**Decisión:** Implementar Paso 2 (Serper) YA. Paso 3 (Crawler) como mejora futura.

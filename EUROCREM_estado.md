# EUROCREM — Estado del proyecto
**Fecha:** 07/06/2026

---

## Scripts

| Archivo | Versión | Estado |
|---------|---------|--------|
| `eurocrem_batch_v2.2.py` | v2.2 | ✅ Activo — enrich leve + fixes WA |
| `eurocrem_enrich_v5.7.py` | v5.7 | ✅ Activo — enrich fuerte (Apify) |

---

## Base de datos (Supabase)

- **Proyecto:** Eurocrem — `crbmgsmmvfkbxrplqfkl.supabase.co`
- **Tabla:** `leads`
- **Total leads:** ~507
- **Barrios:** 14 (Palermo, Recoleta, Belgrano, Villa Crespo, Colegiales, San Telmo, Almagro, Núñez, Caballito, Chacarita, Las Cañitas, Villa Urquiza, Boedo, Puerto Madero)

---

## Estrategia de enriquecimiento

Correr siempre leve primero. Avanzar al siguiente paso solo si falta WA **o** email.
Un lead se considera completo solo cuando tiene **ambos** (WA + email).

| Paso | Script | Costo | Técnica | Condición para ejecutar | Qué busca |
|------|--------|-------|---------|------------------------|-----------|
| 1 | `batch_v2.2` fase 2 | Gratis | requests + BeautifulSoup | Tiene `sitio_web` | wa.me, api.whatsapp.com, mailto, handle IG |
| 1b | `batch_v2.2` fase 2 | Gratis | Linktree `__NEXT_DATA__` JSON | `sitio_web` es Linktree o linkea a uno | Sub-links reales del Linktree → WA/email en cada página |
| 2 | `enrich_v5.7` IT1 | Gratis | Playwright + subpáginas | Sin WA **o** sin email + tiene `sitio_web` | Renderiza JS, visita /contacto /menu /reservas /qr → wa.me, mailto |
| 3 | `enrich_v5.7` IT2 | $ Apify | instagram-profile-scraper | Sin WA **o** sin email + tiene `instagram` | Bio de IG → wa.me, email, links externos |
| 4 | `enrich_v5.7` IT3 | $ Apify | facebook-pages-scraper | Sin WA **o** sin email + tiene `facebook` | Página de FB → WhatsApp, email, sitio web |
| 5 | `enrich_v5.7` | $$ Apify | GMaps scraper | Sin WA **o** sin email (todos tienen `place_id`) | Renderiza Google Maps → teléfono, WA, IG, sitio |
| 6 | `enrich_v5.7` | $$$ Apify | RAG browser (Google search) | Sin WA **o** sin email + sin sitio/IG/FB | Busca "{nombre} {barrio} whatsapp contacto" en Google |

### Reglas de protección
- Nunca pisa campos con `origen_contacto IN ('IG-manual', 'FB-manual')`
- Nunca sobreescribe un campo que ya tiene valor con null
- `enriquecido = True` solo se setea cuando pasó por el enrich fuerte (Apify)

### Validación de números WA
- Solo acepta formato argentino: `+549XXXXXXXXXX` (13 dígitos) o `+5411XXXXXXXX` (12 dígitos, inserta 9)
- Descarta cualquier número con código de país distinto a +54
- Formatea como: `+54 9 XX XXXX-XXXX`

---

## Enrich leve (eurocrem_batch_v2.2.py)

**Cómo correr:**
```
python eurocrem_batch_v2.2.py
```
Fase 1 (captura) está comentada — solo corre fase 2 (enrich).

---

## Enrich fuerte (eurocrem_enrich_v5.7.py)

Usa Apify — cuesta dinero. Correr selectivamente sobre leads que el leve no resolvió.

**Modos:**
```
python eurocrem_enrich_v5.7.py full          # todos los pendientes
python eurocrem_enrich_v5.7.py sinwa         # leads sin whatsapp
python eurocrem_enrich_v5.7.py ids UUID1,UUID2  # leads específicos
```

---

## Web UI

- **Archivo:** `index.html` (basado en `eurocrem_leads.html`)
- **GitHub Pages:** https://pabloasucari-hash.github.io/eurocrem-lead/
- **Features:** lista filtrable, formulario editable, links ↗ para sitio/IG/FB, dashboard por barrio

---

## Decisiones pendientes

- [ ] **Deshabilitar RAG browser (paso 6) en primera corrida del fuerte:**
  - 123 leads sin sitio/IG/FB llegarían al RAG browser
  - Costo estimado RAG: $1.50-6.00 (60-80% del costo total)
  - Efectividad conocida: 0/2 leads encontraron algo en el debug anterior
  - **Acción:** comentar el bloque RAG en `eurocrem_enrich_v5.7.py` antes de correr
  - Revisar manualmente solo los leads prioritarios (alto fit, buena nota Google) si se quiere usar RAG

- [ ] **Costo estimado del fuerte SIN RAG:** ~$1.00-1.50 total (82 leads IG + 1 FB + GMaps)
- [ ] **Evaluar paso 6 (RAG browser):** del debug anterior, los 2 leads que llegaron al RAG browser (Don Hilario, Don Benito) no obtuvieron resultado. Costo alto, eficiencia baja para leads sin presencia web. Considerar deshabilitarlo o usarlo solo manualmente para leads muy prioritarios.

---

## Pendientes

- [ ] Correr enrich leve v2.2 sobre los 13 WA nulleados (corriendo ahora)
- [ ] Analizar cobertura post-enrich leve y decidir qué leads van al enrich fuerte
- [ ] Enrich fuerte selectivo sobre leads con sitio web pero sin WA/email

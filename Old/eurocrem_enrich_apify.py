"""
EUROCREM — eurocrem_enrich_apify.py
Versión: 1.0 — 05/06/2026

Enriquecimiento desde sitio web propio via Apify.
Actor: peterasorensen/snacci — Deep Email, Phone & Social Media Scraper

Ventajas vs Firecrawl:
  - Maneja Wix, Google Sites, JS-heavy sin configuración
  - Crawl inteligente priorizando páginas de contacto
  - Ya resuelve emails falsos de Wix y .png como páginas
  - Detecta WhatsApp, email, IG, FB en una sola llamada
  - Pay-per-result: ~$9 por 1000 emails encontrados

Flujo:
  1. Lee leads pendientes con sitio web propio desde Supabase
  2. Por cada lead: llama al actor de Apify con la URL
  3. Parsea el resultado: extrae WA, email, IG, FB
  4. Guarda en Supabase

Solo procesa leads con origen_contacto = 'pendiente' y sitio_web
con dominio propio (excluye instagram.com y facebook.com).
"""

import os
import re
import time
import logging
from datetime import datetime, timezone

from apify_client import ApifyClient
from supabase import create_client, Client

# ============================================================
# CONFIGURACIÓN
# ============================================================

SUPABASE_URL  = os.getenv("SUPABASE_URL",  "https://crbmgsmmvfkbxrplqfkl.supabase.co")
SUPABASE_KEY  = os.getenv("SUPABASE_KEY",  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNyYm1nc21tdmZrYnhycGxxZmtsIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MDUxNjQ0NywiZXhwIjoyMDk2MDkyNDQ3fQ.7D5od9xbpGP3XbdjrTpgCtL7xWYpeJ6xaa5cBsMsju8")
APIFY_TOKEN   = os.getenv("APIFY_TOKEN",   "apify_api_zWjFWPdJLUef0mCOyMxcYiBN5zgK3o3JDEtU")

# Actor ID del scraper de contactos
ACTOR_ID = "peterasorensen/snacci"

# Páginas a crawlear por sitio (más = más créditos pero mejor cobertura)
# 5 es suficiente para home + contacto + reservas
MAX_LINKS_PER_URL = 5

# Delay entre leads para no saturar la API
DELAY_ENTRE_LEADS = 2

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# ============================================================
# CLIENTES
# ============================================================

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
apify    = ApifyClient(APIFY_TOKEN)

# ============================================================
# DOMINIOS A SALTEAR
# ============================================================

SKIP_DOMINIOS = [
    "instagram.com", "facebook.com",
    "meitre.com", "woki.com", "opentable.com", "thefork.com",
]

# ============================================================
# NORMALIZADORES
# ============================================================

def normalizar_whatsapp(numero: str) -> str | None:
    """Normaliza número a formato +54 9 XX XXXX-XXXX."""
    if not numero:
        return None
    digits = re.sub(r"[^\d]", "", numero)
    if digits.startswith("54"):
        digits = digits[2:]
    if digits.startswith("0"):
        digits = digits[1:]
    if digits.startswith("9") and len(digits) == 11:
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return f"+54 9 {digits[:2]} {digits[2:6]}-{digits[6:]}"


def construir_wame(numero: str) -> str | None:
    if not numero:
        return None
    digits = re.sub(r"[^\d]", "", numero)
    if not digits.startswith("54"):
        digits = "54" + digits
    return f"https://wa.me/{digits}"


EMAIL_BLACKLIST = {
    "example.com", "test.com", "sentry.io", "wixpress.com",
    "sentry-next.wixpress.com", "sentry.wixpress.com",
    "squarespace.com", "shopify.com", "wordpress.com",
    "clarin.com", "lanacion.com.ar", "infobae.com",
    "rappi.com", "pedidosya.com", "ifood.com", "ubereats.com",
    "tripadvisor.com", "yelp.com", "guiaoleo.com",
    "meitre.com", "woki.com", "opentable.com", "thefork.com",
    "instagram.com", "facebook.com", "twitter.com", "tiktok.com",
    "google.com", "googlemail.com",
}


def validar_email(email: str) -> bool:
    if not email or len(email) < 6:
        return False
    email = email.lower().strip()
    dominio = email.split("@")[-1]
    if dominio in EMAIL_BLACKLIST:
        return False
    if not re.match(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$", email):
        return False
    if re.search(r"\.(png|jpg|gif|svg|webp|ico|css|js)$", email, re.I):
        return False
    return True

# ============================================================
# PARSEO DEL RESULTADO DE APIFY
# ============================================================

def parsear_resultado_apify(items: list, nombre: str) -> dict:
    """
    El actor devuelve lista flat de items con formato:
      {'type': 'email', 'value': 'xxx@yyy.com', 'sourceUrl': '...'}
      {'type': 'phone', 'value': '+5491125011888', 'sourceUrl': '...'}
      {'type': 'socialMedia', 'platform': 'instagram', 'value': 'handle', 'url': '...'}
      {'type': 'socialMedia', 'platform': 'whatsapp', 'value': 'url_o_numero', 'url': '...'}
    """
    todos_emails  = []
    todos_phones  = []
    wa_encontrado = None
    ig_encontrado = None
    fb_encontrado = None

    invalidos_ig = {"p", "reel", "reels", "stories", "explore", "instagram", "accounts", "hashtag"}
    paths_inv_fb = {"sharer", "share", "dialog", "login", "watch", "groups", "events", "marketplace"}

    stopwords = {
        "el", "la", "los", "las", "de", "del", "al", "y", "e",
        "parrilla", "restaurante", "bar", "cafe", "bistro", "grill",
        "resto", "casa", "cocina", "cucina", "osteria", "trattoria",
    }
    tokens_nombre = {
        t for t in re.findall(r"[a-z]+", nombre.lower())
        if t not in stopwords and len(t) > 2
    } if nombre else set()

    candidatos_ig = {}  # handle -> url

    for item in items:
        tipo  = item.get("type", "")
        valor = item.get("value", "") or ""

        if tipo == "email":
            e = valor.lower().strip()
            if validar_email(e) and e not in todos_emails:
                todos_emails.append(e)

        elif tipo == "phone":
            n = normalizar_whatsapp(valor)
            if n and n not in todos_phones:
                todos_phones.append(n)

        elif tipo == "socialMedia":
            platform = (item.get("platform") or "").lower()
            url      = item.get("url") or ""

            if platform == "whatsapp" and not wa_encontrado:
                # value puede ser URL wa.me o número directo
                m = re.search(r"wa\.me/[^0-9+]*\+?(\d{7,15})", valor, re.I)
                if m:
                    n = normalizar_whatsapp(m.group(1))
                    if n:
                        wa_encontrado = n
                else:
                    n = normalizar_whatsapp(valor)
                    if n:
                        wa_encontrado = n

            elif platform == "instagram":
                handle = valor.lstrip("@").lower()
                if handle and handle not in invalidos_ig and handle not in candidatos_ig:
                    url_ig = url or f"https://www.instagram.com/{handle}/"
                    candidatos_ig[handle] = (valor.lstrip("@"), url_ig)

            elif platform == "facebook" and not fb_encontrado:
                if url and "facebook.com" in url:
                    path = url.rstrip("/").split("facebook.com/")[-1].split("?")[0]
                    if path and path.lower() not in paths_inv_fb:
                        fb_encontrado = f"https://www.facebook.com/{path}"
                elif valor and not valor.startswith("http"):
                    fb_encontrado = f"https://www.facebook.com/{valor}"

    # Si no encontró WA en socialMedia, usar phones
    if not wa_encontrado and todos_phones:
        wa_encontrado = todos_phones[0]

    # Elegir mejor IG por similitud con nombre del restaurante
    if candidatos_ig:
        def score_ig(handle: str) -> float:
            handle_tokens = set(re.findall(r"[a-z]+", handle))
            aciertos  = sum(1 for t in tokens_nombre if t in handle)
            penalidad = sum(1 for t in handle_tokens if t not in tokens_nombre and t not in stopwords and len(t) > 2)
            return aciertos - (penalidad * 0.5)
        mejor = max(candidatos_ig.keys(), key=score_ig)
        handle_raw, url_ig = candidatos_ig[mejor]
        ig_encontrado = (f"@{handle_raw}", url_ig)

    # Emails de negocio primero
    keywords = ["reserva", "contacto", "info", "eventos", "ventas", "hola", "admin"]
    todos_emails.sort(key=lambda e: next((i for i, k in enumerate(keywords) if k in e), 99))

    return {
        "whatsapp":  wa_encontrado,
        "emails":    todos_emails,
        "instagram": ig_encontrado,
        "facebook":  fb_encontrado,
    }

# ============================================================
# SCRAPE VIA APIFY
# ============================================================

def apify_scrape(url: str, nombre: str) -> dict:
    """
    Llama al actor de Apify para una URL y devuelve los contactos encontrados.
    Retorna dict con: whatsapp, emails, instagram, facebook.
    """
    try:
        run_input = {
            "websites": [url],
            "maxLinksPerStartUrl": MAX_LINKS_PER_URL,
            # Sin proxy — sitios .com.ar bloquean proxies residenciales
            # Apify usa su IP directa que suele funcionar mejor para sitios locales
        }
        log.info(f"  → Apify: {url} (max {MAX_LINKS_PER_URL} páginas)")

        run = apify.actor(ACTOR_ID).call(run_input=run_input)

        if not run:
            log.warning(f"  ✗ Apify: run falló para {url}")
            return {}

        # SDK nuevo devuelve objeto Run, SDK viejo devuelve dict
        dataset_id = (
            run["defaultDatasetId"] if isinstance(run, dict)
            else getattr(run, "default_dataset_id", None)
        )
        if not dataset_id:
            log.warning(f"  ✗ Apify: no dataset_id en run")
            return {}
        items = list(apify.dataset(dataset_id).iterate_items())
        log.info(f"  ✓ Apify: {len(items)} items devueltos")
        for i, item in enumerate(items[:3]):
            log.info(f"  DEBUG item[{i}]: {item}")

        return parsear_resultado_apify(items, nombre)

    except Exception as e:
        log.error(f"  ✗ Apify error {url}: {e}")
        return {}

# ============================================================
# ENRIQUECEDOR POR LEAD
# ============================================================

def enrich_lead(lead: dict) -> dict:
    nombre    = lead["nombre"]
    sitio_web = (lead.get("sitio_web") or "").strip()

    if not sitio_web:
        return {}

    if any(d in sitio_web for d in SKIP_DOMINIOS):
        log.info(f"  Saltando — dominio excluido")
        return {}

    log.info(f"  Web: {sitio_web}")

    resultado = apify_scrape(sitio_web, nombre)

    # Siempre grabamos — aunque Apify falle marcamos como procesado
    wa     = resultado.get("whatsapp") if resultado else None
    emails = (resultado.get("emails") or []) if resultado else []
    ig     = resultado.get("instagram") if resultado else None
    fb     = resultado.get("facebook") if resultado else None

    email_principal  = emails[0] if emails else None
    email_secundario = emails[1] if len(emails) > 1 else None

    campos = {
        "whatsapp":  wa,
        "link_wame": construir_wame(wa) if wa else None,
        "email":     email_principal,
        "notas":     f"2do email: {email_secundario}" if email_secundario else None,
        "instagram": ig[0] if ig else None,
        "link_ig":   ig[1] if ig else None,
        "facebook":  fb,
        "origen_contacto":     "web-auto",
        "enriquecido":         True,
        "fecha_actualizacion": datetime.now(timezone.utc).isoformat(),
    }

    # Notas de estado
    if not campos["notas"]:
        if not resultado:
            campos["notas"] = "error apify"
        elif not wa and not email_principal:
            campos["notas"] = "sin contacto web"
        elif not wa:
            campos["notas"] = "sin whatsapp web"
        elif not email_principal:
            campos["notas"] = "sin email web"

    tiene_wa    = bool(wa)
    tiene_email = bool(email_principal)
    estado = (
        "completo" if (tiene_wa and tiene_email)
        else "parcial" if (tiene_wa or tiene_email)
        else "sin_datos"
    )
    log.info(f"  → {estado} | wzap: {wa or '—'} | email: {email_principal or '—'}")

    return campos

# ============================================================
# REPORTE
# ============================================================

def eurocrem_enrich_apify_reporte(output_path: str = "eurocrem_debug_report_apify.txt"):
    result = supabase.table("leads").select(
        "nombre, barrio, sitio_web, whatsapp, email, instagram, facebook, notas, origen_contacto"
    ).execute()
    leads = result.data
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M")

    completos  = [l for l in leads if l.get("whatsapp") and l.get("email")]
    parciales  = [l for l in leads
                  if (l.get("whatsapp") or l.get("email"))
                  and not (l.get("whatsapp") and l.get("email"))
                  and l.get("origen_contacto") == "web-auto"]
    sin_datos  = [l for l in leads
                  if not l.get("whatsapp") and not l.get("email")
                  and l.get("origen_contacto") == "web-auto"]
    saltados   = [l for l in leads
                  if l.get("sitio_web") and any(
                      d in (l.get("sitio_web") or "") for d in ["instagram.com", "facebook.com"]
                  )]
    sin_web    = [l for l in leads if not l.get("sitio_web")]
    pendientes = [l for l in leads if l.get("origen_contacto") == "pendiente"]

    lines = [
        f"EUROCREM — Debug Report Apify — {ahora}",
        f"Total leads: {len(leads)}",
        f"Completos (wzap + email):  {len(completos)}",
        f"Parciales (wzap o email):  {len(parciales)}",
        f"Sin contacto web:          {len(sin_datos)}",
        f"Saltados (IG/FB como web): {len(saltados)}",
        f"Sin sitio web:             {len(sin_web)}",
        f"Pendientes:                {len(pendientes)}",
        "",
    ]

    def seccion(titulo, items, fmt):
        lines.append("=" * 60)
        lines.append(titulo)
        lines.append("=" * 60)
        for l in items:
            lines.append(fmt(l))
        lines.append("")

    seccion("COMPLETOS", completos,
        lambda l: f"  {l['nombre']} ({l.get('barrio','')}) | wzap: {l.get('whatsapp')} | email: {l.get('email')}")
    seccion("PARCIALES", parciales,
        lambda l: f"  {l['nombre']} ({l.get('barrio','')}) | wzap: {l.get('whatsapp') or '—'} | email: {l.get('email') or '—'} | {l.get('notas') or ''}")
    seccion("SIN CONTACTO WEB", sin_datos,
        lambda l: f"  {l['nombre']} ({l.get('barrio','')}) | web: {l.get('sitio_web') or 'sin web'} | {l.get('notas') or ''}")
    seccion("SALTADOS (IG/FB)", saltados,
        lambda l: f"  {l['nombre']} ({l.get('barrio','')}) | web: {l.get('sitio_web')}")
    seccion("SIN SITIO WEB", sin_web,
        lambda l: f"  {l['nombre']} ({l.get('barrio','')})")
    seccion("PENDIENTES", pendientes,
        lambda l: f"  {l['nombre']} ({l.get('barrio','')}) | web: {l.get('sitio_web') or 'sin web'}")

    reporte = "\n".join(lines)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(reporte)
    log.info(f"Reporte guardado: {output_path}")

# ============================================================
# RUNNER
# ============================================================

def eurocrem_enrich_apify_run():
    result = supabase.table("leads").select(
        "id, nombre, barrio, sitio_web, origen_contacto"
    ).eq("origen_contacto", "pendiente").execute()

    leads = result.data
    total = len(leads)
    log.info(f"\n{'='*60}")
    log.info(f"EUROCREM enrich_apify v1.0 — {total} leads a procesar")
    log.info(f"{'='*60}")

    stats = {"completo": 0, "parcial": 0, "sin_datos": 0, "saltado": 0, "error": 0}

    for i, lead in enumerate(leads):
        log.info(f"\n[{i+1}/{total}] {lead['nombre']} ({lead.get('barrio', '')})")
        try:
            campos = enrich_lead(lead)
            if campos:
                tiene_wa    = bool(campos.get("whatsapp"))
                tiene_email = bool(campos.get("email"))
                if tiene_wa and tiene_email:
                    stats["completo"] += 1
                elif tiene_wa or tiene_email:
                    stats["parcial"] += 1
                else:
                    stats["sin_datos"] += 1
                supabase.table("leads").update(campos).eq("id", lead["id"]).execute()
                log.info(f"  ✓ Supabase actualizado")
            else:
                stats["saltado"] += 1
        except Exception as e:
            log.error(f"  ✗ Error: {e}")
            stats["error"] += 1

        time.sleep(DELAY_ENTRE_LEADS)

    log.info(f"\n{'='*60}")
    log.info(f"RESUMEN:")
    log.info(f"  Completos:  {stats['completo']}")
    log.info(f"  Parciales:  {stats['parcial']}")
    log.info(f"  Sin datos:  {stats['sin_datos']}")
    log.info(f"  Saltados:   {stats['saltado']}")
    log.info(f"  Errores:    {stats['error']}")
    log.info(f"{'='*60}")


if __name__ == "__main__":
    log.info("=== EUROCREM enrich_apify v1.0 START ===")
    eurocrem_enrich_apify_run()
    eurocrem_enrich_apify_reporte()
    log.info("=== EUROCREM enrich_apify v1.0 DONE ===")

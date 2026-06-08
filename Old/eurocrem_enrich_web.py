"""
EUROCREM — eurocrem_enrich_web.py
Versión: 3.1 — 05/06/2026

Estrategia: scrape secuencial con Firecrawl (free tier compatible).
En lugar de crawl_url (rate limit 3/min), hace scrape_url individual
de home + páginas internas detectadas, con sleep entre cada request.

Free tier: 500 créditos/mes, ~1 req cada 20s para no exceder rate limit.
"""

import os
import re
import time
import random
import logging
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, unquote

from firecrawl import FirecrawlApp
from bs4 import BeautifulSoup
from supabase import create_client, Client

# ============================================================
# CONFIGURACIÓN
# ============================================================

SUPABASE_URL      = os.getenv("SUPABASE_URL",      "https://crbmgsmmvfkbxrplqfkl.supabase.co")
SUPABASE_KEY      = os.getenv("SUPABASE_KEY",      "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNyYm1nc21tdmZrYnhycGxxZmtsIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MDUxNjQ0NywiZXhwIjoyMDk2MDkyNDQ3fQ.7D5od9xbpGP3XbdjrTpgCtL7xWYpeJ6xaa5cBsMsju8")
FIRECRAWL_KEY     = os.getenv("FIRECRAWL_API_KEY",  "fc-f5c48a9daab0489cbe4f722d985c7058")

# Free tier: 3 crawls/min → 1 cada 22s para ir seguro
SLEEP_ENTRE_SCRAPES = 22
MAX_PAGINAS_INTERNAS = 4

PAGINAS_INTERNAS_KEYWORDS = [
    "contacto", "contact", "reservas", "reservar", "reservations",
    "delivery", "pedidos", "nosotros", "about",
    "donde-estamos", "ubicacion", "locales", "sucursales",
    "inicio", "home",
]

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

supabase:  Client       = create_client(SUPABASE_URL, SUPABASE_KEY)
firecrawl: FirecrawlApp = FirecrawlApp(api_key=FIRECRAWL_KEY)

# ============================================================
# REGEX
# ============================================================

# Regex WhatsApp — cubren todos los formatos conocidos:
# wa.me/5491... | wa.me/%2B5491... | wa.me/+5491...
RE_WAME     = re.compile(r"wa\.me/[^0-9+]*\+?(\d{7,15})", re.I)
# api.whatsapp.com/send?phone=5491... | ?phone=%3C+5491...> | ?phone=<+5491...> | ?phone=%2B5491...
RE_WA_API   = re.compile(r"(?:api|web)\.whatsapp\.com/send/?\?phone=[^0-9+]*\+?(\d{7,15})", re.I)
# whatsapp://send?phone=5491...
RE_WA_PROTO = re.compile(r"whatsapp://send\?phone=[^0-9+]*\+?(\d{7,15})", re.I)
RE_WA_PROXIMITY = re.compile(
    r"(?:whatsapp|wsp|wpp)\s*[:\-]?\s*((?:\+?54\s?)?(?:9\s?)?(?:11|15|\d{2,3})\s?[\d\s\-]{6,12})",
    re.I
)
RE_JSON_TEL = re.compile(r'"telephone"\s*:\s*"(\d{7,15})"', re.I)
RE_EMAIL    = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

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

# ============================================================
# NORMALIZADORES
# ============================================================

def normalizar_whatsapp(numero: str) -> str | None:
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
# EXTRACTORES
# ============================================================

def extraer_whatsapp(texto: str, url_fuente: str = "") -> tuple[str | None, str]:
    texto_dec = unquote(texto).replace("&#038;", "&").replace("&amp;", "&").replace("&#43;", "+")
    for src in (texto, texto_dec):
        for regex in (RE_WAME, RE_WA_API, RE_WA_PROTO):
            for m in regex.finditer(src):
                n = normalizar_whatsapp(m.group(1))
                if n:
                    log.info(f"    ✓ WA link: {n} [{url_fuente}]")
                    return n, url_fuente
    for m in RE_JSON_TEL.finditer(texto):
        n = normalizar_whatsapp(m.group(1))
        if n:
            log.info(f"    ✓ WA JSON: {n} [{url_fuente}]")
            return n, url_fuente
    m = RE_WA_PROXIMITY.search(texto)
    if m:
        n = normalizar_whatsapp(m.group(1))
        if n:
            log.info(f"    ✓ WA proximidad: {n} [{url_fuente}]")
            return n, url_fuente
    return None, ""


def extraer_emails(texto: str, url_fuente: str = "") -> list[tuple[str, str]]:
    encontrados = []
    for m in re.finditer(r"mailto:([^\s\)\]\"']+)", texto, re.I):
        email = m.group(1).split("?")[0].strip().lower()
        if validar_email(email):
            encontrados.append((1, email))
    for m in re.finditer(r'"email"\s*:\s*"([^"]+)"', texto):
        email = m.group(1).lower()
        if validar_email(email):
            encontrados.append((2, email))
    for m in RE_EMAIL.finditer(texto):
        email = m.group().lower()
        if validar_email(email):
            encontrados.append((3, email))
    vistos = set()
    resultado = []
    for _, email in sorted(encontrados, key=lambda x: x[0]):
        if email not in vistos:
            vistos.add(email)
            resultado.append((email, url_fuente))
    keywords = ["reserva", "contacto", "info", "eventos", "ventas", "hola", "admin"]
    resultado.sort(key=lambda x: next((i for i, k in enumerate(keywords) if k in x[0]), 99))
    if resultado:
        log.info(f"    ✓ Emails: {[e for e, _ in resultado]} [{url_fuente}]")
    return resultado


def extraer_redes(texto: str, nombre: str = "") -> dict:
    redes = {}
    invalidos = {
        "p", "reel", "reels", "stories", "explore", "sharer", "share",
        "dialog", "login", "watch", "groups", "events", "instagram",
        "accounts", "hashtag",
    }
    stopwords = {
        "el", "la", "los", "las", "de", "del", "al", "y", "e",
        "parrilla", "restaurante", "bar", "cafe", "bistro", "grill",
        "resto", "casa", "cocina", "cucina", "osteria", "trattoria",
    }
    tokens_nombre = {
        t for t in re.findall(r"[a-z]+", nombre.lower())
        if t not in stopwords and len(t) > 2
    } if nombre else set()

    candidatos_ig = {}
    for m in re.finditer(r"instagram\.com/([a-zA-Z0-9._]+)", texto):
        handle = m.group(1).lower()
        if handle not in invalidos and handle not in candidatos_ig:
            candidatos_ig[handle] = m.group(1)
    for m in re.finditer(r"facebook\.com/([^\s\)\]\"'/]+)", texto):
        path = m.group(1).rstrip("/").split("?")[0]
        paths_inv = {"sharer", "share", "dialog", "login", "watch", "groups", "events", "marketplace", "pages"}
        if path and path.lower() not in paths_inv and "facebook" not in redes:
            redes["facebook"] = f"https://www.facebook.com/{path}"

    if candidatos_ig:
        def score_ig(handle: str) -> float:
            handle_tokens = set(re.findall(r"[a-z]+", handle))
            aciertos  = sum(1 for t in tokens_nombre if t in handle)
            penalidad = sum(1 for t in handle_tokens if t not in tokens_nombre and t not in stopwords and len(t) > 2)
            return aciertos - (penalidad * 0.5)
        mejor = max(candidatos_ig.keys(), key=score_ig)
        redes["instagram"] = f"@{candidatos_ig[mejor]}"
        redes["link_ig"]   = f"https://www.instagram.com/{candidatos_ig[mejor]}/"
    return redes

# ============================================================
# FIRECRAWL — scrape secuencial
# ============================================================

def fc_scrape(url: str) -> str:
    """Scrape una URL con Firecrawl. Devuelve markdown + html concatenados."""
    try:
        result = firecrawl.scrape_url(
            url,
            formats=["markdown", "rawHtml"],
            only_main_content=False,
        )
        md   = ""
        html = ""
        if hasattr(result, "markdown"):
            md   = result.markdown or ""
            html = result.raw_html or ""
        elif isinstance(result, dict):
            md   = result.get("markdown") or ""
            html = result.get("rawHtml") or ""
        texto = md + "\n" + html
        log.info(f"  ✓ Firecrawl scrape OK: {url} ({len(texto)} chars)")
        return texto
    except Exception as e:
        log.warning(f"  ✗ Firecrawl scrape error {url}: {e}")
        return ""


EXTENSIONES_SKIP = re.compile(
    r"\.(png|jpg|jpeg|gif|svg|webp|ico|pdf|zip|mp4|mp3|css|js|woff|woff2|ttf|eot)(\?.*)?$",
    re.I
)

def es_url_scrapeable(url: str, base_url: str) -> bool:
    """Filtra URLs que no son páginas HTML: imágenes, anclas puras, dominios externos, etc."""
    parsed = urlparse(url)
    # Ancla pura — misma página
    if parsed.fragment and not parsed.path.rstrip("/"):
        return False
    # Extensión de archivo no HTML
    if EXTENSIONES_SKIP.search(parsed.path):
        return False
    # Dominio externo
    if parsed.netloc != urlparse(base_url).netloc:
        return False
    # Misma URL que la base
    if url.rstrip("/").split("#")[0] == base_url.rstrip("/").split("#")[0]:
        return False
    return True


def detectar_paginas_internas(texto_home: str, base_url: str) -> list[str]:
    """
    Detecta URLs internas relevantes desde el markdown/html de la home.
    Busca links que contengan keywords de contacto/reservas.
    Filtra imágenes, anclas puras y dominios externos.
    """
    encontrados = []

    # Buscar en markdown — formato [texto](url)
    for m in re.finditer(r"\[([^\]]*)\]\((https?://[^\)]+)\)", texto_home):
        texto_link = m.group(1).lower()
        url_link   = m.group(2).split(" ")[0]  # quitar title opcional
        if any(k in texto_link or k in url_link.lower() for k in PAGINAS_INTERNAS_KEYWORDS):
            if es_url_scrapeable(url_link, base_url) and url_link not in encontrados:
                encontrados.append(url_link)

    # Buscar en HTML — formato href="..."
    try:
        soup = BeautifulSoup(texto_home, "html.parser")
        for a in soup.find_all("a", href=True):
            href       = a["href"].strip()
            texto_a    = a.get_text().lower().strip()
            href_lower = href.lower()
            if any(k in href_lower or k in texto_a for k in PAGINAS_INTERNAS_KEYWORDS):
                url_abs = urljoin(base_url, href)
                if es_url_scrapeable(url_abs, base_url) and url_abs not in encontrados:
                    encontrados.append(url_abs)
    except Exception:
        pass

    return encontrados[:MAX_PAGINAS_INTERNAS]

# ============================================================
# ENRIQUECEDOR POR LEAD
# ============================================================

def eurocrem_enrich_web_lead(lead: dict) -> dict:
    nombre    = lead["nombre"]
    sitio_web = (lead.get("sitio_web") or "").strip()

    if not sitio_web:
        return {}
    if "instagram.com" in sitio_web or "facebook.com" in sitio_web:
        log.info(f"  Saltando — IG/FB")
        return {}
    skip_dominios = ["meitre.com", "woki.com", "opentable.com", "thefork.com"]
    if any(d in sitio_web for d in skip_dominios):
        log.info(f"  Saltando — plataforma reservas")
        return {}

    log.info(f"  Web: {sitio_web}")

    acumulado = {
        "whatsapp": None, "link_wame": None,
        "email": None, "notas": None,
        "instagram": None, "link_ig": None, "facebook": None,
    }
    fuente_wzap  = ""
    fuente_email = ""

    # ── PASO 1: Home ─────────────────────────────────────────
    texto_home = fc_scrape(sitio_web)
    time.sleep(SLEEP_ENTRE_SCRAPES)

    if not texto_home.strip():
        log.info(f"  ✗ Home vacía")
        return {}

    # Splash page — si el contenido útil es muy poco, probar URLs candidatas
    chars_utiles = len(texto_home.strip())
    if chars_utiles < 5000:
        log.info(f"  → Splash detectada ({chars_utiles} chars), probando candidatas...")
        base = sitio_web.rstrip("/").split("?")[0]
        for url_splash in [f"{base}/qr/", f"{base}/inicio/", f"{base}/home/", f"{base}/bienvenida/"]:
            texto_sp = fc_scrape(url_splash)
            time.sleep(SLEEP_ENTRE_SCRAPES)
            if len(texto_sp.strip()) > chars_utiles:
                log.info(f"  ✓ Splash resuelta: {url_splash}")
                texto_home = texto_sp
                sitio_web  = url_splash
                break

    wa, fw = extraer_whatsapp(texto_home, sitio_web)
    if wa:
        acumulado["whatsapp"]  = wa
        acumulado["link_wame"] = construir_wame(wa)
        fuente_wzap = fw

    emails = extraer_emails(texto_home, sitio_web)
    if emails:
        acumulado["email"] = emails[0][0]
        fuente_email = emails[0][1]
        if len(emails) > 1 and emails[1][0] != emails[0][0]:
            acumulado["notas"] = f"2do email: {emails[1][0]}"

    redes = extraer_redes(texto_home, nombre)
    if redes.get("instagram"):
        acumulado["instagram"] = redes["instagram"]
        acumulado["link_ig"]   = redes.get("link_ig")
    if redes.get("facebook"):
        acumulado["facebook"] = redes["facebook"]

    # ── PASO 2: Páginas internas si faltan datos ──────────────
    falta_wa    = not acumulado.get("whatsapp")
    falta_email = not acumulado.get("email")

    if falta_wa or falta_email:
        paginas = detectar_paginas_internas(texto_home, sitio_web)
        log.info(f"  → Páginas internas detectadas: {paginas}")

        for url_int in paginas:
            if not falta_wa and not falta_email:
                break

            texto_int = fc_scrape(url_int)
            time.sleep(SLEEP_ENTRE_SCRAPES)

            if not texto_int.strip():
                continue

            if falta_wa:
                wa, fw = extraer_whatsapp(texto_int, url_int)
                if wa:
                    acumulado["whatsapp"]  = wa
                    acumulado["link_wame"] = construir_wame(wa)
                    fuente_wzap = fw
                    falta_wa    = False

            if falta_email:
                emails = extraer_emails(texto_int, url_int)
                if emails:
                    acumulado["email"] = emails[0][0]
                    fuente_email = emails[0][1]
                    if len(emails) > 1 and emails[1][0] != emails[0][0]:
                        acumulado["notas"] = f"2do email: {emails[1][0]}"
                    falta_email = False

    # ── Notas de estado ───────────────────────────────────────
    tiene_wa    = bool(acumulado.get("whatsapp"))
    tiene_email = bool(acumulado.get("email"))

    if not acumulado.get("notas"):
        if not tiene_wa and not tiene_email:
            acumulado["notas"] = "sin contacto web"
        elif not tiene_wa:
            acumulado["notas"] = "sin whatsapp web"
        elif not tiene_email:
            acumulado["notas"] = "sin email web"

    estado = (
        "completo" if (tiene_wa and tiene_email)
        else "parcial" if (tiene_wa or tiene_email)
        else "sin_datos"
    )
    log.info(f"  → {estado} | wzap: {fuente_wzap or '—'} | email: {fuente_email or '—'}")

    acumulado["origen_contacto"]     = "web-auto"
    acumulado["enriquecido"]         = True
    acumulado["fecha_actualizacion"] = datetime.now(timezone.utc).isoformat()

    return acumulado

# ============================================================
# REPORTE
# ============================================================

def eurocrem_enrich_web_reporte(output_path: str = "eurocrem_debug_report.txt"):
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
                  if l.get("sitio_web") and (
                      "instagram.com" in (l.get("sitio_web") or "")
                      or "facebook.com" in (l.get("sitio_web") or "")
                  )]
    sin_web    = [l for l in leads if not l.get("sitio_web")]
    pendientes = [l for l in leads if l.get("origen_contacto") == "pendiente"]

    lines = [
        f"EUROCREM — Debug Report — {ahora}",
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
    seccion("SIN CONTACTO WEB — para investigar", sin_datos,
        lambda l: f"  {l['nombre']} ({l.get('barrio','')}) | web: {l.get('sitio_web') or 'sin web'} | IG: {l.get('instagram') or '—'} | {l.get('notas') or ''}")
    seccion("SALTADOS (IG/FB como sitio web)", saltados,
        lambda l: f"  {l['nombre']} ({l.get('barrio','')}) | web: {l.get('sitio_web')}")
    seccion("SIN SITIO WEB", sin_web,
        lambda l: f"  {l['nombre']} ({l.get('barrio','')})")
    seccion("PENDIENTES — no procesados aun", pendientes,
        lambda l: f"  {l['nombre']} ({l.get('barrio','')}) | web: {l.get('sitio_web') or 'sin web'}")

    reporte = "\n".join(lines)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(reporte)
    log.info(f"Reporte guardado: {output_path}")

# ============================================================
# RUNNER
# ============================================================

def eurocrem_enrich_web_run():
    result = supabase.table("leads").select(
        "id, nombre, barrio, sitio_web, origen_contacto"
    ).eq("origen_contacto", "pendiente").execute()

    leads = result.data
    total = len(leads)
    log.info(f"\n{'='*60}")
    log.info(f"EUROCREM enrich_web v3.2 (Firecrawl secuencial) — {total} leads")
    log.info(f"{'='*60}")

    stats = {"completo": 0, "parcial": 0, "sin_datos": 0, "saltado": 0, "error": 0}

    for i, lead in enumerate(leads):
        log.info(f"\n[{i+1}/{total}] {lead['nombre']} ({lead.get('barrio', '')})")
        try:
            campos = eurocrem_enrich_web_lead(lead)
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

    log.info(f"\n{'='*60}")
    log.info(f"RESUMEN:")
    log.info(f"  Completos:  {stats['completo']}")
    log.info(f"  Parciales:  {stats['parcial']}")
    log.info(f"  Sin datos:  {stats['sin_datos']}")
    log.info(f"  Saltados:   {stats['saltado']}")
    log.info(f"  Errores:    {stats['error']}")
    log.info(f"{'='*60}")


if __name__ == "__main__":
    log.info("=== EUROCREM enrich_web v3.2 START ===")
    eurocrem_enrich_web_run()
    eurocrem_enrich_web_reporte()
    log.info("=== EUROCREM enrich_web v3.2 DONE ===")

"""
EUROCREM — eurocrem_enrich_v5.6.py
Versión: 5.6 — 07/06/2026

Cambios sobre v5.1:
  [FIX]   normalizar_whatsapp() soporta "011 15-XXXX-XXXX" (12 dígitos con 15 embebido)
  [FIX]   extraer_contactos_de_html() captura phone=<+NUMBER> con brackets literales (Wix/WP)
  [FIX]   Telefono fallback SOLO cuando lead NO tiene sitio_web NI instagram NI facebook
  [FIX]   _guardar_resultado() filtra a columnas DB válidas (evita error con status_ok, notas_enrich, etc.)
  [SPEED] Batch IG: todos los handles en un solo call Apify
  [SPEED] Batch FB: todas las páginas en un solo call Apify
  [SPEED] Web leads en paralelo con ThreadPoolExecutor (max 4 workers)
  [CLI]   Modos: full | sinwa | reporte | ids ID1,ID2,...
"""

import os
import re
import sys
import time
import logging
import requests as req_lib
from datetime import datetime, timezone
from urllib.parse import urlparse, unquote
from concurrent.futures import ThreadPoolExecutor, as_completed

from apify_client import ApifyClient
from supabase import create_client, Client

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_OK = True
except ImportError:
    PLAYWRIGHT_OK = False

# ============================================================
# CONFIGURACIÓN
# ============================================================

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://crbmgsmmvfkbxrplqfkl.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNyYm1nc21tdmZrYnhycGxxZmtsIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MDUxNjQ0NywiZXhwIjoyMDk2MDkyNDQ3fQ.7D5od9xbpGP3XbdjrTpgCtL7xWYpeJ6xaa5cBsMsju8")
APIFY_TOKEN  = os.getenv("APIFY_TOKEN",  "apify_api_zWjFWPdJLUef0mCOyMxcYiBN5zgK3o3JDEtU")

ACTOR_INSTAGRAM = "apify/instagram-profile-scraper"
ACTOR_FACEBOOK  = "apify/facebook-pages-scraper"
ACTOR_GMAPS     = "compass/google-maps-scraper"

SUBPAGINAS_FALLBACK = ["/qr/", "/reservas", "/contacto", "/menu", "/contacto/"]
DELAY_ENTRE_LEADS   = 0.5
WEB_MAX_WORKERS     = 4

HTTP_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}

# Columnas válidas en Supabase — _guardar_resultado filtra a estas
DB_COLUMNS = {
    "whatsapp", "link_wame", "email", "email_2", "instagram", "link_ig", "facebook",
    "notas", "origen_contacto", "enriquecido", "fecha_actualizacion",
}

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

if not PLAYWRIGHT_OK:
    log.warning("Playwright no instalado — fallback JS desactivado.")

# ============================================================
# CLIENTES
# ============================================================

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
apify = ApifyClient(APIFY_TOKEN)

# ============================================================
# DOMINIOS
# ============================================================

DOMINIOS_IG       = ["instagram.com"]
DOMINIOS_FB       = ["facebook.com"]
DOMINIOS_LINKTREE = ["linktr.ee", "linktree.com"]
DOMINIOS_RESERVAS = [
    "meitre.com", "woki.com", "wokiapp.com", "opentable.com",
    "thefork.com", "apparta.co", "guiaoleo.com",
]
DOMINIOS_NO_SCRAPEAR = (
    DOMINIOS_IG + DOMINIOS_FB + DOMINIOS_LINKTREE
    + ["wa.me", "api.whatsapp.com", "developers.facebook.com",
       "rappi.com", "pedidosya.com", "ubereats.com",
       "tripadvisor.com", "yelp.com"]
)

EXTENSIONES_BINARIAS = re.compile(
    r"\.(pdf|docx?|xlsx?|pptx?|jpg|jpeg|png|gif|webp|svg|ico|zip|rar|mp4|mp3)$",
    re.IGNORECASE,
)
SHORTENERS_CONOCIDOS = {
    "acortar.link", "bit.ly", "t.co", "goo.gl", "ow.ly",
    "tinyurl.com", "short.link", "cutt.ly", "lnk.bio",
    "rb.gy", "shorturl.at", "url.ar",
}
RE_IG_HANDLE = re.compile(r"^[a-zA-Z0-9._]{1,30}$")

# ============================================================
# HELPERS DE URL
# ============================================================

def _netloc(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return ""


def es_url_scrappeable(url: str) -> bool:
    if not url:
        return False
    if "wa.me/" in url or "api.whatsapp.com" in url:
        return False
    if EXTENSIONES_BINARIAS.search(urlparse(url).path):
        return False
    netloc = _netloc(url)
    for d in DOMINIOS_NO_SCRAPEAR + DOMINIOS_RESERVAS:
        if netloc == d or netloc.endswith("." + d):
            return False
    return True


def es_reservas_url(url: str) -> bool:
    if not url:
        return False
    netloc = _netloc(url)
    return any(netloc == d or netloc.endswith("." + d) for d in DOMINIOS_RESERVAS)


def es_linktree(url: str) -> bool:
    if not url:
        return False
    netloc = _netloc(url)
    return any(netloc == d or netloc.endswith("." + d) for d in DOMINIOS_LINKTREE)


def resolver_url_shortener(url: str) -> str:
    try:
        netloc = _netloc(url)
        if netloc in SHORTENERS_CONOCIDOS:
            log.info(f"  → Resolviendo shortener: {url}")
            r = req_lib.head(url, allow_redirects=True, timeout=8, headers=HTTP_HEADERS)
            final = r.url
            if final and final != url:
                return final
    except Exception as e:
        log.debug(f"  shortener error: {e}")
    return url


def limpiar_url_base(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}{p.path.rstrip('/')}"


def extraer_ig_handle_de_url(ig_url: str) -> str | None:
    if not ig_url:
        return None
    if "?next=" in ig_url or "accounts/password/reset" in ig_url:
        m = re.search(r"[?&]next=([^&]+)", ig_url)
        if m:
            ig_url = unquote(m.group(1))
    parsed = urlparse(ig_url)
    segments = [s for s in parsed.path.split("/") if s]
    if not segments:
        return None
    handle = segments[-1].lstrip("@")
    invalidos = {"p", "reel", "reels", "stories", "explore", "instagram",
                 "accounts", "password", "reset", ""}
    if handle.lower() in invalidos:
        return None
    return handle if RE_IG_HANDLE.match(handle) else None

# ============================================================
# NORMALIZADORES
# ============================================================

def normalizar_whatsapp(numero: str) -> str | None:
    """
    FIX v5.5: soporta "011 15-XXXX-XXXX"
    "011 15-2788-9114" → strip 0 → "111527889114" (12 dig)
    digits[2:4]=="15" → área 11 → "11"+"27889114" = 10 dig ✓
    """
    if not numero:
        return None
    digits = re.sub(r"[^\d]", "", numero)
    if digits.startswith("54"):
        digits = digits[2:]
    if digits.startswith("0"):
        digits = digits[1:]
    if digits.startswith("9") and len(digits) == 11:
        digits = digits[1:]
    if len(digits) == 12:
        if digits[2:4] == "15":      # área 2 dígitos (ej: 11 Buenos Aires)
            digits = digits[:2] + digits[4:]
        elif digits[3:5] == "15":    # área 3 dígitos (ej: 221 La Plata)
            digits = digits[:3] + digits[5:]
    if len(digits) != 10:
        return None
    return f"+54 9 {digits[:2]} {digits[2:6]}-{digits[6:]}"


def construir_wame(numero: str) -> str | None:
    if not numero:
        return None
    digits = re.sub(r"[^\d]", "", numero)
    if not digits.startswith("54"):
        digits = "54" + digits
    if len(digits) == 12 and digits[2:3] != "9":
        digits = digits[:2] + "9" + digits[2:]
    return f"https://wa.me/{digits}"


def extraer_numero_de_wame_url(url: str) -> str | None:
    if not url:
        return None
    url_decoded = unquote(url)
    m = re.search(r"(?:wa\.me/|phone=)\+?(\d{7,15})", url_decoded, re.I)
    return normalizar_whatsapp(m.group(1)) if m else None


def es_celular_arg(tel: str) -> bool:
    if not tel:
        return False
    return bool(re.search(r"(?:^|\b)(?:011\s*)?15[-\s]", tel))


EMAIL_BLACKLIST = {
    "example.com", "test.com", "sentry.io", "wixpress.com",
    "sentry-next.wixpress.com", "sentry.wixpress.com",
    "squarespace.com", "shopify.com", "wordpress.com",
    "clarin.com", "lanacion.com.ar", "infobae.com",
    "rappi.com", "pedidosya.com", "ifood.com", "ubereats.com",
    "tripadvisor.com", "yelp.com", "guiaoleo.com",
    "meitre.com", "woki.com", "opentable.com", "thefork.com", "apparta.co",
    "instagram.com", "facebook.com", "twitter.com", "tiktok.com",
    "google.com", "googlemail.com", "linktr.ee",
}


def validar_email(email: str) -> bool:
    if not email or len(email) < 6:
        return False
    email = email.lower().strip()
    dominio = email.split("@")[-1]
    if dominio in EMAIL_BLACKLIST:
        return False
    if not re.match(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,8}$", email):
        return False
    if re.search(r"\.(png|jpg|gif|svg|webp|ico|css|js)$", email, re.I):
        return False
    return True

# ============================================================
# REGEXES
# ============================================================

RE_WA_NUMBER = re.compile(
    r"""(?x)
    (?:\+?54[\s\-]?9[\s\-]?|\+?549|0?9?)
    (?:11|15|2\d{2}|3\d{2})
    [\s\-]?\d{4}[\s\-]?\d{4}
    """, re.VERBOSE,
)

RE_TELEFONO_BIO = re.compile(
    r"""(?x)
    (?:\+?54[\s\-]?9?[\s\-]?)?
    ((?:11|15|2\d{2}|3\d{2})
    [\s\-]?\d{4}[\s\-]?\d{4})
    """, re.VERBOSE,
)

RE_WA_KEYWORDS = re.compile(
    r"whatsapp|wh[aá]tsapp|wsp|wsap|wpp|w\.?a\.?p|📱|wa\.me",
    re.IGNORECASE,
)


def re_wa_proximity(texto: str, es_html: bool = False) -> str | None:
    if not texto:
        return None
    if es_html:
        texto = re.sub(r"<[^>]+>", " ", texto)
        texto = re.sub(r"\s+", " ", texto)
    for kw_match in RE_WA_KEYWORDS.finditer(texto):
        inicio  = max(0, kw_match.start() - 120)
        fin     = min(len(texto), kw_match.end() + 120)
        ventana = texto[inicio:fin]
        for num in RE_WA_NUMBER.findall(ventana):
            n = normalizar_whatsapp(num)
            if n:
                log.info(f"  → RE_WA_PROXIMITY: {n} cerca de '{kw_match.group()}'")
                return n
    return None


def extraer_telefono_bio(texto: str) -> str | None:
    if not texto:
        return None
    for m in RE_TELEFONO_BIO.finditer(texto):
        n = normalizar_whatsapp(m.group(0))
        if n:
            log.info(f"  → Teléfono en bio: {n}")
            return n
    return None

# ============================================================
# EXTRACCIÓN DE CONTACTOS DESDE HTML
# ============================================================

def extraer_contactos_de_html(html: str) -> dict:
    wa      = None
    email   = None
    ig      = None
    ig_url  = None
    fb      = None
    todos_emails = []

    # WA: links estándar
    for link in re.findall(r'https?://(?:wa\.me|api\.whatsapp\.com)[^\s"\'<>]*', html, re.I):
        n = extraer_numero_de_wame_url(link)
        if n:
            wa = n
            break

    # FIX v5.5: WA con phone=<+NUMBER> (Wix/WP brackets literales)
    if not wa:
        for m in re.finditer(
            r'(?:wa\.me|api\.whatsapp\.com)[^"\']{0,60}?phone=(?:&lt;|[<(\s])*\+?(\d{7,15})',
            html, re.I
        ):
            n = normalizar_whatsapp(m.group(1))
            if n:
                log.info(f"  → WA phone=<number> (Wix/WP): {n}")
                wa = n
                break

    # WA: texto plano
    if not wa:
        wa = re_wa_proximity(html, es_html=True)

    # Emails
    for e in re.findall(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,8}', html):
        if validar_email(e):
            todos_emails.append(e.lower().strip())
    todos_emails = list(dict.fromkeys(todos_emails))
    keywords = ["reserva", "contacto", "info", "eventos", "ventas", "hola", "admin"]
    todos_emails.sort(key=lambda e: next((i for i, k in enumerate(keywords) if k in e), 99))
    if todos_emails:
        email = todos_emails[0]

    # Instagram
    for handle_raw in re.findall(
        r'instagram\.com/([a-zA-Z0-9._]{1,30})/?(?:["\'\s?]|$)', html, re.I
    ):
        handle = handle_raw.rstrip("/").split("?")[0]
        invalidos = {"p", "reel", "reels", "stories", "explore", "instagram",
                     "accounts", "password", "reset", ""}
        if handle.lower() not in invalidos and RE_IG_HANDLE.match(handle):
            ig     = f"@{handle}"
            ig_url = f"https://www.instagram.com/{handle}/"
            break

    # Facebook
    for path_raw in re.findall(
        r'facebook\.com/([^?\s"\'<>/][^?\s"\'<>]*)/?(?:["\'\s?]|$)', html, re.I
    ):
        path = path_raw.rstrip("/").split("?")[0]
        paths_inv = {
            "sharer", "share", "dialog", "login", "watch", "groups", "events",
            "developers", "docs", "help", "tr", "pixel", "plugins", "pages",
            "ads", "policy", "privacy", "legal", "terms", "",
        }
        if path.lower().split("/")[0] not in paths_inv:
            fb = f"https://www.facebook.com/{path}"
            break

    return {
        "whatsapp":     wa,
        "email":        email,
        "emails_extra": todos_emails[1:3],
        "instagram":    ig,
        "link_ig":      ig_url,
        "facebook":     fb,
        "status_ok":    True,
    }

# ============================================================
# SCRAPERS
# ============================================================

def fetch_html(url: str, timeout: int = 10) -> str | None:
    try:
        r = req_lib.get(url, headers=HTTP_HEADERS, timeout=timeout, allow_redirects=True)
        if r.status_code == 200:
            return r.text
        log.debug(f"  fetch_html {url} → HTTP {r.status_code}")
    except Exception as e:
        log.debug(f"  fetch_html error {url}: {e}")
    return None


def scrape_simple(url: str) -> dict:
    log.info(f"  [requests] {url}")
    html = fetch_html(url)
    if not html:
        return {"status_ok": False}
    return extraer_contactos_de_html(html)


def scrape_playwright(url: str) -> dict:
    if not PLAYWRIGHT_OK:
        return {"status_ok": False}
    log.info(f"  [playwright] {url}")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx  = browser.new_context(user_agent=HTTP_HEADERS["User-Agent"], locale="es-AR")
            page = ctx.new_page()
            page.goto(url, wait_until="networkidle", timeout=30_000)
            html = page.content()
            browser.close()
        result = extraer_contactos_de_html(html)
        log.info(f"  [playwright] wzap: {result.get('whatsapp') or '—'}")
        return result
    except Exception as e:
        log.error(f"  [playwright] error: {e}")
        return {"status_ok": False}


def scrape_reservas(url: str) -> dict:
    log.info(f"  [reservas] {url}")
    html = fetch_html(url)
    if not html and PLAYWRIGHT_OK:
        log.info(f"  [reservas→playwright] {url}")
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(user_agent=HTTP_HEADERS["User-Agent"])
                page.goto(url, wait_until="networkidle", timeout=25_000)
                html = page.content()
                browser.close()
        except Exception as e:
            log.error(f"  [reservas playwright] error: {e}")
    if not html:
        return {"status_ok": False, "notas_enrich": f"reservas sin respuesta: {_netloc(url)}"}

    wa    = None
    email = None
    for link in re.findall(r'https?://(?:wa\.me|api\.whatsapp\.com)[^\s"\'<>]*', html, re.I):
        n = extraer_numero_de_wame_url(link)
        if n:
            wa = n
            break
    if not wa:
        wa = re_wa_proximity(html, es_html=True)
    if not wa:
        texto_limpio = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
        wa = extraer_telefono_bio(texto_limpio)
    emails = [e.lower().strip() for e in
              re.findall(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,8}', html)
              if validar_email(e)]
    if emails:
        email = list(dict.fromkeys(emails))[0]

    log.info(f"  [reservas] wzap: {wa or '—'} | email: {email or '—'}")
    return {
        "whatsapp":        wa,
        "email":           email,
        "notas_enrich":    f"reservas: {_netloc(url)}",
        "origen_contacto": "web-auto",
        "status_ok":       True,
    }

# ============================================================
# PARSEO INSTAGRAM / FACEBOOK (Apify)
# ============================================================

def parsear_instagram(item: dict) -> dict:
    wa    = None
    email = None
    username  = item.get("username", "")
    ig_url    = f"https://www.instagram.com/{username}/" if username else None
    bio       = item.get("biography") or ""
    ext_url   = item.get("externalUrl") or ""
    ext_urls  = [l.get("url", "") for l in (item.get("externalUrls") or [])]
    todos_links = [u for u in ([ext_url] + ext_urls) if u]

    for link in todos_links:
        n = extraer_numero_de_wame_url(link)
        if n:
            wa = n
            break
    if not wa:
        wa = re_wa_proximity(bio, es_html=False)
    if not wa:
        wa = extraer_telefono_bio(bio)
    if not wa:
        for link in todos_links:
            if es_reservas_url(link):
                r_res = scrape_reservas(link)
                if r_res.get("whatsapp"):
                    wa = r_res["whatsapp"]
                    log.info(f"  → WA desde reservas IG: {wa}")
                    break

    m = re.search(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,8}", bio)
    if m and validar_email(m.group(0)):
        email = m.group(0).lower()

    website      = None
    reservas_url = None
    if ext_url:
        if es_linktree(ext_url):
            lt = parsear_linktree(ext_url)
            if lt.get("whatsapp") and not wa:
                wa = lt["whatsapp"]
            if lt.get("website"):
                website = lt["website"]
        elif es_url_scrappeable(ext_url):
            website = ext_url
        elif es_reservas_url(ext_url) and not wa:
            reservas_url = ext_url

    return {
        "whatsapp":     wa,
        "email":        email,
        "instagram":    f"@{username}" if username else None,
        "link_ig":      ig_url,
        "website":      website,
        "reservas_url": reservas_url,
    }


def parsear_facebook(item: dict) -> dict:
    wa     = None
    email  = None
    fb_url = item.get("facebookUrl") or None
    info_raw  = item.get("info") or []
    info_text = " ".join(info_raw) if isinstance(info_raw, list) else str(info_raw)
    intro     = item.get("intro") or ""
    texto     = f"{info_text} {intro}".strip()

    wa = re_wa_proximity(texto, es_html=False)
    if not wa:
        wa = extraer_telefono_bio(texto)

    m_email = re.search(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,8}", texto)
    if m_email and validar_email(m_email.group(0)):
        email = m_email.group(0).lower().strip()

    website = None
    web_raw = item.get("website") or ""
    if not web_raw:
        web_raw = next(
            (w for w in (item.get("websites") or [])
             if w and "google.com/maps" not in w and "bing.com/maps" not in w), ""
        )
    if web_raw and es_url_scrappeable(web_raw):
        website = web_raw

    return {"whatsapp": wa, "email": email, "facebook": fb_url, "website": website}

# ============================================================
# PARSEO LINKTREE
# ============================================================

def parsear_linktree(url: str) -> dict:
    log.info(f"  [linktree] {url}")
    html = fetch_html(url)
    if not html:
        return {}
    wa      = None
    website = None
    seen    = set()
    links  = re.findall(r'href=["\']([^"\']+)["\']', html)
    links += re.findall(r'https?://[^\s"\'<>]{10,}', html)
    for link in links:
        link = link.strip()
        if not link.startswith("http") or link in seen:
            continue
        seen.add(link)
        if not wa:
            n = extraer_numero_de_wame_url(link)
            if n:
                wa = n
                log.info(f"  [linktree] wzap: {n}")
                continue
        if not website and es_url_scrappeable(link):
            website = link
            log.info(f"  [linktree] website: {link}")
    if not wa:
        texto = re.sub(r"<[^>]+>", " ", html)
        wa = re_wa_proximity(texto)
    return {"whatsapp": wa, "website": website}

# ============================================================
# LLAMADAS APIFY
# ============================================================

def _run_actor(actor_id: str, run_input: dict) -> list:
    try:
        run = apify.actor(actor_id).call(run_input=run_input)
        if not run:
            return []
        dataset_id = (
            run["defaultDatasetId"] if isinstance(run, dict)
            else getattr(run, "default_dataset_id", None)
        )
        if not dataset_id:
            return []
        return list(apify.dataset(dataset_id).iterate_items())
    except Exception as e:
        log.error(f"  ✗ Apify error actor={actor_id}: {e}")
        return []


def scrape_instagram(username: str) -> dict:
    handle = username.lstrip("@").split("?")[0]
    if not RE_IG_HANDLE.match(handle):
        log.warning(f"  [ig-scraper] handle inválido '{handle}' — saltando")
        return {}
    log.info(f"  [ig-scraper] @{handle}")
    items = _run_actor(ACTOR_INSTAGRAM, {"usernames": [handle]})
    return parsear_instagram(items[0]) if items else {}


def scrape_facebook(fb_url: str) -> dict:
    log.info(f"  [fb-scraper] {fb_url}")
    items = _run_actor(ACTOR_FACEBOOK, {"startUrls": [{"url": fb_url}]})
    return parsear_facebook(items[0]) if items else {}



def scrape_gmaps_listing(place_id: str) -> dict:
    """
    Último recurso: scrapea el listing de Google Maps via Apify.
    El actor devuelve contactos (WA, IG, website) que la Places API no expone.
    """
    if not place_id:
        return {}
    gmaps_url = f"https://www.google.com/maps/place/?q=place_id:{place_id}"
    log.info(f"  [gmaps-scraper] {gmaps_url}")
    items = _run_actor(ACTOR_GMAPS, {
        "startUrls": [{"url": gmaps_url}],
        "maxReviews": 0,
        "includeImages": False,
        "language": "es",
    })
    if not items:
        log.info("  [gmaps-scraper] sin resultados")
        return {}

    import json
    item     = items[0]
    item_str = json.dumps(item, ensure_ascii=False)
    wa       = None
    ig       = None
    website  = None

    # 1. Links wa.me explícitos en cualquier campo del item
    for link in re.findall(r'https?://(?:wa\.me|api\.whatsapp\.com)[^\s"''<>]+', item_str, re.I):
        n = extraer_numero_de_wame_url(link)
        if n:
            wa = n
            log.info(f"  [gmaps-scraper] WA link: {n}")
            break

    # 2. Phone → normalizar como WA si no hay link
    if not wa:
        for field in ("phone", "phoneUnformatted", "additionalInfo"):
            raw = item.get(field) or ""
            if isinstance(raw, list):
                raw = " ".join(str(x) for x in raw)
            if raw:
                n = normalizar_whatsapp(str(raw))
                if n:
                    wa = n
                    log.info(f"  [gmaps-scraper] WA desde {field}: {n}")
                    break

    # 3. Instagram en campos de social profiles
    for field in ("socialProfiles", "socialMediaLinks", "socials", "social"):
        socials = item.get(field) or []
        for s in socials:
            url_s = s if isinstance(s, str) else (s.get("url") or s.get("link") or "")
            if "instagram.com" in url_s:
                h = extraer_ig_handle_de_url(url_s)
                if h:
                    ig = f"@{h}"
                    break
        if ig:
            break

    # 4. Instagram en texto completo del item (fallback)
    if not ig:
        for m in re.findall(r'instagram\.com/([a-zA-Z0-9._]{1,30})/?["\'\'\s]', item_str, re.I):
            handle = m.rstrip("/").split("?")[0]
            invalidos = {"p", "reel", "reels", "stories", "explore", ""}
            if handle.lower() not in invalidos and RE_IG_HANDLE.match(handle):
                ig = f"@{handle}"
                break

    # 5. Website scrappeable
    raw_web = item.get("website") or ""
    if raw_web and es_url_scrappeable(raw_web):
        website = raw_web

    log.info(f"  [gmaps-scraper] → wa:{wa or '—'} | ig:{ig or '—'} | web:{website or '—'}")
    return {"whatsapp": wa, "instagram": ig, "website": website}

# ============================================================
# BATCH IG / FB
# ============================================================

def batch_ig(leads: list[dict]) -> dict[str, dict | None]:
    """Un solo call Apify para todos los handles IG."""
    handles_map: dict[str, dict] = {}
    for lead in leads:
        h = extraer_ig_handle_de_url((lead.get("sitio_web") or "").strip())
        if h:
            handles_map[h.lower()] = lead
        else:
            log.warning(f"  [ig-batch] handle inválido: {lead['nombre']}")

    if not handles_map:
        return {l["id"]: None for l in leads}

    log.info(f"  [ig-batch] handles: {list(handles_map.keys())}")
    items = _run_actor(ACTOR_INSTAGRAM, {"usernames": list(handles_map.keys())})
    log.info(f"  [ig-batch] resultados: {len(items)}")

    results: dict[str, dict | None] = {}
    for item in items:
        username = (item.get("username") or "").lower()
        if username not in handles_map:
            continue
        lead = handles_map[username]
        r = parsear_instagram(item)
        website = r.pop("website", None)
        if website and es_url_scrappeable(website):
            log.info(f"  → IG bio website: {website} → IT1")
            r = merge(r, iteracion_1(website))
        reservas_url = r.pop("reservas_url", None)
        if reservas_url and not r.get("whatsapp"):
            r = merge(r, scrape_reservas(reservas_url))
        r["origen_contacto"] = "ig-auto"
        results[lead["id"]] = r
        log.info(f"  [ig-batch] {lead['nombre']}: wzap={r.get('whatsapp') or '—'}")

    for handle, lead in handles_map.items():
        if lead["id"] not in results:
            log.warning(f"  [ig-batch] sin resultado para @{handle}")
            results[lead["id"]] = None

    return results


def batch_fb(leads: list[dict]) -> dict[str, dict | None]:
    """Un solo call Apify para todas las páginas FB."""
    url_to_lead: dict[str, dict] = {}
    for lead in leads:
        url = (lead.get("sitio_web") or "").strip()
        if url:
            url_to_lead[url] = lead

    if not url_to_lead:
        return {l["id"]: None for l in leads}

    log.info(f"  [fb-batch] urls: {list(url_to_lead.keys())}")
    items = _run_actor(ACTOR_FACEBOOK, {"startUrls": [{"url": u} for u in url_to_lead]})
    log.info(f"  [fb-batch] resultados: {len(items)}")

    results: dict[str, dict | None] = {}
    for item in items:
        fb_url_resp = item.get("facebookUrl") or ""
        matched_lead = None
        for orig_url, lead in url_to_lead.items():
            if orig_url in fb_url_resp or fb_url_resp in orig_url:
                matched_lead = lead
                break
        if not matched_lead:
            for orig_url, lead in url_to_lead.items():
                if urlparse(orig_url).path.strip("/") == urlparse(fb_url_resp).path.strip("/"):
                    matched_lead = lead
                    break
        if not matched_lead:
            log.warning(f"  [fb-batch] sin match para {fb_url_resp}")
            continue
        r = parsear_facebook(item)
        website = r.pop("website", None)
        if website and es_url_scrappeable(website):
            r = merge(r, iteracion_1(website))
        r["origen_contacto"] = "fb-auto"
        results[matched_lead["id"]] = r
        log.info(f"  [fb-batch] {matched_lead['nombre']}: wzap={r.get('whatsapp') or '—'}")

    for lead in leads:
        if lead["id"] not in results:
            results[lead["id"]] = None

    return results

# ============================================================
# MERGE
# ============================================================

def merge(base: dict, nuevo: dict) -> dict:
    resultado = dict(base)
    for k, v in nuevo.items():
        if v and not resultado.get(k):
            resultado[k] = v
    return resultado

# ============================================================
# ITERACIÓN 1 — SITIO PROPIO
# ============================================================

def iteracion_1(sitio_web: str) -> dict:
    notas     = []
    resultado = {}
    sitio_web = resolver_url_shortener(sitio_web)

    r1 = scrape_simple(sitio_web)
    ok = r1.get("status_ok", False)
    resultado = merge(resultado, r1)
    notas.append(f"requests raíz: {'ok' if ok else 'sin respuesta'}")

    if not resultado.get("whatsapp") and not resultado.get("email"):
        base_url = limpiar_url_base(sitio_web)
        sub_ok   = False
        for sub in SUBPAGINAS_FALLBACK:
            r_sub = scrape_simple(base_url + sub)
            if r_sub.get("status_ok"):
                resultado = merge(resultado, r_sub)
                notas.append(f"requests {sub}: ok")
                sub_ok = True
                if resultado.get("whatsapp") and resultado.get("email"):
                    break
        if not sub_ok:
            notas.append("subpáginas: sin respuesta")

    if not resultado.get("whatsapp") and not resultado.get("email") and not resultado.get("instagram"):
        r3 = scrape_playwright(sitio_web)
        resultado = merge(resultado, r3)
        notas.append(
            f"playwright: wzap={'si' if r3.get('whatsapp') else 'no'} "
            f"email={'si' if r3.get('email') else 'no'}"
        )

    resultado["notas_enrich"]    = " | ".join(notas)
    resultado["origen_contacto"] = "web-auto"
    return resultado

# ============================================================
# ITERACIÓN 2 / 3 — IG / FB (single, para complementario)
# ============================================================

def iteracion_2(ig_handle: str, resultado_previo: dict = None) -> dict:
    r = scrape_instagram(ig_handle)
    if resultado_previo:
        r = merge(resultado_previo, r)
    website = r.pop("website", None)
    if website and es_url_scrappeable(website):
        r = merge(r, iteracion_1(website))
    reservas_url = r.pop("reservas_url", None)
    if reservas_url and not r.get("whatsapp"):
        r = merge(r, scrape_reservas(reservas_url))
    r["origen_contacto"] = "ig-auto"
    return r


def iteracion_3(fb_url: str, resultado_previo: dict = None) -> dict:
    r = scrape_facebook(fb_url)
    if resultado_previo:
        r = merge(resultado_previo, r)
    website = r.pop("website", None)
    if website and es_url_scrappeable(website):
        r = merge(r, iteracion_1(website))
    r["origen_contacto"] = "fb-auto"
    return r

# ============================================================
# ENRIQUECEDOR POR LEAD
# ============================================================

def enrich_lead(lead: dict) -> dict | None:
    """
    Retorna dict con campos listos para DB, o None si se salta.

    Regla telefono (FIX v5.5):
      Sin web + sin instagram + sin facebook → si 15- → WA
      Sin web + CON instagram o facebook    → NO inventar WA
    """
    sitio_web = (lead.get("sitio_web") or "").strip()
    ig_col    = (lead.get("instagram") or "").strip()
    fb_col    = (lead.get("facebook") or "").strip()
    place_id  = (lead.get("place_id") or "").strip()

    if not sitio_web:
        if not ig_col and not fb_col:
            tel = (lead.get("telefono") or "").strip()
            if es_celular_arg(tel):
                wa = normalizar_whatsapp(tel)
                if wa:
                    log.info(f"  → WA desde telefono (15-): {wa}")
                    return {
                        "whatsapp":            wa,
                        "link_wame":           construir_wame(wa),
                        "notas":               "wzap desde telefono google maps",
                        "origen_contacto":     "telefono-auto",
                        "enriquecido":         True,
                        "fecha_actualizacion": datetime.now(timezone.utc).isoformat(),
                    }
        # Último recurso: Google Maps listing scraper
        if place_id:
            log.info(f"  → Sin web/ig/fb — intentando GMaps scraper")
            gmaps = scrape_gmaps_listing(place_id)
            if gmaps.get("website") and es_url_scrappeable(gmaps["website"]):
                log.info(f"  → Website hallado en GMaps: {gmaps['website']} → IT1")
                sitio_web = gmaps["website"]
                # Fall through al bloque IT1 abajo
            elif gmaps.get("whatsapp") or gmaps.get("instagram"):
                wa_g  = gmaps.get("whatsapp")
                ig_g  = gmaps.get("instagram")
                ig_url_g = f"https://www.instagram.com/{ig_g.lstrip('@')}/" if ig_g else None
                if ig_g and not wa_g:
                    log.info(f"  → IG hallado en GMaps: {ig_g} → IT2")
                    r_ig = iteracion_2(ig_g)
                    wa_g   = r_ig.get("whatsapp") or wa_g
                    ig_url_g = r_ig.get("link_ig") or ig_url_g
                return {
                    "whatsapp":            wa_g,
                    "link_wame":           construir_wame(wa_g) if wa_g else None,
                    "instagram":           ig_g,
                    "link_ig":             ig_url_g,
                    "notas":               "via gmaps-scraper" + ("" if wa_g else " | sin whatsapp"),
                    "origen_contacto":     "web-auto",
                    "enriquecido":         True,
                    "fecha_actualizacion": datetime.now(timezone.utc).isoformat(),
                }

        else:
            log.info("  → Sin web pero tiene IG/FB — no se inventa WA desde telefono")
        if not sitio_web:
            return None

    es_ig       = any(d in sitio_web for d in DOMINIOS_IG)
    es_fb       = any(d in sitio_web for d in DOMINIOS_FB)
    es_lt       = es_linktree(sitio_web)
    es_reservas = es_reservas_url(sitio_web)
    resultado   = {}

    if es_ig:
        handle = extraer_ig_handle_de_url(sitio_web)
        if not handle:
            log.info(f"  → IG URL inválida — saltando")
            return None
        log.info(f"  → IT2 (IG): @{handle}")
        resultado = iteracion_2(handle)

    elif es_fb:
        log.info(f"  → IT3 (FB): {sitio_web}")
        resultado = iteracion_3(sitio_web)

    elif es_lt:
        log.info(f"  → Linktree: {sitio_web}")
        lt = parsear_linktree(sitio_web)
        if lt.get("whatsapp"):
            resultado["whatsapp"] = lt["whatsapp"]
        if lt.get("website"):
            log.info(f"  → Linktree website: {lt['website']} → IT1")
            r_it1 = iteracion_1(lt["website"])
            it1_notas = r_it1.get("notas_enrich", "")
            r_it1["notas_enrich"] = f"linktree→{it1_notas}" if it1_notas else f"linktree: {sitio_web}"
            resultado = merge(resultado, r_it1)
        resultado.setdefault("notas_enrich", f"linktree: {sitio_web}")
        resultado.setdefault("origen_contacto", "web-auto")

    elif es_reservas:
        log.info(f"  → IT reservas: {sitio_web}")
        resultado = scrape_reservas(sitio_web)

    else:
        log.info(f"  → IT1 (web): {sitio_web}")
        resultado = iteracion_1(sitio_web)
        if resultado.get("instagram") and not resultado.get("whatsapp"):
            log.info(f"  → IT2 complementario")
            resultado = iteracion_2(resultado["instagram"], resultado)
        if resultado.get("facebook") and not resultado.get("whatsapp"):
            log.info(f"  → IT3 complementario")
            resultado = iteracion_3(resultado["facebook"], resultado)

    wa           = resultado.get("whatsapp")
    email        = resultado.get("email")
    ig           = resultado.get("instagram")
    ig_url       = resultado.get("link_ig")
    fb           = resultado.get("facebook")
    emails_extra = resultado.get("emails_extra") or []
    notas_enrich = resultado.get("notas_enrich") or ""

    # LAST RESORT: si IT1/IT2/IT3 no encontró nada → GMaps scraper
    gmaps_nota = ""
    if not wa and not email and place_id:
        log.info(f"  → Last resort: GMaps scraper")
        gmaps_lr = scrape_gmaps_listing(place_id)
        if gmaps_lr.get("whatsapp"):
            wa = gmaps_lr["whatsapp"]
            gmaps_nota = "wzap via gmaps-scraper"
        if not ig and gmaps_lr.get("instagram"):
            ig = gmaps_lr["instagram"]
            ig_url = f"https://www.instagram.com/{ig.lstrip('@')}/"

    partes_notas = []
    if emails_extra:
        partes_notas.append(f"2do email: {emails_extra[0]}")
    if notas_enrich:
        partes_notas.append(notas_enrich)
    if gmaps_nota:
        partes_notas.append(gmaps_nota)
    if not wa and not email:
        partes_notas.append("sin contacto web")
    elif not wa:
        partes_notas.append("sin whatsapp")
    elif not email:
        partes_notas.append("sin email")

    estado = "completo" if (wa and email) else "parcial" if (wa or email) else "sin_datos"
    log.info(f"  → {estado} | wzap: {wa or '—'} | email: {email or '—'}")

    return {
        "whatsapp":            wa,
        "link_wame":           construir_wame(wa) if wa else None,
        "email":               email,
        "email_2":             emails_extra[0] if emails_extra else None,
        "instagram":           ig,
        "link_ig":             ig_url,
        "facebook":            fb,
        "notas":               " | ".join(partes_notas) or None,
        "origen_contacto":     resultado.get("origen_contacto", "web-auto"),
        "enriquecido":         True,
        "fecha_actualizacion": datetime.now(timezone.utc).isoformat(),
    }

# ============================================================
# FIX BAD RECORDS
# ============================================================

def fix_bad_records():
    log.info("\n=== FIX BAD RECORDS ===")
    result = supabase.table("leads").select(
        "id, nombre, instagram, link_ig, facebook, email"
    ).execute()
    fixes = 0
    for lead in result.data:
        campos_fix = {}
        ig = lead.get("instagram") or ""
        if ig and not RE_IG_HANDLE.match(ig.lstrip("@")):
            campos_fix["instagram"] = None
            campos_fix["link_ig"]   = None
        fb = lead.get("facebook") or ""
        if fb:
            fb_path = urlparse(fb).path.rstrip("/").lstrip("/").split("/")[0].lower()
            if "developers.facebook.com" in fb or fb_path in {"tr", "help", "pixel", "plugins", "ads", "policy"}:
                campos_fix["facebook"] = None
        email = lead.get("email") or ""
        if email and not validar_email(email):
            campos_fix["email"] = None
        if campos_fix:
            campos_fix["origen_contacto"] = "pendiente"
            campos_fix["enriquecido"]     = False
            supabase.table("leads").update(campos_fix).eq("id", lead["id"]).execute()
            log.info(f"  Limpiado: {lead['nombre']}")
            fixes += 1
    log.info(f"  Registros corregidos: {fixes}")

# ============================================================
# REPORTE
# ============================================================

def generar_reporte(output_path: str = "eurocrem_debug_report_v5.txt"):
    leads = supabase.table("leads").select(
        "nombre, barrio, sitio_web, whatsapp, email, instagram, facebook, notas, origen_contacto, enriquecido"
    ).execute().data
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M")

    completos  = [l for l in leads if l.get("whatsapp") and l.get("email")]
    parciales  = [l for l in leads if (l.get("whatsapp") or l.get("email"))
                  and not (l.get("whatsapp") and l.get("email")) and l.get("enriquecido")]
    sin_datos  = [l for l in leads if not l.get("whatsapp") and not l.get("email") and l.get("enriquecido")]
    pendientes = [l for l in leads if l.get("origen_contacto") == "pendiente"]

    lines = [
        f"EUROCREM — Debug Report v5.5 — {ahora}",
        f"Total: {len(leads)} | Completos: {len(completos)} | Parciales: {len(parciales)} | Sin datos: {len(sin_datos)} | Pendientes: {len(pendientes)}",
        "",
    ]

    def sec(titulo, items, fmt):
        lines.extend([f"\n{'='*60}", titulo, "="*60] + [fmt(l) for l in items])

    sec("COMPLETOS", completos,
        lambda l: f"  {l['nombre']} | wzap: {l.get('whatsapp')} | email: {l.get('email')}")
    sec("PARCIALES", parciales,
        lambda l: f"  {l['nombre']} | wzap: {l.get('whatsapp') or '—'} | email: {l.get('email') or '—'} | {l.get('notas') or ''}")
    sec("SIN DATOS", sin_datos,
        lambda l: f"  {l['nombre']} | {l.get('sitio_web') or 'sin web'} | {l.get('notas') or ''}")
    sec("PENDIENTES", pendientes,
        lambda l: f"  {l['nombre']} | {l.get('sitio_web') or 'sin web'}")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log.info(f"Reporte guardado: {output_path}")

# ============================================================
# GUARDAR RESULTADO — filtra a columnas DB válidas
# ============================================================

def _normalizar_para_db(campos: dict) -> dict:
    """
    Convierte campos internos (notas_enrich, emails_extra, status_ok…)
    al formato de columnas reales de Supabase, y filtra las demás.
    """
    # notas_enrich + emails_extra → notas
    if "notas_enrich" in campos or "emails_extra" in campos:
        partes = []
        emails_extra = campos.get("emails_extra") or []
        if emails_extra:
            partes.append(f"2do email: {emails_extra[0]}")
        notas_enrich = campos.get("notas_enrich") or ""
        if notas_enrich:
            partes.append(notas_enrich)
        wa    = campos.get("whatsapp")
        email = campos.get("email")
        if not wa and not email:
            partes.append("sin contacto web")
        elif not wa:
            partes.append("sin whatsapp")
        elif not email:
            partes.append("sin email")
        campos.setdefault("notas", " | ".join(partes) or None)

    # link_wame si falta
    if campos.get("whatsapp") and not campos.get("link_wame"):
        campos["link_wame"] = construir_wame(campos["whatsapp"])

    # Campos obligatorios
    campos["enriquecido"]         = True
    campos["fecha_actualizacion"] = datetime.now(timezone.utc).isoformat()

    # Filtrar solo columnas DB válidas
    return {k: v for k, v in campos.items() if k in DB_COLUMNS}


def _guardar_resultado(lead: dict, campos: dict | None, stats: dict):
    if campos is None:
        stats["saltado"] += 1
        return

    campos_db = _normalizar_para_db(campos)

    wa, email = campos_db.get("whatsapp"), campos_db.get("email")
    if wa and email:   stats["completo"] += 1
    elif wa or email:  stats["parcial"]  += 1
    else:              stats["sin_datos"] += 1

    supabase.table("leads").update(campos_db).eq("id", lead["id"]).execute()
    log.info(f"  ✓ Guardado: {lead['nombre']}")

# ============================================================
# HELPER THREAD
# ============================================================

def _process_web_lead(lead: dict) -> tuple[str, dict | None]:
    try:
        campos = enrich_lead(lead)
        return lead["id"], campos
    except Exception as e:
        log.error(f"  ✗ Error en {lead['nombre']}: {e}")
        return lead["id"], None

# ============================================================
# RUNNERS
# ============================================================

def run(ids_filter: list[str] | None = None):
    query = supabase.table("leads").select(
        "id, nombre, barrio, sitio_web, instagram, facebook, telefono, origen_contacto, place_id"
    )
    if ids_filter:
        query = query.in_("id", ids_filter)
    else:
        query = query.eq("origen_contacto", "pendiente")
    leads = query.execute().data

    total = len(leads)
    log.info(f"\n{'='*60}\nEUROCREM v5.5 — {total} leads\n{'='*60}")
    stats = {"completo": 0, "parcial": 0, "sin_datos": 0, "saltado": 0, "error": 0}

    ig_leads    = [l for l in leads if any(d in (l.get("sitio_web") or "") for d in DOMINIOS_IG)]
    fb_leads    = [l for l in leads if any(d in (l.get("sitio_web") or "") for d in DOMINIOS_FB)]
    otros_leads = [l for l in leads if l not in ig_leads and l not in fb_leads]

    log.info(f"  IG: {len(ig_leads)} | FB: {len(fb_leads)} | Otros: {len(otros_leads)}")

    # BATCH IG
    if ig_leads:
        log.info(f"\n[BATCH IG] {len(ig_leads)} leads")
        for lead, campos in batch_ig(ig_leads).items():
            lead_obj = next(l for l in ig_leads if l["id"] == lead)
            _guardar_resultado(lead_obj, campos, stats)
            time.sleep(DELAY_ENTRE_LEADS)

    # BATCH FB
    if fb_leads:
        log.info(f"\n[BATCH FB] {len(fb_leads)} leads")
        for lead, campos in batch_fb(fb_leads).items():
            lead_obj = next(l for l in fb_leads if l["id"] == lead)
            _guardar_resultado(lead_obj, campos, stats)
            time.sleep(DELAY_ENTRE_LEADS)

    # PARALLEL WEB + SEQUENTIAL SIN WEB
    con_web   = [l for l in otros_leads if (l.get("sitio_web") or "").strip()]
    sin_web_l = [l for l in otros_leads if not (l.get("sitio_web") or "").strip()]

    if con_web:
        log.info(f"\n[PARALLEL WEB] {len(con_web)} leads — {WEB_MAX_WORKERS} workers")
        resultados_web: dict[str, dict | None] = {}
        with ThreadPoolExecutor(max_workers=WEB_MAX_WORKERS) as executor:
            futures = {executor.submit(_process_web_lead, l): l for l in con_web}
            for future in as_completed(futures):
                lead_id, campos = future.result()
                resultados_web[lead_id] = campos
        for lead in con_web:
            _guardar_resultado(lead, resultados_web.get(lead["id"]), stats)

    if sin_web_l:
        log.info(f"\n[SIN WEB] {len(sin_web_l)} leads")
        for lead in sin_web_l:
            log.info(f"\n  {lead['nombre']}")
            try:
                _guardar_resultado(lead, enrich_lead(lead), stats)
            except Exception as e:
                log.error(f"  ✗ Error: {e}")
                stats["error"] += 1
            time.sleep(DELAY_ENTRE_LEADS)

    log.info(
        f"\n{'='*60}\nRESUMEN:\n"
        f"  Completos: {stats['completo']} | Parciales: {stats['parcial']} | "
        f"Sin datos: {stats['sin_datos']} | Saltados: {stats['saltado']} | Errores: {stats['error']}"
        f"\n{'='*60}"
    )


def run_sin_wa(ids_filter: list[str] | None = None):
    """Re-procesa leads enriquecidos sin whatsapp."""
    query = supabase.table("leads").select(
        "id, nombre, barrio, sitio_web, instagram, facebook, telefono, origen_contacto, place_id"
    ).is_("whatsapp", "null").eq("enriquecido", True)
    if ids_filter:
        query = query.in_("id", ids_filter)
    leads = [l for l in query.execute().data if (l.get("sitio_web") or "").strip()]

    total = len(leads)
    log.info(f"\n{'='*60}\nRUN SIN WA — {total} leads\n{'='*60}")
    stats = {"completo": 0, "parcial": 0, "sin_datos": 0, "saltado": 0, "error": 0}

    ig_leads = [l for l in leads if any(d in (l.get("sitio_web") or "") for d in DOMINIOS_IG)]
    fb_leads = [l for l in leads if any(d in (l.get("sitio_web") or "") for d in DOMINIOS_FB)]
    otros    = [l for l in leads if l not in ig_leads and l not in fb_leads]

    if ig_leads:
        for lead, campos in batch_ig(ig_leads).items():
            lead_obj = next(l for l in ig_leads if l["id"] == lead)
            _guardar_resultado(lead_obj, campos, stats)
    if fb_leads:
        for lead, campos in batch_fb(fb_leads).items():
            lead_obj = next(l for l in fb_leads if l["id"] == lead)
            _guardar_resultado(lead_obj, campos, stats)
    if otros:
        resultados: dict[str, dict | None] = {}
        with ThreadPoolExecutor(max_workers=WEB_MAX_WORKERS) as executor:
            futures = {executor.submit(_process_web_lead, l): l for l in otros}
            for future in as_completed(futures):
                lead_id, campos = future.result()
                resultados[lead_id] = campos
        for lead in otros:
            _guardar_resultado(lead, resultados.get(lead["id"]), stats)

    log.info(f"\nRESUMEN sinwa: {stats}")

# ============================================================
# ENTRY POINT
# ============================================================


def run_sin_contacto():
    """
    Re-procesa leads sin ningún dato de contacto encontrado:
      - enriquecido=True, whatsapp IS NULL, email IS NULL
      - origen_contacto='pendiente', sin web/ig/fb (los 3 stuck)
    Aplica el GMaps scraper como último recurso en ambos casos.
    """
    q1 = supabase.table("leads").select("id").eq("enriquecido", True) \
        .is_("whatsapp", "null").is_("email", "null").execute().data
    q2 = supabase.table("leads").select("id").eq("origen_contacto", "pendiente") \
        .is_("sitio_web", "null").is_("instagram", "null").is_("facebook", "null").execute().data

    ids = list({l["id"] for l in q1 + q2})
    log.info(f"\nsincontacto: {len(ids)} leads (enriquecidos sin datos: {len(q1)} | pendientes sin web: {len(q2)})")
    if not ids:
        log.info("  Nada que procesar.")
        return
    run(ids_filter=ids)
    generar_reporte()

if __name__ == "__main__":
    modo = sys.argv[1].lower() if len(sys.argv) > 1 else "full"
    log.info(f"=== EUROCREM v5.5 START — modo={modo} | Playwright={PLAYWRIGHT_OK} ===")

    if modo == "reporte":
        generar_reporte()

    elif modo == "sinwa":
        run_sin_wa()
        generar_reporte()

    elif modo == "ids":
        if len(sys.argv) < 3:
            print("Uso: python eurocrem_enrich_v5.5.py ids ID1,ID2,ID3")
            sys.exit(1)
        ids = [x.strip() for x in sys.argv[2].split(",") if x.strip()]
        log.info(f"  IDs: {ids}")
        run(ids_filter=ids)
        generar_reporte()

    elif modo == "sincontacto":
        run_sin_contacto()

    elif modo == "full":
        fix_bad_records()
        run()
        generar_reporte()

    else:
        print(f"Modo desconocido: {modo}")
        print("Uso: python eurocrem_enrich_v5.5.py [full|sinwa|sincontacto|reporte|ids ID1,ID2,...]")
        sys.exit(1)

    log.info("=== EUROCREM v5.5 DONE ===")

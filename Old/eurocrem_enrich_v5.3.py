"""
EUROCREM — eurocrem_enrich_v5.3.py
Versión: 5.3 — 07/06/2026

Novedades v5.3:
  - NUEVO: es_celular_arg() + normalizar_telefono_gm() — detecta celulares argentinos
    en el campo telefono de Google Maps y los usa como WA de último recurso.
    Cubre: "011 15-XXXX-XXXX" (15 explícito), y abonados 2xxx/3xxx/6xxx/7xxx (móviles BsAs).
    Impacto directo: Cucina Paradiso, Rosetta, Raggio, República del Fuego, Hierro.
  - FIX: scrape_reservas — RAG se activa cuando no hay WA/email (no solo cuando no hay HTML).
    Antes: Playwright devuelve shell de Meitre/Apparta (html ≠ None) → RAG no corría.
    Ahora: si html existe pero wa=None y email=None → igual intenta RAG.
  - NUEVO: IT2 complementario en rama FB — si el scraper de FB encuentra IG pero no WA,
    corre el scraper de Instagram de Apify (ayuda a Il Matterello y similares).
  - NUEVO: run_sin_wa() — reprocesa leads ya enriquecidos (enriquecido=true) que no tienen
    WhatsApp: aplica telefono fallback y re-corre RAG en reservas si aplica.

Cambios heredados de v5.2:
  - email_2 como campo propio en Supabase
  - parsear_linktree con fallback a Playwright
  - SUBPAGINAS_FALLBACK ampliado
  - _scrape_con_apify_rag() como último recurso
  - scrape_reservas: requests → Playwright → RAG
  - iteracion_1: RAG cuando raíz + subpáginas fallan (Cloudflare)
  - parsear_instagram: busca URLs de reservas en texto de bio
  - fix_bad_records: normaliza precio="" y telefono="" a NULL

Dependencias:
  pip install playwright --break-system-packages
  playwright install chromium
"""

import os
import re
import time
import logging
import requests as req_lib
from datetime import datetime, timezone
from urllib.parse import urlparse, unquote

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
ACTOR_RAG       = "apify/rag-web-browser"

SUBPAGINAS_FALLBACK = [
    "/qr/", "/reservas", "/contacto", "/menu", "/contacto/",
    "/nosotros", "/sobre-nosotros", "/quienes-somos",
    "/contactanos", "/pedidos", "/local",
]

DELAY_ENTRE_LEADS = 1

HTTP_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
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
# DOMINIOS Y FILTROS
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
                log.info(f"  → Resuelto a: {final}")
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
# NORMALIZADORES DE WHATSAPP
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


def normalizar_telefono_gm(tel: str) -> str | None:
    """
    Normaliza teléfonos en formato Google Maps Argentina para uso como WhatsApp.
    Maneja el caso "011 15-XXXX-XXXX" (prefijo 15 explícito de celular) que el
    normalizador estándar no procesa correctamente.

    Ejemplos:
      "011 15-2788-9114"  → "+54 9 11 2788-9114"
      "011 3082-3055"     → "+54 9 11 3082-3055"  (si es celular)
      "011 4831-8493"     → None (landline típico — 4xxx BsAs)
    """
    if not tel:
        return None

    # Intentar normalizador estándar primero
    n = normalizar_whatsapp(tel)
    if n:
        return n

    digits = re.sub(r"[^\d]", "", tel)

    # Formato: 0 + área + 15 + 8 dígitos
    # ej: "01115278 89114" = 0-11-15-2788-9114
    if digits.startswith("0") and len(digits) >= 13:
        sin_trunk = digits[1:]
        # Buenos Aires (área "11", 2 dígitos)
        if sin_trunk.startswith("11") and sin_trunk[2:4] == "15":
            subscriber = sin_trunk[4:]
            if len(subscriber) == 8:
                return f"+54 9 11 {subscriber[:4]}-{subscriber[4:]}"
        # Interior (área 3 dígitos)
        elif len(sin_trunk) >= 14 and sin_trunk[3:5] == "15":
            area = sin_trunk[:3]
            subscriber = sin_trunk[5:]
            if len(subscriber) == 8:
                return f"+54 9 {area} {subscriber[:4]}-{subscriber[4:]}"

    return None


def es_celular_arg(tel: str) -> bool:
    """
    Solo marca como celular si hay certeza: prefijo 15 explícito en el número.
    No inferimos por el primer dígito del abonado — en Argentina no es un
    indicador confiable (3xxx y 6xxx pueden ser fijos o móviles según la asignación).
    """
    if not tel:
        return False
    # "15-" o "15 " después del código de área → celular seguro
    return bool(re.search(r'\b15[-\s]', tel))


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
    """
    Extrae número de WhatsApp de URLs wa.me o api.whatsapp.com.
    Maneja el patrón phone=%3C+NUMBER%3E (angle brackets en URL encoding)
    generado por plugins de WordPress/Elementor — ej:
      https://api.whatsapp.com/send?phone=%3C+5491125011888%3E
    que decodifica a: phone=<+5491125011888>
    """
    if not url:
        return None
    url_decoded = unquote(url)
    # [<(\s]* tolera angle brackets, paréntesis o espacios entre phone= y el número
    m = re.search(r"(?:wa\.me/|phone=)[<(\s]*\+?(\d{7,15})", url_decoded, re.I)
    return normalizar_whatsapp(m.group(1)) if m else None


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
# REGEXES DE TELÉFONO / WHATSAPP
# ============================================================

RE_WA_NUMBER = re.compile(
    r"""(?x)
    (?:\+?54[\s\-]?9[\s\-]?|\+?549|0?9?)
    (?:11|15|2\d{2}|3\d{2})
    [\s\-]?\d{4}[\s\-]?\d{4}
    """,
    re.VERBOSE,
)

RE_TELEFONO_BIO = re.compile(
    r"""(?x)
    (?:\+?54[\s\-]?9?[\s\-]?)?
    ((?:11|15|2\d{2}|3\d{2})
    [\s\-]?\d{4}[\s\-]?\d{4})
    """,
    re.VERBOSE,
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
# EXTRACCIÓN DE CONTACTOS DESDE HTML / TEXTO
# ============================================================

def extraer_contactos_de_html(html: str) -> dict:
    wa      = None
    email   = None
    ig      = None
    ig_url  = None
    fb      = None
    todos_emails = []

    for link in re.findall(r'https?://(?:wa\.me|api\.whatsapp\.com)[^\s"\'<>]*', html, re.I):
        n = extraer_numero_de_wame_url(link)
        if n:
            wa = n
            break

    if not wa:
        wa = re_wa_proximity(html, es_html=True)

    for e in re.findall(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,8}', html):
        if validar_email(e):
            todos_emails.append(e.lower().strip())
    todos_emails = list(dict.fromkeys(todos_emails))
    keywords = ["reserva", "contacto", "info", "eventos", "ventas", "hola", "admin"]
    todos_emails.sort(key=lambda e: next((i for i, k in enumerate(keywords) if k in e), 99))
    if todos_emails:
        email = todos_emails[0]

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

    for path_raw in re.findall(
        r'facebook\.com/([^?\s"\'<>/][^?\s"\'<>]*)/?(?:["\'\s?]|$)', html, re.I
    ):
        path = path_raw.rstrip("/").split("?")[0]
        paths_inv = {
            "sharer", "share", "dialog", "login", "watch",
            "groups", "events", "developers", "docs",
            "help", "tr", "pixel", "plugins", "pages", "ads",
            "policy", "privacy", "legal", "terms", "",
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


def extraer_contactos_de_texto(texto: str) -> dict:
    """Para texto plano / markdown (output del RAG browser)."""
    wa    = None
    email = None
    todos_emails = []

    for link in re.findall(r'https?://(?:wa\.me|api\.whatsapp\.com)[^\s"\'<>]*', texto, re.I):
        n = extraer_numero_de_wame_url(link)
        if n:
            wa = n
            break

    if not wa:
        wa = re_wa_proximity(texto, es_html=False)
    if not wa:
        wa = extraer_telefono_bio(texto)

    for e in re.findall(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,8}', texto):
        if validar_email(e):
            todos_emails.append(e.lower().strip())
    todos_emails = list(dict.fromkeys(todos_emails))
    keywords = ["reserva", "contacto", "info", "eventos", "ventas", "hola", "admin"]
    todos_emails.sort(key=lambda e: next((i for i, k in enumerate(keywords) if k in e), 99))
    if todos_emails:
        email = todos_emails[0]

    return {
        "whatsapp":     wa,
        "email":        email,
        "emails_extra": todos_emails[1:3],
        "status_ok":    bool(wa or email),
    }

# ============================================================
# SCRAPERS PROPIOS
# ============================================================

def fetch_html(url: str, timeout: int = 10) -> str | None:
    try:
        r = req_lib.get(url, headers=HTTP_HEADERS, timeout=timeout,
                        allow_redirects=True)
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
        log.info(f"  [playwright] ok — wzap: {result.get('whatsapp') or '—'}")
        return result
    except Exception as e:
        log.error(f"  [playwright] error: {e}")
        return {"status_ok": False}


def _playwright_get_html(url: str) -> str | None:
    if not PLAYWRIGHT_OK:
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=HTTP_HEADERS["User-Agent"])
            page.goto(url, wait_until="networkidle", timeout=25_000)
            html = page.content()
            browser.close()
            return html
    except Exception as e:
        log.error(f"  [playwright-html] error: {e}")
        return None

# ============================================================
# APIFY RAG BROWSER
# Último recurso: bypasea Cloudflare y sitios con JS asíncrono complejo.
# ============================================================

def _scrape_con_apify_rag(url: str) -> dict:
    log.info(f"  [rag-browser] {url}")
    try:
        run = apify.actor(ACTOR_RAG).call(run_input={
            "query":             url,
            "outputFormats":     ["markdown"],
            "scrapingTool":      "browser-playwright",
            "maxResults":        1,
            "requestTimeoutSecs": 60,
        })
        if not run:
            return {"status_ok": False}
        dataset_id = (
            run["defaultDatasetId"] if isinstance(run, dict)
            else getattr(run, "default_dataset_id", None)
        )
        if not dataset_id:
            return {"status_ok": False}
        items = list(apify.dataset(dataset_id).iterate_items())
        if not items:
            return {"status_ok": False}
        texto = items[0].get("markdown") or items[0].get("text") or ""
        if not texto:
            return {"status_ok": False}
        log.info(f"  [rag-browser] ok — {len(texto)} chars")
        return extraer_contactos_de_texto(texto)
    except Exception as e:
        log.error(f"  [rag-browser] error: {e}")
        return {"status_ok": False}

# ============================================================
# SCRAPER PLATAFORMAS DE RESERVAS
# v5.3 FIX: RAG se activa cuando wa=None Y email=None,
#           no solo cuando html=None. Playwright devuelve el shell
#           de Meitre/Apparta (html ≠ None) pero sin el widget de contacto.
# ============================================================

def scrape_reservas(url: str) -> dict:
    log.info(f"  [reservas] {url}")
    html = fetch_html(url)

    if not html and PLAYWRIGHT_OK:
        log.info(f"  [reservas→playwright] {url}")
        html = _playwright_get_html(url)

    wa    = None
    email = None

    if html:
        for link in re.findall(r'https?://(?:wa\.me|api\.whatsapp\.com)[^\s"\'<>]*', html, re.I):
            n = extraer_numero_de_wame_url(link)
            if n:
                wa = n
                break
        if not wa:
            wa = re_wa_proximity(html, es_html=True)
        if not wa:
            texto_limpio = re.sub(r"<[^>]+>", " ", html)
            texto_limpio = re.sub(r"\s+", " ", texto_limpio)
            wa = extraer_telefono_bio(texto_limpio)
        emails = [e.lower().strip() for e in
                  re.findall(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,8}', html)
                  if validar_email(e)]
        emails = list(dict.fromkeys(emails))
        if emails:
            email = emails[0]

    # v5.3 FIX: RAG aunque hayamos obtenido HTML (puede ser shell sin datos)
    if not wa and not email:
        log.info(f"  [reservas→rag] sin contacto tras requests+Playwright — usando RAG")
        r_rag = _scrape_con_apify_rag(url)
        if r_rag.get("status_ok"):
            wa    = wa    or r_rag.get("whatsapp")
            email = email or r_rag.get("email")

    log.info(f"  [reservas] wzap: {wa or '—'} | email: {email or '—'}")
    return {
        "whatsapp":        wa,
        "email":           email,
        "notas_enrich":    f"reservas: {_netloc(url)}",
        "origen_contacto": "web-auto",
        "status_ok":       True,
    }

# ============================================================
# PARSEO LINKTREE — con fallback a Playwright
# ============================================================

def parsear_linktree(url: str) -> dict:
    log.info(f"  [linktree] {url}")
    html = fetch_html(url)
    if not html:
        log.info(f"  [linktree→playwright] {url}")
        html = _playwright_get_html(url)
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
                    log.info(f"  → WA desde reservas IG (link): {wa}")
                    break

    # v5.2+: también buscar URLs de reservas en el texto de bio
    if not wa:
        for dominio in DOMINIOS_RESERVAS:
            m = re.search(
                rf'https?://(?:[\w\-]+\.)?{re.escape(dominio)}/\S+', bio, re.I
            )
            if m:
                reservas_url_bio = m.group(0).rstrip(")")
                log.info(f"  → Reservas URL en bio texto: {reservas_url_bio}")
                r_res = scrape_reservas(reservas_url_bio)
                if r_res.get("whatsapp"):
                    wa = r_res["whatsapp"]
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
    wa    = None
    email = None
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
             if w and "google.com/maps" not in w and "bing.com/maps" not in w),
            ""
        )
    if web_raw and es_url_scrappeable(web_raw):
        website = web_raw

    # IG encontrado en la página de FB
    ig      = None
    ig_url  = None
    ig_raw  = item.get("instagramUsername") or item.get("instagram") or ""
    if ig_raw:
        handle = ig_raw.lstrip("@")
        if RE_IG_HANDLE.match(handle):
            ig     = f"@{handle}"
            ig_url = f"https://www.instagram.com/{handle}/"

    return {
        "whatsapp": wa,
        "email":    email,
        "facebook": fb_url,
        "website":  website,
        "instagram": ig,
        "link_ig":   ig_url,
    }

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

    # RAG solo cuando raíz Y subpáginas fallaron (patrón Cloudflare)
    raiz_fallo    = "requests raíz: sin respuesta" in notas
    subpags_fallo = "subpáginas: sin respuesta"    in notas
    if raiz_fallo and subpags_fallo and not resultado.get("whatsapp") and not resultado.get("email"):
        log.info(f"  → Sitio bloqueado (Cloudflare?) — usando RAG browser")
        r_rag = _scrape_con_apify_rag(sitio_web)
        if r_rag.get("status_ok"):
            resultado = merge(resultado, r_rag)
            notas.append(
                f"rag: wzap={'si' if r_rag.get('whatsapp') else 'no'} "
                f"email={'si' if r_rag.get('email') else 'no'}"
            )
        else:
            notas.append("rag: sin respuesta")

    resultado["notas_enrich"]    = " | ".join(notas)
    resultado["origen_contacto"] = "web-auto"
    return resultado

# ============================================================
# ITERACIÓN 2 — INSTAGRAM (Apify)
# ============================================================

def iteracion_2(ig_handle: str, resultado_previo: dict = None) -> dict:
    r = scrape_instagram(ig_handle)
    if resultado_previo:
        r = merge(resultado_previo, r)

    website = r.pop("website", None)
    if website and es_url_scrappeable(website):
        log.info(f"  → IG bio website: {website} → IT1")
        r = merge(r, iteracion_1(website))

    reservas_url = r.pop("reservas_url", None)
    if reservas_url and not r.get("whatsapp"):
        log.info(f"  → IG bio reservas: {reservas_url}")
        r_res = scrape_reservas(reservas_url)
        r = merge(r, r_res)

    r["origen_contacto"] = "ig-auto"
    return r

# ============================================================
# ITERACIÓN 3 — FACEBOOK (Apify)
# v5.3: agrega IT2 complementario si FB page tiene IG pero no WA
# ============================================================

def iteracion_3(fb_url: str, resultado_previo: dict = None) -> dict:
    r = scrape_facebook(fb_url)
    if resultado_previo:
        r = merge(resultado_previo, r)

    website = r.pop("website", None)
    if website and es_url_scrappeable(website):
        log.info(f"  → FB page website: {website} → IT1")
        r = merge(r, iteracion_1(website))

    # v5.3: IT2 complementario si FB tiene IG pero sin WA
    if r.get("instagram") and not r.get("whatsapp"):
        log.info(f"  → IT2 complementario desde FB: {r['instagram']}")
        r = iteracion_2(r["instagram"], r)

    r["origen_contacto"] = "fb-auto"
    return r

# ============================================================
# ENRIQUECEDOR POR LEAD
# ============================================================

def enrich_lead(lead: dict) -> dict | None:
    sitio_web = (lead.get("sitio_web") or "").strip()

    if not sitio_web:
        log.info("  → Sin sitio web — saltando")
        return None

    es_ig       = any(d in sitio_web for d in DOMINIOS_IG)
    es_fb       = any(d in sitio_web for d in DOMINIOS_FB)
    es_lt       = es_linktree(sitio_web)
    es_reservas = es_reservas_url(sitio_web)

    resultado = {}

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
            r_it1["notas_enrich"] = (
                f"linktree→{it1_notas}" if it1_notas else f"linktree: {sitio_web}"
            )
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
    email_2      = emails_extra[0] if emails_extra else None

    # v5.3: fallback — teléfono Google Maps como WA si es celular
    wa_desde_gm = False
    if not wa:
        tel_gm = (lead.get("telefono") or "").strip()
        if tel_gm and es_celular_arg(tel_gm):
            wa_candidate = normalizar_telefono_gm(tel_gm)
            if wa_candidate:
                wa = wa_candidate
                wa_desde_gm = True
                log.info(f"  → WA desde telefono Google Maps: {wa}")

    partes_notas = []
    if notas_enrich:
        partes_notas.append(notas_enrich)
    if wa_desde_gm:
        partes_notas.append("wzap desde google maps")
    if not wa and not email:
        partes_notas.append("sin contacto web")
    elif not wa:
        partes_notas.append("sin whatsapp")
    elif not email:
        partes_notas.append("sin email")

    estado = (
        "completo" if (wa and email)
        else "parcial" if (wa or email)
        else "sin_datos"
    )
    log.info(f"  → {estado} | wzap: {wa or '—'} | email: {email or '—'} | email_2: {email_2 or '—'}")

    return {
        "whatsapp":            wa,
        "link_wame":           construir_wame(wa) if wa else None,
        "email":               email,
        "email_2":             email_2,
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
        "id, nombre, instagram, link_ig, facebook, email, email_2, precio, telefono"
    ).execute()
    fixes = 0
    for lead in result.data:
        campos_fix = {}

        for campo in ("precio", "telefono"):
            if lead.get(campo) == "":
                campos_fix[campo] = None

        ig = lead.get("instagram") or ""
        if ig and not RE_IG_HANDLE.match(ig.lstrip("@")):
            log.info(f"  Limpiando IG inválido: {lead['nombre']} → {ig!r}")
            campos_fix["instagram"] = None
            campos_fix["link_ig"]   = None

        fb = lead.get("facebook") or ""
        if fb:
            fb_path = urlparse(fb).path.rstrip("/").lstrip("/").split("/")[0].lower()
            fb_bad = (
                "developers.facebook.com" in fb
                or fb_path in {"tr", "help", "pixel", "plugins", "ads", "policy"}
            )
            if fb_bad:
                log.info(f"  Limpiando FB inválido: {lead['nombre']} → {fb!r}")
                campos_fix["facebook"] = None

        for campo_email in ("email", "email_2"):
            e = lead.get(campo_email) or ""
            if e and not validar_email(e):
                log.info(f"  Limpiando {campo_email} inválido: {lead['nombre']} → {e!r}")
                campos_fix[campo_email] = None

        if campos_fix:
            contacto_sucio = any(k in campos_fix for k in
                                 ("instagram", "link_ig", "facebook", "email", "email_2"))
            if contacto_sucio:
                campos_fix["origen_contacto"] = "pendiente"
                campos_fix["enriquecido"]     = False
            supabase.table("leads").update(campos_fix).eq("id", lead["id"]).execute()
            fixes += 1
    log.info(f"  Registros corregidos: {fixes}")

# ============================================================
# REPORTE
# ============================================================

def generar_reporte(output_path: str = "eurocrem_debug_report_v5.txt"):
    leads = supabase.table("leads").select(
        "nombre, barrio, sitio_web, whatsapp, email, email_2, "
        "instagram, facebook, notas, origen_contacto, enriquecido"
    ).execute().data
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M")

    completos  = [l for l in leads if l.get("whatsapp") and l.get("email")]
    parciales  = [l for l in leads if (l.get("whatsapp") or l.get("email"))
                  and not (l.get("whatsapp") and l.get("email")) and l.get("enriquecido")]
    sin_datos  = [l for l in leads if not l.get("whatsapp") and not l.get("email") and l.get("enriquecido")]
    pendientes = [l for l in leads if l.get("origen_contacto") == "pendiente"]

    lines = [
        f"EUROCREM — Debug Report v5.3 — {ahora}",
        f"Total: {len(leads)} | Completos: {len(completos)} | Parciales: {len(parciales)} | "
        f"Sin datos: {len(sin_datos)} | Pendientes: {len(pendientes)}",
        "",
    ]

    def fmt_completo(l):
        e2 = f" | email_2: {l['email_2']}" if l.get("email_2") else ""
        return f"  {l['nombre']} | wzap: {l.get('whatsapp')} | email: {l.get('email')}{e2}"

    def fmt_parcial(l):
        e2 = f" | email_2: {l['email_2']}" if l.get("email_2") else ""
        return (
            f"  {l['nombre']} | wzap: {l.get('whatsapp') or '—'} | "
            f"email: {l.get('email') or '—'}{e2} | {l.get('notas') or ''}"
        )

    def sec(titulo, items, fmt):
        lines.extend([f"\n{'='*60}", titulo, "="*60] + [fmt(l) for l in items])

    sec("COMPLETOS", completos, fmt_completo)
    sec("PARCIALES", parciales, fmt_parcial)
    sec("SIN DATOS", sin_datos,
        lambda l: f"  {l['nombre']} | {l.get('sitio_web') or 'sin web'} | {l.get('notas') or ''}")
    sec("PENDIENTES", pendientes,
        lambda l: f"  {l['nombre']} | {l.get('sitio_web') or 'sin web'}")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log.info(f"Reporte guardado: {output_path}")

# ============================================================
# RUNNER — procesa leads con origen_contacto = "pendiente"
# ============================================================

def run():
    # v5.3: seleccionamos telefono para el fallback celular
    leads = supabase.table("leads").select(
        "id, nombre, barrio, sitio_web, instagram, facebook, origen_contacto, telefono"
    ).eq("origen_contacto", "pendiente").execute().data

    total = len(leads)
    log.info(f"\n{'='*60}\nEUROCREM enrich_v5.3 — {total} leads\n{'='*60}")
    stats = {"completo": 0, "parcial": 0, "sin_datos": 0, "saltado": 0, "error": 0}

    for i, lead in enumerate(leads):
        log.info(f"\n[{i+1}/{total}] {lead['nombre']} ({lead.get('barrio', '')})")
        try:
            campos = enrich_lead(lead)
            if campos is None:
                stats["saltado"] += 1
                continue
            wa, email = campos.get("whatsapp"), campos.get("email")
            if wa and email:   stats["completo"] += 1
            elif wa or email:  stats["parcial"]  += 1
            else:              stats["sin_datos"] += 1
            supabase.table("leads").update(campos).eq("id", lead["id"]).execute()
            log.info("  ✓ Supabase actualizado")
        except Exception as e:
            log.error(f"  ✗ Error: {e}")
            stats["error"] += 1
        time.sleep(DELAY_ENTRE_LEADS)

    log.info(
        f"\n{'='*60}\nRESUMEN run():\n"
        f"  Completos: {stats['completo']} | Parciales: {stats['parcial']} | "
        f"Sin datos: {stats['sin_datos']} | Saltados: {stats['saltado']} | Errores: {stats['error']}"
        f"\n{'='*60}"
    )

# ============================================================
# RUN_SIN_WA — v5.3
# Reprocesa leads ya enriquecidos pero sin WhatsApp.
# Aplica telefono-fallback (gratis) y re-corre RAG en reservas.
# NO re-corre Apify IG/FB para no gastar créditos.
# ============================================================

def run_sin_wa():
    leads = supabase.table("leads").select(
        "id, nombre, barrio, sitio_web, instagram, facebook, "
        "origen_contacto, telefono, email, enriquecido, notas"
    ).is_("whatsapp", "null").eq("enriquecido", True).execute().data

    total = len(leads)
    log.info(f"\n{'='*60}\nRUN_SIN_WA — {total} leads sin WhatsApp\n{'='*60}")
    actualizados = 0

    for i, lead in enumerate(leads):
        nombre = lead["nombre"]
        log.info(f"\n[{i+1}/{total}] {nombre}")

        wa_nuevo = None
        notas_add = []

        # 1. Telefono Google Maps como celular
        tel_gm = (lead.get("telefono") or "").strip()
        if tel_gm and es_celular_arg(tel_gm):
            wa_candidate = normalizar_telefono_gm(tel_gm)
            if wa_candidate:
                wa_nuevo = wa_candidate
                notas_add.append("wzap desde google maps")
                log.info(f"  → WA desde telefono GM: {wa_nuevo}")

        # 2. Re-correr RAG en reservas si sitio_web es plataforma de reservas
        if not wa_nuevo:
            sitio_web = (lead.get("sitio_web") or "").strip()
            if sitio_web and es_reservas_url(sitio_web):
                log.info(f"  → Re-intentando RAG en reservas: {sitio_web}")
                r_res = scrape_reservas(sitio_web)
                if r_res.get("whatsapp"):
                    wa_nuevo = r_res["whatsapp"]
                    notas_add.append(f"reservas rag: ok")
                    log.info(f"  → WA desde RAG reservas: {wa_nuevo}")

        if wa_nuevo:
            notas_actual = lead.get("notas") or ""
            notas_partes = [p for p in notas_actual.split(" | ") if p and "sin whatsapp" not in p and "sin contacto web" not in p]
            notas_partes += notas_add
            email_actual = lead.get("email")
            if not email_actual:
                notas_partes.append("sin email")

            campos = {
                "whatsapp":            wa_nuevo,
                "link_wame":           construir_wame(wa_nuevo),
                "notas":               " | ".join(notas_partes) or None,
                "fecha_actualizacion": datetime.now(timezone.utc).isoformat(),
            }
            try:
                supabase.table("leads").update(campos).eq("id", lead["id"]).execute()
                log.info(f"  ✓ Actualizado: {wa_nuevo}")
                actualizados += 1
            except Exception as e:
                log.error(f"  ✗ Error actualizando: {e}")
        else:
            log.info(f"  → Sin WA disponible")

        time.sleep(0.3)

    log.info(
        f"\n{'='*60}\nRESUMEN run_sin_wa():\n"
        f"  Actualizados: {actualizados} / {total}\n{'='*60}"
    )


if __name__ == "__main__":
    import sys
    modo = sys.argv[1] if len(sys.argv) > 1 else "full"

    log.info(f"=== EUROCREM enrich_v5.3 START — modo: {modo} ===")
    log.info(f"  Playwright disponible: {PLAYWRIGHT_OK}")

    if modo == "sinwa":
        # Solo reprocesa leads sin WhatsApp (rápido, sin Apify IG/FB)
        run_sin_wa()
    elif modo == "reporte":
        generar_reporte()
    else:
        # Full: fix + enrich pendientes + sin_wa + reporte
        fix_bad_records()
        run()
        run_sin_wa()
        generar_reporte()

    log.info("=== EUROCREM enrich_v5.3 DONE ===")

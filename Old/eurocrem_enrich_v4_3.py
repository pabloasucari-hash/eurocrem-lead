"""
EUROCREM — eurocrem_enrich_v4.py
Versión: 4.3 — 07/06/2026

Pipeline de enriquecimiento completo via Apify.

ITERACIÓN 1 — sitio propio:
  PASO 1: khadinakbar/bulk-website-contact-extractor sobre URL raíz
  PASO 2: si pages_crawled=1 o vacío → reintentar sobre subpáginas /qr/ /reservas /contacto /menu
  PASO 3: si sigue vacío → crawlworks/ai-web-scraper (JS pesado / splash page)
  PASO 4: si sigue sin wzap → RE_WA_PROXIMITY sobre HTML crudo (wzap texto plano sin wa.me)

ITERACIÓN 2 — apify/instagram-profile-scraper sobre IG guardado en IT1
ITERACIÓN 3 — apify/facebook-pages-scraper sobre FB guardado en IT1

Leads con IG/FB como sitio_web van directo a IT2/IT3.
Leads con Linktree como sitio_web → parsear links del Linktree.
Leads sin sitio_web son salteados.

Correcciones v4.3:
  - Fix: agregar "wpp" a RE_WA_KEYWORDS → captura "WPP: 1124933763" en bio de IG
  - Fix: unquote() en extraer_numero_de_wame_url → maneja phone=%2B54... (URL-encoded +)
  - Fix: parsear_linktree() → fetchea Linktree y extrae wa.me/website reales
  - Fix: Linktree en sitio_web → parsear en lugar de saltear
  - Fix: Linktree en IG bio → parsear en lugar de descartar
  - Fix: validar_email TLD máx 8 chars → elimina artefactos tipo "combottom"
"""

import os
import re
import time
import logging
import requests
from datetime import datetime, timezone
from urllib.parse import urlparse, unquote

from apify_client import ApifyClient
from supabase import create_client, Client

# ============================================================
# CONFIGURACIÓN
# ============================================================

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://crbmgsmmvfkbxrplqfkl.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNyYm1nc21tdmZrYnhycGxxZmtsIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MDUxNjQ0NywiZXhwIjoyMDk2MDkyNDQ3fQ.7D5od9xbpGP3XbdjrTpgCtL7xWYpeJ6xaa5cBsMsju8")
APIFY_TOKEN  = os.getenv("APIFY_TOKEN",  "apify_api_zWjFWPdJLUef0mCOyMxcYiBN5zgK3o3JDEtU")

# Actor IDs
ACTOR_KHADINAKBAR = "khadinakbar/bulk-website-contact-extractor"
ACTOR_CRAWLWORKS  = "crawlworks/ai-web-scraper"
ACTOR_INSTAGRAM   = "apify/instagram-profile-scraper"
ACTOR_FACEBOOK    = "apify/facebook-pages-scraper"

# Subpáginas a probar si raíz falla
SUBPAGINAS_FALLBACK = ["/qr/", "/reservas", "/contacto", "/menu", "/contacto/"]

DELAY_ENTRE_LEADS = 2

CRAWLWORKS_PROMPT = (
    "Extract all contact information from this restaurant website. "
    "Find: WhatsApp links or numbers, phone numbers, email addresses, "
    "Instagram handles, Facebook page URLs. "
    "Navigate past any splash page, intro screen or location selector if present."
)

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
apify = ApifyClient(APIFY_TOKEN)

# ============================================================
# DOMINIOS Y FILTROS
# ============================================================

DOMINIOS_IG = ["instagram.com"]
DOMINIOS_FB = ["facebook.com"]
DOMINIOS_LINKTREE = ["linktr.ee", "linktree.com"]
DOMINIOS_RESERVAS = [
    "meitre.com", "woki.com", "wokiapp.com", "opentable.com",
    "thefork.com", "apparta.co", "guiaoleo.com",
]

DOMINIOS_NO_SCRAPEAR = (
    DOMINIOS_RESERVAS
    + DOMINIOS_IG
    + DOMINIOS_FB
    + DOMINIOS_LINKTREE
    + [
        "wa.me", "api.whatsapp.com",
        "developers.facebook.com",
        "rappi.com", "pedidosya.com", "ubereats.com",
        "tripadvisor.com", "yelp.com",
    ]
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

def es_url_scrappeable(url: str) -> bool:
    if not url:
        return False
    if "wa.me/" in url or "api.whatsapp.com" in url:
        return False
    path = urlparse(url).path
    if EXTENSIONES_BINARIAS.search(path):
        return False
    try:
        netloc = urlparse(url).netloc.lower().lstrip("www.")
        for d in DOMINIOS_NO_SCRAPEAR:
            if netloc == d or netloc.endswith("." + d):
                return False
    except Exception:
        return False
    return True


def es_linktree(url: str) -> bool:
    if not url:
        return False
    netloc = urlparse(url).netloc.lower().lstrip("www.")
    return any(netloc == d or netloc.endswith("." + d) for d in DOMINIOS_LINKTREE)


def resolver_url_shortener(url: str) -> str:
    try:
        netloc = urlparse(url).netloc.lower().lstrip("www.")
        if netloc in SHORTENERS_CONOCIDOS:
            log.info(f"  → Resolviendo shortener: {url}")
            r = requests.head(
                url, allow_redirects=True, timeout=8,
                headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:120.0) Gecko/20100101 Firefox/120.0"},
            )
            final = r.url
            if final and final != url:
                log.info(f"  → Resuelto a: {final}")
                return final
    except Exception as e:
        log.debug(f"  resolver_url_shortener error: {e}")
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
    if not RE_IG_HANDLE.match(handle):
        return None
    return handle

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
    if len(digits) == 12 and not digits[2:3] == "9":
        digits = digits[:2] + "9" + digits[2:]
    return f"https://wa.me/{digits}"


def extraer_numero_de_wame_url(url: str) -> str | None:
    """Extrae número de wa.me/... o api.whatsapp.com/send?phone=...
    v4.3 fix: unquote() antes de aplicar regex para manejar phone=%2B54...
    """
    if not url:
        return None
    url_decoded = unquote(url)
    m = re.search(r"(?:wa\.me/|phone=)\+?(\d{7,15})", url_decoded, re.I)
    if m:
        return normalizar_whatsapp(m.group(1))
    return None


EMAIL_BLACKLIST = {
    "example.com", "test.com", "sentry.io", "wixpress.com",
    "sentry-next.wixpress.com", "sentry.wixpress.com",
    "squarespace.com", "shopify.com", "wordpress.com",
    "clarin.com", "lanacion.com.ar", "infobae.com",
    "rappi.com", "pedidosya.com", "ifood.com", "ubereats.com",
    "tripadvisor.com", "yelp.com", "guiaoleo.com",
    "meitre.com", "woki.com", "opentable.com", "thefork.com",
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
    # v4.3 fix: TLD máx 8 chars para filtrar artefactos HTML como "combottom" (9 chars)
    if not re.match(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,8}$", email):
        return False
    if re.search(r"\.(png|jpg|gif|svg|webp|ico|css|js)$", email, re.I):
        return False
    return True

# ============================================================
# RE_WA_PROXIMITY — fallback wzap texto plano
# ============================================================

RE_WA_NUMBER = re.compile(
    r"""(?x)
    (?:
        \+?54[\s\-]?9[\s\-]?
        | \+?549
        | 0?9?
    )
    (?:11|15|2\d{2}|3\d{2})
    [\s\-]?
    \d{4}[\s\-]?\d{4}
    """,
    re.VERBOSE,
)

# v4.3 fix: agregado "wpp" para capturar "WPP: 1124933763" en bio de IG
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
    matches_kw = list(RE_WA_KEYWORDS.finditer(texto))
    if not matches_kw:
        return None
    for kw_match in matches_kw:
        inicio = max(0, kw_match.start() - 120)
        fin    = min(len(texto), kw_match.end() + 120)
        ventana = texto[inicio:fin]
        nums = RE_WA_NUMBER.findall(ventana)
        for num in nums:
            n = normalizar_whatsapp(num)
            if n:
                log.info(f"  → RE_WA_PROXIMITY: encontró {n} cerca de '{kw_match.group()}'")
                return n
    return None


def fetch_html(url: str) -> str | None:
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:120.0) Gecko/20100101 Firefox/120.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
        }
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            return r.text
    except Exception as e:
        log.debug(f"  fetch_html error {url}: {e}")
    return None

# ============================================================
# PARSEO LINKTREE
# ============================================================

def parsear_linktree(url: str) -> dict:
    """
    Fetchea una página Linktree y extrae:
    - wa.me / api.whatsapp.com → número WhatsApp directo
    - URLs scrappeables → website para pasar a IT1
    v4.3: nuevo
    """
    log.info(f"  [linktree] {url}")
    html = fetch_html(url)
    if not html:
        log.info(f"  [linktree] sin respuesta")
        return {}

    wa = None
    website = None

    # Extraer todos los href y URLs en texto
    links = re.findall(r'href=["\']([^"\']+)["\']', html)
    # También buscar URLs en atributos data- y texto plano
    links += re.findall(r'https?://[^\s"\'<>]{10,}', html)

    seen = set()
    for link in links:
        link = link.strip()
        if not link.startswith("http") or link in seen:
            continue
        seen.add(link)

        # WhatsApp directo
        if not wa:
            n = extraer_numero_de_wame_url(link)
            if n:
                wa = n
                log.info(f"  [linktree] wzap encontrado: {n}")
                continue

        # Website scrappeable (sitio propio del restaurante)
        if not website and es_url_scrappeable(link):
            website = link
            log.info(f"  [linktree] website encontrado: {link}")

    # También buscar wzap en texto plano de la página (bio/botones)
    if not wa:
        texto_limpio = re.sub(r"<[^>]+>", " ", html)
        wa = re_wa_proximity(texto_limpio, es_html=False)

    return {"whatsapp": wa, "website": website}

# ============================================================
# PARSEO KHADINAKBAR
# ============================================================

def parsear_khadinakbar(item: dict) -> dict:
    wa = None
    email = None
    emails_extra = []
    ig = None
    ig_url = None
    fb = None

    for wlink in (item.get("whatsapp_links") or []):
        n = extraer_numero_de_wame_url(wlink)
        if n:
            wa = n
            break

    todos_emails = []
    for e in (item.get("emails") or []):
        if validar_email(e):
            todos_emails.append(e.lower().strip())
    keywords = ["reserva", "contacto", "info", "eventos", "ventas", "hola", "admin"]
    todos_emails.sort(
        key=lambda e: next((i for i, k in enumerate(keywords) if k in e), 99)
    )
    if todos_emails:
        email = todos_emails[0]
        emails_extra = todos_emails[1:]

    ig_raw = (item.get("social_links") or {}).get("instagram")
    if ig_raw:
        handle = extraer_ig_handle_de_url(ig_raw)
        if handle:
            ig = f"@{handle}"
            ig_url = f"https://www.instagram.com/{handle}/"

    fb_raw = (item.get("social_links") or {}).get("facebook")
    if fb_raw:
        try:
            fb_netloc = urlparse(fb_raw).netloc.lower().lstrip("www.")
            if fb_netloc in {"facebook.com", "m.facebook.com"}:
                paths_inv = {"sharer", "share", "dialog", "login", "watch",
                             "groups", "events", "developers", "docs"}
                path = fb_raw.rstrip("/").split("facebook.com/")[-1].split("?")[0]
                if path and path.lower().split("/")[0] not in paths_inv:
                    fb = fb_raw if fb_raw.startswith("http") else f"https://www.facebook.com/{path}"
        except Exception:
            pass

    pages_crawled = item.get("pages_crawled", 0)

    return {
        "whatsapp":      wa,
        "email":         email,
        "emails_extra":  emails_extra,
        "instagram":     ig,
        "link_ig":       ig_url,
        "facebook":      fb,
        "pages_crawled": pages_crawled,
    }

# ============================================================
# PARSEO INSTAGRAM SCRAPER
# ============================================================

def parsear_instagram(item: dict) -> dict:
    wa = None
    email = None
    ig_url = None

    username = item.get("username", "")
    if username:
        ig_url = f"https://www.instagram.com/{username}/"

    bio = item.get("biography") or ""
    ext_url = item.get("externalUrl") or ""

    # Buscar wa.me en externalUrl o en links externos
    for link in [ext_url] + [l.get("url", "") for l in (item.get("externalUrls") or [])]:
        n = extraer_numero_de_wame_url(link)
        if n:
            wa = n
            break

    # Buscar wzap en bio (incluye "WPP: ..." gracias a v4.3)
    if not wa:
        wa = re_wa_proximity(bio, es_html=False)

    # Email en bio
    m = re.search(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,8}", bio)
    if m and validar_email(m.group(0)):
        email = m.group(0).lower()

    # Website en externalUrl
    website = None
    if ext_url:
        if es_linktree(ext_url):
            # v4.3: parsear Linktree en lugar de descartar
            lt = parsear_linktree(ext_url)
            if lt.get("whatsapp") and not wa:
                wa = lt["whatsapp"]
            if lt.get("website"):
                website = lt["website"]
        elif es_url_scrappeable(ext_url):
            website = ext_url

    return {
        "whatsapp":  wa,
        "email":     email,
        "instagram": f"@{username}" if username else None,
        "link_ig":   ig_url,
        "website":   website,
    }

# ============================================================
# PARSEO FACEBOOK SCRAPER
# ============================================================

def parsear_facebook(item: dict) -> dict:
    wa = None
    email = None
    fb_url = item.get("facebookUrl") or None

    info_raw = item.get("info") or []
    if isinstance(info_raw, list):
        info_text = " ".join(info_raw)
    else:
        info_text = str(info_raw)

    intro = item.get("intro") or ""
    texto_completo = f"{info_text} {intro}".strip()

    m_tel = re.search(
        r"(\+?(?:54[\s\-]?9?[\s\-]?)?(?:11|15|2\d{2}|3\d{2})[\s\-]?\d{4}[\s\-]?\d{4})",
        texto_completo,
    )
    if m_tel:
        n = normalizar_whatsapp(m_tel.group(1))
        if n:
            wa = n

    m_email = re.search(
        r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,8}",
        texto_completo,
    )
    if m_email and validar_email(m_email.group(0)):
        email = m_email.group(0).lower().strip()

    website = None
    web_raw = item.get("website") or ""
    if not web_raw:
        websites = item.get("websites") or []
        web_raw = next(
            (w for w in websites if w and "google.com/maps" not in w
             and "bing.com/maps" not in w),
            ""
        )
    if web_raw and es_url_scrappeable(web_raw):
        website = web_raw

    return {
        "whatsapp": wa,
        "email":    email,
        "facebook": fb_url,
        "website":  website,
    }

# ============================================================
# LLAMADAS A APIFY
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


def scrape_khadinakbar(url: str) -> dict:
    log.info(f"  [khadinakbar] {url}")
    items = _run_actor(ACTOR_KHADINAKBAR, {
        "startUrls":          [{"url": url}],
        "followContactPages": True,
        "maxPagesPerDomain":  5,
    })
    if not items:
        return {"pages_crawled": 0}
    return parsear_khadinakbar(items[0])


def scrape_crawlworks(url: str) -> dict:
    log.info(f"  [crawlworks] {url}")
    items = _run_actor(ACTOR_CRAWLWORKS, {
        "url":        url,
        "prompt":     CRAWLWORKS_PROMPT,
        "useStealth": True,
    })
    if not items:
        return {}
    item = items[0]
    wa = None
    email = None
    for v in item.values():
        if not isinstance(v, str):
            continue
        for link in re.findall(r"https?://[^\s\"']+", v):
            n = extraer_numero_de_wame_url(link)
            if n and not wa:
                wa = n
        m = re.search(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,8}", v)
        if m and validar_email(m.group(0)) and not email:
            email = m.group(0).lower()
    return {"whatsapp": wa, "email": email, "instagram": None, "facebook": None}


def scrape_instagram(username: str) -> dict:
    handle = username.lstrip("@").split("?")[0]
    if not RE_IG_HANDLE.match(handle):
        log.warning(f"  [instagram-scraper] handle inválido '{handle}' — saltando")
        return {}
    log.info(f"  [instagram-scraper] @{handle}")
    items = _run_actor(ACTOR_INSTAGRAM, {"usernames": [handle]})
    if not items:
        return {}
    return parsear_instagram(items[0])


def scrape_facebook(fb_url: str) -> dict:
    log.info(f"  [facebook-scraper] {fb_url}")
    items = _run_actor(ACTOR_FACEBOOK, {"startUrls": [{"url": fb_url}]})
    if not items:
        return {}
    return parsear_facebook(items[0])

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
    notas = []
    resultado = {}

    sitio_web = resolver_url_shortener(sitio_web)

    r1 = scrape_khadinakbar(sitio_web)
    pages = r1.get("pages_crawled", 0)
    resultado = merge(resultado, r1)
    notas.append(f"khadinakbar raíz: pages={pages}")

    if pages <= 1 or (not resultado.get("whatsapp") and not resultado.get("email")):
        base_url = limpiar_url_base(sitio_web)
        sub_encontro_datos = False
        for sub in SUBPAGINAS_FALLBACK:
            url_sub = base_url + sub
            r_sub = scrape_khadinakbar(url_sub)
            if r_sub.get("pages_crawled", 0) > 0:
                resultado = merge(resultado, r_sub)
                notas.append(f"khadinakbar {sub}: ok")
                sub_encontro_datos = True
                if resultado.get("whatsapp") and resultado.get("email"):
                    break
        if not sub_encontro_datos:
            notas.append("subpáginas: sin resultado")

    if not resultado.get("whatsapp") and not resultado.get("email") and not resultado.get("instagram"):
        r3 = scrape_crawlworks(sitio_web)
        resultado = merge(resultado, r3)
        notas.append(
            f"crawlworks: wzap={'si' if r3.get('whatsapp') else 'no'} "
            f"email={'si' if r3.get('email') else 'no'}"
        )

    if not resultado.get("whatsapp"):
        html = fetch_html(sitio_web)
        wa_prox = re_wa_proximity(html, es_html=True) if html else None
        if wa_prox:
            resultado["whatsapp"] = wa_prox
            notas.append("RE_WA_PROXIMITY: wzap encontrado")
        else:
            notas.append("RE_WA_PROXIMITY: sin resultado")

    resultado["notas_enrich"] = " | ".join(notas)
    resultado["origen_contacto"] = "web-auto"
    return resultado

# ============================================================
# ITERACIÓN 2 — INSTAGRAM
# ============================================================

def iteracion_2(ig_handle: str, resultado_previo: dict = None) -> dict:
    r = scrape_instagram(ig_handle)
    if resultado_previo:
        r = merge(resultado_previo, r)

    website = r.pop("website", None)
    if website and es_url_scrappeable(website):
        log.info(f"  → IG bio tiene website: {website} → IT1")
        r_web = iteracion_1(website)
        r = merge(r, r_web)

    r["origen_contacto"] = "ig-auto"
    return r

# ============================================================
# ITERACIÓN 3 — FACEBOOK
# ============================================================

def iteracion_3(fb_url: str, resultado_previo: dict = None) -> dict:
    r = scrape_facebook(fb_url)
    if resultado_previo:
        r = merge(resultado_previo, r)

    website = r.pop("website", None)
    if website and es_url_scrappeable(website):
        log.info(f"  → FB page tiene website: {website} → IT1")
        r_web = iteracion_1(website)
        r = merge(r, r_web)

    r["origen_contacto"] = "fb-auto"
    return r

# ============================================================
# ENRIQUECEDOR POR LEAD
# ============================================================

def enrich_lead(lead: dict) -> dict | None:
    nombre    = lead.get("nombre", "")
    sitio_web = (lead.get("sitio_web") or "").strip()

    resultado = {}

    if not sitio_web:
        log.info("  → Sin sitio web — saltando")
        return None

    es_ig       = any(d in sitio_web for d in DOMINIOS_IG)
    es_fb       = any(d in sitio_web for d in DOMINIOS_FB)
    es_lt       = es_linktree(sitio_web)
    es_reservas = any(d in sitio_web for d in DOMINIOS_RESERVAS)

    if es_ig:
        handle = extraer_ig_handle_de_url(sitio_web)
        if not handle:
            log.info(f"  → IG URL inválida ({sitio_web}) — saltando")
            return None
        log.info(f"  → IT2 (IG): @{handle}")
        resultado = iteracion_2(handle)

    elif es_fb:
        log.info(f"  → IT3 (FB): {sitio_web}")
        resultado = iteracion_3(sitio_web)

    elif es_lt:
        # v4.3: parsear Linktree en lugar de saltear
        log.info(f"  → Linktree: {sitio_web}")
        lt = parsear_linktree(sitio_web)
        if lt.get("whatsapp"):
            resultado["whatsapp"] = lt["whatsapp"]
        if lt.get("website"):
            log.info(f"  → Linktree encontró website: {lt['website']} → IT1")
            r_web = iteracion_1(lt["website"])
            resultado = merge(resultado, r_web)
        resultado["notas_enrich"] = f"linktree: {sitio_web}"
        resultado["origen_contacto"] = "web-auto"

    elif es_reservas:
        log.info(f"  → Sitio de reservas ({sitio_web}) — saltando IT1")
        resultado = {"notas_enrich": f"sitio reservas: {sitio_web}"}

    else:
        log.info(f"  → IT1 (web): {sitio_web}")
        resultado = iteracion_1(sitio_web)

        if resultado.get("instagram") and not resultado.get("whatsapp"):
            log.info(f"  → IT2 complementario desde IG encontrado en IT1")
            resultado = iteracion_2(resultado["instagram"], resultado)

        if resultado.get("facebook") and not resultado.get("whatsapp"):
            log.info(f"  → IT3 complementario desde FB encontrado en IT1")
            resultado = iteracion_3(resultado["facebook"], resultado)

    wa           = resultado.get("whatsapp")
    email        = resultado.get("email")
    ig           = resultado.get("instagram")
    ig_url       = resultado.get("link_ig")
    fb           = resultado.get("facebook")
    emails_extra = resultado.get("emails_extra") or []
    notas_enrich = resultado.get("notas_enrich") or ""

    partes_notas = []
    if emails_extra:
        partes_notas.append(f"2do email: {emails_extra[0]}")
    if notas_enrich:
        partes_notas.append(notas_enrich)
    if not wa and not email:
        partes_notas.append("sin contacto web")
    elif not wa:
        partes_notas.append("sin whatsapp")
    elif not email:
        partes_notas.append("sin email")

    campos = {
        "whatsapp":            wa,
        "link_wame":           construir_wame(wa) if wa else None,
        "email":               email,
        "instagram":           ig,
        "link_ig":             ig_url,
        "facebook":            fb,
        "notas":               " | ".join(partes_notas) if partes_notas else None,
        "origen_contacto":     resultado.get("origen_contacto", "web-auto"),
        "enriquecido":         True,
        "fecha_actualizacion": datetime.now(timezone.utc).isoformat(),
    }

    tiene_wa    = bool(wa)
    tiene_email = bool(email)
    estado = (
        "completo" if (tiene_wa and tiene_email)
        else "parcial" if (tiene_wa or tiene_email)
        else "sin_datos"
    )
    log.info(f"  → {estado} | wzap: {wa or '—'} | email: {email or '—'}")

    return campos

# ============================================================
# LIMPIEZA DE DATOS MALOS
# ============================================================

def fix_bad_records():
    """
    Limpia registros con datos incorrectos guardados por bugs anteriores.
    - IG handles inválidos (@hl=es, @?next=...) → None
    - FB URLs de developers.facebook.com → None
    - Emails con TLD > 8 chars (artefactos HTML) → None
    Los leads afectados vuelven a origen_contacto="pendiente" para reproceso.
    """
    log.info("\n=== FIX BAD RECORDS ===")
    result = supabase.table("leads").select(
        "id, nombre, instagram, link_ig, facebook, email"
    ).execute()
    leads = result.data

    fixes = 0
    for lead in leads:
        campos_fix = {}

        ig = lead.get("instagram") or ""
        handle = ig.lstrip("@")
        if ig and not RE_IG_HANDLE.match(handle):
            log.info(f"  Limpiando IG inválido para {lead['nombre']}: {ig!r}")
            campos_fix["instagram"] = None
            campos_fix["link_ig"]   = None

        fb = lead.get("facebook") or ""
        if fb and "developers.facebook.com" in fb:
            log.info(f"  Limpiando FB inválido para {lead['nombre']}: {fb!r}")
            campos_fix["facebook"] = None

        email = lead.get("email") or ""
        if email and not validar_email(email):
            log.info(f"  Limpiando email inválido para {lead['nombre']}: {email!r}")
            campos_fix["email"] = None

        if campos_fix:
            campos_fix["origen_contacto"] = "pendiente"
            campos_fix["enriquecido"]     = False
            supabase.table("leads").update(campos_fix).eq("id", lead["id"]).execute()
            fixes += 1

    log.info(f"  Registros corregidos: {fixes}")

# ============================================================
# REPORTE
# ============================================================

def generar_reporte(output_path: str = "eurocrem_debug_report_v4.txt"):
    result = supabase.table("leads").select(
        "nombre, barrio, sitio_web, whatsapp, email, instagram, facebook, notas, origen_contacto, enriquecido"
    ).execute()
    leads = result.data
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M")

    completos  = [l for l in leads if l.get("whatsapp") and l.get("email")]
    parciales  = [l for l in leads
                  if (l.get("whatsapp") or l.get("email"))
                  and not (l.get("whatsapp") and l.get("email"))
                  and l.get("enriquecido")]
    sin_datos  = [l for l in leads
                  if not l.get("whatsapp") and not l.get("email")
                  and l.get("enriquecido")]
    pendientes = [l for l in leads if l.get("origen_contacto") == "pendiente"]
    sin_web    = [l for l in leads if not l.get("sitio_web")]

    lines = [
        f"EUROCREM — Debug Report v4.3 — {ahora}",
        f"Total leads: {len(leads)}",
        f"Completos (wzap + email):  {len(completos)}",
        f"Parciales (wzap o email):  {len(parciales)}",
        f"Sin contacto web:          {len(sin_datos)}",
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
    seccion("PENDIENTES", pendientes,
        lambda l: f"  {l['nombre']} ({l.get('barrio','')}) | web: {l.get('sitio_web') or 'sin web'}")
    seccion("SIN SITIO WEB", sin_web,
        lambda l: f"  {l['nombre']} ({l.get('barrio','')})")

    reporte = "\n".join(lines)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(reporte)
    log.info(f"Reporte guardado: {output_path}")

# ============================================================
# RUNNER
# ============================================================

def run():
    result = supabase.table("leads").select(
        "id, nombre, barrio, sitio_web, instagram, facebook, origen_contacto"
    ).eq("origen_contacto", "pendiente").execute()

    leads = result.data
    total = len(leads)
    log.info(f"\n{'='*60}")
    log.info(f"EUROCREM enrich_v4.3 — {total} leads a procesar")
    log.info(f"{'='*60}")

    stats = {"completo": 0, "parcial": 0, "sin_datos": 0, "saltado": 0, "error": 0}

    for i, lead in enumerate(leads):
        log.info(f"\n[{i+1}/{total}] {lead['nombre']} ({lead.get('barrio', '')})")
        try:
            campos = enrich_lead(lead)
            if campos is None:
                stats["saltado"] += 1
                continue

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
    log.info("=== EUROCREM enrich_v4.3 START ===")
    fix_bad_records()
    run()
    generar_reporte()
    log.info("=== EUROCREM enrich_v4.3 DONE ===")

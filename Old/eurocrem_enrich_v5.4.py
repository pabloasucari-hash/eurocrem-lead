"""
EUROCREM — eurocrem_enrich_v5.4.py
Versión: 5.4 — 07/06/2026

Fixes vs v5.3:
  - FIX normalizar_whatsapp: soporta "011 15-XXXX-XXXX" (13 dígitos con 15 embebido)
    Impacto: Cucina Paradiso Palermo, El Viejo Palermo y cualquier lead con telefono
    Google Maps en ese formato ahora se normalizan correctamente.
  - FIX extraer_contactos_de_html: patrón secundario para phone=<+NUMBER> con
    brackets literales en href (Wix/WP). Soluciona Cucina Paradiso Recoleta.
  - IT2 complementario en iteracion_3 (v5.3).
  - RAG trigger: if not wa and not email (v5.3).
  - es_celular_arg: solo prefijo 15 (v5.3).
  - email_2 como campo DB (v5.2).
  - Playwright en parsear_linktree (v5.2).
  - _scrape_con_apify_rag integrado (v5.2).
  - run_sin_wa(): reprocesa enriquecidos sin WA (v5.3).
  - CLI: full | sinwa | reporte | ids ID1,ID2,... (v5.4).

Uso:
  python eurocrem_enrich_v5.4.py
  python eurocrem_enrich_v5.4.py sinwa
  python eurocrem_enrich_v5.4.py reporte
  python eurocrem_enrich_v5.4.py ids 62aba680,9fc5092b,...
"""

import os, re, sys, time, logging
from datetime import datetime, timezone
from urllib.parse import urlparse, unquote

import requests as req_lib
from apify_client import ApifyClient
from supabase import create_client, Client

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_OK = True
except ImportError:
    PLAYWRIGHT_OK = False

# ============================================================
# CONFIG
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
apify = ApifyClient(APIFY_TOKEN)

# ============================================================
# DOMINIOS
# ============================================================

DOMINIOS_IG       = ["instagram.com"]
DOMINIOS_FB       = ["facebook.com"]
DOMINIOS_LINKTREE = ["linktr.ee", "linktree.com"]
DOMINIOS_RESERVAS = ["meitre.com", "woki.com", "wokiapp.com", "opentable.com",
                     "thefork.com", "apparta.co", "guiaoleo.com"]
DOMINIOS_NO_SCRAPEAR = (DOMINIOS_IG + DOMINIOS_FB + DOMINIOS_LINKTREE
    + ["wa.me", "api.whatsapp.com", "developers.facebook.com",
       "rappi.com", "pedidosya.com", "ubereats.com", "tripadvisor.com", "yelp.com"])

EXTENSIONES_BINARIAS = re.compile(
    r"\.(pdf|docx?|xlsx?|pptx?|jpg|jpeg|png|gif|webp|svg|ico|zip|rar|mp4|mp3)$", re.I)
SHORTENERS_CONOCIDOS = {"acortar.link", "bit.ly", "t.co", "goo.gl", "ow.ly",
    "tinyurl.com", "short.link", "cutt.ly", "lnk.bio", "rb.gy", "shorturl.at", "url.ar"}
RE_IG_HANDLE = re.compile(r"^[a-zA-Z0-9._]{1,30}$")

# ============================================================
# URL HELPERS
# ============================================================

def _netloc(url):
    try: return urlparse(url).netloc.lower().lstrip("www.")
    except: return ""

def es_url_scrappeable(url):
    if not url: return False
    if "wa.me/" in url or "api.whatsapp.com" in url: return False
    if EXTENSIONES_BINARIAS.search(urlparse(url).path): return False
    netloc = _netloc(url)
    return not any(netloc == d or netloc.endswith("." + d) for d in DOMINIOS_NO_SCRAPEAR + DOMINIOS_RESERVAS)

def es_reservas_url(url):
    if not url: return False
    netloc = _netloc(url)
    return any(netloc == d or netloc.endswith("." + d) for d in DOMINIOS_RESERVAS)

def es_linktree(url):
    if not url: return False
    netloc = _netloc(url)
    return any(netloc == d or netloc.endswith("." + d) for d in DOMINIOS_LINKTREE)

def resolver_url_shortener(url):
    try:
        if _netloc(url) in SHORTENERS_CONOCIDOS:
            log.info(f"  → Resolviendo shortener: {url}")
            r = req_lib.head(url, allow_redirects=True, timeout=8, headers=HTTP_HEADERS)
            if r.url and r.url != url:
                log.info(f"  → Resuelto: {r.url}")
                return r.url
    except Exception as e:
        log.debug(f"  shortener error: {e}")
    return url

def limpiar_url_base(url):
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}{p.path.rstrip('/')}"

def extraer_ig_handle_de_url(ig_url):
    if not ig_url: return None
    if "?next=" in ig_url:
        m = re.search(r"[?&]next=([^&]+)", ig_url)
        if m: ig_url = unquote(m.group(1))
    segments = [s for s in urlparse(ig_url).path.split("/") if s]
    if not segments: return None
    handle = segments[-1].lstrip("@")
    invalidos = {"p","reel","reels","stories","explore","instagram","accounts","password","reset",""}
    if handle.lower() in invalidos: return None
    return handle if RE_IG_HANDLE.match(handle) else None

# ============================================================
# NORMALIZADORES
# ============================================================

def normalizar_whatsapp(numero: str) -> str | None:
    """
    v5.4 FIX: soporta formato "011 15-XXXX-XXXX" (Google Maps Argentina).
    Después de strip del 0 STD quedan 12 dígitos con "15" embebido → se elimina.
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
    # FIX v5.4: "011 15-XXXX-XXXX" → 12 dígitos con "15" en pos 2 (área 11) o pos 3 (área 3 dígitos)
    if len(digits) == 12:
        if digits[2:4] == "15":
            digits = digits[:2] + digits[4:]   # área 2 dígitos (ej: 11)
        elif digits[3:5] == "15":
            digits = digits[:3] + digits[5:]   # área 3 dígitos (ej: 221, 341)
    if len(digits) != 10:
        return None
    return f"+54 9 {digits[:2]} {digits[2:6]}-{digits[6:]}"


def construir_wame(numero: str) -> str | None:
    if not numero: return None
    digits = re.sub(r"[^\d]", "", numero)
    if not digits.startswith("54"): digits = "54" + digits
    if len(digits) == 12 and digits[2:3] != "9": digits = digits[:2] + "9" + digits[2:]
    return f"https://wa.me/{digits}"


def extraer_numero_de_wame_url(url: str) -> str | None:
    if not url: return None
    url_decoded = unquote(url)
    m = re.search(r"(?:wa\.me/|phone=)[<(\s]*\+?(\d{7,15})", url_decoded, re.I)
    return normalizar_whatsapp(m.group(1)) if m else None


def es_celular_arg(tel: str) -> bool:
    if not tel: return False
    return bool(re.search(r"\b15[-\s]", tel))


EMAIL_BLACKLIST = {
    "example.com","test.com","sentry.io","wixpress.com","sentry-next.wixpress.com",
    "squarespace.com","shopify.com","wordpress.com","clarin.com","lanacion.com.ar",
    "rappi.com","pedidosya.com","ifood.com","ubereats.com","tripadvisor.com","yelp.com",
    "meitre.com","woki.com","opentable.com","thefork.com","apparta.co",
    "instagram.com","facebook.com","twitter.com","tiktok.com","google.com","linktr.ee",
}

def validar_email(email: str) -> bool:
    if not email or len(email) < 6: return False
    dominio = email.lower().strip().split("@")[-1]
    if dominio in EMAIL_BLACKLIST: return False
    if not re.match(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,8}$", email): return False
    if re.search(r"\.(png|jpg|gif|svg|webp|ico|css|js)$", email, re.I): return False
    return True

# ============================================================
# REGEXES TELÉFONO/WA
# ============================================================

RE_WA_NUMBER = re.compile(r"""(?x)
    (?:\+?54[\s\-]?9[\s\-]?|\+?549|0?9?)
    (?:11|15|2\d{2}|3\d{2}) [\s\-]?\d{4}[\s\-]?\d{4}""", re.VERBOSE)

RE_TELEFONO_BIO = re.compile(r"""(?x)
    (?:\+?54[\s\-]?9?[\s\-]?)?
    ((?:11|15|2\d{2}|3\d{2}) [\s\-]?\d{4}[\s\-]?\d{4})""", re.VERBOSE)

RE_WA_KEYWORDS = re.compile(
    r"whatsapp|wh[aá]tsapp|wsp|wsap|wpp|w\.?a\.?p|📱|wa\.me", re.I)

def re_wa_proximity(texto, es_html=False):
    if not texto: return None
    if es_html:
        texto = re.sub(r"<[^>]+>", " ", texto)
        texto = re.sub(r"\s+", " ", texto)
    for kw in RE_WA_KEYWORDS.finditer(texto):
        ventana = texto[max(0,kw.start()-120):min(len(texto),kw.end()+120)]
        for num in RE_WA_NUMBER.findall(ventana):
            n = normalizar_whatsapp(num)
            if n:
                log.info(f"  → PROXIMITY: {n} cerca de '{kw.group()}'")
                return n
    return None

def extraer_telefono_bio(texto):
    if not texto: return None
    for m in RE_TELEFONO_BIO.finditer(texto):
        n = normalizar_whatsapp(m.group(0))
        if n:
            log.info(f"  → Tel bio: {n}")
            return n
    return None

# ============================================================
# EXTRACCIÓN DE CONTACTOS DESDE HTML
# ============================================================

def extraer_contactos_de_html(html: str) -> dict:
    wa, email, ig, ig_url, fb = None, None, None, None, None
    todos_emails = []

    # WA: URLs estándar (sin brackets)
    for link in re.findall(r'https?://(?:wa\.me|api\.whatsapp\.com)[^\s"\'<>]*', html, re.I):
        n = extraer_numero_de_wame_url(link)
        if n:
            wa = n
            break

    # v5.4 FIX: WA con phone=<+NUMBER> — Wix/WP generan brackets literales en href
    # ej: href="https://api.whatsapp.com/send?phone=<+5491125011888>"
    if not wa:
        for m in re.finditer(
            r'(?:wa\.me|api\.whatsapp\.com)[^"\']{0,60}?phone=[<(\s]*\+?(\d{7,15})',
            html, re.I
        ):
            n = normalizar_whatsapp(m.group(1))
            if n:
                wa = n
                log.info(f"  → WA phone=<number> (Wix/WP): {n}")
                break

    # WA: proximidad keyword en texto
    if not wa:
        wa = re_wa_proximity(html, es_html=True)

    # Emails
    for e in re.findall(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,8}', html):
        if validar_email(e):
            todos_emails.append(e.lower().strip())
    todos_emails = list(dict.fromkeys(todos_emails))
    keywords = ["reserva","contacto","info","eventos","ventas","hola","admin"]
    todos_emails.sort(key=lambda e: next((i for i,k in enumerate(keywords) if k in e), 99))
    if todos_emails: email = todos_emails[0]

    # Instagram
    for h in re.findall(r'instagram\.com/([a-zA-Z0-9._]{1,30})/?(?:["\'\s?]|$)', html, re.I):
        handle = h.rstrip("/").split("?")[0]
        invalidos = {"p","reel","reels","stories","explore","instagram","accounts","password","reset",""}
        if handle.lower() not in invalidos and RE_IG_HANDLE.match(handle):
            ig = f"@{handle}"; ig_url = f"https://www.instagram.com/{handle}/"; break

    # Facebook
    for p in re.findall(r'facebook\.com/([^?\s"\'<>/][^?\s"\'<>]*)/?(?:["\'\s?]|$)', html, re.I):
        path = p.rstrip("/").split("?")[0]
        paths_inv = {"sharer","share","dialog","login","watch","groups","events","developers",
                     "docs","help","tr","pixel","plugins","pages","ads","policy","privacy","legal","terms",""}
        if path.lower().split("/")[0] not in paths_inv:
            fb = f"https://www.facebook.com/{path}"; break

    return {"whatsapp": wa, "email": email, "emails_extra": todos_emails[1:3],
            "instagram": ig, "link_ig": ig_url, "facebook": fb, "status_ok": True}

# ============================================================
# SCRAPERS
# ============================================================

def fetch_html(url, timeout=10):
    try:
        r = req_lib.get(url, headers=HTTP_HEADERS, timeout=timeout, allow_redirects=True)
        return r.text if r.status_code == 200 else None
    except Exception as e:
        log.debug(f"  fetch_html error {url}: {e}")
        return None

def _playwright_get_html(url, timeout_ms=30_000):
    if not PLAYWRIGHT_OK: return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=HTTP_HEADERS["User-Agent"], locale="es-AR")
            page = ctx.new_page()
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            html = page.content()
            browser.close()
            return html
    except Exception as e:
        log.debug(f"  playwright error {url}: {e}")
        return None

def scrape_simple(url):
    log.info(f"  [requests] {url}")
    html = fetch_html(url)
    return extraer_contactos_de_html(html) if html else {"status_ok": False}

def scrape_playwright(url):
    if not PLAYWRIGHT_OK: return {"status_ok": False}
    log.info(f"  [playwright] {url}")
    html = _playwright_get_html(url)
    if not html: return {"status_ok": False}
    result = extraer_contactos_de_html(html)
    log.info(f"  [playwright] wzap: {result.get('whatsapp') or '—'}")
    return result

def _run_actor(actor_id, run_input):
    try:
        run = apify.actor(actor_id).call(run_input=run_input)
        if not run: return []
        dataset_id = run["defaultDatasetId"] if isinstance(run, dict) else getattr(run, "default_dataset_id", None)
        if not dataset_id: return []
        return list(apify.dataset(dataset_id).iterate_items())
    except Exception as e:
        log.error(f"  Apify error {actor_id}: {e}")
        return []

def _scrape_con_apify_rag(url):
    log.info(f"  [rag] {url}")
    try:
        items = _run_actor(ACTOR_RAG, {
            "query": url, "outputFormats": ["markdown"],
            "scrapingTool": "browser-playwright", "maxResults": 1, "requestTimeoutSecs": 60,
        })
        if not items: return {"status_ok": False}
        texto = items[0].get("markdown") or items[0].get("text") or ""
        if not texto: return {"status_ok": False}
        wa = None
        for link in re.findall(r'https?://(?:wa\.me|api\.whatsapp\.com)\S+', texto, re.I):
            n = extraer_numero_de_wame_url(link)
            if n: wa = n; break
        if not wa: wa = re_wa_proximity(texto)
        if not wa: wa = extraer_telefono_bio(texto)
        emails = [e.lower() for e in re.findall(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,8}', texto) if validar_email(e)]
        email = emails[0] if emails else None
        log.info(f"  [rag] wzap: {wa or '—'} | email: {email or '—'}")
        return {"whatsapp": wa, "email": email, "status_ok": True}
    except Exception as e:
        log.error(f"  [rag] error: {e}")
        return {"status_ok": False}

def scrape_reservas(url):
    log.info(f"  [reservas] {url}")
    html = fetch_html(url)
    if not html and PLAYWRIGHT_OK:
        log.info(f"  [reservas→playwright]")
        html = _playwright_get_html(url, timeout_ms=25_000)

    wa, email = None, None
    if html:
        for link in re.findall(r'https?://(?:wa\.me|api\.whatsapp\.com)[^\s"\'<>]*', html, re.I):
            n = extraer_numero_de_wame_url(link)
            if n: wa = n; break
        if not wa:
            for m in re.finditer(r'(?:wa\.me|api\.whatsapp\.com)[^"\']{0,60}?phone=[<(\s]*\+?(\d{7,15})', html, re.I):
                n = normalizar_whatsapp(m.group(1))
                if n: wa = n; break
        if not wa: wa = re_wa_proximity(html, es_html=True)
        if not wa:
            texto_limpio = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
            wa = extraer_telefono_bio(texto_limpio)
        emails = list(dict.fromkeys([e.lower() for e in re.findall(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,8}', html) if validar_email(e)]))
        if emails: email = emails[0]

    # v5.3: RAG si sin datos
    if not wa and not email:
        log.info(f"  [reservas→rag]")
        r_rag = _scrape_con_apify_rag(url)
        if r_rag.get("status_ok"):
            wa = wa or r_rag.get("whatsapp")
            email = email or r_rag.get("email")

    if not html and not wa and not email:
        return {"status_ok": False, "notas_enrich": f"reservas sin respuesta: {_netloc(url)}"}
    log.info(f"  [reservas] wzap: {wa or '—'} | email: {email or '—'}")
    return {"whatsapp": wa, "email": email, "notas_enrich": f"reservas: {_netloc(url)}", "origen_contacto": "web-auto", "status_ok": True}

# ============================================================
# PARSERS IG / FB / LINKTREE
# ============================================================

def parsear_instagram(item):
    wa, email = None, None
    username = item.get("username", "")
    ig_url = f"https://www.instagram.com/{username}/" if username else None
    bio = item.get("biography") or ""
    ext_url = item.get("externalUrl") or ""
    ext_urls = [l.get("url","") for l in (item.get("externalUrls") or [])]
    todos_links = [u for u in ([ext_url] + ext_urls) if u]

    for link in todos_links:
        n = extraer_numero_de_wame_url(link)
        if n: wa = n; break
    if not wa: wa = re_wa_proximity(bio)
    if not wa: wa = extraer_telefono_bio(bio)
    if not wa:
        for url in re.findall(r'https?://\S+', bio):
            if es_reservas_url(url):
                r = scrape_reservas(url)
                if r.get("whatsapp"): wa = r["whatsapp"]; break
    if not wa:
        for link in todos_links:
            if es_reservas_url(link):
                r = scrape_reservas(link)
                if r.get("whatsapp"): wa = r["whatsapp"]; break

    m = re.search(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,8}', bio)
    if m and validar_email(m.group(0)): email = m.group(0).lower()

    website, reservas_url = None, None
    if ext_url:
        if es_linktree(ext_url):
            lt = parsear_linktree(ext_url)
            if lt.get("whatsapp") and not wa: wa = lt["whatsapp"]
            if lt.get("website"): website = lt["website"]
        elif es_url_scrappeable(ext_url): website = ext_url
        elif es_reservas_url(ext_url) and not wa: reservas_url = ext_url

    return {"whatsapp": wa, "email": email, "instagram": f"@{username}" if username else None,
            "link_ig": ig_url, "website": website, "reservas_url": reservas_url}


def parsear_facebook(item):
    wa, email = None, None
    fb_url = item.get("facebookUrl") or None
    info_raw = item.get("info") or []
    info_text = " ".join(info_raw) if isinstance(info_raw, list) else str(info_raw)
    texto = f"{info_text} {item.get('intro') or ''}".strip()

    wa = re_wa_proximity(texto)
    if not wa: wa = extraer_telefono_bio(texto)
    m_e = re.search(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,8}', texto)
    if m_e and validar_email(m_e.group(0)): email = m_e.group(0).lower()

    ig, ig_url = None, None
    for h in re.findall(r'instagram\.com/([a-zA-Z0-9._]{1,30})/?', texto, re.I):
        handle = h.rstrip("/").split("?")[0]
        if handle.lower() not in {"p","reel","reels","stories","explore",""} and RE_IG_HANDLE.match(handle):
            ig = f"@{handle}"; ig_url = f"https://www.instagram.com/{handle}/"; break

    web_raw = item.get("website") or ""
    if not web_raw:
        web_raw = next((w for w in (item.get("websites") or []) if w and "google.com/maps" not in w), "")
    website = web_raw if web_raw and es_url_scrappeable(web_raw) else None
    return {"whatsapp": wa, "email": email, "instagram": ig, "link_ig": ig_url, "facebook": fb_url, "website": website}


def parsear_linktree(url):
    log.info(f"  [linktree] {url}")
    html = fetch_html(url)
    if not html or 'id="root"' in html[:2000]:
        log.info(f"  [linktree→playwright]")
        html = _playwright_get_html(url)
    if not html: return {}

    wa, website, seen = None, None, set()
    links = re.findall(r'href=["\']([^"\']+)["\']', html) + re.findall(r'https?://[^\s"\'<>]{10,}', html)
    for link in links:
        link = link.strip()
        if not link.startswith("http") or link in seen: continue
        seen.add(link)
        if not wa:
            n = extraer_numero_de_wame_url(link)
            if n: wa = n; log.info(f"  [linktree] wzap: {n}"); continue
        if not website and es_url_scrappeable(link):
            website = link; log.info(f"  [linktree] website: {link}")
    if not wa:
        wa = re_wa_proximity(re.sub(r"<[^>]+>", " ", html))
    return {"whatsapp": wa, "website": website}

# ============================================================
# APIFY
# ============================================================

def scrape_instagram(username):
    handle = username.lstrip("@").split("?")[0]
    if not RE_IG_HANDLE.match(handle): return {}
    log.info(f"  [ig] @{handle}")
    items = _run_actor(ACTOR_INSTAGRAM, {"usernames": [handle]})
    return parsear_instagram(items[0]) if items else {}

def scrape_facebook(fb_url):
    log.info(f"  [fb] {fb_url}")
    items = _run_actor(ACTOR_FACEBOOK, {"startUrls": [{"url": fb_url}]})
    return parsear_facebook(items[0]) if items else {}

# ============================================================
# MERGE
# ============================================================

def merge(base, nuevo):
    r = dict(base)
    for k, v in nuevo.items():
        if v and not r.get(k): r[k] = v
    return r

# ============================================================
# ITERACIONES
# ============================================================

def iteracion_1(sitio_web):
    notas = []
    resultado = {}
    sitio_web = resolver_url_shortener(sitio_web)

    r1 = scrape_simple(sitio_web)
    resultado = merge(resultado, r1)
    notas.append(f"requests raíz: {'ok' if r1.get('status_ok') else 'sin respuesta'}")

    if not resultado.get("whatsapp") and not resultado.get("email"):
        base_url = limpiar_url_base(sitio_web)
        sub_ok = False
        for sub in SUBPAGINAS_FALLBACK:
            r_sub = scrape_simple(base_url + sub)
            if r_sub.get("status_ok"):
                resultado = merge(resultado, r_sub)
                notas.append(f"requests {sub}: ok")
                sub_ok = True
                if resultado.get("whatsapp") and resultado.get("email"): break
        if not sub_ok: notas.append("subpáginas: sin respuesta")

    if not resultado.get("whatsapp") and not resultado.get("email") and not resultado.get("instagram"):
        r3 = scrape_playwright(sitio_web)
        resultado = merge(resultado, r3)
        notas.append(f"playwright: wzap={'si' if r3.get('whatsapp') else 'no'} email={'si' if r3.get('email') else 'no'}")

    if not resultado.get("whatsapp") and not resultado.get("email"):
        r_rag = _scrape_con_apify_rag(sitio_web)
        if r_rag.get("status_ok"):
            resultado = merge(resultado, r_rag)
            notas.append(f"rag: {'ok' if r_rag.get('whatsapp') or r_rag.get('email') else 'sin respuesta'}")

    resultado["notas_enrich"] = " | ".join(notas)
    resultado["origen_contacto"] = "web-auto"
    return resultado


def iteracion_2(ig_handle, resultado_previo=None):
    r = scrape_instagram(ig_handle)
    if resultado_previo: r = merge(resultado_previo, r)
    website = r.pop("website", None)
    if website and es_url_scrappeable(website):
        log.info(f"  → IG website: {website}")
        r = merge(r, iteracion_1(website))
    reservas_url = r.pop("reservas_url", None)
    if reservas_url and not r.get("whatsapp"):
        r = merge(r, scrape_reservas(reservas_url))
    r["origen_contacto"] = "ig-auto"
    return r


def iteracion_3(fb_url, resultado_previo=None):
    r = scrape_facebook(fb_url)
    if resultado_previo: r = merge(resultado_previo, r)
    website = r.pop("website", None)
    if website and es_url_scrappeable(website):
        log.info(f"  → FB website: {website}")
        r = merge(r, iteracion_1(website))
    # v5.3: IT2 complementario si FB encontró IG pero sin WA
    if r.get("instagram") and not r.get("whatsapp"):
        log.info(f"  → IT2 complementario desde FB: {r['instagram']}")
        r = iteracion_2(r["instagram"], r)
    r["origen_contacto"] = "fb-auto"
    return r

# ============================================================
# ENRICH LEAD
# ============================================================

def enrich_lead(lead):
    sitio_web = (lead.get("sitio_web") or "").strip()

    if not sitio_web:
        # v5.4: telefono fallback para leads sin web con prefijo 15
        tel = (lead.get("telefono") or "").strip()
        if es_celular_arg(tel):
            wa = normalizar_whatsapp(tel)
            if wa:
                log.info(f"  → WA desde telefono (15-): {wa}")
                return {"whatsapp": wa, "link_wame": construir_wame(wa),
                        "origen_contacto": "tel-fallback", "enriquecido": True,
                        "fecha_actualizacion": datetime.now(timezone.utc).isoformat()}
        log.info("  → Sin sitio web — saltando")
        return None

    es_ig = any(d in sitio_web for d in DOMINIOS_IG)
    es_fb = any(d in sitio_web for d in DOMINIOS_FB)
    es_lt = es_linktree(sitio_web)
    es_res = es_reservas_url(sitio_web)
    resultado = {}

    if es_ig:
        handle = extraer_ig_handle_de_url(sitio_web)
        if not handle: return None
        log.info(f"  → IT2 IG: @{handle}")
        resultado = iteracion_2(handle)
    elif es_fb:
        log.info(f"  → IT3 FB: {sitio_web}")
        resultado = iteracion_3(sitio_web)
    elif es_lt:
        log.info(f"  → Linktree: {sitio_web}")
        lt = parsear_linktree(sitio_web)
        if lt.get("whatsapp"): resultado["whatsapp"] = lt["whatsapp"]
        if lt.get("website"):
            r_it1 = iteracion_1(lt["website"])
            r_it1["notas_enrich"] = f"linktree→{r_it1.get('notas_enrich','')}"
            resultado = merge(resultado, r_it1)
        resultado.setdefault("notas_enrich", f"linktree: {sitio_web}")
        resultado.setdefault("origen_contacto", "web-auto")
    elif es_res:
        log.info(f"  → reservas: {sitio_web}")
        resultado = scrape_reservas(sitio_web)
    else:
        log.info(f"  → IT1 web: {sitio_web}")
        resultado = iteracion_1(sitio_web)
        if resultado.get("instagram") and not resultado.get("whatsapp"):
            log.info(f"  → IT2 complementario")
            resultado = iteracion_2(resultado["instagram"], resultado)
        if resultado.get("facebook") and not resultado.get("whatsapp"):
            log.info(f"  → IT3 complementario")
            resultado = iteracion_3(resultado["facebook"], resultado)

    wa           = resultado.get("whatsapp")
    email        = resultado.get("email")
    emails_extra = resultado.get("emails_extra") or []
    notas_enrich = resultado.get("notas_enrich") or ""
    partes_notas = [notas_enrich] if notas_enrich else []
    if not wa and not email: partes_notas.append("sin contacto web")
    elif not wa:  partes_notas.append("sin whatsapp")
    elif not email: partes_notas.append("sin email")

    log.info(f"  → {'completo' if wa and email else 'parcial' if wa or email else 'sin_datos'} | wzap: {wa or '—'} | email: {email or '—'}")
    return {
        "whatsapp":            wa,
        "link_wame":           construir_wame(wa) if wa else None,
        "email":               email,
        "email_2":             emails_extra[0] if emails_extra else None,
        "instagram":           resultado.get("instagram"),
        "link_ig":             resultado.get("link_ig"),
        "facebook":            resultado.get("facebook"),
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
    result = supabase.table("leads").select("id, nombre, instagram, link_ig, facebook, email, precio, telefono").execute()
    fixes = 0
    for lead in result.data:
        campos_fix = {}
        for campo in ("precio", "telefono", "email"):
            if lead.get(campo) == "": campos_fix[campo] = None
        ig = lead.get("instagram") or ""
        if ig and not RE_IG_HANDLE.match(ig.lstrip("@")):
            campos_fix.update({"instagram": None, "link_ig": None})
        fb = lead.get("facebook") or ""
        if fb:
            fb_path = urlparse(fb).path.rstrip("/").lstrip("/").split("/")[0].lower()
            if "developers.facebook.com" in fb or fb_path in {"tr","help","pixel","plugins","ads","policy"}:
                campos_fix["facebook"] = None
        email = lead.get("email") or ""
        if email and not validar_email(email): campos_fix["email"] = None
        if campos_fix:
            if any(k in campos_fix for k in ("instagram","facebook","email")):
                campos_fix.update({"origen_contacto": "pendiente", "enriquecido": False})
            supabase.table("leads").update(campos_fix).eq("id", lead["id"]).execute()
            fixes += 1
    log.info(f"  Registros corregidos: {fixes}")

# ============================================================
# RUN SIN WA
# ============================================================

def run_sin_wa():
    leads = supabase.table("leads").select(
        "id, nombre, sitio_web, telefono, email, enriquecido, notas, origen_contacto"
    ).is_("whatsapp", "null").eq("enriquecido", True).execute().data

    log.info(f"\n{'='*60}\nrun_sin_wa — {len(leads)} leads sin WA\n{'='*60}")
    for lead in leads:
        log.info(f"\n[sinwa] {lead['nombre']}")
        cambios = {}
        tel = (lead.get("telefono") or "").strip()
        if es_celular_arg(tel):
            wa = normalizar_whatsapp(tel)
            if wa:
                log.info(f"  → WA desde telefono (15-): {wa}")
                cambios["whatsapp"] = wa
                cambios["link_wame"] = construir_wame(wa)
                cambios["notas"] = ((lead.get("notas") or "").replace("sin whatsapp","").replace("sin contacto web","").strip(" |") + " | wzap desde google maps").strip(" |")
        sitio_web = (lead.get("sitio_web") or "").strip()
        if not cambios.get("whatsapp") and es_reservas_url(sitio_web):
            r_rag = _scrape_con_apify_rag(sitio_web)
            if r_rag.get("whatsapp"):
                cambios["whatsapp"] = r_rag["whatsapp"]
                cambios["link_wame"] = construir_wame(r_rag["whatsapp"])
        if cambios:
            cambios["fecha_actualizacion"] = datetime.now(timezone.utc).isoformat()
            supabase.table("leads").update(cambios).eq("id", lead["id"]).execute()
            log.info(f"  ✓ Actualizado")
        else:
            log.info(f"  — Sin mejora")

# ============================================================
# REPORTE
# ============================================================

def generar_reporte(output_path="eurocrem_debug_report_v5.txt"):
    leads = supabase.table("leads").select(
        "nombre, sitio_web, whatsapp, email, email_2, notas, origen_contacto, enriquecido"
    ).execute().data
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M")
    completos  = [l for l in leads if l.get("whatsapp") and l.get("email")]
    parciales  = [l for l in leads if (l.get("whatsapp") or l.get("email")) and not (l.get("whatsapp") and l.get("email")) and l.get("enriquecido")]
    sin_datos  = [l for l in leads if not l.get("whatsapp") and not l.get("email") and l.get("enriquecido")]
    pendientes = [l for l in leads if l.get("origen_contacto") == "pendiente"]

    lines = [f"EUROCREM — Debug Report v5.4 — {ahora}",
             f"Total: {len(leads)} | Completos: {len(completos)} | Parciales: {len(parciales)} | Sin datos: {len(sin_datos)} | Pendientes: {len(pendientes)}", ""]

    def sec(titulo, items, fmt):
        lines.extend([f"\n{'='*60}", titulo, "="*60] + [fmt(l) for l in items])

    sec("COMPLETOS", completos,
        lambda l: f"  {l['nombre']} | wzap: {l['whatsapp']} | email: {l['email']}" + (f" | email_2: {l['email_2']}" if l.get("email_2") else ""))
    sec("PARCIALES", parciales,
        lambda l: f"  {l['nombre']} | wzap: {l.get('whatsapp') or '—'} | email: {l.get('email') or '—'} | {l.get('notas') or ''}")
    sec("SIN DATOS", sin_datos,
        lambda l: f"  {l['nombre']} | {l.get('sitio_web') or 'sin web'} | {l.get('notas') or ''}")
    sec("PENDIENTES", pendientes,
        lambda l: f"  {l['nombre']} | {l.get('sitio_web') or 'sin web'}")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log.info(f"Reporte: {output_path}")

# ============================================================
# RUNNERS
# ============================================================

def run(ids_filter=None):
    query = supabase.table("leads").select(
        "id, nombre, barrio, sitio_web, instagram, facebook, origen_contacto, telefono"
    )
    if ids_filter:
        query = query.in_("id", ids_filter)
    else:
        query = query.eq("origen_contacto", "pendiente")
    leads = query.execute().data

    total = len(leads)
    log.info(f"\n{'='*60}\nEUROCREM v5.4 — {total} leads\n{'='*60}")
    stats = {"completo": 0, "parcial": 0, "sin_datos": 0, "saltado": 0, "error": 0}

    for i, lead in enumerate(leads):
        log.info(f"\n[{i+1}/{total}] {lead['nombre']} ({lead.get('barrio','')})")
        try:
            campos = enrich_lead(lead)
            if campos is None:
                stats["saltado"] += 1; continue
            wa, email = campos.get("whatsapp"), campos.get("email")
            if wa and email: stats["completo"] += 1
            elif wa or email: stats["parcial"] += 1
            else: stats["sin_datos"] += 1
            supabase.table("leads").update(campos).eq("id", lead["id"]).execute()
            log.info("  ✓ Supabase actualizado")
        except Exception as e:
            log.error(f"  ✗ Error: {e}"); stats["error"] += 1
        if not ids_filter:
            time.sleep(DELAY_ENTRE_LEADS)

    log.info(f"\nRESUMEN: Completos: {stats['completo']} | Parciales: {stats['parcial']} | Sin datos: {stats['sin_datos']} | Saltados: {stats['saltado']} | Errores: {stats['error']}")

# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else "full"
    log.info(f"=== EUROCREM v5.4 START — modo: {modo} | Playwright: {PLAYWRIGHT_OK} ===")

    if modo == "reporte":
        generar_reporte()

    elif modo == "sinwa":
        run_sin_wa()
        generar_reporte()

    elif modo == "ids":
        # Uso: python script.py ids ID1,ID2,ID3
        ids = sys.argv[2].split(",") if len(sys.argv) > 2 else []
        if not ids:
            log.error("Uso: python script.py ids ID1,ID2,...")
        else:
            run(ids_filter=ids)
            generar_reporte()

    else:  # full
        fix_bad_records()
        run()
        run_sin_wa()
        generar_reporte()

    log.info("=== EUROCREM v5.4 DONE ===")

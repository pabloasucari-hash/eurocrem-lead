"""
EUROCREM — eurocrem_enrich_v4.py
Versión: 4.1 — 07/06/2026

Pipeline de enriquecimiento completo via Apify.

ITERACIÓN 1 — sitio propio:
  PASO 1: khadinakbar/bulk-website-contact-extractor sobre URL raíz
  PASO 2: si pages_crawled=1 o vacío → reintentar sobre subpáginas /qr/ /reservas /contacto /menu
  PASO 3: si sigue vacío → crawlworks/ai-web-scraper (JS pesado / splash page)
  PASO 4: si sigue sin wzap → RE_WA_PROXIMITY sobre HTML crudo (wzap texto plano sin wa.me)

ITERACIÓN 2 — apify/instagram-profile-scraper sobre IG guardado en IT1
ITERACIÓN 3 — apify/facebook-pages-scraper sobre FB guardado en IT1

Leads con IG/FB como sitio_web van directo a IT2/IT3.
Leads sin sitio_web son salteados.
No toca: place_id, nombre, direccion, barrio, tipo, google_*, precio,
         horarios, abierto, telefono, sitio_web, fecha_alta.

Correcciones v4.1:
  - Fix: for...else en PASO 2 siempre añadía "sin resultado" — reemplazado con flag
  - Fix: parsear_facebook ahora parsea campo "info" (texto) para phone/email
         ya que apify/facebook-pages-scraper no devuelve campos "phone"/"email" separados
  - Fix: khadinakbar calls incluyen followContactPages=True y maxPagesPerDomain=5
  - Fix: API keys hardcodeadas eliminadas — usar variables de entorno o .env
"""

import os
import re
import time
import logging
import requests
from datetime import datetime, timezone
from urllib.parse import urlparse

from apify_client import ApifyClient
from supabase import create_client, Client

# ============================================================
# CONFIGURACIÓN
# ============================================================

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
APIFY_TOKEN  = os.getenv("APIFY_TOKEN")

if not SUPABASE_URL or not SUPABASE_KEY or not APIFY_TOKEN:
    raise EnvironmentError(
        "Faltan variables de entorno: SUPABASE_URL, SUPABASE_KEY, APIFY_TOKEN. "
        "Usá un archivo .env o exportalas antes de correr el script."
    )

# Actor IDs
ACTOR_KHADINAKBAR = "khadinakbar/bulk-website-contact-extractor"
ACTOR_CRAWLWORKS  = "crawlworks/ai-web-scraper"
ACTOR_INSTAGRAM   = "apify/instagram-profile-scraper"
ACTOR_FACEBOOK    = "apify/facebook-pages-scraper"

# Subpáginas a probar si raíz falla
SUBPAGINAS_FALLBACK = ["/qr/", "/reservas", "/contacto", "/menu", "/contacto/"]

# Delay entre leads
DELAY_ENTRE_LEADS = 2

# Prompt para crawlworks
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
# DOMINIOS QUE INDICAN SOCIAL COMO SITIO_WEB
# ============================================================

DOMINIOS_IG = ["instagram.com"]
DOMINIOS_FB = ["facebook.com"]
DOMINIOS_RESERVAS = [
    "meitre.com", "woki.com", "wokiapp.com", "opentable.com",
    "thefork.com", "linktr.ee", "linktree.com",
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
    # Asegurar que tenga el 9 de celular
    if len(digits) == 12 and not digits[2:3] == "9":
        digits = digits[:2] + "9" + digits[2:]
    return f"https://wa.me/{digits}"


def extraer_numero_de_wame_url(url: str) -> str | None:
    """Extrae número de wa.me/... o api.whatsapp.com/send?phone=..."""
    if not url:
        return None
    m = re.search(r"(?:wa\.me/|phone=)\+?(\d{7,15})", url, re.I)
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
    if not re.match(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$", email):
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
        \+?54[\s\-]?9[\s\-]?          # +54 9
        | \+?549                        # +549
        | 0?9?                          # local
    )
    (?:11|15|2\d{2}|3\d{2})           # área
    [\s\-]?
    \d{4}[\s\-]?\d{4}                 # número
    """,
    re.VERBOSE,
)

RE_WA_KEYWORDS = re.compile(
    r"whatsapp|wh[aá]tsapp|wsp|wsap|w\.?a\.?p|📱|wa\.me",
    re.IGNORECASE,
)


def re_wa_proximity(texto: str, es_html: bool = False) -> str | None:
    """
    Busca números de teléfono en texto plano cercanos a keywords de WhatsApp.
    Si es_html=True, limpia tags HTML antes de procesar.
    Retorna el número normalizado o None.
    """
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
    """Fetcha HTML crudo de una URL con headers de browser."""
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
# PARSEO KHADINAKBAR
# ============================================================

def parsear_khadinakbar(item: dict) -> dict:
    """
    Parsea un item del output de khadinakbar/bulk-website-contact-extractor.
    Output format:
      {
        whatsapp_links: ["https://wa.me/549...", "https://api.whatsapp.com/send?phone=..."],
        emails: ["...@..."],
        phones: ["..."],
        social_links: {facebook: "...", instagram: "..."},
        pages_crawled: N
      }
    """
    wa = None
    email = None
    emails_extra = []
    ig = None
    ig_url = None
    fb = None

    # WhatsApp — tomar el primer link válido
    for wlink in (item.get("whatsapp_links") or []):
        n = extraer_numero_de_wame_url(wlink)
        if n:
            wa = n
            break

    # Email — validar y ordenar por relevancia
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

    # Instagram
    ig_raw = (item.get("social_links") or {}).get("instagram")
    if ig_raw:
        handle = ig_raw.rstrip("/").split("/")[-1].lstrip("@")
        invalidos = {"p", "reel", "reels", "stories", "explore", "instagram"}
        if handle and handle.lower() not in invalidos:
            ig = f"@{handle}"
            ig_url = ig_raw if ig_raw.startswith("http") else f"https://www.instagram.com/{handle}/"

    # Facebook
    fb_raw = (item.get("social_links") or {}).get("facebook")
    if fb_raw:
        paths_inv = {"sharer", "share", "dialog", "login", "watch", "groups", "events"}
        path = fb_raw.rstrip("/").split("facebook.com/")[-1].split("?")[0]
        if path and path.lower() not in paths_inv:
            fb = fb_raw if fb_raw.startswith("http") else f"https://www.facebook.com/{path}"

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
    """
    Parsea output de apify/instagram-profile-scraper.
    Campos útiles: biography, externalUrl, username.
    """
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

    # Buscar wzap en bio como texto plano (sin HTML)
    if not wa:
        wa = re_wa_proximity(bio, es_html=False)

    # Email en bio
    m = re.search(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", bio)
    if m and validar_email(m.group(0)):
        email = m.group(0).lower()

    # Website en externalUrl — para pasar a IT1 si tiene sitio propio
    website = None
    if ext_url and not any(d in ext_url for d in ["instagram.com", "facebook.com", "linktr.ee"]):
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
    """
    Parsea output de apify/facebook-pages-scraper.

    IMPORTANTE: el actor NO devuelve campos "phone" ni "email" separados.
    Los datos de contacto vienen embebidos en el campo "info" (lista de strings)
    y en "website"/"websites". Se parsean con regex.
    """
    wa = None
    email = None
    fb_url = item.get("facebookUrl") or None

    # Construir texto plano desde el campo "info" (lista de strings)
    info_raw = item.get("info") or []
    if isinstance(info_raw, list):
        info_text = " ".join(info_raw)
    else:
        info_text = str(info_raw)

    # También agregar "intro" si existe
    intro = item.get("intro") or ""
    texto_completo = f"{info_text} {intro}".strip()

    # Teléfono en texto → intentar normalizar como WhatsApp
    m_tel = re.search(
        r"(\+?(?:54[\s\-]?9?[\s\-]?)?(?:11|15|2\d{2}|3\d{2})[\s\-]?\d{4}[\s\-]?\d{4})",
        texto_completo,
    )
    if m_tel:
        n = normalizar_whatsapp(m_tel.group(1))
        if n:
            wa = n

    # Email en texto
    m_email = re.search(
        r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
        texto_completo,
    )
    if m_email and validar_email(m_email.group(0)):
        email = m_email.group(0).lower().strip()

    # Website — para pasar a IT1 si tiene sitio propio
    website = None
    web_raw = item.get("website") or ""
    # También revisar lista "websites"
    if not web_raw:
        websites = item.get("websites") or []
        web_raw = next(
            (w for w in websites if w and "google.com/maps" not in w
             and "bing.com/maps" not in w),
            ""
        )
    if web_raw and not any(d in web_raw for d in ["instagram.com", "facebook.com"]):
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
    """Ejecuta actor y devuelve lista de items del dataset."""
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
    """Llama khadinakbar y parsea primer item."""
    log.info(f"  [khadinakbar] {url}")
    items = _run_actor(ACTOR_KHADINAKBAR, {
        "startUrls":         [{"url": url}],
        "followContactPages": True,
        "maxPagesPerDomain":  5,
    })
    if not items:
        return {"pages_crawled": 0}
    return parsear_khadinakbar(items[0])


def scrape_crawlworks(url: str) -> dict:
    """Llama crawlworks/ai-web-scraper como fallback JS."""
    log.info(f"  [crawlworks] {url}")
    items = _run_actor(ACTOR_CRAWLWORKS, {
        "url":       url,
        "prompt":    CRAWLWORKS_PROMPT,
        "useStealth": True,
    })
    if not items:
        return {}
    item = items[0]
    wa = None
    email = None
    ig = None
    fb = None

    # Buscar wzap en cualquier campo string del output
    for v in item.values():
        if not isinstance(v, str):
            continue
        for link in re.findall(r"https?://[^\s\"']+", v):
            n = extraer_numero_de_wame_url(link)
            if n and not wa:
                wa = n
        m = re.search(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", v)
        if m and validar_email(m.group(0)) and not email:
            email = m.group(0).lower()

    return {"whatsapp": wa, "email": email, "instagram": ig, "facebook": fb}


def scrape_instagram(username: str) -> dict:
    """Llama instagram-profile-scraper."""
    handle = username.lstrip("@")
    log.info(f"  [instagram-scraper] @{handle}")
    items = _run_actor(ACTOR_INSTAGRAM, {"usernames": [handle]})
    if not items:
        return {}
    return parsear_instagram(items[0])


def scrape_facebook(fb_url: str) -> dict:
    """Llama facebook-pages-scraper."""
    log.info(f"  [facebook-scraper] {fb_url}")
    items = _run_actor(ACTOR_FACEBOOK, {"startUrls": [{"url": fb_url}]})
    if not items:
        return {}
    return parsear_facebook(items[0])

# ============================================================
# MERGE DE RESULTADOS
# ============================================================

def merge(base: dict, nuevo: dict) -> dict:
    """
    Combina base con nuevo — nuevo solo pisa si base no tiene el campo.
    """
    resultado = dict(base)
    for k, v in nuevo.items():
        if v and not resultado.get(k):
            resultado[k] = v
    return resultado

# ============================================================
# ITERACIÓN 1 — SITIO PROPIO
# ============================================================

def iteracion_1(sitio_web: str) -> dict:
    """
    Enriquece desde sitio web propio.
    Retorna dict con: whatsapp, email, emails_extra, instagram, link_ig, facebook, notas_enrich
    """
    notas = []
    resultado = {}

    # PASO 1 — khadinakbar raíz
    r1 = scrape_khadinakbar(sitio_web)
    pages = r1.get("pages_crawled", 0)
    resultado = merge(resultado, r1)
    notas.append(f"khadinakbar raíz: pages={pages}")

    # PASO 2 — subpáginas si pages_crawled <= 1 o sin wzap+email
    if pages <= 1 or (not resultado.get("whatsapp") and not resultado.get("email")):
        base_url = sitio_web.rstrip("/")
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

    # PASO 3 — crawlworks si sigue completamente vacío
    if not resultado.get("whatsapp") and not resultado.get("email") and not resultado.get("instagram"):
        r3 = scrape_crawlworks(sitio_web)
        resultado = merge(resultado, r3)
        notas.append(
            f"crawlworks: wzap={'si' if r3.get('whatsapp') else 'no'} "
            f"email={'si' if r3.get('email') else 'no'}"
        )

    # PASO 4 — RE_WA_PROXIMITY si todavía sin WhatsApp
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
    """Enriquece desde perfil de Instagram."""
    r = scrape_instagram(ig_handle)
    if resultado_previo:
        r = merge(resultado_previo, r)

    # Si la bio tiene website propio → pasar por IT1
    website = r.pop("website", None)
    if website and not any(d in website for d in ["instagram.com", "facebook.com", "linktr.ee"]):
        log.info(f"  → IG bio tiene website: {website} → IT1")
        r_web = iteracion_1(website)
        r = merge(r, r_web)

    r["origen_contacto"] = "ig-auto"
    return r

# ============================================================
# ITERACIÓN 3 — FACEBOOK
# ============================================================

def iteracion_3(fb_url: str, resultado_previo: dict = None) -> dict:
    """Enriquece desde página de Facebook."""
    r = scrape_facebook(fb_url)
    if resultado_previo:
        r = merge(resultado_previo, r)

    # Si la página tiene website propio → pasar por IT1
    website = r.pop("website", None)
    if website and not any(d in website for d in ["instagram.com", "facebook.com", "linktr.ee"]):
        log.info(f"  → FB page tiene website: {website} → IT1")
        r_web = iteracion_1(website)
        r = merge(r, r_web)

    r["origen_contacto"] = "fb-auto"
    return r

# ============================================================
# ENRIQUECEDOR POR LEAD
# ============================================================

def enrich_lead(lead: dict) -> dict | None:
    """
    Decide qué iteraciones correr según el sitio_web del lead.
    Retorna dict de campos para actualizar en Supabase, o None si se saltea.
    """
    nombre    = lead.get("nombre", "")
    sitio_web = (lead.get("sitio_web") or "").strip()

    resultado = {}

    if not sitio_web:
        log.info("  → Sin sitio web — saltando")
        return None

    es_ig       = any(d in sitio_web for d in DOMINIOS_IG)
    es_fb       = any(d in sitio_web for d in DOMINIOS_FB)
    es_reservas = any(d in sitio_web for d in DOMINIOS_RESERVAS)

    if es_ig:
        handle = sitio_web.rstrip("/").split("/")[-1].lstrip("@")
        if not handle or handle.lower() in {"instagram", "p"}:
            log.info("  → IG URL inválida — saltando")
            return None
        log.info(f"  → IT2 (IG): @{handle}")
        resultado = iteracion_2(handle)

    elif es_fb:
        log.info(f"  → IT3 (FB): {sitio_web}")
        resultado = iteracion_3(sitio_web)

    elif es_reservas:
        log.info(f"  → Sitio de reservas ({sitio_web}) — saltando IT1, buscando IG/FB")
        resultado = {"notas_enrich": f"sitio reservas: {sitio_web}"}

    else:
        # Sitio propio → IT1
        log.info(f"  → IT1 (web): {sitio_web}")
        resultado = iteracion_1(sitio_web)

        # Si IT1 encontró IG → IT2 para completar
        if resultado.get("instagram") and not resultado.get("whatsapp"):
            log.info(f"  → IT2 complementario desde IG encontrado en IT1")
            resultado = iteracion_2(resultado["instagram"], resultado)

        # Si IT1 encontró FB → IT3 para completar
        if resultado.get("facebook") and not resultado.get("whatsapp"):
            log.info(f"  → IT3 complementario desde FB encontrado en IT1")
            resultado = iteracion_3(resultado["facebook"], resultado)

    # Construir campos para Supabase
    wa          = resultado.get("whatsapp")
    email       = resultado.get("email")
    ig          = resultado.get("instagram")
    ig_url      = resultado.get("link_ig")
    fb          = resultado.get("facebook")
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
# REPORTE
# ============================================================

def generar_reporte(output_path: str = "eurocrem_debug_report_v4.txt"):
    result = supabase.table("leads").select(
        "nombre, barrio, sitio_web, whatsapp, email, instagram, facebook, notas, origen_contacto"
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
        f"EUROCREM — Debug Report v4.1 — {ahora}",
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
    log.info(f"EUROCREM enrich_v4.1 — {total} leads a procesar")
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
    log.info("=== EUROCREM enrich_v4.1 START ===")
    run()
    generar_reporte()
    log.info("=== EUROCREM enrich_v4.1 DONE ===")

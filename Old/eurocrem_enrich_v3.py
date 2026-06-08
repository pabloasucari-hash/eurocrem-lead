"""
EUROCREM — Fase 2: Enriquecimiento automático
Versión: 3.0 — 04/06/2026

Fuentes en cascada:
  1. Sitio web propio → email, wa.me, redes
  2. Linktree / bio links → WhatsApp, email
  3. Instagram pública → handle, email en bio
  4. Serper API → Google Search legal (2.500/mes gratis)
  5. Directorios argentinos: Guía Óleo, Restaurantes.com.ar, Dondevamos
  6. TripAdvisor → tel, distinciones
  7. Meitre / Woki → tel, web
  8. Prensa: Infobae, La Nación, Clarín → dueño, WA, IG

Reglas:
  - NUNCA pisa campos con origen_contacto IN ('IG-manual', 'FB-manual')
  - Solo actualiza campos vacíos
  - Valida WhatsApp: solo números argentinos (+54)
  - Valida email: filtro de emails falsos/imagen
"""

import os
import re
import time
import random
import logging
from urllib.parse import quote_plus, urljoin, urlparse
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from bs4 import BeautifulSoup
from supabase import create_client, Client

# ============================================================
# CONFIGURACIÓN
# ============================================================

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://crbmgsmmvfkbxrplqfkl.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNyYm1nc21tdmZrYnhycGxxZmtsIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MDUxNjQ0NywiZXhwIjoyMDk2MDkyNDQ3fQ.7D5od9xbpGP3XbdjrTpgCtL7xWYpeJ6xaa5cBsMsju8")
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "4cf15ee62762c395892014591766696919bb4205")

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# ============================================================
# CLIENTE SUPABASE
# ============================================================

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ============================================================
# CONSTANTES Y PATRONES
# ============================================================

EMAIL_REGEX = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)
WAME_REGEX = re.compile(r"wa\.me/(\d{10,15})")
# Formatos adicionales de link WhatsApp:
#   api.whatsapp.com/send?phone=549...
#   api.whatsapp.com/send/?phone=549...
#   web.whatsapp.com/send?phone=549...
#   whatsapp://send?phone=549...
WHATSAPP_PHONE_REGEX = re.compile(
    r"(?:api|web)?\.?whatsapp\.com/(?:send/?)?\?phone=(\d{10,15})"
)
WHATSAPP_PROTO_REGEX = re.compile(r"whatsapp://send\?phone=(\d{10,15})")
PHONE_ARG_REGEX = re.compile(
    r"(?:\+?54\s?)?(?:9\s?)?(?:11|15)\s?[\d\s\-]{8,12}"
)
IG_HANDLE_REGEX = re.compile(
    r"instagram\.com/([a-zA-Z0-9._]+)(?:/|\?|$)"
)
FB_PAGE_REGEX = re.compile(
    r"facebook\.com/(?:pages/[^/]+/\d+|[a-zA-Z0-9._\-]+)(?:/|\?|$)"
)

# Emails falsos / imágenes a filtrar
EMAIL_BLACKLIST_DOMAINS = {
    # Plataformas de desarrollo / hosting
    "example.com", "test.com", "sentry.io", "wixpress.com",
    "squarespace.com", "shopify.com", "wordpress.com",
    # Medios de prensa argentinos
    "clarin.com", "lanacion.com.ar", "infobae.com", "pagina12.com.ar",
    "telam.com.ar", "cronista.com", "ambito.com", "perfil.com",
    # Directorios / plataformas de delivery y reservas
    "rappi.com", "pedidosya.com", "ifood.com", "ubereats.com",
    "tripadvisor.com", "tripadvisor.com.ar", "yelp.com",
    "guiaoleo.com", "restaurantes.com.ar", "dondevamos.com",
    "meitre.com", "woki.com", "opentable.com",
    "thefork.com", "eltenedor.com",
    # Redes sociales
    "instagram.com", "facebook.com", "twitter.com", "tiktok.com",
    # Google / Meta
    "google.com", "gmail.com", "googlemail.com",
    "support.google.com", "maps.google.com",
}
EMAIL_BLACKLIST_PATTERNS = re.compile(
    r"\.(png|jpg|gif|svg|webp|ico|css|js)$", re.IGNORECASE
)

# User agents rotativos para evitar bloqueos
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

# ============================================================
# UTILIDADES HTTP
# ============================================================

def get_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }

def fetch_url(url: str, timeout: int = 10) -> str | None:
    """Fetch URL con retry y manejo de errores."""
    if not url:
        return None
    if not url.startswith("http"):
        url = "https://" + url
    # Saltar URLs que sabemos que no sirven sin login
    skip_domains = ["instagram.com", "facebook.com", "wa.me", "whatsapp.com"]
    if any(d in url for d in skip_domains):
        return None
    try:
        resp = requests.get(
            url,
            headers=get_headers(),
            timeout=timeout,
            allow_redirects=True,
            verify=False
        )
        if resp.status_code == 200:
            return resp.text
        elif resp.status_code in (403, 429):
            log.info(f"  ⛔ Sitio bloqueó el acceso ({resp.status_code}): {url}")
        elif resp.status_code == 404:
            log.info(f"  ❌ Sitio no encontrado (404): {url}")
        else:
            log.info(f"  ⚠️  HTTP {resp.status_code}: {url}")
    except requests.exceptions.Timeout:
        log.info(f"  ⏱️  Timeout al acceder: {url}")
    except requests.exceptions.ConnectionError:
        log.info(f"  ⛔ Sin conexión / dominio no existe: {url}")
    except Exception as e:
        log.debug(f"fetch_url error {url}: {e}")
    return None

def fetch_instagram_public(handle: str) -> str | None:
    """Fetch de perfil público de Instagram (sin login) — solo bio."""
    handle = handle.lstrip("@")
    url = f"https://www.instagram.com/{handle}/"
    try:
        resp = requests.get(url, headers=get_headers(), timeout=10)
        if resp.status_code == 200:
            return resp.text
    except Exception as e:
        log.debug(f"Instagram fetch error {handle}: {e}")
    return None

# ============================================================
# VALIDADORES
# ============================================================

def validar_email(email: str) -> bool:
    """Valida que el email sea real y no una imagen o dominio blacklisteado.
    Rechaza emails con caracteres no-válidos pegados (ej: PARADISOinfo@, combottom)."""
    if not email or len(email) < 5:
        return False
    
    # Rechazar si tiene caracteres no-email antes del @ 
    # (debe ser alfanumérico + puntos/guiones/underscores)
    # Pero rechaza claramente si tiene MAYÚSCULAS pegadas (PARADISOinfo es basura)
    local_part = email.split("@")[0]
    if not all(c.isalnum() or c in "._-" for c in local_part):
        return False
    
    # Rechazar si tiene caracteres no-válidos pegados después del dominio
    # (ej: combottom, /bottom, -bottom)
    domain = email.split("@")[-1].lower()
    if not all(c.isalnum() or c in ".-" for c in domain):
        return False
    
    if EMAIL_BLACKLIST_PATTERNS.search(email):
        return False
    if domain in EMAIL_BLACKLIST_DOMAINS:
        return False
    
    # Debe tener al menos un punto en el dominio
    if "." not in domain:
        return False
    
    # Patrón final: debe ser email válido
    if not EMAIL_REGEX.match(email):
        return False
    
    # Última validación: rechazar emails "pegajosos" como PARADISOinfo@ 
    # (tiene secuencias de MAYÚSCULAS + minúsculas sin separar, típico de HTML roto)
    if re.search(r"[A-Z]{2,}[a-z]+@", email):
        # "PARADISOinfo@" tiene PARADISO en MAYÚSCULAS pegado a "info" en minúsculas
        # Eso es típico de HTML donde había <div class="PARADISO">info@...
        return False
    
    return True

def telefono_a_whatsapp(telefono: str) -> str | None:
    """
    Convierte el teléfono de Places API a WhatsApp SOLO si es celular argentino.
    En CABA el celular se identifica por el '15' tras el código de área (011 15-XXXX-XXXX)
    o por el prefijo de celular. Los fijos (011 4XXX-XXXX, 011 5XXX-XXXX sin 15) se descartan.

    Ejemplos:
      '011 15-6587-4967' → +54 9 11 6587-4967  (celular)
      '011 4815-4506'    → None                (fijo)
    """
    if not telefono:
        return None

    digits = re.sub(r"[^\d]", "", telefono)

    # Sacar prefijo internacional/nacional
    if digits.startswith("54"):
        digits = digits[2:]
    if digits.startswith("0"):
        digits = digits[1:]

    # Caso 1: formato CABA con 15 explícito → "11 15 XXXXXXXX"
    # Después de sacar 0: "1115XXXXXXXX" (12 dígitos) = 11 + 15 + 8 dígitos
    if digits.startswith("11") and digits[2:4] == "15" and len(digits) == 12:
        numero = digits[4:]  # los 8 dígitos del celular
        return f"+54 9 11 {numero[:4]}-{numero[4:]}"

    # Caso 2: "15 XXXXXXXX" sin código de área (9 o 10 dígitos)
    if digits.startswith("15") and len(digits) == 10:
        numero = digits[2:]
        return f"+54 9 11 {numero[:4]}-{numero[4:]}"

    # Caso 3: ya viene como celular 11 + 8 dígitos con 9 adelante (formato celular)
    if digits.startswith("9") and len(digits) == 11 and digits[1:3] == "11":
        numero = digits[3:]
        return f"+54 9 11 {numero[:4]}-{numero[4:]}"

    # Si no es claramente celular, no lo tratamos como WhatsApp
    return None

def normalizar_whatsapp(numero: str) -> str | None:
    """
    Normaliza número a formato +54 9 11 XXXX-XXXX.
    Solo acepta números argentinos.
    """
    if not numero:
        return None
    # Limpiar todo lo que no sea dígito
    digits = re.sub(r"[^\d]", "", numero)

    # Debe empezar con 54 o ser número local argentino
    if digits.startswith("54"):
        digits = digits[2:]  # sacar el 54
    elif digits.startswith("0"):
        digits = digits[1:]  # sacar el 0 inicial
    
    # Debe tener entre 10 y 11 dígitos después de sacar el 54
    if len(digits) < 10 or len(digits) > 11:
        return None

    # Debe ser de Buenos Aires (11) o celular (9 11)
    if digits.startswith("9"):
        digits = digits[1:]  # sacar el 9
    
    if not digits.startswith("11") and not digits.startswith("15"):
        # Podría ser otro área de Argentina — aceptar igual
        if len(digits) == 10:
            return f"+54 9 {digits[:2]} {digits[2:6]}-{digits[6:]}"
        return None

    # Formato final
    if len(digits) == 10:
        return f"+54 9 {digits[:2]} {digits[2:6]}-{digits[6:]}"
    
    return None

def extraer_whatsapp_de_wame(html: str) -> str | None:
    """Extrae número de WhatsApp desde links (ícono del footer) o texto plano.
    Cubre: wa.me/, api.whatsapp.com/send?phone=, web.whatsapp.com, whatsapp://send, tel:
    También busca números cercanos a la palabra "WHATSAPP" en el texto.
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            try:
                soup = BeautifulSoup(html, "html5lib")
            except Exception:
                # Si parsear falla, buscar con regex puro
                log.debug("  HTML roto para WhatsApp, usando regex puro")
                soup = None

    # 1. PRIORIDAD: links <a href> que apuntan a WhatsApp (si soup está OK)
    if soup:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "whatsapp" not in href.lower() and "wa.me" not in href.lower() and "tel:" not in href.lower():
                continue
            # Probar todos los formatos de link
            for regex in (WAME_REGEX, WHATSAPP_PHONE_REGEX, WHATSAPP_PROTO_REGEX):
                m = regex.search(href)
                if m:
                    normalizado = normalizar_whatsapp(m.group(1))
                    if normalizado:
                        return normalizado
            # También buscar número en links tel:
            if href.startswith("tel:"):
                numero = href.replace("tel:", "").strip()
                normalizado = normalizar_whatsapp(numero)
                if normalizado:
                    return normalizado

    # 2. Mismos patrones de link pero en el HTML crudo (por si no es un <a>)
    for regex in (WAME_REGEX, WHATSAPP_PHONE_REGEX, WHATSAPP_PROTO_REGEX):
        for m in regex.findall(html):
            normalizado = normalizar_whatsapp(m)
            if normalizado:
                return normalizado

    # 3. Buscar números CERCANOS a la palabra "WHATSAPP" en el HTML
    #    Patrón: "WHATSAPP" seguido por 1-3 líneas y un número argentino
    WA_PROXIMITY = re.compile(
        r"whatsapp[^a-z0-9]*?((?:\+?54\s?)?(?:9\s?)?(?:11|15|\d{2,3})\s?[\d.\s\-]{6,14})",
        re.IGNORECASE | re.DOTALL
    )
    for match in WA_PROXIMITY.finditer(html):
        numero = normalizar_whatsapp(match.group(1))
        if numero:
            return numero

    # 4. Texto plano cerca de la palabra "whatsapp"
    if soup:
        texto = soup.get_text(" ", strip=True)
    else:
        # Si no hay soup, extraer texto manualmente
        texto = re.sub(r"<[^>]+>", " ", html)
    
    WA_KEYWORDS = re.compile(
        r"(?:whatsapp|wsp|wpp)\s*[:\-]?\s*((?:\+?54\s?)?(?:9\s?)?(?:11|15|\d{2,3})\s?[\d\s\-]{6,12})",
        re.IGNORECASE
    )
    for match in WA_KEYWORDS.finditer(texto):
        numero = normalizar_whatsapp(match.group(1))
        if numero:
            return numero

    # 5. Número argentino precedido por íconos de teléfono/WhatsApp
    FOOTER_WA = re.compile(r"[©☎📲📱💬]\s*((?:11|15)\s?[\d\s\-]{7,10})")
    for match in FOOTER_WA.finditer(texto):
        numero = normalizar_whatsapp(match.group(1))
        if numero:
            return numero

    return None

def construir_wame(numero: str) -> str | None:
    """Construye link wa.me desde número normalizado."""
    if not numero:
        return None
    digits = re.sub(r"[^\d]", "", numero)
    if not digits.startswith("54"):
        digits = "54" + digits.lstrip("0")
    return f"https://wa.me/{digits}"

# ============================================================
# EXTRACTORES GENÉRICOS
# ============================================================

def extraer_emails_de_html(html: str, nombre: str = "") -> list:
    """Extrae emails de HTML — mailto: primero, luego texto plano.
    Si se pasa nombre, filtra emails cuyo dominio no tiene relación con el restaurante.
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        # Fallback a parsers más tolerantes
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            try:
                soup = BeautifulSoup(html, "html5lib")
            except Exception:
                # Si todo falla, extraer con regex puro sin parsear
                log.debug("  HTML roto, extrayendo emails con regex puro")
                return [e for e in EMAIL_REGEX.findall(html) if validar_email(e.lower())]
    
    emails = []

    # mailto: links (más confiables)
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("mailto:"):
            email = href.replace("mailto:", "").split("?")[0].strip().lower()
            if validar_email(email):
                emails.append(("mailto", email))

    # Texto plano
    texto = soup.get_text()
    for match in EMAIL_REGEX.finditer(texto):
        email = match.group().lower()
        if validar_email(email):
            emails.append(("texto", email))

    # Priorizar: mailto > palabras clave de negocio > resto
    keywords = ["evento", "reserva", "contacto", "info", "ventas", "marketing", "hola", "admin"]

    resultado = []
    # 1. mailto con keywords
    for fuente, e in emails:
        if fuente == "mailto" and any(k in e for k in keywords):
            resultado.append(e)
    # 2. mailto sin keywords
    for fuente, e in emails:
        if fuente == "mailto" and e not in resultado:
            resultado.append(e)
    # 3. texto con keywords
    for fuente, e in emails:
        if fuente == "texto" and any(k in e for k in keywords) and e not in resultado:
            resultado.append(e)
    # 4. resto
    for fuente, e in emails:
        if e not in resultado:
            resultado.append(e)

    resultado = list(dict.fromkeys(resultado))  # dedup preservando orden

    # Filtrar emails cuyo dominio no tiene relación con el nombre del restaurante
    # Solo aplica cuando la URL origen NO es el sitio propio del restaurante
    # (es decir, cuando venimos de un directorio o página de terceros)
    if nombre:
        tokens = _tokens_nombre(nombre)
        if tokens:
            filtrados = [e for e in resultado if _url_es_relevante(e.split("@")[-1], nombre)]
            # Si el filtro dejó algo, usarlo; si no, devolver vacío (mejor nada que basura)
            resultado = filtrados

    return resultado

def extraer_instagram_de_html(html: str) -> str | None:
    """Extrae handle de Instagram del HTML."""
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        match = IG_HANDLE_REGEX.search(href)
        if match:
            handle = match.group(1)
            # Filtrar handles genéricos
            if handle.lower() not in ("instagram", "p", "reel", "stories", "explore", "sharer"):
                return f"@{handle}"
    # Buscar en texto
    match = IG_HANDLE_REGEX.search(html)
    if match:
        handle = match.group(1)
        if handle.lower() not in ("instagram", "p", "reel", "stories", "explore"):
            return f"@{handle}"
    return None

def _tokens_nombre(nombre: str) -> set:
    """Extrae tokens significativos del nombre del restaurante para comparar con URLs."""
    stopwords = {"el", "la", "los", "las", "de", "del", "al", "y", "e", "parrilla",
                 "restaurante", "bar", "cafe", "bistro", "grill", "resto", "casa"}
    tokens = re.findall(r"[a-záéíóúñü]+", nombre.lower())
    return {t for t in tokens if t not in stopwords and len(t) > 2}

def _url_es_relevante(url: str, nombre: str) -> bool:
    """True si la URL tiene al menos un token del nombre del restaurante."""
    tokens = _tokens_nombre(nombre)
    if not tokens:
        return True  # sin tokens no podemos filtrar, aceptar
    url_lower = url.lower()
    return any(t in url_lower for t in tokens)

def extraer_facebook_de_html(html: str, nombre: str = "") -> str | None:
    """Extrae URL de página de Facebook del HTML, validando que sea del restaurante."""
    # Paths genéricos de Facebook que no son páginas de negocios
    PATHS_IGNORADOS = {
        "sharer", "share", "dialog", "login", "watch", "groups",
        "events", "marketplace", "gaming", "pages/create",
        # Medios conocidos
        "lavoz.com.ar", "pagina12ok", "michelinguideworldwide",
        "clarin", "infobae", "lanacion",
    }
    soup = BeautifulSoup(html, "html.parser")
    candidatos = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "facebook.com/" not in href:
            continue
        url_limpia = href.split("?")[0].rstrip("/")
        partes = url_limpia.split("facebook.com/")
        if len(partes) < 2:
            continue
        path = partes[1].lower()
        if not path or any(ig in path for ig in PATHS_IGNORADOS):
            continue
        candidatos.append(url_limpia)

    if not candidatos:
        return None

    # Si tenemos nombre, priorizar URLs que lo contengan
    if nombre:
        for url in candidatos:
            if _url_es_relevante(url, nombre):
                return url
        # Ninguna matchea el nombre — no guardar basura
        return None

    return candidatos[0]

def extraer_links_contacto(html: str, base_url: str) -> list:
    """Encuentra links a páginas de contacto/reservas dentro del sitio.
    Esas páginas suelen tener el WhatsApp/email que no está en la home."""
    soup = BeautifulSoup(html, "html.parser")
    keywords = ["contacto", "contact", "reserva", "reservas", "pedidos",
                "delivery", "donde-estamos", "ubicacion", "locales"]
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        texto_link = a.get_text().lower()
        href_lower = href.lower()
        if any(k in href_lower or k in texto_link for k in keywords):
            url = urljoin(base_url, href)
            # Solo links del mismo dominio
            if urlparse(url).netloc == urlparse(base_url).netloc:
                links.append(url)
    return list(dict.fromkeys(links))[:3]  # máximo 3 páginas internas

def extraer_links_bio(html: str) -> list:
    """Extrae links de bio (Linktree, etc.) del HTML."""
    soup = BeautifulSoup(html, "html.parser")
    bio_domains = ["linktr.ee", "linkin.bio", "bio.link", "beacons.ai", "taplink.cc", "ola.click"]
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if any(d in href for d in bio_domains):
            links.append(href)
    return list(set(links))

def detectar_sitio_es_instagram(url: str) -> str | None:
    """Si el sitio web ES instagram.com/handle, devuelve el handle."""
    if not url:
        return None
    match = IG_HANDLE_REGEX.search(url)
    if match and "instagram.com" in url:
        handle = match.group(1)
        if handle.lower() not in ("p", "reel", "stories", "explore"):
            return f"@{handle}"
    return None

# ============================================================
# SERPER API — Google Search legal
# ============================================================

def google_search(query: str, num_results: int = 8) -> list:
    """
    Búsqueda via Serper.dev — devuelve lista de (url, snippet).
    Mantiene compatibilidad: si el caller solo usa el primer elemento, sigue funcionando.
    """
    if not SERPER_API_KEY:
        log.warning("SERPER_API_KEY no configurada — saltando búsqueda web")
        return []
    try:
        resp = requests.post(
            "https://google.serper.dev/search",
            headers={
                "X-API-KEY": SERPER_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "q": query,
                "num": min(num_results, 10),
                "gl": "ar",
                "hl": "es",
            },
            timeout=10,
        )
        if resp.status_code != 200:
            log.debug(f"Serper status {resp.status_code}: {resp.text[:200]}")
            return []
        data = resp.json()
        resultados = []
        for item in data.get("organic", []):
            link = item.get("link", "")
            snippet = item.get("snippet", "")
            if link and "google.com" not in link:
                resultados.append((link, snippet))
        # Knowledge Graph
        kg = data.get("knowledgeGraph", {})
        if kg.get("website"):
            resultados.append((kg["website"], ""))
        log.debug(f"Serper → {len(resultados)} resultados para: {query[:60]}")
        return resultados
    except Exception as e:
        log.debug(f"Serper error: {e}")
        return []

def serper_extraer_contacto_de_snippet(snippet: str) -> dict:
    """
    Extrae email y WhatsApp del snippet de Google (bio de Instagram/Facebook).
    El snippet es el texto corto que Google muestra debajo del título.
    """
    datos = {}
    if not snippet:
        return datos

    # Email
    for match in EMAIL_REGEX.finditer(snippet):
        email = match.group().lower()
        if validar_email(email):
            datos["email"] = email
            break

    # WhatsApp — wa.me o número plano
    wame = extraer_whatsapp_de_wame(snippet)
    if wame:
        datos["whatsapp"] = wame
        datos["link_wame"] = construir_wame(wame)

    return datos

def clasificar_url(url: str) -> str:
    """Clasifica una URL por tipo de fuente."""
    url_lower = url.lower()
    if "instagram.com" in url_lower:
        return "instagram"
    if "facebook.com" in url_lower:
        return "facebook"
    if "tripadvisor.com" in url_lower or "tripadvisor.com.ar" in url_lower:
        return "skip"  # bloquea siempre con 403, no vale la pena intentar
    if "guiaoleocom" in url_lower or "guiaoleo.com" in url_lower:
        return "guiaoleo"
    if "restaurantes.com.ar" in url_lower:
        return "restaurantes_ar"
    if "dondevamos.com" in url_lower:
        return "dondevamos"
    if "meitre.com" in url_lower:
        return "meitre"
    if "woki.com" in url_lower:
        return "woki"
    if "infobae.com" in url_lower or "lanacion.com.ar" in url_lower or "clarin.com" in url_lower \
            or "pagina12.com.ar" in url_lower or "perfil.com" in url_lower or "ambito.com" in url_lower:
        return "prensa"
    if "rappi.com" in url_lower or "pedidosya.com" in url_lower or "ubereats.com" in url_lower \
            or "ifood.com" in url_lower:
        return "delivery"  # nunca tiene email del restaurante
    if "linktr.ee" in url_lower or "bio.link" in url_lower:
        return "biolink"
    return "web"

# ============================================================
# EXTRACTORES POR FUENTE ESPECÍFICA
# ============================================================

def extraer_de_tripadvisor(html: str) -> dict:
    """Extrae datos específicos de TripAdvisor."""
    datos = {}
    soup = BeautifulSoup(html, "html.parser")
    
    # Teléfono
    for elem in soup.find_all(string=re.compile(r"\+?54\s?\d")):
        tel = elem.strip()
        if re.search(r"\d{8,}", tel):
            datos["telefono_ta"] = tel
            break

    # Distinciones
    distinciones = []
    texto = soup.get_text()
    if "Travelers' Choice" in texto or "Travellers' Choice" in texto:
        distinciones.append("TripAdvisor Travellers' Choice")
    if "Certificate of Excellence" in texto:
        distinciones.append("TripAdvisor Certificate of Excellence")
    if distinciones:
        datos["guias"] = " | ".join(distinciones)

    # Web oficial
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "website" in a.get("data-tracker", "").lower() or "website" in str(a.get("class", "")).lower():
            datos["web_ta"] = href
            break

    return datos

def extraer_de_guiaoleo(html: str, nombre: str = "") -> dict:
    """Extrae datos de Guía Óleo y directorios similares.
    Tolerante a HTML roto (entidades malformadas)."""
    datos = {}
    
    try:
        # Intentar parsear para validar HTML
        soup = BeautifulSoup(html, "html.parser")
    except (ValueError, Exception) as e:
        # HTML roto — intentar con parsers más tolerantes
        log.debug(f"  ⚠️ HTML roto de Guía Óleo: {str(e)[:60]}")
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            try:
                soup = BeautifulSoup(html, "html5lib")
            except Exception:
                # Último recurso: extraer sin parsear, solo regex
                log.warning(f"  ⚠️ No se puede parsear Guía Óleo de {nombre}")
                return {}

    # Extraer datos — protegido con try-catch
    try:
        emails = extraer_emails_de_html(html, nombre)
        if emails:
            datos["email"] = emails[0]
    except Exception as e:
        log.debug(f"  Error extrayendo email de Guía Óleo: {e}")

    try:
        wame = extraer_whatsapp_de_wame(html)
        if wame:
            datos["whatsapp"] = wame
    except Exception as e:
        log.debug(f"  Error extrayendo WhatsApp de Guía Óleo: {e}")

    try:
        ig = extraer_instagram_de_html(html)
        if ig:
            datos["instagram"] = ig
    except Exception as e:
        log.debug(f"  Error extrayendo IG de Guía Óleo: {e}")

    return datos

def extraer_de_prensa(html: str) -> dict:
    """Extrae datos de artículos de prensa gastronómica."""
    datos = {}
    texto = BeautifulSoup(html, "html.parser").get_text()

    # WhatsApp mencionado en texto
    wame = extraer_whatsapp_de_wame(html)
    if wame:
        datos["whatsapp"] = wame

    # Email mencionado
    emails = extraer_emails_de_html(html)
    if emails:
        datos["email"] = emails[0]

    # Instagram mencionado
    ig = extraer_instagram_de_html(html)
    if ig:
        datos["instagram"] = ig

    # Dueño / decisor — buscar patrones como "el dueño X" "el chef X"
    patron_decisor = re.compile(
        r"(?:dueño|propietario|chef|cocinero|gerente|socio)[a-z\s,]*?([A-Z][a-záéíóúñ]+\s[A-Z][a-záéíóúñ]+)",
        re.IGNORECASE
    )
    match = patron_decisor.search(texto)
    if match:
        datos["decisor"] = match.group(1).strip()

    return datos

# ============================================================
# ENRIQUECEDOR PRINCIPAL POR LEAD
# ============================================================

def enriquecer_lead(lead: dict) -> dict:
    """
    Enriquece un lead usando todas las fuentes disponibles.
    Devuelve dict con campos a actualizar (solo los que encontró).
    """
    nombre = lead["nombre"]
    barrio = lead.get("barrio", "Buenos Aires")
    sitio_web = lead.get("sitio_web", "")
    origen = lead.get("origen_contacto", "pendiente")

    # Si el origen es manual → no tocar nada
    if origen in ("IG-manual", "FB-manual"):
        log.info(f"  Saltando {nombre} — origen manual protegido")
        return {}

    acumulado = {}  # campos encontrados en esta corrida

    def ya_tiene(campo):
        return bool(lead.get(campo)) or bool(acumulado.get(campo))

    def guardar(campo, valor, fuente=""):
        if not ya_tiene(campo) and valor:
            acumulado[campo] = valor
            if fuente:
                log.info(f"    ✓ {campo}: {valor} [{fuente}]")

    nombre_q = nombre.title()

    # ----------------------------------------------------------
    # PASO 0: Teléfono de Places → WhatsApp (si es celular)
    # Gratis, instantáneo, ya está en la base. Prioridad máxima.
    # ----------------------------------------------------------
    telefono = lead.get("telefono", "")
    if telefono and not ya_tiene("whatsapp"):
        wa_tel = telefono_a_whatsapp(telefono)
        if wa_tel:
            guardar("whatsapp", wa_tel, "telefono_places")
            guardar("link_wame", construir_wame(wa_tel), "telefono_places")

    # ----------------------------------------------------------
    # DETECTAR TIPO DE SITIO WEB
    # ----------------------------------------------------------
    es_instagram = sitio_web and "instagram.com" in sitio_web
    es_facebook  = sitio_web and "facebook.com" in sitio_web
    es_linktree  = sitio_web and any(d in sitio_web for d in ["linktr.ee", "linkin.bio", "bio.link", "beacons.ai", "taplink.cc"])
    es_web_real  = sitio_web and not es_instagram and not es_facebook and not es_linktree

    # ----------------------------------------------------------
    # CAMINO A: Sitio web real → fetch + BeautifulSoup
    # ----------------------------------------------------------
    if es_web_real:
        log.info(f"  🌐 Fetching sitio web: {sitio_web}")
        html_web = fetch_url(sitio_web)
        if html_web:
            log.info(f"  ✅ Sitio web OK — extrayendo datos")
            emails = extraer_emails_de_html(html_web)
            if emails:
                guardar("email", emails[0], "sitio_web")
            wame = extraer_whatsapp_de_wame(html_web)
            guardar("whatsapp", wame, "sitio_web")
            if wame:
                guardar("link_wame", construir_wame(wame), "sitio_web")
            guardar("instagram", extraer_instagram_de_html(html_web), "sitio_web")
            guardar("facebook", extraer_facebook_de_html(html_web, nombre), "sitio_web")

            # Bio links (Linktree, etc.)
            for bio_url in extraer_links_bio(html_web):
                html_bio = fetch_url(bio_url)
                if html_bio:
                    if not ya_tiene("whatsapp"):
                        wame2 = extraer_whatsapp_de_wame(html_bio)
                        guardar("whatsapp", wame2, "biolink")
                        if wame2:
                            guardar("link_wame", construir_wame(wame2), "biolink")
                    if not ya_tiene("email"):
                        emails2 = extraer_emails_de_html(html_bio)
                        if emails2:
                            guardar("email", emails2[0], "biolink")
                    if not ya_tiene("instagram"):
                        guardar("instagram", extraer_instagram_de_html(html_bio), "biolink")

            # Páginas internas de contacto/reservas (el WA suele estar ahí, no en la home)
            if not ya_tiene("whatsapp") or not ya_tiene("email"):
                for cont_url in extraer_links_contacto(html_web, sitio_web):
                    log.info(f"    🔎 Visitando página interna: {cont_url}")
                    html_cont = fetch_url(cont_url)
                    if not html_cont:
                        continue
                    time.sleep(random.uniform(0.2, 0.5))
                    if not ya_tiene("whatsapp"):
                        wame3 = extraer_whatsapp_de_wame(html_cont)
                        guardar("whatsapp", wame3, "pagina_contacto")
                        if wame3:
                            guardar("link_wame", construir_wame(wame3), "pagina_contacto")
                    if not ya_tiene("email"):
                        emails3 = extraer_emails_de_html(html_cont)
                        if emails3:
                            guardar("email", emails3[0], "pagina_contacto")
                    # Cortar apenas tengamos ambos
                    if ya_tiene("whatsapp") and ya_tiene("email"):
                        break

    # ----------------------------------------------------------
    # CAMINO B: Linktree como sitio web → fetch directo
    # ----------------------------------------------------------
    elif es_linktree:
        log.info(f"  🔗 Fetching Linktree: {sitio_web}")
        html_bio = fetch_url(sitio_web)
        if html_bio:
            log.info(f"  ✅ Linktree OK — extrayendo datos")
            wame = extraer_whatsapp_de_wame(html_bio)
            guardar("whatsapp", wame, "linktree")
            if wame:
                guardar("link_wame", construir_wame(wame), "linktree")
            emails = extraer_emails_de_html(html_bio)
            if emails:
                guardar("email", emails[0], "linktree")
            guardar("instagram", extraer_instagram_de_html(html_bio), "linktree")
            guardar("facebook", extraer_facebook_de_html(html_bio, nombre), "linktree")

    # ----------------------------------------------------------
    # CAMINO C: Instagram como sitio web → extraer handle + Serper bio
    # ----------------------------------------------------------
    elif es_instagram:
        ig_match = IG_HANDLE_REGEX.search(sitio_web)
        if ig_match:
            handle = ig_match.group(1)
            if handle.lower() not in ("p", "reel", "stories", "explore"):
                guardar("instagram", f"@{handle}", "sitio_web=IG")
                guardar("link_ig", sitio_web.split("?")[0], "sitio_web=IG")
                # Buscar bio en Serper — el snippet tiene el contenido de la bio
                log.info(f"  📱 Instagram detectado — buscando bio via Serper: @{handle}")
                resultados = google_search(f"site:instagram.com/{handle}", num_results=3)
                for url, snippet in resultados:
                    if snippet:
                        datos = serper_extraer_contacto_de_snippet(snippet)
                        for campo, valor in datos.items():
                            guardar(campo, valor, "ig_snippet")

    # ----------------------------------------------------------
    # CAMINO D: Facebook como sitio web → extraer URL + Serper bio
    # ----------------------------------------------------------
    elif es_facebook:
        fb_url = sitio_web.split("?")[0]
        guardar("facebook", fb_url, "sitio_web=FB")
        # Extraer handle de Facebook para buscar en Serper
        fb_path = fb_url.rstrip("/").split("facebook.com/")[-1]
        if fb_path:
            log.info(f"  📘 Facebook detectado — buscando bio via Serper: {fb_path}")
            resultados = google_search(f"site:facebook.com/{fb_path}", num_results=3)
            for url, snippet in resultados:
                if snippet:
                    datos = serper_extraer_contacto_de_snippet(snippet)
                    for campo, valor in datos.items():
                        guardar(campo, valor, "fb_snippet")

    # ----------------------------------------------------------
    # PASO 2: Serper sobre Instagram conocido (cualquier camino)
    # ----------------------------------------------------------
    ig_handle = acumulado.get("instagram") or lead.get("instagram")
    if ig_handle and (not ya_tiene("email") or not ya_tiene("whatsapp")):
        handle_clean = ig_handle.lstrip("@")
        log.info(f"  📱 Buscando bio IG via Serper: @{handle_clean}")
        resultados = google_search(f"site:instagram.com/{handle_clean}", num_results=3)
        for url, snippet in resultados:
            if snippet:
                datos = serper_extraer_contacto_de_snippet(snippet)
                for campo, valor in datos.items():
                    guardar(campo, valor, "ig_snippet")

    # ----------------------------------------------------------
    # PASO 3: Serper sobre Facebook conocido (cualquier camino)
    # ----------------------------------------------------------
    fb_url = acumulado.get("facebook") or lead.get("facebook")
    if fb_url and (not ya_tiene("email") or not ya_tiene("whatsapp")):
        fb_path = fb_url.rstrip("/").split("facebook.com/")[-1]
        if fb_path and len(fb_path) > 2:
            log.info(f"  📘 Buscando bio FB via Serper: {fb_path}")
            resultados = google_search(f"site:facebook.com/{fb_path}", num_results=3)
            for url, snippet in resultados:
                if snippet:
                    datos = serper_extraer_contacto_de_snippet(snippet)
                    for campo, valor in datos.items():
                        guardar(campo, valor, "fb_snippet")

    # ----------------------------------------------------------
    # PASO 4: Serper sobre directorios (Guía Óleo, Meitre, Woki)
    # ----------------------------------------------------------
    if not ya_tiene("email") or not ya_tiene("whatsapp"):
        SITES_DIR = "site:guiaoleo.com OR site:restaurantes.com.ar OR site:meitre.com OR site:woki.com"
        resultados = google_search(f'"{nombre_q}" Buenos Aires {SITES_DIR}', num_results=5)
        log.info(f"  🔍 Directorios → {len(resultados)} resultados")
        for url, snippet in resultados:
            tipo = clasificar_url(url)
            if tipo in ("skip", "delivery", "instagram", "facebook"):
                continue
            # Primero intentar extraer del snippet
            if snippet and (not ya_tiene("email") or not ya_tiene("whatsapp")):
                datos = serper_extraer_contacto_de_snippet(snippet)
                for campo, valor in datos.items():
                    guardar(campo, valor, f"{tipo}_snippet")
            # Si no alcanza, fetchear la página
            if not ya_tiene("email") or not ya_tiene("whatsapp"):
                html = fetch_url(url)
                if html:
                    time.sleep(random.uniform(0.2, 0.5))
                    datos_dir = extraer_de_guiaoleo(html, nombre)
                    for campo, valor in datos_dir.items():
                        guardar(campo, valor, tipo)

    # ----------------------------------------------------------
    # PASO 5: Serper búsqueda libre si todavía falta instagram o facebook
    # ----------------------------------------------------------
    if not ya_tiene("instagram"):
        resultados = google_search(f'"{nombre_q}" Buenos Aires site:instagram.com', num_results=3)
        for url, snippet in resultados:
            ig_match = IG_HANDLE_REGEX.search(url)
            if ig_match:
                handle = ig_match.group(1)
                HANDLES_INVALIDOS = {"p", "reel", "reels", "stories", "explore", "sharer", "share"}
                if handle.lower() not in HANDLES_INVALIDOS:
                    guardar("instagram", f"@{handle}", "serper→IG")
                    guardar("link_ig", url.split("?")[0], "serper→IG")
                    # También intentar extraer contacto del snippet
                    if snippet:
                        datos = serper_extraer_contacto_de_snippet(snippet)
                        for campo, valor in datos.items():
                            guardar(campo, valor, "ig_snippet")
                    break

    if not ya_tiene("facebook"):
        resultados = google_search(f'"{nombre_q}" Buenos Aires site:facebook.com', num_results=3)
        for url, snippet in resultados:
            fb_path = url.rstrip("/").split("facebook.com/")[-1].split("?")[0]
            PATHS_INVALIDOS = {"sharer", "share", "dialog", "login", "watch", "groups", "events"}
            if fb_path and fb_path.lower() not in PATHS_INVALIDOS:
                guardar("facebook", f"https://www.facebook.com/{fb_path}", "serper→FB")
                if snippet:
                    datos = serper_extraer_contacto_de_snippet(snippet)
                    for campo, valor in datos.items():
                        guardar(campo, valor, "fb_snippet")
                break

    # ----------------------------------------------------------
    # PASO 5: Marcar origen_contacto si encontramos algo nuevo
    # ----------------------------------------------------------
    if acumulado and origen == "pendiente":
        acumulado["origen_contacto"] = "web-auto"

    return acumulado

# ============================================================
# RUNNER PRINCIPAL
# ============================================================

def enriquecer_todos():
    """Enriquece todos los leads pendientes o parcialmente enriquecidos."""

    # Traer leads que NO son manuales y les falta algún campo clave
    result = supabase.table("leads").select(
        "id, nombre, barrio, sitio_web, telefono, whatsapp, email, instagram, facebook, origen_contacto, guias, decisor"
    ).in_("origen_contacto", ["pendiente", "Maps-web", "web-auto"]).execute()

    leads = result.data
    total = len(leads)
    log.info(f"Enriqueciendo {total} leads...")

    for i, lead in enumerate(leads):
        nombre = lead["nombre"]
        log.info(f"\n[{i+1}/{total}] {nombre} ({lead.get('barrio','')})")

        # Verificar si ya tiene todo
        tiene_todo = all([
            lead.get("email"),
            lead.get("whatsapp") or lead.get("instagram") or lead.get("facebook")
        ])
        if tiene_todo:
            log.info(f"  Ya tiene datos suficientes — saltando")
            continue

        campos = enriquecer_lead(lead)

        if campos:
            supabase.table("leads").update(campos).eq("id", lead["id"]).execute()
            log.info(f"  Guardado: {list(campos.keys())}")
        else:
            log.info(f"  Sin datos nuevos")

        # Delay mínimo entre leads (cortesía a los sitios web)
        time.sleep(random.uniform(0.5, 1))

    log.info(f"\n=== ENRIQUECIMIENTO COMPLETO ===")


if __name__ == "__main__":
    log.info("=== EUROCREM ENRICH v3.0 START ===")
    enriquecer_todos()
    log.info("=== EUROCREM ENRICH v3.0 DONE ===")

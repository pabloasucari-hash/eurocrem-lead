"""
EUROCREM — Fase 2: Enriquecimiento automático
Versión: 2.0 — 03/06/2026

Fuentes en cascada:
  1. Sitio web propio → email, wa.me, redes
  2. Linktree / bio links → WhatsApp, email
  3. Instagram pública → handle, email en bio
  4. Google Search → encuentra URLs en directorios, prensa, redes
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
from bs4 import BeautifulSoup
from supabase import create_client, Client

# ============================================================
# CONFIGURACIÓN
# ============================================================

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://crbmgsmmvfkbxrplqfkl.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNyYm1nc21tdmZrYnhycGxxZmtsIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MDUxNjQ0NywiZXhwIjoyMDk2MDkyNDQ3fQ.7D5od9xbpGP3XbdjrTpgCtL7xWYpeJ6xaa5cBsMsju8")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "AIzaSyDj8Q--Sn63RiYOp3ZF93Hp6P4BH51K49M")
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID", "25f2abf8674a0441e")

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
    "example.com", "test.com", "sentry.io", "wixpress.com",
    "squarespace.com", "shopify.com", "wordpress.com"
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
        log.debug(f"HTTP {resp.status_code} para {url}")
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
    """Valida que el email sea real y no una imagen o dominio blacklisteado."""
    if not email or len(email) < 5:
        return False
    if EMAIL_BLACKLIST_PATTERNS.search(email):
        return False
    domain = email.split("@")[-1].lower()
    if domain in EMAIL_BLACKLIST_DOMAINS:
        return False
    # Debe tener al menos un punto en el dominio
    if "." not in domain:
        return False
    return bool(EMAIL_REGEX.match(email))

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
    """Extrae número de un link wa.me/."""
    matches = WAME_REGEX.findall(html)
    for m in matches:
        normalizado = normalizar_whatsapp(m)
        if normalizado:
            return normalizado
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

def extraer_emails_de_html(html: str) -> list:
    """Extrae emails de HTML — mailto: primero, luego texto plano."""
    soup = BeautifulSoup(html, "html.parser")
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

    return list(dict.fromkeys(resultado))  # dedup preservando orden

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

def extraer_facebook_de_html(html: str) -> str | None:
    """Extrae URL de página de Facebook del HTML."""
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "facebook.com/" in href:
            # Limpiar tracking params
            url_limpia = href.split("?")[0].rstrip("/")
            # Filtrar URLs genéricas de Facebook
            partes = url_limpia.split("facebook.com/")
            if len(partes) > 1:
                path = partes[1]
                if path and path not in ("sharer", "share", "dialog", "login", ""):
                    return url_limpia
    return None

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
# GOOGLE SEARCH SCRAPING
# ============================================================

def google_search(query: str, num_results: int = 8) -> list:
    """
    Búsqueda via Google Custom Search API (oficial, no bloquea).
    100 búsquedas gratis por día.
    """
    try:
        time.sleep(random.uniform(1, 2))
        urls = []
        # Custom Search API devuelve máx 10 por llamada
        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "key": GOOGLE_API_KEY,
            "cx": GOOGLE_CSE_ID,
            "q": query,
            "num": min(num_results, 10),
            "lr": "lang_es",
            "gl": "ar",
        }
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code != 200:
            log.debug(f"Custom Search API status {resp.status_code}: {resp.text[:200]}")
            return []
        data = resp.json()
        items = data.get("items", [])
        for item in items:
            link = item.get("link", "")
            if link and "google.com" not in link:
                urls.append(link)
        log.debug(f"Google CSE → {len(urls)} URLs para: {query[:60]}")
        return urls
    except Exception as e:
        log.debug(f"Google CSE error: {e}")
        return []

def clasificar_url(url: str) -> str:
    """Clasifica una URL por tipo de fuente."""
    url_lower = url.lower()
    if "instagram.com" in url_lower:
        return "instagram"
    if "facebook.com" in url_lower:
        return "facebook"
    if "tripadvisor.com" in url_lower or "tripadvisor.com.ar" in url_lower:
        return "tripadvisor"
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
    if "infobae.com" in url_lower or "lanacion.com.ar" in url_lower or "clarin.com" in url_lower:
        return "prensa"
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

def extraer_de_guiaoleo(html: str) -> dict:
    """Extrae datos de Guía Óleo."""
    datos = {}
    soup = BeautifulSoup(html, "html.parser")
    texto = soup.get_text()

    emails = extraer_emails_de_html(html)
    if emails:
        datos["email"] = emails[0]

    wame = extraer_whatsapp_de_wame(html)
    if wame:
        datos["whatsapp"] = wame

    ig = extraer_instagram_de_html(html)
    if ig:
        datos["instagram"] = ig

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
        """True si el lead ya tiene el campo O ya lo encontramos en esta corrida."""
        return bool(lead.get(campo)) or bool(acumulado.get(campo))

    def guardar(campo, valor, fuente=""):
        """Guarda un campo si está vacío y el valor es válido."""
        if not ya_tiene(campo) and valor:
            acumulado[campo] = valor
            if fuente:
                log.info(f"    ✓ {campo}: {valor} [{fuente}]")

    # ----------------------------------------------------------
    # PASO 1: Si el sitio web ES Instagram → extraer handle
    # ----------------------------------------------------------
    if sitio_web:
        ig_directo = detectar_sitio_es_instagram(sitio_web)
        if ig_directo:
            guardar("instagram", ig_directo, "sitio_web=IG")
            sitio_web = None  # no intentar fetch de IG

    # ----------------------------------------------------------
    # PASO 2: Fetch sitio web propio
    # ----------------------------------------------------------
    if sitio_web and not all(ya_tiene(c) for c in ["email", "whatsapp", "instagram"]):
        html_web = fetch_url(sitio_web)
        if html_web:
            # Email
            emails = extraer_emails_de_html(html_web)
            if emails:
                guardar("email", emails[0], "sitio_web")

            # WhatsApp vía wa.me
            wame = extraer_whatsapp_de_wame(html_web)
            guardar("whatsapp", wame, "sitio_web")
            if wame:
                guardar("link_wame", construir_wame(wame), "sitio_web")

            # Instagram
            ig = extraer_instagram_de_html(html_web)
            guardar("instagram", ig, "sitio_web")

            # Facebook
            fb = extraer_facebook_de_html(html_web)
            guardar("facebook", fb, "sitio_web")

            # Bio links (Linktree, etc.)
            bio_links = extraer_links_bio(html_web)
            for bio_url in bio_links:
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
                        ig2 = extraer_instagram_de_html(html_bio)
                        guardar("instagram", ig2, "biolink")

    # ----------------------------------------------------------
    # PASO 3: Google Search — buscar en directorios y prensa
    # ----------------------------------------------------------
    if not all(ya_tiene(c) for c in ["email", "whatsapp", "instagram", "facebook"]):
        queries = [
            f'"{nombre}" Buenos Aires email whatsapp',
            f'"{nombre}" {barrio} restaurante contacto',
            f'"{nombre}" Buenos Aires site:guiaoleo.com OR site:tripadvisor.com.ar OR site:restaurantes.com.ar',
            f'"{nombre}" Buenos Aires instagram OR facebook',
        ]

        urls_procesadas = set()

        for query in queries:
            if all(ya_tiene(c) for c in ["email", "whatsapp", "instagram"]):
                break

            time.sleep(random.uniform(3, 6))
            urls = google_search(query)
            log.info(f"  Google Search '{query[:50]}...' → {len(urls)} URLs")

            for url in urls:
                if url in urls_procesadas:
                    continue
                urls_procesadas.add(url)

                tipo = clasificar_url(url)

                # Instagram — solo extraer handle de la URL
                if tipo == "instagram":
                    match = IG_HANDLE_REGEX.search(url)
                    if match:
                        handle = f"@{match.group(1)}"
                        guardar("instagram", handle, "google_search→IG_url")
                        guardar("link_ig", url, "google_search")
                    continue  # no hacer fetch de IG

                # Facebook — extraer URL de la URL misma
                if tipo == "facebook":
                    guardar("facebook", url.split("?")[0], "google_search→FB_url")
                    continue  # no hacer fetch de FB

                # Resto: hacer fetch y extraer
                html = fetch_url(url)
                if not html:
                    continue

                time.sleep(random.uniform(0.5, 1.5))

                if tipo == "tripadvisor":
                    datos_ta = extraer_de_tripadvisor(html)
                    if not ya_tiene("guias") and datos_ta.get("guias"):
                        guardar("guias", datos_ta["guias"], "tripadvisor")

                elif tipo in ("guiaoleo", "restaurantes_ar", "dondevamos", "meitre", "woki"):
                    datos_dir = extraer_de_guiaoleo(html)  # misma lógica genérica
                    for campo, valor in datos_dir.items():
                        guardar(campo, valor, tipo)

                elif tipo == "prensa":
                    datos_prensa = extraer_de_prensa(html)
                    for campo, valor in datos_prensa.items():
                        guardar(campo, valor, "prensa")

                elif tipo in ("web", "biolink"):
                    emails = extraer_emails_de_html(html)
                    if emails:
                        guardar("email", emails[0], tipo)
                    wame = extraer_whatsapp_de_wame(html)
                    guardar("whatsapp", wame, tipo)
                    if wame:
                        guardar("link_wame", construir_wame(wame), tipo)
                    ig = extraer_instagram_de_html(html)
                    guardar("instagram", ig, tipo)
                    fb = extraer_facebook_de_html(html)
                    guardar("facebook", fb, tipo)

    # ----------------------------------------------------------
    # PASO 4: Instagram pública — extraer email de bio
    # ----------------------------------------------------------
    ig_handle = acumulado.get("instagram") or lead.get("instagram")
    if ig_handle and not ya_tiene("email"):
        handle_clean = ig_handle.lstrip("@")
        html_ig = fetch_instagram_public(handle_clean)
        if html_ig:
            # Email en bio (aparece en texto plano a veces)
            emails_ig = extraer_emails_de_html(html_ig)
            if emails_ig:
                guardar("email", emails_ig[0], "instagram_bio")

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
        "id, nombre, barrio, sitio_web, whatsapp, email, instagram, facebook, origen_contacto, guias, decisor"
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

        # Delay entre leads para no ser bloqueado
        time.sleep(random.uniform(2, 4))

    log.info(f"\n=== ENRIQUECIMIENTO COMPLETO ===")


if __name__ == "__main__":
    log.info("=== EUROCREM ENRICH v2.0 START ===")
    enriquecer_todos()
    log.info("=== EUROCREM ENRICH v2.0 DONE ===")

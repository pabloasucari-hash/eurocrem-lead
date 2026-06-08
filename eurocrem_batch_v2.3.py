"""
EUROCREM — Batch de captura y enriquecimiento
Versión: 2.3 — 08/06/2026

Fases:
  1. Captura: Places API → leads nuevos en Supabase
  2. Enriquecimiento: sitio web / Linktree / Facebook / Instagram bio
              → intenta completar whatsapp, email, instagram

Reglas de merge:
  - place_id ya existe → actualiza solo campos Maps (nota, reseñas, precio, horarios, abierto, telefono, lat, lng)
  - place_id nuevo     → inserta fila completa con enriquecido=False, origen_contacto='pendiente'
  - NUNCA pisa campos con origen_contacto IN ('IG-manual', 'FB-manual')

Cambios v2.3:
  - max_results subido de 15 a 60 (3 páginas Places API) → captura más restaurantes por query
  - Guarda lat/lng de geometry.location en INSERT y UPDATE → permite análisis georeferencial
  - No requiere borrar registros existentes: dedup por place_id, existentes se actualizan con coords

Cambios v2.2:
  - Fix: extraer_wame valida formato argentino (+549...) — descarta números extranjeros
  - Fix: reconoce api.whatsapp.com/send/?phone= además de wa.me/
  - Fix: normaliza números BA sin el 9 (5411XXXXXXXX → 54911XXXXXXXX)
  - Fix: sigue sub-links de Linktree para encontrar páginas reales con WA
"""

import os
import re
import time
import logging
import requests
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from supabase import create_client, Client

# ============================================================
# CONFIGURACIÓN — completar con variables de entorno en Railway
# ============================================================

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://crbmgsmmvfkbxrplqfkl.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNyYm1nc21tdmZrYnhycGxxZmtsIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MDUxNjQ0NywiZXhwIjoyMDk2MDkyNDQ3fQ.7D5od9xbpGP3XbdjrTpgCtL7xWYpeJ6xaa5cBsMsju8")
GOOGLE_PLACES_KEY = os.getenv("GOOGLE_PLACES_KEY", "AIzaSyDj8Q--Sn63RiYOp3ZF93Hp6P4BH51K49M")

# ============================================================
# GRILLA DE BÚSQUEDA — barrio × subrubro
# ============================================================

BARRIOS = [
    # Bloque original
    "Palermo", "Recoleta", "Belgrano", "Villa Crespo",
    "Colegiales", "San Telmo", "Almagro", "Núñez", "Caballito",
    # Expansión v2
    "Chacarita", "Las Cañitas", "Villa Urquiza", "Boedo", "Puerto Madero",
]

TIPOS_COCINA = [
    "restaurante italiano",
    "parrilla restaurante",
    "trattoria osteria",
    "restaurante autor contemporaneo",
    "bistro restaurante",
    "bodegon restaurante",
    "restaurante eventos privados",
    "cocina mediterranea restaurante",
]

GRILLA = [
    {"barrio": barrio, "query": f"{tipo} {barrio} Buenos Aires"}
    for barrio in BARRIOS
    for tipo in TIPOS_COCINA
]

# Tipos a excluir (no son restaurantes con carta de postre)
EXCLUIR_TIPOS = [
    "bar", "cafe", "bakery", "fast_food", "meal_takeaway",
    "liquor_store", "night_club", "convenience_store"
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
# CLIENTE SUPABASE
# ============================================================

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ============================================================
# FASE 1 — CAPTURA (Places API)
# ============================================================

def buscar_lugares(query: str, max_results: int = 60) -> list:
    """Llama a Places API Text Search y devuelve lista de lugares."""
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {
        "query": query,
        "type": "restaurant",
        "language": "es",
        "region": "ar",                         # bias hacia Argentina
        "location": "-34.6037,-58.3816",        # centro de Buenos Aires
        "radius": "30000",                      # 30 km — cubre toda la ciudad
        "key": GOOGLE_PLACES_KEY,
    }
    resultados = []
    while len(resultados) < max_results:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if data.get("status") not in ("OK", "ZERO_RESULTS"):
            log.warning(f"Places API error: {data.get('status')} — {query}")
            break
        resultados.extend(data.get("results", []))
        next_token = data.get("next_page_token")
        if not next_token or len(resultados) >= max_results:
            break
        params = {"pagetoken": next_token, "key": GOOGLE_PLACES_KEY}
        time.sleep(3)  # Places API requiere mínimo 2s, usamos 3s para evitar INVALID_REQUEST
    return resultados[:max_results]


def obtener_detalle_lugar(place_id: str) -> dict:
    """Obtiene detalle completo de un lugar por place_id."""
    url = "https://maps.googleapis.com/maps/api/place/details/json"
    fields = (
        "place_id,name,formatted_address,formatted_phone_number,"
        "website,opening_hours,price_level,rating,user_ratings_total,"
        "types,geometry,photos"
    )
    params = {"place_id": place_id, "fields": fields, "language": "es", "key": GOOGLE_PLACES_KEY}
    resp = requests.get(url, params=params, timeout=10)
    data = resp.json()
    if data.get("status") == "OK":
        return data.get("result", {})
    return {}


def es_excluible(tipos: list) -> bool:
    """Devuelve True si el lugar debe ser excluido por tipo."""
    return any(t in EXCLUIR_TIPOS for t in tipos)


def precio_label(price_level) -> str:
    mapping = {0: "$", 1: "$", 2: "$$", 3: "$$$", 4: "$$$$"}
    return mapping.get(price_level, "")


def extraer_barrio_de_direccion(direccion: str) -> str:
    """Intenta extraer barrio conocido de la dirección."""
    barrios = [
        "Palermo", "Recoleta", "Belgrano", "Villa Crespo", "Colegiales",
        "San Telmo", "Microcentro", "Balvanera", "Almagro", "Núñez",
        "Caballito", "Barrio Norte", "Chacarita", "Las Cañitas",
        "Villa Urquiza", "Boedo", "Puerto Madero",
    ]
    for b in barrios:
        if b.lower() in direccion.lower():
            return b
    return ""


def capturar_leads():
    """Fase 1: recorre la grilla y guarda leads nuevos en Supabase."""
    place_ids_vistos = set()
    nuevos = 0
    actualizados = 0

    for item in GRILLA:
        barrio = item["barrio"]
        query = item["query"]
        log.info(f"Buscando: {query}")

        lugares = buscar_lugares(query)
        for lugar in lugares:
            place_id = lugar.get("place_id")
            if not place_id or place_id in place_ids_vistos:
                continue
            place_ids_vistos.add(place_id)

            tipos = lugar.get("types", [])
            if es_excluible(tipos):
                log.info(f"  Excluido por tipo: {lugar.get('name')} {tipos}")
                continue

            # Filtrar resultados fuera de Argentina
            addr_check = lugar.get("formatted_address", "")
            if "Argentina" not in addr_check:
                log.info(f"  Excluido (fuera de Argentina): {lugar.get('name')} — {addr_check}")
                continue

            # Obtener detalle completo
            detalle = obtener_detalle_lugar(place_id)
            if not detalle:
                continue

            nombre = detalle.get("name", "")
            direccion = detalle.get("formatted_address", "")
            telefono = detalle.get("formatted_phone_number", "")
            sitio_web = detalle.get("website", "")
            rating = detalle.get("rating")
            resenas = detalle.get("user_ratings_total")
            precio = precio_label(detalle.get("price_level"))
            abierto = detalle.get("opening_hours", {}).get("open_now")
            horarios_raw = detalle.get("opening_hours", {}).get("weekday_text", [])
            horarios = " | ".join(horarios_raw) if horarios_raw else ""
            barrio_detectado = extraer_barrio_de_direccion(direccion) or barrio
            loc = detalle.get("geometry", {}).get("location", {})
            lat = loc.get("lat")
            lng = loc.get("lng")
            photos = detalle.get("photos", [])
            photo_ref = photos[0].get("photo_reference") if photos else None

            # ¿Ya existe en Supabase?
            existente = supabase.table("leads").select("id,origen_contacto").eq("place_id", place_id).execute()

            if existente.data:
                # Actualizar solo campos Maps (nunca campos protegidos)
                # Siempre actualiza stats y coords; solo pisa texto si Maps devuelve algo
                update_data = {
                    "google_nota": rating,
                    "google_resenas": resenas,
                    "abierto": abierto,
                    "lat": lat,
                    "lng": lng,
                    "fecha_actualizacion": datetime.now(timezone.utc).isoformat(),
                }
                if photo_ref:
                    update_data["photo_ref"] = photo_ref
                if precio:
                    update_data["precio"] = precio
                if horarios:
                    update_data["horarios"] = horarios
                if telefono:
                    update_data["telefono"] = telefono
                if sitio_web:
                    update_data["sitio_web"] = sitio_web
                supabase.table("leads").update(update_data).eq("place_id", place_id).execute()
                actualizados += 1
                log.info(f"  Actualizado: {nombre}")
            else:
                # Insertar nuevo lead — solo columnas que existen en la DB
                supabase.table("leads").insert({
                    "place_id": place_id,
                    "segmento": "Restaurante",
                    "nombre": nombre,
                    "direccion": direccion,
                    "barrio": barrio_detectado,
                    "google_nota": rating,
                    "google_resenas": resenas,
                    "precio": precio,
                    "telefono": telefono,
                    "sitio_web": sitio_web if sitio_web else None,
                    "horarios": horarios,
                    "abierto": abierto,
                    "lat": lat,
                    "lng": lng,
                    "photo_ref": photo_ref,
                    "origen_contacto": "pendiente",
                    "enriquecido": False,
                    "fit": "Medio",
                }).execute()
                nuevos += 1
                log.info(f"  Nuevo: {nombre} ({barrio_detectado})")

            time.sleep(0.2)  # Rate limit suave

    log.info(f"Captura finalizada — Nuevos: {nuevos} | Actualizados: {actualizados}")


# ============================================================
# FASE 2 — ENRIQUECIMIENTO AUTOMÁTICO
# ============================================================

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; EurocremBot/1.0)"
}

EMAIL_REGEX   = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
WAME_REGEX    = re.compile(r"wa\.me/(\d+)")
WA_API_REGEX  = re.compile(r"[?&]phone=(\d+)")
PHONE_ARG     = re.compile(r"(\+?54\s?9?\s?11\s?[\d\s\-]{8,})")

SKIP_LINKTREE_DOMAINS = [
    "instagram.com", "facebook.com", "twitter.com", "tiktok.com",
    "youtube.com", "linktr.ee", "linkedin.com", "spotify.com",
    "google.com", "apple.com", "wa.me", "whatsapp.com",
]


def _es_numero_arg(numero: str) -> bool:
    """Valida que el número sea argentino: 549XXXXXXXXXX (13 dígitos) o 5411XXXXXXXX (12 dígitos)."""
    if numero.startswith("549") and len(numero) == 13:
        return True
    if numero.startswith("5411") and len(numero) == 12:
        return True
    return False


def _formatear_wa_arg(numero: str) -> str:
    """Formatea un número argentino como +54 9 XX XXXX-XXXX."""
    if numero.startswith("5411") and len(numero) == 12:
        numero = "549" + numero[2:]  # Inserta el 9 faltante
    area = numero[3:5]
    local = numero[5:]
    return f"+54 9 {area} {local[:4]}-{local[4:]}"


def extraer_links_sitio(html: str) -> list:
    """Extrae sub-links reales de Linktree via __NEXT_DATA__ JSON o <a> tags."""
    import json
    links = []

    # Intento 1: parsear __NEXT_DATA__ (Next.js embebe todos los links como JSON)
    try:
        soup = BeautifulSoup(html, "html.parser")
        script = soup.find("script", {"id": "__NEXT_DATA__"})
        if script and script.string:
            data = json.loads(script.string)
            # Los links están en props.pageProps.links o account.links
            page_props = data.get("props", {}).get("pageProps", {})
            raw_links = (
                page_props.get("links") or
                page_props.get("account", {}).get("links") or
                []
            )
            for item in raw_links:
                url = item.get("url") or item.get("href") or ""
                if url.startswith("http") and not any(d in url for d in SKIP_LINKTREE_DOMAINS):
                    links.append(url)
    except Exception:
        pass

    # Intento 2: fallback a <a> tags
    if not links:
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not href.startswith("http"):
                continue
            if any(d in href for d in SKIP_LINKTREE_DOMAINS):
                continue
            links.append(href)

    return list(dict.fromkeys(links))[:4]


def fetch_url(url: str) -> str | None:
    """Hace GET a una URL y devuelve el texto HTML. Devuelve None si falla."""
    try:
        if not url.startswith("http"):
            url = "https://" + url
        resp = requests.get(url, headers=HEADERS, timeout=10, allow_redirects=True)
        if resp.status_code == 200:
            return resp.text
    except Exception as e:
        log.debug(f"fetch_url error {url}: {e}")
    return None


def extraer_emails(html: str) -> list:
    """Extrae emails solo de links mailto: — evita basura del texto libre de la página."""
    soup = BeautifulSoup(html, "html.parser")
    emails = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("mailto:"):
            email = href.replace("mailto:", "").split("?")[0].strip().lower()
            if email and "@" in email and "." in email.split("@")[-1]:
                emails.append(email)
    return list(set(emails))


def extraer_wame(html: str) -> str | None:
    """Extrae número de WhatsApp argentino de wa.me o api.whatsapp.com/send/?phone=."""
    for numero in WAME_REGEX.findall(html) + WA_API_REGEX.findall(html):
        if _es_numero_arg(numero):
            return _formatear_wa_arg(numero)
    return None


def extraer_instagram(html: str) -> str | None:
    """Extrae handle de Instagram del HTML."""
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "instagram.com/" in href:
            partes = href.rstrip("/").split("/")
            handle = partes[-1]
            if handle and handle not in ("instagram.com", "p", "reel", "stories"):
                return f"@{handle}"
    return None


def enriquecer_lead(lead: dict):
    """Intenta completar whatsapp, email, instagram para un lead."""
    lead_id = lead["id"]
    nombre = lead["nombre"]
    sitio_web = lead.get("sitio_web", "")
    origen = lead.get("origen_contacto", "pendiente")

    if origen in ("IG-manual", "FB-manual"):
        return

    campos_a_actualizar = {}

    urls_a_intentar = []
    if sitio_web:
        urls_a_intentar.append(("web", sitio_web))

    for fuente, url in urls_a_intentar:
        if not url:
            continue
        log.info(f"  Enriqueciendo {nombre} desde {fuente}: {url}")
        html = fetch_url(url)
        if not html:
            continue

        if not lead.get("whatsapp"):
            wame = extraer_wame(html)
            if wame:
                campos_a_actualizar["whatsapp"] = wame
                campos_a_actualizar["link_wame"] = f"https://wa.me/{wame.replace('+','').replace(' ','')}"
                campos_a_actualizar["origen_contacto"] = "web-auto"
                log.info(f"    WhatsApp encontrado: {wame}")

        if not lead.get("email"):
            emails = extraer_emails(html)
            if emails:
                preferidos = [e for e in emails if any(k in e.lower() for k in
                    ["evento", "reserva", "contacto", "info", "ventas", "marketing", "hola"])]
                email_elegido = preferidos[0] if preferidos else emails[0]
                campos_a_actualizar["email"] = email_elegido
                log.info(f"    Email encontrado: {email_elegido}")

        if not lead.get("instagram"):
            ig = extraer_instagram(html)
            if ig:
                campos_a_actualizar["instagram"] = ig
                log.info(f"    Instagram encontrado: {ig}")

        # Buscar Linktree linkeado desde el sitio principal
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "linktr.ee" in href or "linkin.bio" in href or "bio.link" in href:
                html2 = fetch_url(href)
                if html2:
                    if not campos_a_actualizar.get("whatsapp") and not lead.get("whatsapp"):
                        wame2 = extraer_wame(html2)
                        if wame2:
                            campos_a_actualizar["whatsapp"] = wame2
                            campos_a_actualizar["origen_contacto"] = "web-auto"
                    if not campos_a_actualizar.get("email") and not lead.get("email"):
                        emails2 = extraer_emails(html2)
                        if emails2:
                            campos_a_actualizar["email"] = emails2[0]
                    # Seguir sub-links del Linktree hacia páginas reales (ej: Framer)
                    if not campos_a_actualizar.get("whatsapp") and not lead.get("whatsapp"):
                        for sub_url in extraer_links_sitio(html2):
                            html3 = fetch_url(sub_url)
                            if html3:
                                wame3 = extraer_wame(html3)
                                if wame3:
                                    campos_a_actualizar["whatsapp"] = wame3
                                    campos_a_actualizar["origen_contacto"] = "web-auto"
                                    log.info(f"    WhatsApp via Linktree sub-link: {wame3}")
                                    break

        # Si el sitio principal ES un Linktree, seguir sub-links directamente
        is_linktree = sitio_web and any(d in sitio_web for d in ["linktr.ee", "linkin.bio", "bio.link"])
        if is_linktree and not campos_a_actualizar.get("whatsapp") and not lead.get("whatsapp"):
            for sub_url in extraer_links_sitio(html):
                html3 = fetch_url(sub_url)
                if html3:
                    wame3 = extraer_wame(html3)
                    if wame3:
                        campos_a_actualizar["whatsapp"] = wame3
                        campos_a_actualizar["origen_contacto"] = "web-auto"
                        log.info(f"    WhatsApp via Linktree sub-link directo: {wame3}")
                        break

    if campos_a_actualizar:
        supabase.table("leads").update(campos_a_actualizar).eq("id", lead_id).execute()
        log.info(f"  Guardado: {list(campos_a_actualizar.keys())}")
    else:
        log.info(f"  Sin datos nuevos para {nombre}")

    time.sleep(0.5)


def enriquecer_leads():
    """Fase 2: enriquece todos los leads con origen_contacto = pendiente o Maps-web."""
    leads = supabase.table("leads").select(
        "id, nombre, sitio_web, whatsapp, email, instagram, origen_contacto"
    ).in_("origen_contacto", ["pendiente", "Maps-web", "web-auto"]).execute()

    total = len(leads.data)
    log.info(f"Enriqueciendo {total} leads...")

    for i, lead in enumerate(leads.data):
        log.info(f"[{i+1}/{total}] {lead['nombre']}")
        enriquecer_lead(lead)


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    log.info("=== EUROCREM BATCH START ===")

    log.info("--- FASE 1: Captura ---")
    capturar_leads()

    log.info("--- FASE 2: Enriquecimiento ---")
    enriquecer_leads()

    log.info("=== EUROCREM BATCH DONE ===")

"""
eurocrem_vdrmota_batch_v1.py
────────────────────────────
Corre vdrmota/contact-info-scraper en leads de Supabase que tienen:
  - sitio_web propio (no instagram/facebook/linktree)
  - Y no tienen whatsapp  O  no tienen email

NO sobreescribe datos existentes. Solo completa campos vacíos.
NO corre automáticamente — ejecutar manualmente cuando se quiera.

Uso:
    python eurocrem_vdrmota_batch_v1.py [--dry-run] [--limit N]

    --dry-run   Solo muestra qué leads procesaría, no llama Apify ni guarda nada
    --limit N   Procesa máximo N leads (default: todos)
"""

import os, re, time, json, argparse, requests
from supabase import create_client

# ─── CONFIG ───────────────────────────────────────────────────────────────────
SUPABASE_URL  = "https://crbmgsmmvfkbxrplqfkl.supabase.co"
SUPABASE_KEY  = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNyYm1nc21tdmZrYnhycGxxZmtsIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MDUxNjQ0NywiZXhwIjoyMDk2MDkyNDQ3fQ.7D5od9xbpGP3XbdjrTpgCtL7xWYpeJ6xaa5cBsMsju8"
APIFY_TOKEN   = os.getenv("APIFY_API_TOKEN", "")   # export APIFY_API_TOKEN=apify_api_xxx

ACTOR_ID      = "vdrmota/contact-info-scraper"
APIFY_BASE    = "https://api.apify.com/v2"

# Dominios que NO sirve mandar a este actor (ya los procesa el pipeline IG/FB)
SKIP_DOMAINS  = {"instagram.com", "facebook.com", "linktr.ee", "linktree.com",
                 "beacons.ai", "atom.bio", "taplink.ws", "taplink.cc"}

# ─── HELPERS ──────────────────────────────────────────────────────────────────
def es_sitio_propio(url: str) -> bool:
    if not url:
        return False
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lower().lstrip("www.")
        return not any(host.endswith(d) for d in SKIP_DOMAINS)
    except Exception:
        return False

def normalizar_wa(wa_url: str) -> str | None:
    """Extrae número de wa.me o api.whatsapp.com y devuelve +549XXXXXXXXXX (AR) o +número"""
    if not wa_url:
        return None
    m = re.search(r'(?:wa\.me|whatsapp\.com/send[?]phone=)[\D]*(\d{7,15})', wa_url)
    if not m:
        return None
    num = m.group(1)
    # Si empieza con 54 → argento → asegurar +54
    if num.startswith("549"):
        return f"+{num}"
    if num.startswith("54"):
        return f"+{num}"
    return f"+{num}"

def extraer_contacto(items: list[dict]) -> dict:
    """
    Busca en los ítems (páginas crawleadas) del actor:
      - whatsapp (primer wa.me válido)
      - email    (primer email que no sea de soporte/plataforma)
      - telefono (primer número)
    """
    EMAILS_SKIP = {"privacy@", "support@", "hello@atom", "help@atom",
                   "admin@atom", "hr@atom", "dpo.support", "contact@gdpr"}

    wa = email = telefono = None

    for item in items:
        if item.get("type") not in (None, "page", "summary"):
            continue

        # WhatsApp
        if not wa:
            for w in (item.get("whatsapp") or []):
                num = normalizar_wa(w)
                if num:
                    wa = num
                    break

        # Email
        if not email:
            for e in (item.get("emails") or []):
                e = e.lower().strip()
                if any(skip in e for skip in EMAILS_SKIP):
                    continue
                if re.match(r"[^@]+@[^@]+\.[^@]+", e):
                    email = e
                    break

        # Teléfono (uncertain)
        if not telefono:
            for p in (item.get("phone_numbers") or []):
                p = re.sub(r"[\s\-().]", "", p)
                if len(p) >= 8:
                    telefono = p
                    break

        if wa and email and telefono:
            break

    return {"whatsapp": wa, "email": email, "telefono": telefono}

# ─── APIFY ────────────────────────────────────────────────────────────────────
def run_actor(url: str) -> list[dict]:
    """Corre el actor para UNA URL y devuelve los ítems del dataset."""
    if not APIFY_TOKEN:
        raise RuntimeError("APIFY_API_TOKEN no definido. Exportalo antes de correr.")

    payload = {
        "startUrls": [{"url": url}],
        "maxRequestsPerStartUrl": 15,
        "mergeContacts": False,         # queremos ver cada página individualmente
        "maxDepth": 2,
        "useBrowser": False,            # más barato; si no encuentra nada, probar True
        "proxyConfig": {"useApifyProxy": True}
    }

    # Arrancar run
    r = requests.post(
        f"{APIFY_BASE}/acts/{ACTOR_ID}/runs",
        headers={"Authorization": f"Bearer {APIFY_TOKEN}"},
        json=payload,
        params={"waitForFinish": 120}   # esperar hasta 2 min
    )
    r.raise_for_status()
    run_data = r.json()["data"]
    dataset_id = run_data["defaultDatasetId"]

    # Obtener resultados
    res = requests.get(
        f"{APIFY_BASE}/datasets/{dataset_id}/items",
        headers={"Authorization": f"Bearer {APIFY_TOKEN}"},
        params={"limit": 200, "clean": "true"}
    )
    res.raise_for_status()
    return res.json()

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run",  action="store_true", help="Solo muestra leads, no llama Apify")
    parser.add_argument("--limit",    type=int, default=9999, help="Máximo de leads a procesar")
    parser.add_argument("--browser",  action="store_true", help="Usar useBrowser=True (más lento/caro)")
    args = parser.parse_args()

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    # Traer leads sin WA o sin email, que tengan sitio_web
    resp = sb.from_("leads").select(
        "id, nombre, barrio, sitio_web, whatsapp, email, telefono"
    ).not_.is_("sitio_web", "null").execute()

    candidatos = [
        l for l in resp.data
        if es_sitio_propio(l.get("sitio_web", ""))
        and (not l.get("whatsapp") or not l.get("email"))
    ][:args.limit]

    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Leads candidatos: {len(candidatos)}\n")

    resultados = {"encontrado": [], "sin_datos": [], "error": []}

    for i, lead in enumerate(candidatos, 1):
        nombre = lead["nombre"]
        url    = lead["sitio_web"]
        print(f"[{i}/{len(candidatos)}] {nombre} — {url}")

        if args.dry_run:
            print("   → (dry-run, saltando)")
            continue

        try:
            items = run_actor(url)
            contacto = extraer_contacto(items)

            updates = {}
            if not lead.get("whatsapp") and contacto["whatsapp"]:
                updates["whatsapp"] = contacto["whatsapp"]
            if not lead.get("email") and contacto["email"]:
                updates["email"] = contacto["email"]
            if not lead.get("telefono") and contacto["telefono"]:
                updates["telefono"] = contacto["telefono"]

            if updates:
                sb.from_("leads").update(updates).eq("id", lead["id"]).execute()
                print(f"   ✓ Guardado: {updates}")
                resultados["encontrado"].append({"nombre": nombre, "datos": updates})
            else:
                print("   — Sin datos nuevos")
                resultados["sin_datos"].append(nombre)

        except Exception as e:
            print(f"   ✗ Error: {e}")
            resultados["error"].append({"nombre": nombre, "error": str(e)})

        time.sleep(2)  # pausa entre llamadas

    # Resumen
    print("\n" + "="*50)
    print(f"RESUMEN")
    print(f"  Con datos nuevos : {len(resultados['encontrado'])}")
    print(f"  Sin datos        : {len(resultados['sin_datos'])}")
    print(f"  Errores          : {len(resultados['error'])}")

    if resultados["encontrado"]:
        print("\nLeads actualizados:")
        for r in resultados["encontrado"]:
            print(f"  • {r['nombre']}: {r['datos']}")

    # Guardar log
    log_path = "vdrmota_batch_log.json"
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(resultados, f, ensure_ascii=False, indent=2)
    print(f"\nLog guardado en {log_path}")


if __name__ == "__main__":
    main()

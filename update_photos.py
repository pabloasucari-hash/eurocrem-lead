"""
Actualiza photo_ref para todos los leads que ya tienen place_id en Supabase.
Correlo CON el batch pausado o después que termine.
Uso: python update_photos.py
"""
import os, requests, time
from supabase import create_client

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://crbmgsmmvfkbxrplqfkl.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNyYm1nc21tdmZrYnhycGxxZmtsIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MDUxNjQ0NywiZXhwIjoyMDk2MDkyNDQ3fQ.7D5od9xbpGP3XbdjrTpgCtL7xWYpeJ6xaa5cBsMsju8")
GOOGLE_KEY = "AIzaSyDj8Q--Sn63RiYOp3ZF93Hp6P4BH51K49M"

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def get_photo_ref(place_id: str) -> str | None:
    r = requests.get(
        "https://maps.googleapis.com/maps/api/place/details/json",
        params={
            "place_id": place_id,
            "fields": "photos",
            "key": GOOGLE_KEY
        },
        timeout=10
    )
    data = r.json()
    if data.get("status") != "OK":
        return None
    photos = data.get("result", {}).get("photos", [])
    if photos:
        return photos[0].get("photo_reference")
    return None

# Traer todos los leads sin foto que tienen place_id
print("Cargando leads sin foto...")
resp = supabase.table("leads").select("id,nombre,place_id,photo_ref").execute()
leads = [l for l in resp.data if not l.get("photo_ref") and l.get("place_id")]
print(f"Leads sin foto: {len(leads)}")

actualizados = 0
sin_foto = 0

for i, lead in enumerate(leads, 1):
    place_id = lead.get("place_id")
    nombre = lead.get("nombre", "")
    if not place_id:
        continue

    photo_ref = get_photo_ref(place_id)

    if photo_ref:
        supabase.table("leads").update({"photo_ref": photo_ref}).eq("id", lead["id"]).execute()
        actualizados += 1
        print(f"  [{i}/{len(leads)}] ✓ {nombre[:40]}")
    else:
        sin_foto += 1
        if i % 50 == 0:
            print(f"  [{i}/{len(leads)}] — {nombre[:40]} (sin foto en Google)")

    time.sleep(0.15)  # Rate limit suave

print(f"\nFIN: {actualizados} fotos guardadas, {sin_foto} sin foto en Google")

"""
Debug: muestra qué devuelve Places API para fotos de un restaurante.
Correlo con: python debug_photo.py
"""
import requests

KEY = "AIzaSyDj8Q--Sn63RiYOp3ZF93Hp6P4BH51K49M"

# Buscar un restaurante conocido
query = "Piegari Ristorante Recoleta Buenos Aires"
print(f"Buscando: {query}")

r = requests.get(
    "https://maps.googleapis.com/maps/api/place/textsearch/json",
    params={"query": query, "key": KEY},
    timeout=10
)
results = r.json().get("results", [])
if not results:
    print("No se encontró el restaurante")
    exit()

place_id = results[0]["place_id"]
nombre = results[0].get("name")
print(f"Encontrado: {nombre} | place_id: {place_id}")

# Pedir detalle CON photos
r2 = requests.get(
    "https://maps.googleapis.com/maps/api/place/details/json",
    params={
        "place_id": place_id,
        "fields": "name,photos,geometry",
        "language": "es",
        "key": KEY
    },
    timeout=10
)
data = r2.json()
print(f"\nStatus API: {data.get('status')}")
result = data.get("result", {})
print(f"Keys en result: {list(result.keys())}")

photos = result.get("photos", [])
print(f"Cantidad de fotos: {len(photos)}")

if photos:
    print(f"photo_reference (primeros 80 chars): {photos[0].get('photo_reference', 'FALTA')[:80]}")
    print("\n✅ OK — el API devuelve fotos. El batch debería estar guardando photo_ref.")
else:
    print("\n⚠️  El API NO devuelve fotos para este restaurante.")
    print("Posibles causas:")
    print("  - El campo 'photos' no está habilitado en tu proyecto de Google Cloud")
    print("  - El restaurante genuinamente no tiene fotos en Google Maps")

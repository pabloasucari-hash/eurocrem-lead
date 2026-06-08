"""
Ranking checker — Places API
Muestra en qué posición aparece cada resultado para una query dada.
También incluye lat/lng para uso georeferencial.
Correlo con: python BODEGON_NUNEZ.PY
"""

import requests
import time

GOOGLE_PLACES_KEY = "AIzaSyDj8Q--Sn63RiYOp3ZF93Hp6P4BH51K49M"


def ranking_query(query: str, max_pages: int = 3):
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {
        "query": query,
        "type": "restaurant",
        "language": "es",
        "key": GOOGLE_PLACES_KEY,
    }
    todos = []
    pagina = 1
    while pagina <= max_pages:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        status = data.get("status")
        if status not in ("OK", "ZERO_RESULTS"):
            print(f"  ERROR Places API: {status}")
            break
        resultados = data.get("results", [])
        todos.extend(resultados)
        print(f"  Página {pagina}: {len(resultados)} resultados")
        next_token = data.get("next_page_token")
        if not next_token:
            break
        pagina += 1
        params = {"pagetoken": next_token, "key": GOOGLE_PLACES_KEY}
        time.sleep(2)  # Places API requiere espera entre páginas
    return todos


def mostrar_ranking(resultados: list, buscar: str = ""):
    print(f"\n{'#':>3}  {'Nombre':<45} {'Lat':>10} {'Lng':>11}  Dirección")
    print("-" * 120)
    for i, r in enumerate(resultados, 1):
        nombre = r.get("name", "")
        direccion = r.get("formatted_address", "")[:55]
        loc = r.get("geometry", {}).get("location", {})
        lat = loc.get("lat", 0)
        lng = loc.get("lng", 0)
        marca = " <<<" if buscar and buscar.lower() in nombre.lower() else ""
        print(f"{i:>3}. {nombre:<45} {lat:>10.5f} {lng:>11.5f}  {direccion}{marca}")


if __name__ == "__main__":
    # Queries que usa el batch para Núñez
    queries = [
        "bodegon restaurante Núñez Buenos Aires",
        "parrilla restaurante Núñez Buenos Aires",
        "restaurante italiano Núñez Buenos Aires",
    ]

    # Término a resaltar — cambiá para buscar otro nombre
    BUSCAR = "bodeg"

    for q in queries:
        print(f"\n{'='*60}")
        print(f"QUERY: {q}")
        print(f"{'='*60}")
        resultados = ranking_query(q)
        mostrar_ranking(resultados, buscar=BUSCAR)
        print(f"\nTotal: {len(resultados)} resultados")
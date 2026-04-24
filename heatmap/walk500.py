#!/usr/bin/env python3
"""
walk500.py — מה יש במרחק 500 מ' הליכה מכתובת?
חישוב לפי רשת דרכים (לא אווירי) + מפה אינטראקטיבית
"""

import sys
import time
import json
import math
import requests
import webbrowser
from pathlib import Path

import osmnx as ox
import networkx as nx
import folium
from folium.plugins import MarkerCluster
from shapely.geometry import mapping
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut

# ── קטגוריות POI ────────────────────────────────────────────────────────────
POI_CATEGORIES = {
    "מסעדות ובתי קפה": {
        "query": '(node["amenity"~"restaurant|cafe|fast_food|bar|pub|food_court"]{bbox};'
                 ' way["amenity"~"restaurant|cafe|fast_food|bar|pub|food_court"]{bbox};);',
        "icon": "cutlery",
        "color": "red",
        "emoji": "🍽️",
    },
    "סופרמרקטים וחנויות": {
        "query": '(node["shop"~"supermarket|convenience|bakery|butcher|greengrocer|deli|grocery"]{bbox};'
                 ' way["shop"~"supermarket|convenience|bakery|butcher|greengrocer|deli|grocery"]{bbox};);',
        "icon": "shopping-cart",
        "color": "orange",
        "emoji": "🛒",
    },
    "תחבורה ציבורית": {
        "query": '(node["highway"="bus_stop"]{bbox};'
                 ' node["railway"~"station|subway_entrance|tram_stop"]{bbox};'
                 ' node["public_transport"~"stop_position|platform"]{bbox};);',
        "icon": "bus",
        "color": "blue",
        "emoji": "🚌",
    },
    "פארקים ושטחים ירוקים": {
        "query": '(way["leisure"~"park|garden|playground|sports_centre"]{bbox};'
                 ' node["leisure"~"park|garden|playground"]{bbox};);',
        "icon": "tree",
        "color": "green",
        "emoji": "🌳",
    },
    "בריאות ורפואה": {
        "query": '(node["amenity"~"pharmacy|hospital|clinic|doctors|dentist"]{bbox};'
                 ' way["amenity"~"pharmacy|hospital|clinic|doctors|dentist"]{bbox};);',
        "icon": "plus-sign",
        "color": "darkred",
        "emoji": "💊",
    },
    "בנקים ו-ATM": {
        "query": '(node["amenity"~"bank|atm"]{bbox};);',
        "icon": "usd",
        "color": "darkblue",
        "emoji": "🏦",
    },
    "חינוך": {
        "query": '(node["amenity"~"school|kindergarten|university|library"]{bbox};'
                 ' way["amenity"~"school|kindergarten|university|library"]{bbox};);',
        "icon": "education",
        "color": "purple",
        "emoji": "📚",
    },
}

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
MAX_DISTANCE = 500  # מטר


def geocode_address(address: str) -> tuple[float, float]:
    """המרת כתובת לקואורדינטות."""
    geolocator = Nominatim(user_agent="walk500_app")
    try:
        location = geolocator.geocode(address, timeout=10)
    except GeocoderTimedOut:
        raise ValueError("Nominatim timeout — נסה שוב")
    if not location:
        raise ValueError(f"לא נמצאה כתובת: {address}")
    print(f"  📍 {location.address}")
    return location.latitude, location.longitude


def get_isodistance_polygon(lat: float, lon: float, distance: float = MAX_DISTANCE):
    """חישוב פוליגון 500מ' הליכה לפי רשת דרכים (OSMnx)."""
    print("  🗺️  טוען רשת דרכים...")
    G = ox.graph_from_point(
        (lat, lon),
        dist=distance + 200,  # מרווח בטחון
        network_type="walk",
        simplify=True,
    )
    center_node = ox.nearest_nodes(G, lon, lat)
    subgraph = nx.ego_graph(G, center_node, radius=distance, distance="length")
    nodes_gdf, _ = ox.graph_to_gdfs(subgraph)
    polygon = nodes_gdf.unary_union.convex_hull
    return polygon


def bbox_from_polygon(polygon) -> str:
    """Bounding box לשאילתת Overpass."""
    b = polygon.bounds  # (minx, miny, maxx, maxy)
    return f"{b[1]},{b[0]},{b[3]},{b[2]}"


def query_overpass(overpass_query: str, retries: int = 3) -> list[dict]:
    """שאילתת Overpass API."""
    full_query = f"[out:json][timeout:25];\n{overpass_query}\nout center;"
    headers = {"User-Agent": "walk500_app/1.0"}
    for attempt in range(retries):
        try:
            resp = requests.get(
                OVERPASS_URL,
                params={"data": full_query},
                headers=headers,
                timeout=35,
            )
            resp.raise_for_status()
            elements = resp.json().get("elements", [])
            return elements
        except requests.RequestException as e:
            if attempt < retries - 1:
                time.sleep(3)
            else:
                print(f"    ⚠️  Overpass שגיאה: {e}")
                return []


def element_coords(el: dict) -> tuple[float, float] | None:
    """חילוץ קואורדינטות מאלמנט OSM."""
    if el["type"] == "node":
        return el.get("lat"), el.get("lon")
    center = el.get("center", {})
    if center:
        return center.get("lat"), center.get("lon")
    return None


def point_in_polygon(lat: float, lon: float, polygon) -> bool:
    from shapely.geometry import Point
    return polygon.contains(Point(lon, lat))


def build_map(
    lat: float,
    lon: float,
    polygon,
    poi_results: dict[str, list],
    address: str,
) -> folium.Map:
    """בניית מפת Folium."""
    m = folium.Map(location=[lat, lon], zoom_start=16, tiles="CartoDB positron")

    # פוליגון הליכה
    folium.GeoJson(
        data=mapping(polygon),
        style_function=lambda _: {
            "fillColor": "#3388ff",
            "color": "#1a66cc",
            "weight": 2,
            "fillOpacity": 0.12,
        },
        tooltip="איזור הליכה 500מ' (רשת דרכים)",
        name="איזור הליכה",
    ).add_to(m)

    # סמן מרכז
    folium.Marker(
        [lat, lon],
        tooltip=address,
        popup=folium.Popup(f"<b>{address}</b>", max_width=250),
        icon=folium.Icon(color="black", icon="home", prefix="glyphicon"),
    ).add_to(m)

    # שכבות POI
    total = 0
    for category, items in poi_results.items():
        if not items:
            continue
        cat_info = POI_CATEGORIES[category]
        layer = folium.FeatureGroup(name=f"{cat_info['emoji']} {category} ({len(items)})")
        cluster = MarkerCluster().add_to(layer)
        for item in items:
            coords = element_coords(item)
            if not coords or None in coords:
                continue
            name = (
                item.get("tags", {}).get("name")
                or item.get("tags", {}).get("name:he")
                or category
            )
            popup_lines = [f"<b>{name}</b>"]
            for k in ("amenity", "shop", "cuisine", "opening_hours", "phone", "website"):
                v = item.get("tags", {}).get(k)
                if v:
                    popup_lines.append(f"{k}: {v}")
            folium.Marker(
                coords,
                popup=folium.Popup("<br>".join(popup_lines), max_width=250),
                tooltip=name,
                icon=folium.Icon(
                    color=cat_info["color"],
                    icon=cat_info["icon"],
                    prefix="glyphicon",
                ),
            ).add_to(cluster)
        layer.add_to(m)
        total += len(items)

    folium.LayerControl(collapsed=False).add_to(m)

    # כותרת
    title_html = f"""
    <div style="position:fixed;top:10px;left:50%;transform:translateX(-50%);
                background:white;padding:10px 18px;border-radius:8px;
                box-shadow:0 2px 8px rgba(0,0,0,0.3);z-index:9999;font-family:Arial;
                direction:rtl;text-align:right;">
        <b>📍 {address}</b><br>
        <span style="font-size:12px;color:#555">500 מ׳ הליכה • {total} מקומות</span>
    </div>
    """
    m.get_root().html.add_child(folium.Element(title_html))

    return m


def main():
    if len(sys.argv) > 1:
        address = " ".join(sys.argv[1:])
    else:
        address = input("הזן כתובת (לדוגמה: Rothschild 1, Tel Aviv): ").strip()

    if not address:
        print("לא הוזנה כתובת.")
        sys.exit(1)

    print(f"\n🔍 מחפש: {address}")

    # 1. Geocode
    print("\n[1/4] ממיר כתובת לקואורדינטות...")
    lat, lon = geocode_address(address)
    print(f"       lat={lat:.5f}, lon={lon:.5f}")

    # 2. Isodistance
    print("\n[2/4] מחשב פוליגון הליכה (500מ' רשת דרכים)...")
    polygon = get_isodistance_polygon(lat, lon)
    bbox = bbox_from_polygon(polygon)
    print(f"       bbox: {bbox}")

    # 3. Overpass POIs
    print("\n[3/4] שואל מה יש באיזור...")
    poi_results = {}
    for category, cat_info in POI_CATEGORIES.items():
        q = cat_info["query"].replace("{bbox}", f"({bbox})")
        elements = query_overpass(q)
        # סינון לפי פוליגון בלבד (לא bbox)
        filtered = [
            e for e in elements
            if (c := element_coords(e)) and c[0] and point_in_polygon(c[0], c[1], polygon)
        ]
        poi_results[category] = filtered
        emoji = cat_info["emoji"]
        print(f"       {emoji} {category}: {len(filtered)}")

    # 4. Map
    print("\n[4/4] בונה מפה...")
    m = build_map(lat, lon, polygon, poi_results, address)
    out_path = Path(__file__).parent / "walk500_result.html"
    m.save(str(out_path))
    print(f"\n✅ המפה נשמרה: {out_path}")

    # סיכום
    print("\n── סיכום ──────────────────────────────────────────")
    for cat, items in poi_results.items():
        if items:
            emoji = POI_CATEGORIES[cat]["emoji"]
            print(f"  {emoji} {cat}: {len(items)}")
    total = sum(len(v) for v in poi_results.values())
    print(f"\n  סה\"כ: {total} מקומות במרחק הליכה")
    print("────────────────────────────────────────────────────\n")

    webbrowser.open(str(out_path))


if __name__ == "__main__":
    main()

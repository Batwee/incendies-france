import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import requests
import datetime
import json
import os
import time
from streamlit_autorefresh import st_autorefresh

# ==========================================
# CONFIGURATION
# ==========================================
st.set_page_config(page_title="Incendies & Forêts à Risque", page_icon="🌲", layout="wide")

FRANCE_BBOX = {"lat_min": 41.3, "lat_max": 51.1, "lon_min": -5.2, "lon_max": 9.6}

CACHE_DIR = "cache_data"
os.makedirs(CACHE_DIR, exist_ok=True)
FIRES_JSON_PATH = os.path.join(CACHE_DIR, "fires_data.json")
FORESTS_GEOJSON_PATH = os.path.join(CACHE_DIR, "forests_data.geojson")
WEATHER_JSON_PATH = os.path.join(CACHE_DIR, "weather_data.json")

# ==========================================
# CONTOURS GEOJSON DE SECOURS (MASSIFS FRANÇAIS)
# ==========================================
DEFAULT_FORESTS_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"name": "Forêt des Landes de Gascogne", "is_pine": True, "leaf_type": "needleleaved"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-1.15, 44.75], [-0.35, 44.80], [-0.15, 44.05], [-0.55, 43.65], [-1.35, 43.60], [-1.45, 44.35], [-1.15, 44.75]
                ]]
            }
        },
        {
            "type": "Feature",
            "properties": {"name": "Massif des Maures & Estérel (Var)", "is_pine": True, "leaf_type": "needleleaved"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [6.10, 43.30], [6.90, 43.60], [6.95, 43.45], [6.60, 43.10], [6.20, 43.10], [6.10, 43.30]
                ]]
            }
        },
        {
            "type": "Feature",
            "properties": {"name": "Parc National des Cévennes", "is_pine": True, "leaf_type": "mixed"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [3.20, 44.40], [3.90, 44.50], [4.10, 44.10], [3.50, 43.90], [3.20, 44.40]
                ]]
            }
        },
        {
            "type": "Feature",
            "properties": {"name": "Massif des Vosges", "is_pine": True, "leaf_type": "needleleaved"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [6.80, 48.50], [7.50, 48.60], [7.30, 47.80], [6.70, 47.90], [6.80, 48.50]
                ]]
            }
        },
        {
            "type": "Feature",
            "properties": {"name": "Forêt de Fontainebleau", "is_pine": False, "leaf_type": "broadleaved"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [2.55, 48.48], [2.78, 48.46], [2.75, 48.32], [2.52, 48.34], [2.55, 48.48]
                ]]
            }
        },
        {
            "type": "Feature",
            "properties": {"name": "Forêts de Corse (Vizzavona / Bavella)", "is_pine": True, "leaf_type": "needleleaved"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [8.80, 42.20], [9.30, 42.30], [9.35, 41.70], [8.90, 41.75], [8.80, 42.20]
                ]]
            }
        }
    ]
}

def is_cache_valid(filepath, max_age_seconds):
    if os.path.exists(filepath):
        return (time.time() - os.path.getmtime(filepath)) < max_age_seconds
    return False

# ==========================================
# 1. INCENDIES ACTIFS (NASA FIRMS)
# ==========================================
@st.cache_data(ttl=1800)
def fetch_fires_data():
    if is_cache_valid(FIRES_JSON_PATH, 1800):
        try:
            df = pd.read_json(FIRES_JSON_PATH)
            if not df.empty:
                df['datetime'] = pd.to_datetime(df['datetime_str'])
                return df
        except Exception:
            pass

    urls = [
        "https://firms.modaps.eosdis.nasa.gov/data/active_fire/suomi-npp-viirs-c2/csv/SUOMI_NPP_VIIRS_C2_Europe_7d.csv",
        "https://firms.modaps.eosdis.nasa.gov/data/active_fire/modis-c6.1/csv/MODIS_C6_1_Europe_7d.csv"
    ]
    
    dfs = []
    for url in urls:
        try:
            df_raw = pd.read_csv(url)
            df_fr = df_raw[
                (df_raw['latitude'] >= FRANCE_BBOX["lat_min"]) & 
                (df_raw['latitude'] <= FRANCE_BBOX["lat_max"]) &
                (df_raw['longitude'] >= FRANCE_BBOX["lon_min"]) & 
                (df_raw['longitude'] <= FRANCE_BBOX["lon_max"])
            ].copy()
            if not df_fr.empty:
                dfs.append(df_fr)
        except Exception:
            continue

    if not dfs:
        return pd.DataFrame()

    data = pd.concat(dfs, ignore_index=True)
    data['acq_time'] = data['acq_time'].astype(str).str.zfill(4)
    data['datetime_str'] = data['acq_date'] + ' ' + data['acq_time'].str[:2] + ':' + data['acq_time'].str[2:] + ':00'
    data['datetime'] = pd.to_datetime(data['datetime_str'])

    # Essayer de garder 36h, sinon garder 7 jours si le filtre 36h ne donne rien
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=36)
    data_filtered = data[data['datetime'] >= cutoff]
    if data_filtered.empty:
        data_filtered = data

    columns_to_keep = ['latitude', 'longitude', 'datetime_str', 'confidence']
    data_clean = data_filtered[columns_to_keep].copy()

    if not data_clean.empty:
        try:
            data_clean.to_json(FIRES_JSON_PATH, orient="records")
        except Exception:
            pass

    data_clean['datetime'] = pd.to_datetime(data_clean['datetime_str'])
    return data_clean

# ==========================================
# 2. MÉTÉO (OPEN-METEO)
# ==========================================
@st.cache_data(ttl=3600)
def fetch_weather_grid():
    if is_cache_valid(WEATHER_JSON_PATH, 3600):
        try:
            with open(WEATHER_JSON_PATH, 'r') as f:
                return json.load(f)
        except Exception:
            pass

    points = [
        (43.5, 6.0), (43.8, 5.0), (44.5, -0.5), (44.2, 3.5), 
        (43.0, 2.5), (48.4, 2.6), (48.5, 7.2), (42.0, 9.0)
    ]
    
    lat_str = ",".join([str(p[0]) for p in points])
    lon_str = ",".join([str(p[1]) for p in points])
    
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat_str}&longitude={lon_str}&current=temperature_2m,relative_humidity_2m,wind_speed_10m"
    
    weather_dict = {}
    try:
        res = requests.get(url, timeout=8).json()
        for i, loc in enumerate(res if isinstance(res, list) else [res]):
            cur = loc.get("current", {})
            weather_dict[f"{points[i][0]},{points[i][1]}"] = {
                "temp": cur.get("temperature_2m", 22),
                "humidity": cur.get("relative_humidity_2m", 45),
                "wind": cur.get("wind_speed_10m", 15)
            }
        with open(WEATHER_JSON_PATH, 'w') as f:
            json.dump(weather_dict, f)
    except Exception:
        pass

    return weather_dict

# ==========================================
# 3. CONTOURS DES FORÊTS (GEOJSON GARANTI)
# ==========================================
@st.cache_data(ttl=86400)
def fetch_forest_polygons():
    if is_cache_valid(FORESTS_GEOJSON_PATH, 86400):
        try:
            with open(FORESTS_GEOJSON_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if data.get("features"):
                    return data
        except Exception:
            pass

    # Tentative d'appel léger à l'API Overpass
    overpass_query = """
    [out:json][timeout:10];
    (
      way["landuse"="forest"](43.2,-0.8,44.8,-0.1);
      way["landuse"="forest"](43.1,6.0,43.7,6.9);
    );
    out body 20;
    >;
    out skel qt;
    """
    
    try:
        response = requests.post("https://overpass-api.de/api/interpreter", data={'data': overpass_query}, timeout=8)
        data = response.json()
        nodes = {node['id']: (node['lon'], node['lat']) for node in data.get('elements', []) if node['type'] == 'node'}
        features = []
        
        for elem in data.get('elements', []):
            if elem['type'] == 'way' and 'nodes' in elem:
                coords = [nodes[nid] for nid in elem['nodes'] if nid in nodes]
                if len(coords) >= 4 and coords[0] == coords[-1]:
                    features.append({
                        "type": "Feature",
                        "geometry": {"type": "Polygon", "coordinates": [coords]},
                        "properties": {
                            "name": elem.get('tags', {}).get('name', 'Zone Forestière'),
                            "is_pine": True,
                            "leaf_type": "needleleaved"
                        }
                    })
                    
        if features:
            geojson = {"type": "FeatureCollection", "features": features}
            with open(FORESTS_GEOJSON_PATH, 'w', encoding='utf-8') as f:
                json.dump(geojson, f)
            return geojson
    except Exception:
        pass

    # Si Overpass échoue ou dépasse le délai, retourner la base complète intégrée
    return DEFAULT_FORESTS_GEOJSON

# ==========================================
# ANALYSE DU RISQUE PAR FORÊT
# ==========================================
def analyze_forest_risk(feature, df_fires, weather_data):
    coords = feature['geometry']['coordinates'][0]
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    
    min_lon, max_lon = min(lons), max(lons)
    min_lat, max_lat = min(lats), max(lats)
    center_lat, center_lon = (min_lat + max_lat) / 2, (min_lon + max_lon) / 2

    # Incendie à proximité
    has_fire = False
    if not df_fires.empty:
        fires_near = df_fires[
            (df_fires['latitude'] >= min_lat - 0.05) & (df_fires['latitude'] <= max_lat + 0.05) &
            (df_fires['longitude'] >= min_lon - 0.05) & (df_fires['longitude'] <= max_lon + 0.05)
        ]
        has_fire = not fires_near.empty

    if has_fire:
        return {
            "status": "INCENDIE ACTIF EN COURS",
            "color": "#D32F2F",
            "fillColor": "#FF1744",
            "opacity": 0.85,
            "risk_label": "🔥 Foyer d'incendie détecté par satellite"
        }

    # Données météo les plus proches
    closest_weather = {"temp": 22, "humidity": 45, "wind": 15}
    min_dist = 999
    for key, w_info in weather_data.items():
        w_lat, w_lon = map(float, key.split(','))
        dist = ((w_lat - center_lat)**2 + (w_lon - center_lon)**2)**0.5
        if dist < min_dist:
            min_dist = dist
            closest_weather = w_info

    temp = closest_weather['temp']
    hum = closest_weather['humidity']
    is_pine = feature['properties'].get('is_pine', False)

    is_very_dry = (temp >= 24 and hum <= 45) or (temp >= 28) or (hum <= 35)
    
    if is_very_dry and is_pine:
        return {
            "status": "RISQUE EXTRÊME DE PROPAGATION",
            "color": "#E65100",
            "fillColor": "#FF5722",
            "opacity": 0.70,
            "risk_label": f"⚠️ Climat très sec & résineux (Temp: {temp}°C, Humidité: {hum}%)"
        }
    elif is_very_dry or is_pine:
        return {
            "status": "RISQUE ÉLEVÉ",
            "color": "#F57C00",
            "fillColor": "#FF9800",
            "opacity": 0.55,
            "risk_label": f"⚡ Sécheresse / Risque modéré (Temp: {temp}°C, Humidité: {hum}%)"
        }
    else:
        return {
            "status": "RISQUE FAIBLE",
            "color": "#2E7D32",
            "fillColor": "#4CAF50",
            "opacity": 0.40,
            "risk_label": f"🌲 Conditions normales (Temp: {temp}°C, Humidité: {hum}%)"
        }

# ==========================================
# APPLICATION PRINCIPALE
# ==========================================
def main():
    st.title("🌲 Zones Forestières & Incendies en France")
    
    st_autorefresh(interval=5 * 60 * 1000, key="datarefresh")
    
    df_fires = fetch_fires_data()
    weather_data = fetch_weather_grid()
    forests_geojson = fetch_forest_polygons()

    # Indicateurs
    col1, col2, col3 = st.columns(3)
    col1.metric("🔥 Foyers récents détectés", len(df_fires) if not df_fires.empty else 0)
    col2.metric("🌲 Massifs forestiers suivis", len(forests_geojson.get('features', [])))
    col3.metric("⏱️ Actualisation carte", datetime.datetime.now().strftime("%H:%M:%S"))

    # Initialisation de la carte Leaflet
    m = folium.Map(location=[45.8, 2.5], zoom_start=6, tiles="CartoDB dark_matter")

    # 1. Rendu des massifs forestiers (GeoJSON)
    if forests_geojson.get('features'):
        for feature in forests_geojson['features']:
            risk = analyze_forest_risk(feature, df_fires, weather_data)
            name = feature['properties'].get('name', 'Massif Forestier')
            
            popup_html = f"""
            <div style="font-family: Arial; font-size: 13px; min-width: 180px;">
                <b>{name}</b><br><br>
                <b>État :</b> <span style="color:{risk['color']};"><b>{risk['status']}</b></span><br>
                <b>Détail :</b> {risk['risk_label']}
            </div>
            """

            folium.GeoJson(
                feature,
                style_function=lambda x, r=risk: {
                    'fillColor': r['fillColor'],
                    'color': r['color'],
                    'weight': 2,
                    'fillOpacity': r['opacity']
                },
                tooltip=f"{name} - {risk['status']}",
                popup=folium.Popup(popup_html, max_width=280)
            ).add_to(m)

    # 2. Rendu direct des foyers d'incendies (Cercles rouges vifs)
    if not df_fires.empty:
        fire_group = folium.FeatureGroup(name="Foyers d'incendies détectés", show=True)
        for _, row in df_fires.iterrows():
            popup_fire = f"""
            <div style="font-family: Arial; font-size: 12px;">
                <b style="color:red;">Foyer d'incendie détecté</b><br>
                <b>Date/Heure :</b> {row['datetime_str']} UTC<br>
                <b>Coordonnées :</b> {row['latitude']}, {row['longitude']}
            </div>
            """
            folium.Circle(
                location=[row['latitude'], row['longitude']],
                radius=1500,
                color="#FF0000",
                fill=True,
                fill_color="#FF0000",
                fill_opacity=0.9,
                popup=folium.Popup(popup_fire, max_width=220),
                tooltip="🔥 Foyer d'incendie actif"
            ).add_to(fire_group)
        fire_group.add_to(m)

    # Affichage unique
    st_folium(m, width="100%", height=720, returned_objects=[])

if __name__ == "__main__":
    main()
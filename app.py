import streamlit as st
import pandas as pd
import folium
from folium.plugins import HeatMap
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
st.set_page_config(page_title="Suivi Satellite des Incendies", page_icon="🔥", layout="wide")

# Emprise géographique élargie (France métropolitaine + Corse)
FRANCE_BBOX = {"lat_min": 41.0, "lat_max": 51.5, "lon_min": -5.5, "lon_max": 10.0}

CACHE_DIR = "cache_data"
os.makedirs(CACHE_DIR, exist_ok=True)
FIRES_JSON_PATH = os.path.join(CACHE_DIR, "fires_data_multi.json")

def is_cache_valid(filepath, max_age_seconds):
    if os.path.exists(filepath):
        return (time.time() - os.path.getmtime(filepath)) < max_age_seconds
    return False

# ==========================================
# CLASSIFICATION SELON L'ANCIENNETÉ
# ==========================================
def get_recency_info(dt):
    now = datetime.datetime.utcnow()
    hours_ago = (now - dt).total_seconds() / 3600.0

    if hours_ago <= 12:
        return "#8B0000", "< 12h", "Moins de 12 heures"    # Rouge très foncé
    elif hours_ago <= 24:
        return "#FF0000", "< 24h", "12h à 24h"           # Rouge vif
    elif hours_ago <= 48:
        return "#FF7F00", "< 48h", "24h à 48h"           # Orange
    elif hours_ago <= 72:
        return "#FFB84D", "< 72h", "48h à 72h"           # Orange clair
    else:
        return "#FFE082", "> 72h", "Plus de 72h"         # Jaune clair

# ==========================================
# RÉCUPÉRATION DE TOUTES LES SOURCES SATELLITES (NASA FIRMS)
# ==========================================
@st.cache_data(ttl=1800)
def fetch_all_fires_data():
    if is_cache_valid(FIRES_JSON_PATH, 1800):
        try:
            df = pd.read_json(FIRES_JSON_PATH)
            if not df.empty:
                df['datetime'] = pd.to_datetime(df['datetime_str'])
                return df
        except Exception:
            pass

    # Flux 7 jours complets couvrant l'Europe (Toutes constellations satellites disponibles)
    urls = [
        "https://firms.modaps.eosdis.nasa.gov/data/active_fire/suomi-npp-viirs-c2/csv/SUOMI_NPP_VIIRS_C2_Europe_7d.csv",
        "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_Europe_7d.csv",
        "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-21-viirs-c2/csv/J2_VIIRS_C2_Europe_7d.csv",
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
    
    # Formatage propre des timestamps
    data['acq_time'] = data['acq_time'].astype(str).str.zfill(4)
    data['datetime_str'] = data['acq_date'] + ' ' + data['acq_time'].str[:2] + ':' + data['acq_time'].str[2:] + ':00'
    data['datetime'] = pd.to_datetime(data['datetime_str'])

    columns_to_keep = ['latitude', 'longitude', 'datetime_str', 'confidence']
    data_clean = data[columns_to_keep].drop_duplicates(subset=['latitude', 'longitude', 'datetime_str']).copy()

    if not data_clean.empty:
        try:
            data_clean.to_json(FIRES_JSON_PATH, orient="records")
        except Exception:
            pass

    data_clean['datetime'] = pd.to_datetime(data_clean['datetime_str'])
    return data_clean

# ==========================================
# APPLICATION PRINCIPALE
# ==========================================
def main():
    st.title("🔥 Carte de Détection des Foyers par Satellite")
    
    st_autorefresh(interval=5 * 60 * 1000, key="datarefresh")
    
    df_fires = fetch_all_fires_data()

    # Barre latérale
    st.sidebar.header("🎨 Légende de Récence")
    st.sidebar.markdown("""
    - 🟤 **Rouge très foncé** : Moins de 12h
    - 🔴 **Rouge vif** : 12h à 24h
    - 🟠 **Orange** : 24h à 48h
    - 🟡 **Orange clair** : 48h à 72h
    - ⚪ **Jaune** : Plus de 72h
    """)
    st.sidebar.markdown("---")
    st.sidebar.info("""
    **Affichage intelligent :**
    - **Dézoome (Vue nationale)** : La carte thermique indique la densité globale des zones touchées sans surcharger les foyers isolés.
    - **Zoome (Vue départementale)** : La heatmap s'efface pour laisser apparaître uniquement les points précis.
    """)

    col1, col2 = st.columns(2)
    col1.metric("🔥 Total des détections (7 derniers jours)", len(df_fires) if not df_fires.empty else 0)
    col2.metric("⏱️ Horodatage", datetime.datetime.now().strftime("%H:%M:%S"))

    # Initialisation de la carte Folium sans fond par défaut (définis ci-dessous)
    m = folium.Map(location=[46.5, 2.5], zoom_start=6, tiles=None)

    # 1. FONDS DE CARTE SELECTIONNABLES
    # Vue Satellite HD Esri
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri World Imagery",
        name="🛰️ Satellite HD (Esri)",
        overlay=False,
        control=True
    ).add_to(m)

    # Vue Topographique / Végétation
    folium.TileLayer(
        tiles="https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        attr="OpenTopoMap",
        name="🌲 Relief & Végétation (OpenTopoMap)",
        overlay=False,
        control=True
    ).add_to(m)

    # Vue Plan standard OpenStreetMap
    folium.TileLayer(
        tiles="OpenStreetMap",
        name="🗺️ Carte Routière (OpenStreetMap)",
        overlay=False,
        control=True
    ).add_to(m)

    if not df_fires.empty:
        # 2. COUCHE HEATMAP ADAPTATIVE (Visuel global dézoomé, masqué si zoom > 8)
        heat_data = [[row['latitude'], row['longitude'], 0.6] for _, row in df_fires.iterrows()]
        
        heatmap_layer = HeatMap(
            heat_data,
            name="🔥 Densité (Vue d'ensemble)",
            radius=11,          # Rayon restreint pour ne pas saturer sur un point isolé
            blur=9,             # Flou modéré
            min_opacity=0.25,
            max_zoom=8,         # Masque automatiquement la heatmap dès qu'on zoome plus près
            gradient={0.3: '#FFE082', 0.6: '#FF7F00', 0.85: '#FF0000', 1.0: '#8B0000'}
        )
        heatmap_layer.add_to(m)

        # 3. COUCHE POINTS PRÉCIS (Visibles à toutes les échelles, prioritaires au zoom)
        points_group = folium.FeatureGroup(name="📍 Points précis (Horodatés)", show=True)
        
        for _, row in df_fires.iterrows():
            color, label_age, age_desc = get_recency_info(row['datetime'])
            
            popup_html = f"""
            <div style="font-family: Arial; font-size: 12px; min-width: 170px;">
                <b style="color:{color};">🔥 Détection satellite ({label_age})</b><br><br>
                <b>Ancienneté :</b> {age_desc}<br>
                <b>Date/Heure UTC :</b> {row['datetime_str']}<br>
                <b>Coordonnées :</b> {row['latitude']:.4f}, {row['longitude']:.4f}
            </div>
            """
            
            folium.CircleMarker(
                location=[row['latitude'], row['longitude']],
                radius=3.5,              # Petit rond très lisible
                color="#000000",         # Bordure noire fine pour ressortir sur fond satellite
                weight=0.5,
                fill=True,
                fill_color=color,
                fill_opacity=0.95,
                popup=folium.Popup(popup_html, max_width=240),
                tooltip=f"Feu {label_age} ({row['datetime_str']})"
            ).add_to(points_group)

        points_group.add_to(m)

    # Sélecteur de couches en haut à droite de la carte
    folium.LayerControl(collapsed=False).add_to(m)

    # Rendu final
    st_folium(m, width="100%", height=760, returned_objects=[])

if __name__ == "__main__":
    main()
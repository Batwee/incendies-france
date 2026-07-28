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
st.set_page_config(page_title="Suivi Satellite des Incendies (France & Espagne)", page_icon="🔥", layout="wide")

REGION_BBOX = {"lat_min": 35.0, "lat_max": 51.5, "lon_min": -10.0, "lon_max": 10.0}

CACHE_DIR = "cache_data"
os.makedirs(CACHE_DIR, exist_ok=True)
FIRES_JSON_PATH = os.path.join(CACHE_DIR, "fires_data_fr_es.json")

def is_cache_valid(filepath, max_age_seconds):
    if os.path.exists(filepath):
        return (time.time() - os.path.getmtime(filepath)) < max_age_seconds
    return False

# ==========================================
# CLASSIFICATION SELON L'ANCIENNETÉ
# ==========================================
def get_recency_info(dt, now):
    hours_ago = (now - dt).total_seconds() / 3600.0

    if hours_ago <= 12:
        return "#8B0000", "< 12h", "Moins de 12 heures", True
    elif hours_ago <= 24:
        return "#FF0000", "< 24h", "12h à 24h", True
    elif hours_ago <= 48:
        return "#FF7F00", "< 48h", "24h à 48h", True
    elif hours_ago <= 72:
        return "#FFB84D", "< 72h", "48h à 72h", True
    else:
        return "#FFE082", "> 72h", "Plus de 72h", False  # Marqué comme ancien

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
            df_sub = df_raw[
                (df_raw['latitude'] >= REGION_BBOX["lat_min"]) & 
                (df_raw['latitude'] <= REGION_BBOX["lat_max"]) &
                (df_raw['longitude'] >= REGION_BBOX["lon_min"]) & 
                (df_raw['longitude'] <= REGION_BBOX["lon_max"])
            ].copy()
            if not df_sub.empty:
                dfs.append(df_sub)
        except Exception:
            continue

    if not dfs:
        return pd.DataFrame()

    data = pd.concat(dfs, ignore_index=True)
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
    st.title("🔥 Détection des Foyers par Satellite (France & Espagne)")
    
    st_autorefresh(interval=5 * 60 * 1000, key="datarefresh")
    
    df_fires = fetch_all_fires_data()
    now_utc = datetime.datetime.utcnow()

    # Indicateurs d'en-tête
    col1, col2 = st.columns(2)
    col1.metric("🔥 Total foyers détectés (7 jours)", len(df_fires) if not df_fires.empty else 0)
    col2.metric("⏱️ Dernière actualisation", datetime.datetime.now().strftime("%H:%M:%S"))

    # LÉGENDE CORRIGÉE (TEXTE BLANC SUR FOND SOMBRE RENDERÉ PROPREMENT)
    st.markdown("""
    <div style="background-color: #262730; padding: 12px; border-radius: 8px; margin-bottom: 12px; border: 1px solid #444; color: #FFFFFF;">
        <span style="font-weight: bold; margin-right: 15px; color: #FFFFFF;">🎨 Récence des détections :</span>
        <span style="margin-right: 15px; font-size: 14px; color: #FFFFFF;"><span style="color:#8B0000;">🟤</span> <b style="color: #FFFFFF;">&lt; 12h</b></span>
        <span style="margin-right: 15px; font-size: 14px; color: #FFFFFF;"><span style="color:#FF0000;">🔴</span> <b style="color: #FFFFFF;">12h à 24h</b></span>
        <span style="margin-right: 15px; font-size: 14px; color: #FFFFFF;"><span style="color:#FF7F00;">🟠</span> <b style="color: #FFFFFF;">24h à 48h</b></span>
        <span style="margin-right: 15px; font-size: 14px; color: #FFFFFF;"><span style="color:#FFB84D;">🟡</span> <b style="color: #FFFFFF;">48h à 72h</b></span>
        <span style="margin-right: 15px; font-size: 14px; color: #FFFFFF;"><span style="color:#FFE082;">⚪</span> <b style="color: #FFFFFF;">&gt; 72h (Zone diffuse)</b></span>
    </div>
    """, unsafe_allow_html=True)

    current_zoom = st.session_state.get("last_zoom", 5)
    current_bounds = st.session_state.get("last_bounds", None)

    # Carte centrée sur les Pyrénées
    m = folium.Map(location=[43.0, 1.5], zoom_start=current_zoom, tiles=None)

    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri World Imagery",
        name="🛰️ Satellite HD (Esri)",
        overlay=False
    ).add_to(m)

    folium.TileLayer(
        tiles="https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        attr="OpenTopoMap",
        name="🌲 Relief & Végétation",
        overlay=False
    ).add_to(m)

    folium.TileLayer(
        tiles="OpenStreetMap",
        name="🗺️ Carte Routière",
        overlay=False
    ).add_to(m)

    # SEUIL DE ZOOM ÉLEVÉ POUR ÉVITER TOUT PLANTAGE (Niveau local / commune)
    ZOOM_THRESHOLD = 10 

    if not df_fires.empty:
        if current_zoom < ZOOM_THRESHOLD:
            # 1. VUE MACRO (< ZOOM 10) : Carte de densité globale
            st.info("🗺️ **Vue d'ensemble** : Densité globale affichée. Zoomez fortement sur une zone précise (zoom ≥ 10) pour voir les foyers exacts.")
            
            # Échantillonnage léger pour garantir 60 FPS lors du pan/zoom
            sample_df = df_fires if len(df_fires) < 10000 else df_fires.sample(10000, random_state=42)
            heat_data = sample_df[['latitude', 'longitude']].values.tolist()
            
            HeatMap(
                heat_data,
                radius=10,
                blur=8,
                min_opacity=0.35,
                gradient={0.2: '#FFE082', 0.5: '#FF7F00', 0.8: '#FF0000', 1.0: '#8B0000'}
            ).add_to(m)

        else:
            # 2. VUE ZOOMÉE (≥ ZOOM 10) : Extraction stricte de l'emprise visible
            st.success(f"📍 **Zoom local ({current_zoom})** : Affichage des foyers actifs sur la zone visible.")
            
            df_visible = df_fires
            if current_bounds:
                try:
                    sw = current_bounds['_southWest']
                    ne = current_bounds['_northEast']
                    df_visible = df_fires[
                        (df_fires['latitude'] >= sw['lat']) & (df_fires['latitude'] <= ne['lat']) &
                        (df_fires['longitude'] >= sw['lng']) & (df_fires['longitude'] <= ne['lng'])
                    ]
                except Exception:
                    df_visible = df_fires

            if not df_visible.empty:
                # Séparation des points : Récents (<=72h) vs Anciens (>72h)
                hours_ago_series = (now_utc - df_visible['datetime']).dt.total_seconds() / 3600.0
                
                df_recent = df_visible[hours_ago_series <= 72]
                df_old = df_visible[hours_ago_series > 72]

                # A) Les points de plus de 72h sont affichés sous forme de voile jaune (Heatmap très douce)
                # Cela évite de créer des milliers de marqueurs individuels inutiles
                if not df_old.empty:
                    old_heat_data = df_old[['latitude', 'longitude']].values.tolist()
                    HeatMap(
                        old_heat_data,
                        radius=15,
                        blur=12,
                        min_opacity=0.25,
                        gradient={0.4: '#FFE082', 1.0: '#FFD54F'}
                    ).add_to(m)

                # B) Seuls les points récents (<= 72h) sont créés sous forme de CircleMarker
                # On limite strictement à 500 marqueurs max pour prémunir tout plantage du navigateur
                if len(df_recent) > 500:
                    df_recent = df_recent.sort_values(by='datetime', ascending=False).head(500)

                for _, row in df_recent.iterrows():
                    color, label_age, age_desc, _ = get_recency_info(row['datetime'], now_utc)
                    
                    popup_html = f"""
                    <div style="font-family: Arial; font-size: 12px; min-width: 150px;">
                        <b style="color:{color};">🔥 Foyer ({label_age})</b><br><br>
                        <b>Ancienneté :</b> {age_desc}<br>
                        <b>Date UTC :</b> {row['datetime_str']}<br>
                        <b>Coordonnées :</b> {row['latitude']:.4f}, {row['longitude']:.4f}
                    </div>
                    """
                    
                    folium.CircleMarker(
                        location=[row['latitude'], row['longitude']],
                        radius=4,
                        color="#000000",
                        weight=0.5,
                        fill=True,
                        fill_color=color,
                        fill_opacity=0.9,
                        popup=folium.Popup(popup_html, max_width=220),
                        tooltip=f"Feu {label_age} ({row['datetime_str']})"
                    ).add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)

    # Capture réactive du zoom et des limites
    map_output = st_folium(
        m, 
        width="100%", 
        height=720, 
        key="main_fire_map",
        returned_objects=["zoom", "bounds"]
    )

    if map_output and "zoom" in map_output and map_output["zoom"] is not None:
        new_zoom = map_output["zoom"]
        new_bounds = map_output.get("bounds")
        
        if new_zoom != st.session_state.get("last_zoom") or new_bounds != st.session_state.get("last_bounds"):
            st.session_state["last_zoom"] = new_zoom
            st.session_state["last_bounds"] = new_bounds
            st.rerun()

if __name__ == "__main__":
    main()
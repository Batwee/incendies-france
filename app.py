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
# CONFIGURATION ET CONSTANTES
# ==========================================
st.set_page_config(page_title="Incendies France", page_icon="🔥", layout="wide")

# Limites géographiques de la France métropolitaine
FRANCE_BBOX = {"lat_min": 41.3, "lat_max": 51.1, "lon_min": -5.2, "lon_max": 9.6}

# Répertoire de cache local sur disque
CACHE_DIR = "cache_data"
os.makedirs(CACHE_DIR, exist_ok=True)
FIRES_JSON_PATH = os.path.join(CACHE_DIR, "fires_data.json")
WEATHER_JSON_PATH = os.path.join(CACHE_DIR, "weather_data.json")

def is_cache_valid(filepath, max_age_seconds):
    """Vérifie si le fichier JSON local existe et est récent."""
    if os.path.exists(filepath):
        return (time.time() - os.path.getmtime(filepath)) < max_age_seconds
    return False

# ==========================================
# DONNÉES INCENDIES (NASA FIRMS)
# ==========================================
@st.cache_data(ttl=1800)
def fetch_and_format_firms_data():
    """
    Récupère, filtre et formate les données d'incendies.
    Remarque: AUCUN appel à st.* n'est fait ici pour éviter CacheReplayClosureError.
    """
    # 1. Utilisation du cache local JSON si valide (30 minutes)
    if is_cache_valid(FIRES_JSON_PATH, 1800):
        try:
            df = pd.read_json(FIRES_JSON_PATH)
            df['datetime'] = pd.to_datetime(df['datetime_str'])
            return df
        except Exception:
            pass

    # 2. Rechargement via l'API NASA si le cache est expiré
    urls = {
        "MODIS": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/modis-c6.1/csv/MODIS_C6_1_Europe_7d.csv",
        "VIIRS_SNPP": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/suomi-npp-viirs-c2/csv/SUOMI_NPP_VIIRS_C2_Europe_7d.csv",
        "VIIRS_NOAA20": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_Europe_7d.csv",
        "VIIRS_NOAA21": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-21-viirs-c2/csv/J2_VIIRS_C2_Europe_7d.csv"
    }
    
    dfs = []
    for source, url in urls.items():
        try:
            df_raw = pd.read_csv(url)
            # Filtrage précoce sur la France
            df_fr = df_raw[
                (df_raw['latitude'] >= FRANCE_BBOX["lat_min"]) & 
                (df_raw['latitude'] <= FRANCE_BBOX["lat_max"]) &
                (df_raw['longitude'] >= FRANCE_BBOX["lon_min"]) & 
                (df_raw['longitude'] <= FRANCE_BBOX["lon_max"])
            ].copy()
            
            if not df_fr.empty:
                df_fr['source'] = source
                dfs.append(df_fr)
        except Exception:
            continue
            
    if not dfs:
        return pd.DataFrame()
        
    data = pd.concat(dfs, ignore_index=True)
    
    # Formatage de la date et heure
    data['acq_time'] = data['acq_time'].astype(str).str.zfill(4)
    data['datetime_str'] = data['acq_date'] + ' ' + data['acq_time'].str[:2] + ':' + data['acq_time'].str[2:] + ':00'
    data['datetime'] = pd.to_datetime(data['datetime_str'])

    # Normalisation robuste de la confiance (évite ValueError)
    def normalize_confidence(row):
        val = row['confidence']
        if pd.isna(val): return 50
        if isinstance(val, str):
            val_clean = val.strip().lower()
            if val_clean in ['l', 'low']: return 33
            if val_clean in ['n', 'nominal']: return 66
            if val_clean in ['h', 'high']: return 100
            try: return int(float(val_clean))
            except ValueError: return 50
        try: return int(val)
        except (ValueError, TypeError): return 50

    data['confidence_pct'] = data.apply(normalize_confidence, axis=1)
    data['frp'] = data['frp'].fillna(0.0) if 'frp' in data.columns else 0.0

    # Conservation uniquement des colonnes nécessaires
    columns_to_keep = ['latitude', 'longitude', 'datetime_str', 'confidence_pct', 'frp', 'source']
    data_clean = data[columns_to_keep]

    # Sauvegarde dans le JSON local
    try:
        data_clean.to_json(FIRES_JSON_PATH, orient="records")
    except Exception:
        pass
    
    data_clean['datetime'] = data['datetime']
    return data_clean

# ==========================================
# DONNÉES ZONES MENACÉES (OPEN-METEO)
# ==========================================
@st.cache_data(ttl=3600)
def fetch_and_format_threat_zones():
    """Calcul de l'indice de risque de propagation météo."""
    if is_cache_valid(WEATHER_JSON_PATH, 3600):
        try:
            with open(WEATHER_JSON_PATH, 'r') as f:
                return json.load(f)
        except Exception:
            pass

    lats = [42.5, 43.5, 44.5, 45.5, 46.5, 47.5, 48.5, 49.5]
    lons = [-4.0, -2.0, 0.0, 2.0, 4.0, 6.0, 8.0]
    points = [(lat, lon) for lat in lats for lon in lons]
    
    lat_str = ",".join([str(p[0]) for p in points])
    lon_str = ",".join([str(p[1]) for p in points])
    
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat_str}&longitude={lon_str}&current=temperature_2m,relative_humidity_2m,wind_speed_10m"
    
    risk_data = []
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        weather_data = response.json()
        
        for i, loc_weather in enumerate(weather_data):
            current = loc_weather.get("current", {})
            temp = current.get("temperature_2m", 15)
            hum = current.get("relative_humidity_2m", 50)
            wind = current.get("wind_speed_10m", 10)
            
            r_temp = max(0, min((temp - 15) / 25, 1))
            r_hum = max(0, min((80 - hum) / 60, 1))
            r_wind = max(0, min(wind / 50, 1))
            risk_score = (r_temp * 0.4 + r_wind * 0.4 + r_hum * 0.2) * 100
            
            if risk_score > 20:
                risk_data.append([points[i][0], points[i][1], risk_score / 100])
                
        with open(WEATHER_JSON_PATH, 'w') as f:
            json.dump(risk_data, f)
            
    except Exception:
        pass
        
    return risk_data

# ==========================================
# APPLICATION PRINCIPALE
# ==========================================
def main():
    st.title("🔥 Zones d'Incendies en France (Temps Réel)")
    
    # Rafraîchissement automatique discret
    st_autorefresh(interval=5 * 60 * 1000, key="datarefresh")
    
    # Chargement des données
    with st.spinner("Chargement des zones d'incendies récentes..."):
        df_fires = fetch_and_format_firms_data()
        threat_grid = fetch_and_format_threat_zones()
    
    if df_fires.empty:
        st.warning("Aucune détection d'incendie enregistrée dans les données récentes.")
        return
        
    # FILTRE AUTOMATIQUE : Seulement les 36 dernières heures pour avoir des données très récentes
    now = datetime.datetime.utcnow()
    cutoff = now - datetime.timedelta(hours=36)
    df_recent = df_fires[df_fires['datetime'] >= cutoff]
    
    # Si très peu de feux en 36h, étendre aux dernières 48h
    if len(df_recent) < 5:
        cutoff = now - datetime.timedelta(hours=48)
        df_recent = df_fires[df_fires['datetime'] >= cutoff]

    # En-tête informatif
    col1, col2 = st.columns(2)
    col1.metric("🔥 Zones d'incendie détectées (Récents)", len(df_recent))
    col2.metric("⏱️ Horodateur (UTC)", now.strftime("%H:%M - %d/%m/%Y"))
    
    # Carte Folium centrée sur la France
    m = folium.Map(location=[46.2276, 2.2137], zoom_start=6, tiles="CartoDB dark_matter")
    
    # Couche 1 : ZONES d'incendie en ROUGE (Cercle géographique réel de 2 km)
    fire_group = folium.FeatureGroup(name="Zones d'incendie en cours", show=True)
    
    for _, row in df_recent.iterrows():
        popup_html = f"""
        <div style="font-family: Arial; font-size: 13px;">
            <b style="color: #ff4b4b;">Zone d'incendie touchée</b><br>
            <b>Détection :</b> {row['datetime_str']} UTC<br>
            <b>Intensité (FRP) :</b> {row['frp']} MW<br>
            <b>Confiance :</b> {row['confidence_pct']}%<br>
            <b>Source satellite :</b> {row['source']}
        </div>
        """
        
        # Zone géographique en rouge (rayon de 2000 mètres sur le terrain)
        folium.Circle(
            location=[row['latitude'], row['longitude']],
            radius=2000,             # 2 km de rayon géographique réels
            color='#FF0000',         # Bordure rouge vif
            weight=2,
            fill=True,
            fill_color='#FF0000',    # Remplissage rouge
            fill_opacity=0.55,       # Opacité pour voir la forêt / terrain sous le rouge
            popup=folium.Popup(popup_html, max_width=250),
            tooltip="Zone touchée par un incendie"
        ).add_to(fire_group)
        
    fire_group.add_to(m)
    
    # Couche 2 : Zones à risque météo (Heatmap activable)
    if threat_grid:
        threat_group = folium.FeatureGroup(name="Zones à risque (Météo/Propagation)", show=False)
        HeatMap(
            threat_grid, 
            radius=35, 
            blur=25, 
            gradient={0.2: 'blue', 0.4: 'lime', 0.6: 'yellow', 0.8: 'orange', 1.0: 'red'}
        ).add_to(threat_group)
        threat_group.add_to(m)
        
    # Contrôle pour afficher/masquer la couche météo
    folium.LayerControl(collapsed=False).add_to(m)
    
    # Affichage de la carte plein écran
    st_folium(m, width="100%", height=720, returned_objects=[])

if __name__ == "__main__":
    main()
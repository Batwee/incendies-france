import streamlit as st
import pandas as pd
import folium
from folium.plugins import HeatMap, MarkerCluster
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

# Limites géographiques de la France métropolitaine (filtrage précoce)
FRANCE_BBOX = {"lat_min": 41.3, "lat_max": 51.1, "lon_min": -5.2, "lon_max": 9.6}

# Dossier pour stocker nos fichiers de cache JSON locaux
CACHE_DIR = "cache_data"
os.makedirs(CACHE_DIR, exist_ok=True)
FIRES_JSON_PATH = os.path.join(CACHE_DIR, "fires_data.json")
WEATHER_JSON_PATH = os.path.join(CACHE_DIR, "weather_data.json")

# ==========================================
# FONCTIONS UTILITAIRES (JSON CACHE)
# ==========================================
def is_cache_valid(filepath, max_age_seconds):
    """Vérifie si le fichier JSON existe et est suffisamment récent."""
    if os.path.exists(filepath):
        file_age = time.time() - os.path.getmtime(filepath)
        return file_age < max_age_seconds
    return False

# ==========================================
# GESTION DES DONNÉES : INCENDIES (NASA)
# ==========================================
@st.cache_data(ttl=1800) # Cache mémoire Streamlit additionnel
def fetch_and_format_firms_data():
    """
    Récupère, filtre, formate et sauvegarde les données d'incendies.
    Utilise le cache JSON local si disponible pour éviter les appels réseau.
    """
    # 1. Vérification du cache local (Valide 30 minutes)
    if is_cache_valid(FIRES_JSON_PATH, 1800):
        try:
            df = pd.read_json(FIRES_JSON_PATH)
            # Reconversion de la date (texte) en objet datetime pour les filtres Streamlit
            df['datetime'] = pd.to_datetime(df['datetime_str'])
            return df
        except Exception as e:
            st.warning("Erreur de lecture du cache JSON. Rechargement depuis l'API...")

    # 2. Si le cache est invalide ou absent, on interroge les API de la NASA
    urls = {
        "MODIS": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/modis-c6.1/csv/MODIS_C6_1_Europe_7d.csv",
        "VIIRS_SNPP": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/suomi-npp-viirs-c2/csv/SUOMI_NPP_VIIRS_C2_Europe_7d.csv",
        "VIIRS_NOAA20": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_Europe_7d.csv",
        "VIIRS_NOAA21": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-21-viirs-c2/csv/J2_VIIRS_C2_Europe_7d.csv"
    }
    
    dfs = []
    for source, url in urls.items():
        try:
            # Récupération des données brutes
            df_raw = pd.read_csv(url)
            
            # FILTRAGE ULTRA-PRÉCOCE : On ne garde que la France pour alléger la RAM
            df_fr = df_raw[
                (df_raw['latitude'] >= FRANCE_BBOX["lat_min"]) & 
                (df_raw['latitude'] <= FRANCE_BBOX["lat_max"]) &
                (df_raw['longitude'] >= FRANCE_BBOX["lon_min"]) & 
                (df_raw['longitude'] <= FRANCE_BBOX["lon_max"])
            ].copy()
            
            if not df_fr.empty:
                df_fr['source'] = source
                dfs.append(df_fr)
        except Exception as e:
            st.toast(f"⚠️ API {source} inaccessible temporairement.")
            continue
            
    if not dfs:
        return pd.DataFrame()
        
    data = pd.concat(dfs, ignore_index=True)
    
    # 3. FORMATAGE DES DONNÉES
    # Création d'une date ISO standardisée
    data['acq_time'] = data['acq_time'].astype(str).str.zfill(4)
    data['datetime_str'] = data['acq_date'] + ' ' + data['acq_time'].str[:2] + ':' + data['acq_time'].str[2:] + ':00'
    data['datetime'] = pd.to_datetime(data['datetime_str'])

    # Fonction ultra robuste pour la confiance (Correction de la ValueError)
    def normalize_confidence(row):
        val = row['confidence']
        if pd.isna(val): return 50
        
        if isinstance(val, str):
            val_clean = val.strip().lower()
            if val_clean in ['l', 'low']: return 33
            if val_clean in ['n', 'nominal']: return 66
            if val_clean in ['h', 'high']: return 100
            try:
                return int(float(val_clean))
            except ValueError:
                return 50 
                
        try:
            return int(val)
        except (ValueError, TypeError):
            return 50

    data['confidence_pct'] = data.apply(normalize_confidence, axis=1)
    
    # Remplacer les FRP (Fire Radiative Power) manquants par 0
    if 'frp' not in data.columns:
        data['frp'] = 0.0
    else:
        data['frp'] = data['frp'].fillna(0.0)

    # 4. ÉPURATION : On ne garde QUE ce qui sert à l'affichage
    columns_to_keep = ['latitude', 'longitude', 'datetime_str', 'confidence_pct', 'frp', 'source']
    data_clean = data[columns_to_keep]

    # 5. SAUVEGARDE JSON (Écriture sur disque pour la prochaine fois)
    data_clean.to_json(FIRES_JSON_PATH, orient="records")
    
    # On rajoute l'objet datetime pour l'utilisation immédiate dans Streamlit
    data_clean['datetime'] = data['datetime']
    
    return data_clean

# ==========================================
# GESTION DES DONNÉES : ZONES MENACÉES (MÉTÉO)
# ==========================================
@st.cache_data(ttl=3600)
def fetch_and_format_threat_zones():
    """
    Récupère la météo, calcule le risque et le met en cache JSON.
    """
    if is_cache_valid(WEATHER_JSON_PATH, 3600): # Cache d'une heure pour la météo
        try:
            with open(WEATHER_JSON_PATH, 'r') as f:
                return json.load(f)
        except Exception:
            pass # Si erreur, on refait l'appel API

    # Grille de points sur la France
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
            
            # Algorithme de risque incendie
            r_temp = max(0, min((temp - 15) / 25, 1))
            r_hum = max(0, min((80 - hum) / 60, 1))
            r_wind = max(0, min(wind / 50, 1))
            risk_score = (r_temp * 0.4 + r_wind * 0.4 + r_hum * 0.2) * 100
            
            if risk_score > 20: # Filtrage : on ignore les zones sans risque
                risk_data.append([points[i][0], points[i][1], risk_score / 100])
                
        # Sauvegarde JSON formatée [ [lat, lon, intensite], [...] ]
        with open(WEATHER_JSON_PATH, 'w') as f:
            json.dump(risk_data, f)
            
    except Exception as e:
        st.toast("⚠️ Impossible de charger la modélisation météo.")
        
    return risk_data

# ==========================================
# INTERFACE UTILISATEUR & CARTE
# ==========================================
def main():
    st.title("🔥 Suivi des Incendies en France")
    
    # -- Barre latérale --
    st.sidebar.header("⚙️ Paramètres & Filtres")
    
    auto_refresh = st.sidebar.checkbox("Actualisation automatique (5 min)", value=False)
    if auto_refresh:
        st_autorefresh(interval=5 * 60 * 1000, key="datarefresh")
    
    time_filter = st.sidebar.selectbox("Historique", ["Dernières 24h", "Dernières 48h", "Derniers 7 jours"])
    
    # Récupération optimisée des données
    with st.spinner("Analyse et formatage des données satellitaires..."):
        df_fires = fetch_and_format_firms_data()
        threat_grid = fetch_and_format_threat_zones()
    
    if df_fires.empty:
        st.error("Aucune donnée d'incendie n'est disponible actuellement.")
        return
        
    # Application du filtre temporel
    now = datetime.datetime.utcnow()
    if time_filter == "Dernières 24h":
        cutoff = now - datetime.timedelta(days=1)
    elif time_filter == "Dernières 48h":
        cutoff = now - datetime.timedelta(days=2)
    else:
        cutoff = now - datetime.timedelta(days=7)
        
    df_filtered = df_fires[df_fires['datetime'] >= cutoff]
    
    # Filtres complémentaires UI
    st.sidebar.markdown("---")
    min_confidence = st.sidebar.slider("Confiance minimum (%)", 0, 100, 50)
    
    available_sources = df_fires['source'].unique().tolist() if not df_fires.empty else []
    sources_selected = st.sidebar.multiselect("Sources Satellitaires", options=available_sources, default=available_sources)
    
    # Filtrage final du DataFrame
    df_filtered = df_filtered[
        (df_filtered['confidence_pct'] >= min_confidence) &
        (df_filtered['source'].isin(sources_selected))
    ]
    
    # -- Métriques --
    col1, col2, col3 = st.columns(3)
    col1.metric("🔥 Foyers actifs affichés", len(df_filtered))
    
    cache_time = "Inconnu"
    if os.path.exists(FIRES_JSON_PATH):
        cache_time = datetime.datetime.fromtimestamp(os.path.getmtime(FIRES_JSON_PATH)).strftime("%H:%M:%S")
    col2.metric("⏱️ Dernière actualisation (Cache)", cache_time)
    
    # -- Rendu de la carte --
    m = folium.Map(location=[46.2276, 2.2137], zoom_start=6, tiles="CartoDB dark_matter")
    
    fire_group = folium.FeatureGroup(name="Foyers d'incendie", show=True)
    marker_cluster = MarkerCluster().add_to(fire_group)
    
    for _, row in df_filtered.iterrows():
        popup_html = f"""
        <div style="font-family: Arial; font-size: 13px;">
            <b style="color: #ff4b4b;">Détails du Foyer</b><br>
            <b>Heure (UTC):</b> {row['datetime_str']}<br>
            <b>Confiance:</b> {row['confidence_pct']}%<br>
            <b>Puissance:</b> {row['frp']} MW<br>
            <b>Source:</b> {row['source']}
        </div>
        """
        color = "red" if row['confidence_pct'] > 80 else "orange" if row['confidence_pct'] > 50 else "yellow"
        
        folium.CircleMarker(
            location=[row['latitude'], row['longitude']],
            radius=6,
            color=color,
            fill=True,
            fill_opacity=0.7,
            popup=folium.Popup(popup_html, max_width=250)
        ).add_to(marker_cluster)
        
    fire_group.add_to(m)
    
    if threat_grid:
        threat_group = folium.FeatureGroup(name="Risque Météo (Heatmap)", show=False)
        HeatMap(
            threat_grid, radius=35, blur=20, 
            gradient={0.2: 'blue', 0.4: 'lime', 0.6: 'yellow', 0.8: 'orange', 1.0: 'red'}
        ).add_to(threat_group)
        threat_group.add_to(m)
        
    folium.LayerControl(collapsed=False).add_to(m)
    
    # Affichage de la carte
    st_folium(m, width="100%", height=700, returned_objects=[])

if __name__ == "__main__":
    main()
import streamlit as st
import pandas as pd
import folium
from folium.plugins import HeatMap, MarkerCluster
from streamlit_folium import st_folium
import requests
import datetime
from streamlit_autorefresh import st_autorefresh

# ==========================================
# CONFIGURATION DE LA PAGE
# ==========================================
st.set_page_config(page_title="Incendies France", page_icon="🔥", layout="wide")

# Limites géographiques approximatives de la France métropolitaine
FRANCE_BBOX = {"lat_min": 41.3, "lat_max": 51.1, "lon_min": -5.2, "lon_max": 9.6}

# ==========================================
# GESTION DES DONNÉES : INCENDIES (NASA FIRMS)
# ==========================================
@st.cache_data(ttl=1800) # Cache de 30 minutes
def fetch_firms_data():
    """
    Récupère et fusionne les données CSV des 7 derniers jours des capteurs MODIS et VIIRS pour l'Europe,
    puis filtre sur la France.
    """
    urls = {
        "MODIS": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/modis-c6.1/csv/MODIS_C6_1_Europe_7d.csv",
        "VIIRS_SNPP": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/suomi-npp-viirs-c2/csv/SUOMI_NPP_VIIRS_C2_Europe_7d.csv",
        "VIIRS_NOAA20": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_Europe_7d.csv",
        "VIIRS_NOAA21": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-21-viirs-c2/csv/J2_VIIRS_C2_Europe_7d.csv"
    }
    
    dfs = []
    for source, url in urls.items():
        try:
            df = pd.read_csv(url)
            df['source'] = source
            dfs.append(df)
        except Exception as e:
            st.toast(f"⚠️ Source {source} indisponible.")
            continue
            
    if not dfs:
        return pd.DataFrame() # Retourne un dataframe vide si tout échoue
        
    data = pd.concat(dfs, ignore_index=True)
    
    # Filtrer géographiquement pour la France
    data = data[
        (data['latitude'] >= FRANCE_BBOX["lat_min"]) & (data['latitude'] <= FRANCE_BBOX["lat_max"]) &
        (data['longitude'] >= FRANCE_BBOX["lon_min"]) & (data['longitude'] <= FRANCE_BBOX["lon_max"])
    ]
    
    # Création d'un vrai objet datetime pour le filtrage (acq_date: YYYY-MM-DD, acq_time: HHMM)
    data['acq_time'] = data['acq_time'].astype(str).str.zfill(4)
    data['datetime'] = pd.to_datetime(data['acq_date'] + ' ' + data['acq_time'], format='%Y-%m-%d %H%M')
    
    # Uniformisation de la confiance (MODIS: 0-100, VIIRS: l, n, h)
    def normalize_confidence(row):
        val = row['confidence']
        if pd.isna(val): return 50
        if isinstance(val, str):
            if val == 'l': return 33
            if val == 'n': return 66
            if val == 'h': return 100
        return int(val)
        
    data['confidence_pct'] = data.apply(normalize_confidence, axis=1)
    
    return data

# ==========================================
# GESTION DES DONNÉES : ZONES MENACÉES (MÉTÉO)
# ==========================================
@st.cache_data(ttl=3600) # Cache de 1 heure
def fetch_threat_zones():
    """
    Génère une grille de points sur la France, interroge Open-Meteo et calcule un indice de risque.
    """
    # Création d'une grille de points représentative (~30 points)
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
        
        # Open-Meteo renvoie une liste de réponses quand il y a plusieurs coordonnées
        for i, loc_weather in enumerate(weather_data):
            current = loc_weather.get("current", {})
            temp = current.get("temperature_2m", 15)
            hum = current.get("relative_humidity_2m", 50)
            wind = current.get("wind_speed_10m", 10)
            
            # Formule maison d'Indice de Risque (0 à 100)
            # Températures hautes, humidité basse et vent fort = risque max
            r_temp = max(0, min((temp - 15) / 25, 1)) # Échelle de 15°C à 40°C
            r_hum = max(0, min((80 - hum) / 60, 1))   # Échelle de 80% à 20%
            r_wind = max(0, min(wind / 50, 1))        # Échelle de 0 à 50 km/h
            
            # Poids : Vent 40%, Température 40%, Humidité 20%
            risk_score = (r_temp * 0.4 + r_wind * 0.4 + r_hum * 0.2) * 100
            
            if risk_score > 20: # Ne garder que les zones avec un risque minimum
                risk_data.append([points[i][0], points[i][1], risk_score / 100])
                
    except Exception as e:
        st.toast("⚠️ Impossible de charger la météo pour les zones menacées.")
        
    return risk_data

# ==========================================
# INTERFACE UTILISATEUR & LOGIQUE
# ==========================================
def main():
    st.title("🔥 Suivi des Incendies en France (Temps Réel)")
    
    # -- Barre latérale --
    st.sidebar.header("⚙️ Paramètres")
    
    # Auto-refresh
    auto_refresh = st.sidebar.checkbox("Actualisation automatique (5 min)", value=False)
    if auto_refresh:
        st_autorefresh(interval=5 * 60 * 1000, key="datarefresh")
    
    # Filtres de temps
    time_filter = st.sidebar.selectbox(
        "Historique des données",
        ["Dernières 24h", "Dernières 48h", "Derniers 7 jours"]
    )
    
    # Chargement des données
    with st.spinner("Récupération des données spatiales..."):
        df_fires = fetch_firms_data()
        threat_grid = fetch_threat_zones()
    
    if df_fires.empty:
        st.error("Aucune donnée d'incendie n'est disponible actuellement via les API.")
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
    
    # Filtres complémentaires
    st.sidebar.markdown("---")
    min_confidence = st.sidebar.slider("Confiance minimum (%)", 0, 100, 50)
    sources_selected = st.sidebar.multiselect(
        "Sources Satellitaires",
        options=df_filtered['source'].unique(),
        default=df_filtered['source'].unique()
    )
    
    df_filtered = df_filtered[
        (df_filtered['confidence_pct'] >= min_confidence) &
        (df_filtered['source'].isin(sources_selected))
    ]
    
    # -- Indicateurs --
    col1, col2 = st.columns(2)
    col1.metric("🔥 Foyers détectés", len(df_filtered))
    col2.metric("⏱️ Dernière mise à jour des API", now.strftime("%H:%M UTC"))
    
    # -- Construction de la Carte Folium --
    m = folium.Map(location=[46.2276, 2.2137], zoom_start=6, tiles="CartoDB dark_matter")
    
    # Couche 1 : Les feux en cours (Cluster pour l'optimisation visuelle)
    fire_group = folium.FeatureGroup(name="Foyers d'incendie (Satellites)", show=True)
    marker_cluster = MarkerCluster().add_to(fire_group)
    
    for _, row in df_filtered.iterrows():
        # HTML personnalisé pour la Popup avec max d'infos
        popup_html = f"""
        <div style="font-family: Arial; min-width: 200px;">
            <h4 style="color: #ff4b4b; margin-top: 0;">Foyer détecté</h4>
            <b>Date/Heure (UTC) :</b> {row['datetime'].strftime('%d/%m/%Y %H:%M')}<br>
            <b>Confiance :</b> {row['confidence_pct']}%<br>
            <b>Intensité (FRP) :</b> {row.get('frp', 'N/A')} MW<br>
            <b>Satellite/Capteur :</b> {row['source']}<br>
            <b>Position :</b> {row['latitude']}, {row['longitude']}
        </div>
        """
        
        # Couleur en fonction de la confiance
        color = "red" if row['confidence_pct'] > 80 else "orange" if row['confidence_pct'] > 50 else "yellow"
        
        folium.CircleMarker(
            location=[row['latitude'], row['longitude']],
            radius=6,
            color=color,
            fill=True,
            fill_opacity=0.7,
            tooltip=f"Confiance: {row['confidence_pct']}%",
            popup=folium.Popup(popup_html, max_width=300)
        ).add_to(marker_cluster)
        
    fire_group.add_to(m)
    
    # Couche 2 : Zones forestières menacées (HeatMap Météo)
    if threat_grid:
        threat_group = folium.FeatureGroup(name="Zones menacées (Risque météo)", show=False)
        HeatMap(
            threat_grid, 
            radius=40, 
            blur=25, 
            gradient={0.2: 'blue', 0.4: 'lime', 0.6: 'yellow', 0.8: 'orange', 1.0: 'red'}
        ).add_to(threat_group)
        threat_group.add_to(m)
        
    # Contrôle des couches
    folium.LayerControl(collapsed=False).add_to(m)
    
    # Affichage pleine largeur dans Streamlit
    st_folium(m, width="100%", height=700, returned_objects=[])
    
    st.caption("Données satellitaires fournies par NASA FIRMS. Analyse des risques de propagation propulsée par Open-Meteo. Les données peuvent avoir jusqu'à 3h de décalage par rapport au temps réel en fonction du passage des satellites en orbite polaire.")

if __name__ == "__main__":
    main()
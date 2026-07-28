# 🔥 Suivi des Incendies en France (Temps Quasi-Réel)

Une application web interactive développée en Python avec Streamlit et Folium (Leaflet). Elle permet de visualiser les incendies en cours en France métropolitaine grâce aux données satellitaires de la NASA, et d'estimer les zones forestières menacées via des données météorologiques.

## ✨ Fonctionnalités

*   🗺️ **Carte interactive plein écran** : Visualisation des foyers d'incendie avec regroupement (clustering) pour une navigation fluide.
*   🛰️ **Données de pointe** : Fusion en temps quasi-réel des données issues des capteurs satellites de la NASA (MODIS, VIIRS Suomi-NPP, VIIRS NOAA-20 et VIIRS NOAA-21).
*   ⚠️ **Couche "Zones Menacées"** : Affichage d'une carte de chaleur (HeatMap) calculant un indice de risque de propagation basé sur la météo en temps réel (vent, température, humidité).
*   🎛️ **Filtres dynamiques** : Filtrage par historique (24h, 48h, 7 jours), par niveau de confiance (0-100%) et par source satellitaire.
*   🔄 **Actualisation automatique** : Option de rafraîchissement des données toutes les 5 minutes pour un suivi en direct.
*   🛡️ **Résilience** : L'application gère les erreurs d'API et continue de fonctionner même si une source de données est temporairement indisponible.
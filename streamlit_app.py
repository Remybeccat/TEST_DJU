import streamlit as st
from meteostat import Stations, Daily, Hourly
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderUnavailable, GeocoderTimedOut
import pandas as pd
import matplotlib.pyplot as plt
import math
import datetime
import timedelta
import altair as alt
from opencage.geocoder import OpenCageGeocode


st.write("L'application a démarré")  # Vérification initiale

# Fonction pour obtenir la latitude et la longitude à partir de l'adresse
#def get_coordinates_old(address):
#    geolocator = Nominatim(user_agent="weather_app", timeout=10)  # Augmenter le délai d'attente
#    try:
#        location = geolocator.geocode(address)
#        if location:
#            return location.latitude, location.longitude
#        else:
#            return None, None
#    except (GeocoderUnavailable, GeocoderTimedOut) as e:
#        st.error(f"Erreur de géolocalisation : {str(e)}. Veuillez réessayer plus tard.")
#        return None, None

def get_coordinates(address):
    key = "b9d04993bd4e471ab7a210c42585b523"
    geocoder = OpenCageGeocode(key)
    try:
        results = geocoder.geocode(address)
        if results and len(results):
            return results[0]['geometry']['lat'], results[0]['geometry']['lng']
        else:
            return None, None
    except Exception as e:
        st.error(f"Erreur OpenCage : {str(e)}")
        return None, None


# Fonction pour calculer la distance entre deux points géographiques (en km)
def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    a = math.sin(dlat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

# Fonction pour obtenir les stations météo proches
def get_nearby_stations(latitude, longitude):
    stations = Stations()
    nearby_stations = stations.nearby(latitude, longitude).fetch(5)
    return nearby_stations

# Fonction pour récupérer les données météorologiques journalières d'une station donnée via son id
def get_weather_data(station_id, start, end):
    data = Daily(station_id, start, end)
    data = data.fetch()
    return data

# Fonction pour récupérer les données météorologiques horaires d'une station donnée via son id
def get_weather_data_hourly(station_id, start, end):
    data = Hourly(station_id, start, end)
    data = data.fetch()
    return data

# Fonction pour calculer les DJU (Degré Jour Unifié)
def calculate_dju_meteo(data, reference_temp):
    dju = data.apply(lambda row: max(0, reference_temp - (row['tmin']+row['tmax'])/2) if pd.notnull(row['tmin']) and pd.notnull(row['tmax']) else 0, axis=1)
    return dju.sum()

def calculate_dju_costic(data, reference_temp):
    def costic_dju(row, reference_temp):
        t_min = row['tmin']
        t_max = row['tmax']
        t_moy = row['tavg']
        
        #si tmax ou tmin est vide, on retourne DJU = 0
        if pd.isnull(row['tmax']) or pd.isnull(row['tmin']) :
            return 0
        # Si la référence est supérieure à la température maximale, on retourne DJU = 0
        elif reference_temp > t_max :
            return reference_temp - (t_max + t_min)/2
        # Si la référence est inférieure à la température minimale, on retourne la différence
        elif reference_temp < t_min :
            return 0
        # Si la référence est entre t_min et t_max, on utilise la formule Costic
        else :
            return (reference_temp - t_min) * (0.08 + 0.42 * (reference_temp - t_min) / (t_max - t_min))
    
    # Applique la fonction costic_dju à chaque ligne des données
    dju = data.apply(lambda row: costic_dju(row, reference_temp), axis=1)
    
    return dju.sum()

st.title('Analyse Météo avec Meteostat et Streamlit')

# Demander à l'utilisateur de saisir une adresse
address = st.text_input("Entrez une adresse ou une ville (ex. Paris, France):")

if address:
    st.write("Adresse saisie : ", address)  # Vérification de l'entrée utilisateur

    # Obtenir la latitude et la longitude à partir de l'adresse
    lat, lon = get_coordinates(address)
    
    if lat is not None and lon is not None:
        st.write(f"Adresse trouvée : Latitude = {lat}, Longitude = {lon}")

        # Obtenir les stations météo les plus proches
        nearby_stations = get_nearby_stations(lat, lon)

        if not nearby_stations.empty:
            st.write("Stations météo trouvées :")

            # Calculer la distance entre l'adresse et chaque station
            if 'latitude' in nearby_stations.columns and 'longitude' in nearby_stations.columns:
                nearby_stations['distance en km'] = nearby_stations.apply(
                    lambda row: round(haversine(lat, lon, row['latitude'], row['longitude'])), axis=1
                )
    
            # Vérifier si les colonnes 'name' et 'distance en km' existent avant de les afficher
            if 'name' in nearby_stations.columns and 'distance en km' and 'elevation' in nearby_stations.columns:
                st.dataframe(nearby_stations[['name', 'distance en km', 'elevation']].rename(columns={'elevation': 'Altitude (m)'}))
            else:
                st.write("Les colonnes 'name' et/ou 'distance en km' et/ou 'elevation' sont manquantes.")
            
            # Sélectionner une station par nom
            selected_station_name = st.selectbox("Sélectionnez une station :", nearby_stations['name'])
            
            # Sélectionner l'année
            year_max = datetime.date.today()
            year_max = year_max.year
            year = st.number_input("Sélectionnez une année :", min_value=2000, max_value=year_max, value=year_max - 1)

            # Récupérer l'ID de la station sélectionnée
            selected_station_id = nearby_stations.loc[nearby_stations['name'] == selected_station_name].index[0]

            # Récupérer les données météorologiques
            #start_date = f"{year}-01-01"
            #end_date = f"{year}-12-31"
            start_date = datetime.datetime(year, 1, 1)
            end_date = datetime.datetime(year, 12, 31)
            end_date_hour = datetime.datetime(year, 12, 31, 23, 59)

            # donées journalières
            data = get_weather_data(selected_station_id, start_date, end_date)
            if not data.empty:
                st.write(f"Données météo pour {selected_station_name} en {year}")
                st.dataframe(data)

                # Demander à l'utilisateur de saisir une température de référence pour calculer les DJU
                reference_temp = st.number_input("Entrez la température de référence pour calculer les DJU :", min_value=-30.0, max_value=50.0, value=18.0)

                # Calculer les DJU
                required_cols = ['tmin', 'tavg', 'tmax']
                if all(col in data.columns for col in required_cols):
                    # calculs DJU et graphiques
                    dju_meteo = calculate_dju_meteo(data, reference_temp)
                    dju_costic = calculate_dju_costic(data, reference_temp)
                    st.write(f"Le total des DJU méthode météo pour l'année {year} est : {dju_meteo:.2f}")
                    st.write(f"Le total des DJU méthode COSTIC pour l'année {year} est : {dju_costic:.2f}")
    
                    # Créer un graphique des températures min, moy et max
                    plt.figure(figsize=(10, 6))
                    plt.plot(data.index, data['tmin'], color='blue', label='Température Min (°C)')
                    plt.plot(data.index, data['tavg'], color='black', label='Température Moy (°C)')
                    plt.plot(data.index, data['tmax'], color='red', label='Température Max (°C)')
                    plt.fill_between(data.index, data['tmin'], data['tmax'], color='gray', alpha=0.1)
                    plt.title(f'Températures Min, Moy et Max pour {selected_station_name} en {year}')
                    plt.xlabel('Date')
                    plt.ylabel('Température (°C)')
                    plt.legend()
    
                    # Afficher le graphique
                    st.pyplot(plt)        
                else:
                        st.warning("Les données météo sont incomplètes pour les calculs.")
            else:
                st.write(f"Aucune donnée disponible pour la station '{selected_station_name}' en {year}.")


            # données horaires
            with st.spinner("Chargement des données horaires..."):
                data_hour = get_weather_data_hourly(selected_station_id, start_date, end_date_hour)   
            
            if not data_hour.empty:
                st.write(f"Données météo horaires pour {selected_station_name} en {year}")
                st.dataframe(data_hour)
                # Créer un graphique des températures min, moy et max
                plt.figure(figsize=(10, 6))
                plt.plot(data_hour.index, data_hour['temp'], color='blue', label='Température (°C)')
                plt.title(f'Températures horaires pour {selected_station_name} en {year}')
                plt.xlabel('Date')
                plt.ylabel('Température (°C)')
                plt.legend()
                # Afficher le graphique
                st.pyplot(plt)
            else:
                st.write(f"Aucune donnée disponible pour la station '{selected_station_name}' en {year}.")    
        else:
            st.write("Aucune station météo trouvée à proximité.")
    else:
        st.write("Adresse non valide ou introuvable.")

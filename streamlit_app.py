import streamlit as st
import meteostat as ms
import pandas as pd
import matplotlib.pyplot as plt
import math
import datetime
from opencage.geocoder import OpenCageGeocode
import traceback
import os
import sqlite3
import urllib.request
import requests
st.write("L'application a démarré")  # Vérification initiale
import http.client
# -----------------------------
# Géocodage (OpenCage)
# -----------------------------
def get_coordinates(address: str):
    key = "b9d04993bd4e471ab7a210c42585b523"
    geocoder = OpenCageGeocode(key)
    try:
        results = geocoder.geocode(address)
        if results and len(results):
            return results[0]["geometry"]["lat"], results[0]["geometry"]["lng"]
        return None, None
    except Exception as e:
        st.error(f"Erreur OpenCage : {str(e)}")
        return None, None
# -----------------------------
# Distance entre deux points (km)
# -----------------------------
def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c
# -----------------------------
# OPTION 1 : Stations sans meteostat.stations.nearby()
# - Télécharge stations.db (officiel)
# - Filtre en SQL via bounding box (sans acos/cos/sin)
# - Calcule distances en Python
# -----------------------------

@st.cache_data(show_spinner=False)  
def get_nearby_stations(latitude, longitude):
    POINT = ms.Point(latitude, longitude) 
    stations = ms.stations.nearby(POINT, radius = 300000, limit = 10)
    stations["distance_km"] = round(stations["distance"] / 1000,2)
    return stations
    
# -----------------------------
# Meteostat : séries temporelles
# -----------------------------
def get_weather_data(station_id, start, end):
    df = ms.daily(station_id, start, end).fetch()
    if df is None:
        return pd.DataFrame()
    return df
    
def get_weather_data_hourly(station_id, start, end):
    df = ms.hourly(station_id, start, end).fetch()
    if df is None:
        return pd.DataFrame()
    return df

# -----------------------------
# DJU
# -----------------------------
def calculate_dju_meteo(data, reference_temp):
    dju = data.apply(
        lambda row: max(0, reference_temp - (row["tmin"] + row["tmax"]) / 2)
        if pd.notnull(row["tmin"]) and pd.notnull(row["tmax"])
        else 0,
        axis=1,
    )
    return dju.sum()
def calculate_dju_costic(data, reference_temp):
    def costic_dju(row, reference_temp):
        t_min = row["tmin"]
        t_max = row["tmax"]
        if pd.isnull(t_max) or pd.isnull(t_min):
            return 0
        elif reference_temp > t_max:
            return reference_temp - (t_max + t_min) / 2
        elif reference_temp < t_min:
            return 0
        else:
            return (reference_temp - t_min) * (0.08 + 0.42 * (reference_temp - t_min) / (t_max - t_min))
    dju = data.apply(lambda row: costic_dju(row, reference_temp), axis=1)
    return dju.sum()
# -----------------------------
# UI Streamlit
# -----------------------------
st.title("Analyse Météo avec Meteostat et Streamlit")
address = st.text_input("Entrez une adresse ou une ville (ex. Paris, France):")
if address:
    st.write("Adresse saisie : ", address)
    lat, lon = get_coordinates(address)
    if lat is None or lon is None:
        st.write("Adresse non valide ou introuvable.")
    else:
        st.write(f"Adresse trouvée : Latitude = {lat}, Longitude = {lon}")
        try:
            with st.spinner("Recherche de stations météo proches..."):
                nearby_stations = get_nearby_stations(lat, lon)
        except Exception as e:
            st.error(str(e))
            st.text(traceback.format_exc())
            raise
        if nearby_stations.empty:
            st.write("Aucune station météo trouvée à proximité.")
        else:
            st.write("Stations météo trouvées :")
            if (
                "name" in nearby_stations.columns
                and "distance_km" in nearby_stations.columns
                and "elevation" in nearby_stations.columns
            ):
                st.dataframe(
                    nearby_stations[["name", "distance_km", "elevation"]]
                    .rename(columns={
                        "distance_km": "Distance (km)",
                        "elevation": "Altitude (m)"
                    })
                )
                
            else:
                st.dataframe(nearby_stations)
            
            # Sélection station 
            selected_station_name = st.selectbox("Sélectionnez une station :", nearby_stations["name"].tolist()) 
            #selected_station_id = nearby_stations.loc[nearby_stations["name"] == selected_station_name, "id"].iloc[0]
            selected_station_id = nearby_stations.loc[nearby_stations["name"] == selected_station_name]
            st.write(selected_station_id)
            st.write(selected_station_id["id"])
            
            year_max = datetime.date.today().year
            start_date_FR = st.date_input(
                "Selectionner la date de début",
                datetime.datetime(year_max - 1, 1, 1),
                max_value=datetime.date.today(),
                format="DD/MM/YYYY",
            )
            end_date_FR = st.date_input(
                "Selectionner la date de fin",
                datetime.datetime(year_max, 1, 1),
                max_value=datetime.date.today(),
                format="DD/MM/YYYY",
            )
            start_date = datetime.datetime(start_date_FR.year, start_date_FR.month, start_date_FR.day)
            end_date = datetime.datetime(end_date_FR.year, end_date_FR.month, end_date_FR.day)
            end_date_hour = datetime.datetime(end_date_FR.year, end_date_FR.month, end_date_FR.day, 23, 59)
            
            # Données journalières
            with st.spinner("Chargement des données journalières..."):
                data = get_weather_data(selected_station_id, start_date, end_date)
                
            if not data.empty:
                st.write(
                    f"Données météos journalières pour la station {selected_station_name} du {start_date_FR} au {end_date_FR}"
                )
                st.dataframe(data)
                reference_temp = st.number_input(
                    "Entrez la température de référence pour calculer les DJU :",
                    min_value=-30.0,
                    max_value=50.0,
                    value=18.0,
                )
                required_cols = ["tmin", "temp", "tmax"]
                if all(col in data.columns for col in required_cols):
                    dju_meteo = calculate_dju_meteo(data, reference_temp)
                    dju_costic = calculate_dju_costic(data, reference_temp)
                    st.write(
                        f"Le total des DJU méthode météo pour la période du {start_date_FR} au {end_date_FR} est : {dju_meteo:.2f}"
                    )
                    st.write(
                        f"Le total des DJU méthode COSTIC pour la période du {start_date_FR} au {end_date_FR} est : {dju_costic:.2f}"
                    )
                   
                    plt.figure(figsize=(10, 6))
                    plt.plot(data.index, data["tmin"], label="Température Min (°C)")
                    plt.plot(data.index, data["temp"], label="Température Moy (°C)")
                    plt.plot(data.index, data["tmax"], label="Température Max (°C)")
                    plt.fill_between(data.index, data["tmin"], data["tmax"], alpha=0.1)
                    plt.title(
                        f"Températures Min, Moy et Max pour {selected_station_name} du {start_date_FR} au {end_date_FR}"
                    )
                    plt.xlabel("Date")
                    plt.ylabel("Température (°C)")
                    plt.legend()
                    st.pyplot(plt)
                
                else:
                    st.warning("Les données météo sont incomplètes pour les calculs.")
            else:
                st.write(
                    f"Aucune donnée disponible pour la station '{selected_station_name}' du {start_date_FR} au {end_date_FR}."
                )
            # Données horaires
            with st.spinner("Chargement des données horaires..."):
                data_hour = get_weather_data_hourly(selected_station_id, start_date, end_date_hour)
            if not data_hour.empty:
                st.write(
                    f"Données météos horaires pour la station {selected_station_name} du {start_date_FR} au {end_date_FR}"
                )
                st.dataframe(data_hour)
                plt.figure(figsize=(10, 6))
                if "temp" in data_hour.columns:
                    plt.plot(data_hour.index, data_hour["temp"], label="Température (°C)")
                else:
                    st.warning("Colonne 'temp' absente des données horaires.")
                plt.title(f"Températures horaires pour {selected_station_name} du {start_date_FR} au {end_date_FR}")
                plt.xlabel("Date")
                plt.ylabel("Température (°C)")
                plt.legend()
                st.pyplot(plt)
            else:
                st.write(
                    f"Aucune donnée disponible pour la station '{selected_station_name}' du {start_date_FR} au {end_date_FR}."
                )

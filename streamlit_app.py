import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import math
import datetime
from meteostat import Point, Stations, Daily, Hourly
from opencage.geocoder import OpenCageGeocode
import traceback

# ============================================================
# CONFIG
# ============================================================
st.set_page_config(page_title="Analyse météo DJU", layout="wide")

# ============================================================
# GÉOCODAGE
# ============================================================
def get_coordinates(address):
    geocoder = OpenCageGeocode(st.secrets["OPENCAGE_KEY"])
    try:
        results = geocoder.geocode(address)
        if results:
            return results[0]["geometry"]["lat"], results[0]["geometry"]["lng"]
    except Exception as e:
        st.error(e)
    return None, None


# ============================================================
# DJU
# ============================================================
def calculate_dju_meteo(df, ref):
    dju = []
    for _, row in df.iterrows():
        if pd.notna(row["tmin"]) and pd.notna(row["tmax"]):
            dju.append(max(0, ref - (row["tmin"] + row["tmax"]) / 2))
    return sum(dju)


def calculate_dju_costic(df, ref):
    dju = []
    for _, row in df.iterrows():
        if pd.isna(row["tmin"]) or pd.isna(row["tmax"]):
            continue
        tmin, tmax = row["tmin"], row["tmax"]
        if ref > tmax:
            dju.append(ref - (tmax + tmin) / 2)
        elif ref < tmin:
            dju.append(0)
        else:
            dju.append((ref - tmin) * (0.08 + 0.42 * (ref - tmin) / (tmax - tmin)))
    return sum(dju)


# ============================================================
# UI
# ============================================================
st.title("Analyse Météo DJU (Meteostat Python)")

address = st.text_input("Adresse ou ville")

if address:
    lat, lon = get_coordinates(address)

    if lat is None:
        st.warning("Adresse introuvable")
        st.stop()

    st.success(f"Coordonnées : {lat:.4f}, {lon:.4f}")

    point = Point(lat, lon)

    # ========================================================
    # STATIONS (CORRECTION PRINCIPALE)
    # ========================================================
    with st.spinner("Recherche de stations météo proches..."):
        stations = Stations().nearby(point, radius=300000).fetch(10)

    if stations.empty:
        st.warning("Aucune station trouvée")
        st.stop()

    # Création d’un label unique
    stations["label"] = stations["name"] + " (" + stations.index + ")"

    selected_label = st.selectbox(
        "Choisir une station météo",
        stations["label"].tolist()
    )

    station_id = stations.loc[
        stations["label"] == selected_label
    ].index[0]

    st.info(f"Station sélectionnée : {station_id}")

    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("Date début", datetime.date(2023, 1, 1))
    with col2:
        end_date = st.date_input("Date fin", datetime.date.today())

    start_dt = datetime.datetime.combine(start_date, datetime.time.min)
    end_dt = datetime.datetime.combine(end_date, datetime.time.max)

    # ========================================================
    # DONNÉES JOURNALIÈRES
    # ========================================================
    st.subheader("Données journalières")

    daily = Daily(station_id, start_dt, end_dt).fetch()

    if daily.empty:
        st.warning("Pas de données journalières")
    else:
        st.dataframe(daily)

        ref = st.number_input("Température de référence (°C)", value=18.0)

        if {"tmin", "tmax"}.issubset(daily.columns):
            dju_m = calculate_dju_meteo(daily, ref)
            dju_c = calculate_dju_costic(daily, ref)

            st.success(f"DJU météo : {dju_m:.2f}")
            st.success(f"DJU COSTIC : {dju_c:.2f}")

        fig, ax = plt.subplots()
        ax.plot(daily.index, daily["tmin"], label="Tmin")
        ax.plot(daily.index, daily["tmax"], label="Tmax")
        ax.legend()
        st.pyplot(fig)

    # ========================================================
    # DONNÉES HORAIRES
    # ========================================================
    st.subheader("Données horaires")

    hourly = Hourly(station_id, start_dt, end_dt).fetch()

    if not hourly.empty and "temp" in hourly.columns:
        fig, ax = plt.subplots()
        ax.plot(hourly.index, hourly["temp"])
        ax.set_ylabel("Température °C")
        st.pyplot(fig)
    else:
        st.warning("Pas de données horaires")

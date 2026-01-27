import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import math
import datetime
import requests
from opencage.geocoder import OpenCageGeocode


# =============================
# CONFIG
# =============================
OPENCAGE_KEY = "b9d04993bd4e471ab7a210c42585b523"
API_BASE = "https://meteostat.p.rapidapi.com/"

HEADERS = {
	"x-rapidapi-key": "6c535c0d33msh028047f4f04ffacp1faba2jsna3e3b8329813",
	"x-rapidapi-host": "meteostat.p.rapidapi.com"
}

# =============================
# GEO
# =============================
def get_coordinates(address):
    geocoder = OpenCageGeocode(OPENCAGE_KEY)
    results = geocoder.geocode(address)
    if results:
        g = results[0]["geometry"]
        return g["lat"], g["lng"]
    return None, None


def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dl/2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1-a))


# =============================
# API METEOSTAT
# =============================
@st.cache_data(ttl=86400)
def api_get(url, params):
    r = requests.get(url, headers=HEADERS, params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("data", [])


@st.cache_data(ttl=86400)
def get_nearby_stations_api(lat, lon, radius=300, limit=10):
    url = f"{API_BASE}/stations/nearby"
    data = api_get(url, {
        "lat": lat,
        "lon": lon,
        "radius": radius,
        "limit": limit
    })
    return pd.DataFrame(data)


@st.cache_data(ttl=86400)
def get_daily_api(station, start, end):
    url = f"{API_BASE}/stations/daily"
    data = api_get(url, {
        "station": station,
        "start": start.strftime("%Y-%m-%d"),
        "end": end.strftime("%Y-%m-%d")
    })
    return pd.DataFrame(data)


@st.cache_data(ttl=86400)
def get_hourly_api(station, start, end):
    url = f"{API_BASE}/stations/hourly"
    data = api_get(url, {
        "station": station,
        "start": start.strftime("%Y-%m-%d"),
        "end": end.strftime("%Y-%m-%d")
    })
    return pd.DataFrame(data)


# =============================
# DJU
# =============================
def calculate_dju_meteo(df, ref):
    return (
        (ref - (df["tmin"] + df["tmax"]) / 2)
        .clip(lower=0)
        .sum()
    )


def calculate_dju_costic(df, ref):
    def f(row):
        tmin, tmax = row["tmin"], row["tmax"]
        if pd.isnull(tmin) or pd.isnull(tmax):
            return 0
        if ref > tmax:
            return ref - (tmin + tmax) / 2
        if ref < tmin:
            return 0
        return (ref - tmin) * (0.08 + 0.42 * (ref - tmin) / (tmax - tmin))
    return df.apply(f, axis=1).sum()


# =============================
# UI
# =============================
st.title("Analyse météo (API Meteostat)")

address = st.text_input("Ville ou adresse")

if address:
    lat, lon = get_coordinates(address)
    st.success(f"📍 {lat:.4f}, {lon:.4f}")

    stations = get_nearby_stations_api(lat, lon)
    st.success(f"stations")
	
    if stations.empty:
        st.warning("Aucune station trouvée")
        st.stop()
	
    st.dataframe(stations[["id", "name", "distance"]])

    station_id = st.selectbox("Station", stations["id"])
    station_name = stations.loc[stations["id"] == station_id, "name"].values[0]

    year = datetime.date.today().year

    start = st.date_input("Début", datetime.date(year-1, 1, 1))
    end = st.date_input("Fin", datetime.date(year, 1, 1))

    start_dt = datetime.datetime.combine(start, datetime.time.min)
    end_dt = datetime.datetime.combine(end, datetime.time.max)

    # DAILY
    df = get_daily_api(station_id, start_dt, end_dt)

    if not df.empty:
        df["time"] = pd.to_datetime(df["time"])
        df = df.set_index("time")

        st.subheader("Données journalières")
        st.dataframe(df)

        ref = st.number_input("Température de référence", 0.0, 30.0, 18.0)

        st.write("DJU météo :", round(calculate_dju_meteo(df, ref), 1))
        st.write("DJU COSTIC :", round(calculate_dju_costic(df, ref), 1))

        plt.figure(figsize=(10,5))
        plt.plot(df.index, df["tmin"], label="Tmin")
        plt.plot(df.index, df["tavg"], label="Tavg")
        plt.plot(df.index, df["tmax"], label="Tmax")
        plt.legend()
        st.pyplot(plt)

    # HOURLY
    dfh = get_hourly_api(station_id, start_dt, end_dt)

    if not dfh.empty:
        dfh["time"] = pd.to_datetime(dfh["time"])
        dfh = dfh.set_index("time")

        st.subheader("Données horaires")
        st.dataframe(dfh.head(500))

        plt.figure(figsize=(10,5))
        plt.plot(dfh.index, dfh["temp"])
        st.pyplot(plt)

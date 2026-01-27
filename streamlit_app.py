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
	#r.raise_for_status()
	return r.json().get("data", [])


@st.cache_data(ttl=86400)
def get_nearby_stations_api(lat, lon, radius=300, limit=10):
	url = f"{API_BASE}/stations/nearby"
	data = api_get(url, {
		"lat": lat,
		"lon": lon,
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
# Normalisation du temps
# =============================

def normalize_time_column(df):
	if "time" in df.columns:
		df["time"] = pd.to_datetime(df["time"])
		df = df.set_index("time")
	elif "date" in df.columns:
		df["date"] = pd.to_datetime(df["date"])
		df = df.set_index("date")
	return df

# =============================
# UI
# =============================
st.title("Analyse météo (API Meteostat)")

address = st.text_input("Ville ou adresse")

if address:
	lat, lon = get_coordinates(address)
	st.success(f"📍 {lat:.4f}, {lon:.4f}")
	
	stations = get_nearby_stations_api(lat, lon)
	
	if stations.empty:
		st.warning("Aucune station trouvée")
		st.stop()
	# conversion de la distance en km, renommage des stations et tri selon la distance
	stations["distance_km"] = (stations["distance"] / 1000).round(1)
	stations["name"] = stations["name"].apply(
		lambda x: x.get("en") if isinstance(x, dict) else x
	)
	stations = stations.sort_values("distance_km")
	
	st.dataframe(
		stations[["name", "distance_km"]]
		.rename(columns={"name":"nom des stations","distance_km": "Distance (km)"})
	)

	# Sélection par nom
	station_name = st.selectbox("Sélectionnez une station",stations["name"].tolist())
	station_id = stations.loc[stations["name"] == station_name, "id"].iloc[0]
	
	# Dates
	today = datetime.date.today()
	start_date = st.date_input("Date de début", datetime.date(today.year-1, 1, 1), max_value=today)
	end_date = st.date_input("Date de fin", datetime.date(today.year, 1, 1), max_value=today)

	start_dt = datetime.datetime.combine(start_date, datetime.time.min)
	end_dt = datetime.datetime.combine(end_date, datetime.time.max)
	
	# DAILY
	df = get_daily_api(station_id, start_dt, end_dt)
	if df.empty:
		st.warning("Aucune donnée journalière disponible pour cette période.")
	else:
		df = normalize_time_column(df)
		st.subheader("Données journalières")
		st.dataframe(df)

		ref_temp = st.number_input("Température de référence pour DJU", -30.0, 50.0, 18.0)

		st.write("DJU méthode météo :", round(calculate_dju_meteo(df, ref_temp),1))
		st.write("DJU méthode COSTIC :", round(calculate_dju_costic(df, ref_temp),1))

		# Graphique
		plt.figure(figsize=(10,5))
		if all(c in df.columns for c in ["tmin","tavg","tmax"]):
			plt.plot(df.index, df["tmin"], label="Tmin")
			plt.plot(df.index, df["tavg"], label="Tavg")
			plt.plot(df.index, df["tmax"], label="Tmax")
			plt.fill_between(df.index, df["tmin"], df["tmax"], alpha=0.1)
			plt.legend()
			plt.title(f"Températures journalières pour {station_name}")
			plt.xlabel("Date")
			plt.ylabel("°C")
			st.pyplot(plt)

	# --- HOURLY ---
	dfh = get_hourly_api(station_id, start_dt, end_dt)
	if dfh.empty:
		st.warning("Aucune donnée horaire disponible pour cette période.")
	else:
		dfh = normalize_time_column(dfh)
		st.subheader("Données horaires")
		st.dataframe(dfh.head(500))

		if "temp" in dfh.columns:
			plt.figure(figsize=(10,5))
			plt.plot(dfh.index, dfh["temp"])
			plt.title(f"Températures horaires pour {station_name}")
			plt.xlabel("Date")
			plt.ylabel("°C")
			st.pyplot(plt)

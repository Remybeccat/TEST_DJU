import datetime
import math
import os
import traceback

import matplotlib.pyplot as plt
import meteostat as ms
import pandas as pd
import streamlit as st
from opencage.geocoder import OpenCageGeocode


# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------
st.set_page_config(
    page_title="Analyse météo et DJU"
)


# ---------------------------------------------------------
# Géocodage OpenCage
# ---------------------------------------------------------
def get_opencage_api_key():
    # Clé enregistrée dans les secrets Streamlit
    try:
        key = st.secrets["OPENCAGE_API_KEY"]
        if key:
            return key
    except (KeyError, FileNotFoundError):
        pass

    # Alternative : variable d'environnement
    key = os.getenv("OPENCAGE_API_KEY")
    if key:
        return key

    return None



@st.cache_data(show_spinner=False)
def get_coordinates(address: str):
    api_key = get_opencage_api_key()

    if not api_key:
        return None, None, (
            "Clé OpenCage absente. Ajoutez OPENCAGE_API_KEY dans les variables "
            "d'environnement ou dans .streamlit/secrets.toml."
        )

    geocoder = OpenCageGeocode(api_key)

    try:
        results = geocoder.geocode(address, no_annotations=1, limit=1)
        if results:
            return (
                results[0]["geometry"]["lat"],
                results[0]["geometry"]["lng"],
                None,
            )
        return None, None, "Adresse non valide ou introuvable."
    except Exception as exc:
        return None, None, f"Erreur OpenCage : {exc}"


# ---------------------------------------------------------
# Meteostat : stations et séries temporelles
# ---------------------------------------------------------
@st.cache_data(show_spinner=False)
def get_nearby_stations(latitude: float, longitude: float):
    point = ms.Point(latitude, longitude)
    stations = ms.stations.nearby(
        point,
        radius=300_000,
        limit=10,
    ).copy()

    if stations.empty:
        return stations

    # Selon la version de Meteostat, l'identifiant peut être dans l'index
    # ou dans une colonne "id".
    if "id" in stations.columns:
        stations["_station_id"] = stations["id"].astype(str)
    else:
        stations["_station_id"] = stations.index.astype(str)

    if "distance" in stations.columns:
        stations["distance_km"] = (
            pd.to_numeric(stations["distance"], errors="coerce") / 1000
        ).round(2)
    else:
        stations["distance_km"] = pd.NA

    return stations


@st.cache_data(show_spinner=False)
def get_weather_data(station_id: str, start, end):
    station = ms.Station(id=str(station_id))
    data = ms.daily(station, start, end).fetch()

    if data is None:
        return pd.DataFrame()

    return data


@st.cache_data(show_spinner=False)
def get_weather_data_hourly(station_id: str, start, end):
    station = ms.Station(id=str(station_id))
    data = ms.hourly(station, start, end).fetch()

    if data is None:
        return pd.DataFrame()

    return data


# ---------------------------------------------------------
# Préparation des données journalières
# ---------------------------------------------------------
def prepare_daily_data(data: pd.DataFrame, start_date, end_date):
    """
    Reconstitue un calendrier journalier complet afin que les jours absents
    soient visibles et ne soient pas assimilés à des DJU nuls.
    """
    prepared = data.copy()

    if not prepared.empty:
        prepared.index = pd.to_datetime(prepared.index)
        prepared = prepared[~prepared.index.duplicated(keep="first")]

    full_index = pd.date_range(
        start=pd.Timestamp(start_date),
        end=pd.Timestamp(end_date),
        freq="D",
    )

    prepared = prepared.reindex(full_index)
    prepared.index.name = "date"

    for column in ("tmin", "temp", "tmax"):
        if column not in prepared.columns:
            prepared[column] = pd.NA
        prepared[column] = pd.to_numeric(prepared[column], errors="coerce")

    # Uniquement pour l'affichage : si Meteostat ne fournit pas "temp",
    # on utilise la moyenne de Tmin et Tmax.
    prepared["temp"] = prepared["temp"].fillna(
        (prepared["tmin"] + prepared["tmax"]) / 2
    )

    return prepared


# ---------------------------------------------------------
# Calcul des DJU journaliers
# ---------------------------------------------------------
def calculate_dju_meteo_daily(
    data: pd.DataFrame,
    reference_temp: float,
) -> pd.Series:
    """
    Méthode météo :
        DJU = max(0 ; Tbase - (Tmin + Tmax) / 2)

    Une journée sans Tmin ou Tmax retourne NaN et non zéro.
    """
    valid = data["tmin"].notna() & data["tmax"].notna()
    mean_temperature = (data["tmin"] + data["tmax"]) / 2

    dju = (reference_temp - mean_temperature).clip(lower=0)
    return dju.where(valid)


def calculate_dju_costic_daily(
    data: pd.DataFrame,
    reference_temp: float,
) -> pd.Series:
    """Méthode COSTIC appliquée à chaque journée."""

    def costic_dju(row):
        t_min = row["tmin"]
        t_max = row["tmax"]

        if pd.isna(t_min) or pd.isna(t_max):
            return math.nan

        # Données incohérentes
        if t_max < t_min:
            return math.nan

        # Cas hiver
        if reference_temp >= t_max:
            return reference_temp - (t_max + t_min) / 2

        # Cas été
        if reference_temp <= t_min:
            return 0.0

        # Cas de mi-saison : t_min < Tbase < t_max
        return (reference_temp - t_min) * (
            0.08
            + 0.42
            * (reference_temp - t_min)
            / (t_max - t_min)
        )

    return data.apply(costic_dju, axis=1)


def add_daily_dju_columns(
    data: pd.DataFrame,
    reference_temp: float,
) -> pd.DataFrame:
    result = data.copy()
    result["dju_meteo"] = calculate_dju_meteo_daily(
        result,
        reference_temp,
    )
    result["dju_costic"] = calculate_dju_costic_daily(
        result,
        reference_temp,
    )
    return result


# ---------------------------------------------------------
# Tableau DJU journalier ou mensuel
# ---------------------------------------------------------
def build_dju_table(
    daily_data: pd.DataFrame,
    scale: str,
) -> pd.DataFrame:
    """
    Le calcul est toujours effectué jour par jour.

    - Échelle journalière : une ligne par jour.
    - Échelle mensuelle : somme des DJU journaliers par mois.
    """
    if scale == "Journalière":
        table = daily_data[
            ["tmin", "temp", "tmax", "dju_meteo", "dju_costic"]
        ].copy()

        table = table.rename(
            columns={
                "tmin": "Tmin (°C)",
                "temp": "Tmoy (°C)",
                "tmax": "Tmax (°C)",
                "dju_meteo": "DJU météo",
                "dju_costic": "DJU COSTIC",
            }
        )

        table.insert(
            0,
            "Date",
            table.index.strftime("%d/%m/%Y"),
        )

        return table.reset_index(drop=True)

    # Agrégation mensuelle des températures pour information
    monthly_temperatures = daily_data[
        ["tmin", "temp", "tmax"]
    ].resample("MS").mean()

    # Les DJU mensuels sont la somme des DJU journaliers disponibles
    monthly_dju = daily_data[
        ["dju_meteo", "dju_costic"]
    ].resample("MS").sum(min_count=1)

    calculated_days = (
        daily_data["dju_costic"]
        .resample("MS")
        .count()
        .rename("Jours calculés")
    )

    period_days = (
        daily_data["dju_costic"]
        .resample("MS")
        .size()
        .rename("Jours dans la période")
    )

    table = pd.concat(
        [
            monthly_temperatures,
            monthly_dju,
            calculated_days,
            period_days,
        ],
        axis=1,
    )

    table["Couverture (%)"] = (
        100 * table["Jours calculés"] / table["Jours dans la période"]
    )

    table = table.rename(
        columns={
            "tmin": "Tmin moyenne (°C)",
            "temp": "Tmoy moyenne (°C)",
            "tmax": "Tmax moyenne (°C)",
            "dju_meteo": "DJU météo",
            "dju_costic": "DJU COSTIC",
        }
    )

    table.insert(
        0,
        "Mois",
        table.index.strftime("%m/%Y"),
    )

    return table.reset_index(drop=True)


def format_dju_table_for_display(table: pd.DataFrame) -> pd.DataFrame:
    formatted = table.copy()

    columns_to_round = [
        "Tmin (°C)",
        "Tmoy (°C)",
        "Tmax (°C)",
        "Tmin moyenne (°C)",
        "Tmoy moyenne (°C)",
        "Tmax moyenne (°C)",
        "DJU météo",
        "DJU COSTIC",
        "Couverture (%)",
    ]

    for column in columns_to_round:
        if column in formatted.columns:
            formatted[column] = pd.to_numeric(
                formatted[column],
                errors="coerce",
            ).round(2)

    return formatted


# ---------------------------------------------------------
# Interface Streamlit
# ---------------------------------------------------------
st.title("Analyse météo et calcul des DJU")

address = st.text_input(
    "Entrez une adresse ou une ville",
    placeholder="Ex. Paris, France",
)

if address:
    latitude, longitude, geocoding_error = get_coordinates(address)

    if geocoding_error:
        st.error(geocoding_error)

    elif latitude is not None and longitude is not None:
        st.success(
            f"Adresse trouvée : latitude {latitude:.5f}, "
            f"longitude {longitude:.5f}"
        )

        try:
            with st.spinner("Recherche des stations météo proches..."):
                nearby_stations = get_nearby_stations(
                    latitude,
                    longitude,
                )

        except Exception as exc:
            st.error(f"Erreur pendant la recherche des stations : {exc}")
            st.code(traceback.format_exc())
            st.stop()

        if nearby_stations.empty:
            st.warning("Aucune station météo trouvée à proximité.")
            st.stop()

        # Tableau des stations
        station_columns = [
            column
            for column in [
                "name",
                "distance_km",
                "elevation",
                "country",
                "region",
            ]
            if column in nearby_stations.columns
        ]

        stations_table = nearby_stations[station_columns].rename(
            columns={
                "name": "Station",
                "distance_km": "Distance (km)",
                "elevation": "Altitude (m)",
                "country": "Pays",
                "region": "Région",
            }
        )

        st.subheader("Stations météo trouvées")
        st.dataframe(
            stations_table,
            use_container_width=True,
        )

        # Libellé unique : nom + identifiant + distance
        station_options = {}

        for _, row in nearby_stations.iterrows():
            station_id = str(row["_station_id"])
            station_name = row.get("name", station_id)

            if pd.isna(station_name):
                station_name = station_id

            distance = row.get("distance_km", pd.NA)

            if pd.notna(distance):
                label = (
                    f"{station_name} — {station_id} — "
                    f"{float(distance):.2f} km"
                )
            else:
                label = f"{station_name} — {station_id}"

            station_options[label] = {
                "id": station_id,
                "name": str(station_name),
            }

        selected_station_label = st.selectbox(
            "Sélectionnez une station",
            options=list(station_options.keys()),
        )

        selected_station = station_options[selected_station_label]
        selected_station_id = selected_station["id"]
        selected_station_name = selected_station["name"]

        # Période
        today = datetime.date.today()
        default_start = datetime.date(today.year - 1, 1, 1)
        default_end = min(
            datetime.date(today.year, 1, 1),
            today,
        )

        date_col_1, date_col_2 = st.columns(2)

        with date_col_1:
            start_date_fr = st.date_input(
                "Sélectionnez la date de début",
                value=default_start,
                max_value=today,
                format="DD/MM/YYYY",
            )

        with date_col_2:
            end_date_fr = st.date_input(
                "Sélectionnez la date de fin",
                value=default_end,
                max_value=today,
                format="DD/MM/YYYY",
            )

        if end_date_fr < start_date_fr:
            st.error(
                "La date de fin doit être postérieure ou égale "
                "à la date de début."
            )
            st.stop()

        reference_temp = st.number_input(
            "Température de base pour le calcul des DJU (°C)",
            min_value=-30.0,
            max_value=50.0,
            value=18.0,
            step=0.5,
        )

        dju_scale = st.radio(
            "Échelle du tableau des DJU",
            options=["Journalière", "Mensuelle"],
            horizontal=True,
            help=(
                "En mode mensuel, l'application additionne les DJU "
                "calculés pour chaque journée du mois."
            ),
        )

        start_datetime = datetime.datetime.combine(
            start_date_fr,
            datetime.time.min,
        )
        end_datetime = datetime.datetime.combine(
            end_date_fr,
            datetime.time.min,
        )
        end_datetime_hourly = datetime.datetime.combine(
            end_date_fr,
            datetime.time(23, 59),
        )

        # -------------------------------------------------
        # Données journalières et tableau DJU
        # -------------------------------------------------
        with st.spinner("Chargement des données journalières..."):
            raw_daily_data = get_weather_data(
                selected_station_id,
                start_datetime,
                end_datetime,
            )

        if raw_daily_data.empty:
            st.warning(
                f"Aucune donnée journalière disponible pour la station "
                f"« {selected_station_name} » du "
                f"{start_date_fr.strftime('%d/%m/%Y')} au "
                f"{end_date_fr.strftime('%d/%m/%Y')}."
            )

        else:
            daily_data = prepare_daily_data(
                raw_daily_data,
                start_date_fr,
                end_date_fr,
            )

            if daily_data["tmin"].isna().all() or daily_data["tmax"].isna().all():
                st.warning(
                    "Les colonnes Tmin ou Tmax ne contiennent aucune donnée "
                    "exploitable pour la période retenue."
                )
            else:
                daily_data_with_dju = add_daily_dju_columns(
                    daily_data,
                    reference_temp,
                )

                dju_table = build_dju_table(
                    daily_data_with_dju,
                    dju_scale,
                )
                dju_table_display = format_dju_table_for_display(
                    dju_table
                )

                total_dju_meteo = daily_data_with_dju[
                    "dju_meteo"
                ].sum(min_count=1)

                total_dju_costic = daily_data_with_dju[
                    "dju_costic"
                ].sum(min_count=1)

                calculated_days = int(
                    daily_data_with_dju["dju_costic"]
                    .notna()
                    .sum()
                )
                total_period_days = len(daily_data_with_dju)

                st.subheader(
                    f"Tableau des DJU — échelle {dju_scale.lower()}"
                )

                metric_1, metric_2, metric_3 = st.columns(3)

                with metric_1:
                    st.metric(
                        "Total DJU météo",
                        (
                            f"{total_dju_meteo:.2f}"
                            if pd.notna(total_dju_meteo)
                            else "Non calculable"
                        ),
                    )

                with metric_2:
                    st.metric(
                        "Total DJU COSTIC",
                        (
                            f"{total_dju_costic:.2f}"
                            if pd.notna(total_dju_costic)
                            else "Non calculable"
                        ),
                    )

                with metric_3:
                    st.metric(
                        "Jours calculés",
                        f"{calculated_days} / {total_period_days}",
                    )

                if calculated_days < total_period_days:
                    st.warning(
                        f"{total_period_days - calculated_days} jour(s) "
                        "ne disposent pas simultanément de Tmin et Tmax. "
                        "Ils sont laissés vides et exclus des totaux."
                    )

                st.dataframe(
                    dju_table_display,
                    use_container_width=True,
                    hide_index=True,
                )

                # Préparer les métadonnées
                header_info = f"Station : {selected_station_name} ; Période : {start_date_fr} -> {end_date_fr} ; T° base DJU : {reference_temp}\n "

                # Convertir le tableau en CSV
                csv_body = dju_table_display.to_csv(
                    index=False,
                    sep=";",
                    decimal=","
                )

                # Concaténer
                csv_data = (header_info + csv_body).encode("utf-8-sig")

                # Bouton téléchargement
                st.download_button(
                    label="Télécharger le tableau DJU en CSV",
                    data=csv_data,
                    file_name=f"dju_{selected_station_name}.csv",
                    mime="text/csv",
                )


                # Courbe des températures journalières
                st.subheader("Températures journalières")

                fig_daily, ax_daily = plt.subplots(figsize=(10, 6))

                ax_daily.plot(
                    daily_data.index,
                    daily_data["tmin"],
                    label="Température minimale (°C)",
                )
                ax_daily.plot(
                    daily_data.index,
                    daily_data["temp"],
                    label="Température moyenne (°C)",
                )
                ax_daily.plot(
                    daily_data.index,
                    daily_data["tmax"],
                    label="Température maximale (°C)",
                )
                ax_daily.fill_between(
                    daily_data.index,
                    daily_data["tmin"],
                    daily_data["tmax"],
                    alpha=0.1,
                )
                ax_daily.set_title(
                    f"Températures journalières — "
                    f"{selected_station_name}"
                )
                ax_daily.set_xlabel("Date")
                ax_daily.set_ylabel("Température (°C)")
                ax_daily.legend()
                fig_daily.autofmt_xdate()
                fig_daily.tight_layout()

                st.pyplot(fig_daily)
                plt.close(fig_daily)

                with st.expander(
                    "Afficher les données journalières brutes"
                ):
                    st.dataframe(
                        raw_daily_data,
                        use_container_width=True,
                    )

        # -------------------------------------------------
        # Données horaires
        # -------------------------------------------------
        with st.expander("Afficher les données horaires"):
            with st.spinner("Chargement des données horaires..."):
                hourly_data = get_weather_data_hourly(
                    selected_station_id,
                    start_datetime,
                    end_datetime_hourly,
                )

            if hourly_data.empty:
                st.info(
                    f"Aucune donnée horaire disponible pour la station "
                    f"« {selected_station_name} » sur la période retenue."
                )

            else:
                st.dataframe(
                    hourly_data,
                    use_container_width=True,
                )

                if "temp" not in hourly_data.columns:
                    st.warning(
                        "La colonne « temp » est absente des données horaires."
                    )

                else:
                    fig_hourly, ax_hourly = plt.subplots(
                        figsize=(10, 6)
                    )
                    ax_hourly.plot(
                        hourly_data.index,
                        hourly_data["temp"],
                        label="Température horaire (°C)",
                    )
                    ax_hourly.set_title(
                        f"Températures horaires — "
                        f"{selected_station_name}"
                    )
                    ax_hourly.set_xlabel("Date")
                    ax_hourly.set_ylabel("Température (°C)")
                    ax_hourly.legend()
                    fig_hourly.autofmt_xdate()
                    fig_hourly.tight_layout()

                    st.pyplot(fig_hourly)
                    plt.close(fig_hourly)
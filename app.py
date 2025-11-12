import streamlit as st 
import pandas as pd
import folium
from streamlit_folium import st_folium

from helpers import (
    query_station_status,
    get_station_latlon,
    join_latlon,
    get_marker_color,
    geocode,
    get_bike_availability,
    get_dock_availability,
    run_osrm,
)

st.set_page_config(
    page_title="NYC Citi Bike Station Status",
    page_icon="🚲",
    layout="wide",
)

st.title("NYC Citi Bike Station Status")
st.markdown(
    "Live system feed sourced from the official [Citi Bike GBFS](https://gbfs.citibikenyc.com/gbfs/en/) endpoints."
)


@st.cache_data(ttl=60)
def load_station_data():
    status_df = query_station_status()
    info_df = get_station_latlon()
    stations_df = join_latlon(status_df, info_df)

    if stations_df.empty:
        return status_df, info_df, stations_df

    numeric_columns = [
        "num_bikes_available",
        "num_docks_available",
        "mechanical",
        "ebike",
        "capacity",
    ]

    for col in numeric_columns:
        if col in stations_df.columns:
            stations_df[col] = pd.to_numeric(
                stations_df[col], errors="coerce"
            ).fillna(0)

    stations_df["station_id"] = stations_df["station_id"].astype(str)
    stations_df["region_id"] = stations_df.get("region_id", pd.NA)

    return status_df, info_df, stations_df


def refresh_data():
    query_station_status.clear()
    get_station_latlon.clear()
    load_station_data.clear()
    st.toast("Data refreshed. Please wait…")
    st.rerun()


status_df, info_df, stations_df = load_station_data()

if stations_df.empty:
    st.error("Citi Bike data is currently unavailable.")
    st.stop()

last_updated = stations_df["last_updated"].max()
last_reported = stations_df["last_reported"].max()

st.caption(
    f"Last system update: {last_updated.tz_convert('America/New_York').strftime('%Y-%m-%d %I:%M %p %Z')}"
)

with st.sidebar:
    st.header("Controls")
    if st.button("🔄 Refresh data"):
        refresh_data()

    st.subheader("Filters")
    max_bikes = int(stations_df["num_bikes_available"].max())
    max_docks = int(stations_df["num_docks_available"].max())

    min_bikes = st.slider(
        "Minimum bikes available",
        min_value=0,
        max_value=max(1, max_bikes),
        value=0,
    )
    min_docks = st.slider(
        "Minimum docks available",
        min_value=0,
        max_value=max(1, max_docks),
        value=0,
    )

    bike_type_labels = {
        "Any bike": None,
        "Classic (mechanical)": "mechanical",
        "E-bike": "ebike",
    }
    bike_type_choice = st.selectbox(
        "Bike type availability",
        options=list(bike_type_labels.keys()),
    )
    bike_type_value = bike_type_labels[bike_type_choice]

    search_text = st.text_input("Station name search")

    st.subheader("Find Stations Near You")
    user_input_address = st.text_input(
        "Enter an NYC address or landmark", placeholder="e.g., Times Square"
    )
    col_bike, col_dock = st.columns(2)
    find_bike_station = col_bike.button("Nearest bikes")
    find_dock_station = col_dock.button("Nearest docks")


def apply_filters(df):
    filtered = df.copy()

    if min_bikes > 0:
        filtered = filtered[filtered["num_bikes_available"] >= min_bikes]
    if min_docks > 0:
        filtered = filtered[filtered["num_docks_available"] >= min_docks]
    if bike_type_value:
        if bike_type_value in filtered.columns:
            filtered = filtered[filtered[bike_type_value] > 0]
        else:
            filtered = filtered.iloc[0:0]
    if search_text and "name" in filtered.columns:
        filtered = filtered[
            filtered["name"].str.contains(search_text, case=False, na=False)
        ]

    return filtered


filtered_stations = apply_filters(stations_df)

total_bikes = int(filtered_stations["num_bikes_available"].sum())
total_docks = int(filtered_stations["num_docks_available"].sum())
total_stations = len(filtered_stations)
avg_bikes_per_station = (
    round(total_bikes / total_stations, 1) if total_stations else 0
)

metric_cols = st.columns(4)
metric_cols[0].metric("Stations", f"{total_stations}")
metric_cols[1].metric("Bikes available", f"{total_bikes}")
metric_cols[2].metric("Docks available", f"{total_docks}")
metric_cols[3].metric("Avg bikes / station", f"{avg_bikes_per_station}")

if "user_location" not in st.session_state:
    st.session_state["user_location"] = None
if "nearest_bike_station" not in st.session_state:
    st.session_state["nearest_bike_station"] = None
if "nearest_dock_station" not in st.session_state:
    st.session_state["nearest_dock_station"] = None
if "route_coordinates" not in st.session_state:
    st.session_state["route_coordinates"] = None
if "route_duration" not in st.session_state:
    st.session_state["route_duration"] = None


def lookup_and_store_nearest(address, lookup_type):
    coords = geocode(address)
    if not coords:
        st.warning("Location not found. Please try a different address.")
        return

    st.session_state["user_location"] = coords

    if lookup_type == "bike":
        modes = [bike_type_value] if bike_type_value else []
        nearest = get_bike_availability(coords, stations_df, modes)
        st.session_state["nearest_bike_station"] = nearest
        st.session_state["nearest_dock_station"] = None
    else:
        nearest = get_dock_availability(coords, stations_df)
        st.session_state["nearest_dock_station"] = nearest
        st.session_state["nearest_bike_station"] = None

    if nearest:
        route_coords, route_duration = run_osrm(nearest, coords)
        st.session_state["route_coordinates"] = route_coords
        st.session_state["route_duration"] = route_duration
    else:
        st.session_state["route_coordinates"] = None
        st.session_state["route_duration"] = None


if user_input_address and find_bike_station:
    lookup_and_store_nearest(user_input_address, "bike")
elif user_input_address and find_dock_station:
    lookup_and_store_nearest(user_input_address, "dock")


def create_station_popup(row):
    station_name = row.get("name") or f"Station {row.get('station_id', 'n/a')}"
    bikes = int(row.get("num_bikes_available", 0))
    docks = int(row.get("num_docks_available", 0))
    mechanical = int(row.get("mechanical", 0))
    ebike = int(row.get("ebike", 0))
    capacity = int(row.get("capacity", 0))
    last_reported_row = row.get("last_reported")
    last_reported_local = (
        last_reported_row.tz_convert("America/New_York").strftime(
            "%Y-%m-%d %I:%M %p %Z"
        )
        if pd.notnull(last_reported_row)
        else "n/a"
    )
    html = f"""
    <div style="font-size: 14px;">
        <strong>{station_name}</strong><br/>
        Bikes available: {bikes} (Classic {mechanical} / E-bike {ebike})<br/>
        Docks available: {docks}<br/>
        Capacity: {capacity}<br/>
        Last reported: {last_reported_local}
    </div>
    """
    return folium.Popup(html, max_width=250)


def build_map(df):
    if df.empty:
        centroid = [40.7549, -73.9840]  # Midtown Manhattan fallback
    else:
        centroid = [df["lat"].mean(), df["lon"].mean()]

    m = folium.Map(location=centroid, zoom_start=13, tiles="cartodbpositron")

    for _, row in df.iterrows():
        color = get_marker_color(int(row.get("num_bikes_available", 0)))
        folium.CircleMarker(
            location=[row["lat"], row["lon"]],
            radius=6,
            color=color,
            fill=True,
            fill_opacity=0.8,
            popup=create_station_popup(row),
        ).add_to(m)

    user_location = st.session_state["user_location"]
    if user_location:
        folium.Marker(
            location=user_location,
            tooltip="Your location",
            icon=folium.Icon(color="blue", icon="info-sign"),
        ).add_to(m)

    highlighted_station = (
        st.session_state["nearest_bike_station"]
        or st.session_state["nearest_dock_station"]
    )
    if highlighted_station:
        station_row = stations_df[
            stations_df["station_id"] == highlighted_station[0]
        ]
        if not station_row.empty:
            row = station_row.iloc[0]
            station_name = row.get("name") or f"Station {row.get('station_id', 'n/a')}"
            folium.Marker(
                location=[row["lat"], row["lon"]],
                tooltip=station_name,
                icon=folium.Icon(color="green", icon="ok-sign"),
                popup=create_station_popup(row),
            ).add_to(m)

    route_coords = st.session_state["route_coordinates"]
    if route_coords:
        folium.PolyLine(
            locations=route_coords,
            color="blue",
            weight=4,
            opacity=0.7,
        ).add_to(m)

    return st_folium(m, height=600, width='stretch')


map_container = st.container()
with map_container:
    st.subheader("Interactive map")
    build_map(filtered_stations)


nearest_bike = st.session_state["nearest_bike_station"]
nearest_dock = st.session_state["nearest_dock_station"]
route_duration = st.session_state["route_duration"]

if nearest_bike:
    station_row = stations_df[stations_df["station_id"] == nearest_bike[0]]
    if not station_row.empty:
        row = station_row.iloc[0]
        station_name = row.get("name") or f"Station {row.get('station_id', 'n/a')}"
        st.info(
            f"Nearest station with bikes: **{station_name}** "
            f"({row['num_bikes_available']} bikes / {row['num_docks_available']} docks)."
        )
if nearest_dock:
    station_row = stations_df[stations_df["station_id"] == nearest_dock[0]]
    if not station_row.empty:
        row = station_row.iloc[0]
        station_name = row.get("name") or f"Station {row.get('station_id', 'n/a')}"
        st.info(
            f"Nearest station with docks: **{station_name}** "
            f"({row['num_bikes_available']} bikes / {row['num_docks_available']} docks)."
        )
if route_duration:
    st.caption(f"Estimated travel time by OSRM routing: ~{route_duration} minutes.")


with st.expander("Station details"):
    display_columns = [
        "station_id",
        "name",
        "num_bikes_available",
        "mechanical",
        "ebike",
        "num_docks_available",
        "capacity",
        "lat",
        "lon",
        "last_reported",
    ]
    available_columns = [col for col in display_columns if col in filtered_stations]
    table_df = filtered_stations[available_columns].copy()
    st.dataframe(
        table_df.sort_values("num_bikes_available", ascending=False),
        width='stretch',
    )
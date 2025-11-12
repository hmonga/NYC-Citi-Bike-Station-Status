import urllib.request  # Import module for working with URLs
import urllib.error
import json  # Import module for working with JSON data
import pandas as pd  # Import pandas for data manipulation
from geopy.distance import geodesic  # Import geodesic for calculating distances
from geopy.geocoders import Nominatim  # Import Nominatim for geocoding
import streamlit as st  # Import Streamlit for creating web apps

NYC_GBFS_BASE_URL = "https://gbfs.citibikenyc.com/gbfs/en"
NYC_STATION_STATUS_URL = f"{NYC_GBFS_BASE_URL}/station_status.json"
NYC_STATION_INFO_URL = f"{NYC_GBFS_BASE_URL}/station_information.json"


@st.cache_data(ttl=60)  # Cache the function's output to improve performance
# Define the function to query station status from a given URL
def query_station_status(url=NYC_STATION_STATUS_URL):
    try:
        with urllib.request.urlopen(url) as data_url:  # Open the URL
            data = json.loads(data_url.read().decode())  # Read and decode the JSON data
    except urllib.error.URLError as exc:
        st.error(f"Unable to reach Citi Bike status endpoint: {exc.reason}")
        return pd.DataFrame()

    df = pd.DataFrame(data['data']['stations'])  # Convert the data to a DataFrame
    if df.empty:
        return df

    df = df[(df['is_renting'] == 1) & (df['is_returning'] == 1)]
    df = df.drop_duplicates(['station_id', 'last_reported'])  # Remove duplicate records
    df['last_reported'] = pd.to_datetime(df['last_reported'], unit='s', utc=True)
    df['last_updated'] = pd.to_datetime(data['last_updated'], unit='s', utc=True)

    if 'num_bikes_available_types' in df:
        bike_types = (
            pd.json_normalize(df['num_bikes_available_types'])
            .fillna(0)
            .astype(int)
        )
        bike_types.columns = [col.lower() for col in bike_types.columns]
        df = pd.concat([df.drop(columns=['num_bikes_available_types']), bike_types], axis=1)

    for col in ['mechanical', 'ebike']:
        if col not in df:
            df[col] = 0

    df['station_id'] = df['station_id'].astype(str)
    return df  # Return the DataFrame


@st.cache_data(ttl=3600)
# Define the function to get station latitude and longitude from a given URL
def get_station_latlon(url=NYC_STATION_INFO_URL):
    try:
        with urllib.request.urlopen(url) as data_url:  # Open the URL
            latlon = json.loads(data_url.read().decode())  # Read and decode the JSON data
    except urllib.error.URLError as exc:
        st.error(f"Unable to reach Citi Bike station info endpoint: {exc.reason}")
        return pd.DataFrame()
    latlon = pd.DataFrame(latlon['data']['stations'])  # Convert the data to a DataFrame
    if not latlon.empty:
        latlon['station_id'] = latlon['station_id'].astype(str)
    return latlon  # Return the DataFrame

# Define the function to join two DataFrames on station_id
def join_latlon(df1, df2):
    if df1.empty or df2.empty:
        return pd.DataFrame()
    df1 = df1.copy()
    df2 = df2.copy()
    df1['station_id'] = df1['station_id'].astype(str)
    df2['station_id'] = df2['station_id'].astype(str)
    merge_columns = ['station_id', 'lat', 'lon', 'name', 'capacity', 'region_id']
    available_cols = [col for col in merge_columns if col in df2.columns]
    df = df1.merge(df2[available_cols], 
                how='left', 
                on='station_id')  # Merge the DataFrames on station_id
    return df  # Return the merged DataFrame

# Function to determine marker color based on the number of bikes available
def get_marker_color(num_bikes_available):
    if num_bikes_available > 3:
        return 'green'
    elif 0 < num_bikes_available <= 3:
        return 'yellow'
    else:
        return 'red'

# Define the function to geocode an address
def geocode(address):
    geolocator = Nominatim(user_agent="clicked-demo")  # Create a geolocator object
    location = geolocator.geocode(address)  # Geocode the address
    if location is None:
        return ''  # Return an empty string if the address is not found
    return (location.latitude, location.longitude)  # Return the latitude and longitude

# Define the function to get bike availability near a location
def get_bike_availability(latlon, df, input_bike_modes):
    """Calculate distance from each station to the user and return a single station id, lat, lon"""
    df = df.copy().reset_index(drop=True)
    if 'mechanical' not in df.columns:
        df['mechanical'] = 0
    if 'ebike' not in df.columns:
        df['ebike'] = 0

    df['distance'] = df.apply(
        lambda row: geodesic(latlon, (row['lat'], row['lon'])).km, axis=1
    )

    if len(input_bike_modes) == 1:
        mode = input_bike_modes[0]
        if mode in df.columns:
            df = df[df[mode] > 0]
    else:
        df = df[(df['mechanical'] > 0) | (df['ebike'] > 0)]

    df = df[df['num_bikes_available'] > 0]
    if df.empty:
        return None

    closest = df.loc[df['distance'].idxmin()]
    return [closest['station_id'], closest['lat'], closest['lon']]

# Define the function to get dock availability near a location
def get_dock_availability(latlon, df):
    """Calculate distance from each station to the user and return a single station id, lat, lon"""
    df = df.copy().reset_index(drop=True)
    df['distance'] = df.apply(
        lambda row: geodesic(latlon, (row['lat'], row['lon'])).km, axis=1
    )
    df = df[df['num_docks_available'] > 0]  # Remove stations without available docks

    if df.empty:
        return None

    closest = df.loc[df['distance'].idxmin()]
    return [closest['station_id'], closest['lat'], closest['lon']]

import requests  # Import requests for making HTTP requests

# Define the function to run OSRM and get route coordinates and duration
def run_osrm(chosen_station, iamhere):
    start = "{},{}".format(iamhere[1], iamhere[0])  # Format the start coordinates
    end = "{},{}".format(chosen_station[2], chosen_station[1])  # Format the end coordinates
    url = 'http://router.project-osrm.org/route/v1/driving/{};{}?geometries=geojson'.format(start, end)  # Create the OSRM API URL

    headers = {'Content-type': 'application/json'}
    try:
        r = requests.get(url, headers=headers, timeout=10)  # Make the API request
        r.raise_for_status()
    except requests.RequestException:
        return None, None

    routejson = r.json()  # Parse the JSON response
    routes = routejson.get('routes', [])
    if not routes:
        return None, None

    coordinates = [[lat, lon] for lon, lat in routes[0]['geometry']['coordinates']]
    duration = round(routes[0]['duration'] / 60, 1)  # Convert duration to minutes

    return coordinates, duration  # Return the coordinates and duration
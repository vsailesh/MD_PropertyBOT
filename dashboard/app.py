import streamlit as st
import pandas as pd
import pydeck as pdk
import os

st.set_page_config(page_title="Maryland Hindu Property Owners", layout="wide", page_icon="🏠")

st.markdown("""
<style>
    .main {
        background-color: #0e1117;
        color: white;
    }
    .stMetric {
        background-color: #1e2130;
        padding: 15px;
        border-radius: 10px;
        border: 1px solid #3e4461;
    }
    h1, h2, h3 {
        color: #ff4b4b !important;
    }
</style>
""", unsafe_allow_html=True)

st.title("🗺️ Maryland Hindu Property Owners Dashboard")
st.markdown("Interactive visualization of property ownership patterns across Maryland counties.")

# Load Data
DATA_FILE = 'data/Hindu_Origin_Owners_Mapped.xlsx'

if not os.path.exists(DATA_FILE):
    # Fallback to base file if mapped doesn't exist yet
    DATA_FILE = 'data/Hindu_Origin_Owners.xlsx'
    st.warning("📍 Geocoding is in progress. Showing raw data (map will be empty until coordinates are added).")

@st.cache_data(ttl=60) # Refresh every minute while background script runs
def load_data(file):
    df = pd.read_excel(file)
    # Ensure coordinates are numeric
    if 'latitude' in df.columns and 'longitude' in df.columns:
        df['latitude'] = pd.to_numeric(df['latitude'], errors='coerce')
        df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')
    return df

try:
    df = load_data(DATA_FILE)
    
    # Sidebar Filters
    st.sidebar.header("🔍 Filters")
    counties = sorted(df['county'].unique().tolist())
    selected_county = st.sidebar.multiselect("Select County", counties, default=counties)
    
    # Filter Data
    filtered_df = df[df['county'].isin(selected_county)]
    
    # Summary Metrics
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total Owners", f"{len(df):,}")
    with col2:
        mapped_count = filtered_df['latitude'].notnull().sum() if 'latitude' in filtered_df.columns else 0
        st.metric("Mapped Properties", f"{mapped_count:,}")
    with col3:
        top_county = filtered_df['county'].value_counts().idxmax() if not filtered_df.empty else "N/A"
        st.metric("Top County", top_county)

    # Map Section
    st.subheader("📍 Property Distribution & County Boundaries")
    
    map_df = filtered_df.dropna(subset=['latitude', 'longitude'])
    
    # Load County Boundaries
    COUNTY_GEOJSON = 'data/maryland-counties.geojson'
    geojson_data = None
    if os.path.exists(COUNTY_GEOJSON):
        import json
        with open(COUNTY_GEOJSON, 'r') as f:
            geojson_data = json.load(f)

    layers = []
    
    # 1. Base Layer: Standard OpenStreetMap (OSM) Tiles
    # This provides the "Google Maps" look with highways, routes, and building footprints
    layers.append(pdk.Layer(
        "TileLayer",
        data="https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        get_tile_data=None, # Not needed for standard XYZ
        min_zoom=0,
        max_zoom=19,
        tileSize=256,
        pickable=False,
    ))

    # 2. Boundary Layer: Maryland Counties
    if geojson_data:
        layers.append(pdk.Layer(
            "GeoJsonLayer",
            geojson_data,
            opacity=0.2, # Slightly more visible
            stroked=True,
            filled=True,
            extruded=False,
            wireframe=True,
            get_fill_color=[100, 100, 255, 40],
            get_line_color=[150, 150, 150],
            get_line_width=150,
            pickable=True,
        ))

    # 3. Property Layer: Hindu-origin owners
    if not map_df.empty:
        layers.append(pdk.Layer(
            "ScatterplotLayer",
            map_df,
            get_position=["longitude", "latitude"],
            get_color=[255, 75, 75, 230],
            get_radius=40, # Reduced from 100 for better visibility of map features
            pickable=True,
            stroked=True,
            get_line_color=[255, 255, 255], # White outline looks professional against OSM
            get_line_width=5,
            radius_min_pixels=4,
            radius_max_pixels=10,
        ))
        
    # View State
    if not map_df.empty:
        v_lat, v_lon = map_df['latitude'].mean(), map_df['longitude'].mean()
        v_zoom = 13 # Zoomed in closer to see street names and landmarks clearly
    else:
        v_lat, v_lon = 39.0458, -76.6413 # Center of MD
        v_zoom = 8

    view_state = pdk.ViewState(
        latitude=v_lat,
        longitude=v_lon,
        zoom=v_zoom,
        pitch=0,
    )
    
    # Tooltip logic
    tooltip = {
        "html": "<b>Owner:</b> {owner_name}<br/><b>Address:</b> {address}<br/><b>County:</b> {county}",
        "style": {"backgroundColor": "#1e2130", "color": "white", "border": "1px solid #ff4b4b"}
    }
    
    # Use map_style=None to use the custom TileLayer as base
    st.pydeck_chart(pdk.Deck(
        layers=layers,
        initial_view_state=view_state,
        tooltip=tooltip,
        map_style=None 
    ))
    
    if map_df.empty:
        st.info("Waiting for more coordinates... The background geocoding script has processed some addresses, but none match your filters yet.")

    # Data Table
    st.subheader("📋 Property Details")
    st.dataframe(filtered_df[['owner_name', 'address', 'county', 'sub_category']], use_container_width=True)

    # Refresh Button
    if st.button("🔄 Refresh Data"):
        st.rerun()

except Exception as e:
    st.error(f"Error loading dashboard: {e}")
    st.info("Make sure Hindu_Origin_Owners.xlsx exists in the project root.")

st.markdown("---")
st.caption("Data source: Maryland SDAT | Geocoding: OpenStreetMap (Nominatim)")

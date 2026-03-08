import streamlit as st
import pandas as pd
import pydeck as pdk
import os

st.set_page_config(page_title="Maryland Property Owners", layout="wide", page_icon="🏠")

st.markdown("""
<style>
    /* Main background and text */
    .stApp {
        background-color: #0e1117;
        color: #ffffff !important;
    }
    
    /* Metrics Styling */
    [data-testid="stMetric"] {
        background-color: #1e2130;
        padding: 20px;
        border-radius: 12px;
        border: 1px solid #3e4461;
        box-shadow: 0 4px 6px rgba(0,0,0,0.3);
    }
    
    [data-testid="stMetricLabel"] {
        color: #ccd0d8 !important;
        font-weight: 600 !important;
        font-size: 16px !important;
    }
    
    [data-testid="stMetricValue"] {
        color: #ffffff !important;
        font-weight: 800 !important;
    }

    /* Headers */
    h1, h2, h3 {
        color: #ff4b4b !important;
        font-family: 'Inter', sans-serif;
    }
    
    /* Sidebar text fix */
    .css-1d391kg {
        color: white !important;
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

    # 1. CLEANING: Drop rows that are just headers or technical names (just in case)
    technical_names = ['owner_name', 'address', 'street_name', 'Street_name', 'Owner Name', 'Address', 'Street Name']
    for col in df.columns:
        df = df[~df[col].astype(str).isin(technical_names)]

    # 2. Normalize column names - detect actual columns
    # Map common column name variations
    col_mapping = {}
    for col in df.columns:
        col_lower = col.lower().strip()
        if 'owner' in col_lower and 'name' in col_lower:
            col_mapping['owner_name'] = col
        elif col_lower == 'address' or col_lower == 'addr':
            col_mapping['address'] = col
        elif col_lower == 'county':
            col_mapping['county'] = col
        elif col_lower == 'latitude' or col_lower == 'lat':
            col_mapping['latitude'] = col
        elif col_lower == 'longitude' or col_lower == 'lon' or col_lower == 'long':
            col_mapping['longitude'] = col
        elif 'category' in col_lower or 'sub_category' in col_lower:
            col_mapping['sub_category'] = col

    df.columns = df.columns.astype(str)  # Ensure column names are strings

    # 3. FORMATTING: Title Case for names and addresses
    if 'owner_name' in col_mapping:
        df[col_mapping['owner_name']] = df[col_mapping['owner_name']].astype(str).str.title()
    if 'address' in col_mapping:
        df[col_mapping['address']] = df[col_mapping['address']].astype(str).str.title()
    if 'county' in col_mapping:
        df[col_mapping['county']] = df[col_mapping['county']].astype(str).str.title()

    # 4. COORDINATES: Ensure numeric
    if 'latitude' in col_mapping and 'longitude' in col_mapping:
        df[col_mapping['latitude']] = pd.to_numeric(df[col_mapping['latitude']], errors='coerce')
        df[col_mapping['longitude']] = pd.to_numeric(df[col_mapping['longitude']], errors='coerce')

    # Store column mapping for later use
    df.attrs['col_mapping'] = col_mapping
    return df

try:
    df = load_data(DATA_FILE)
    col_mapping = df.attrs.get('col_mapping', {})

    # Helper function to get actual column name
    def get_col(mapped_name):
        return col_mapping.get(mapped_name, mapped_name)

    # Get actual column names
    county_col = get_col('county')
    lat_col = get_col('latitude')
    lon_col = get_col('longitude')
    owner_col = get_col('owner_name')
    addr_col = get_col('address')
    cat_col = get_col('sub_category')

    # Check if required columns exist
    if county_col not in df.columns:
        st.error(f"Column '{county_col}' not found in data. Available columns: {list(df.columns)}")
        st.info("Available columns in file:")
        st.write(df.columns.tolist())
    else:
        # Sidebar Filters
        st.sidebar.header("🔍 Interactive Filters")
        
        # 1. Global Search
        search_query = st.sidebar.text_input("Search by Owner or Address", "").strip().lower()
        
        # 2. County Filter
        counties = sorted(df[county_col].unique().astype(str).tolist())
        selected_county = st.sidebar.multiselect("Select Counties", counties, default=counties)
        
        # 3. Category Filter
        categories = []
        if cat_col in df.columns:
            categories = sorted(df[cat_col].unique().astype(str).tolist())
            selected_cat = st.sidebar.multiselect("Select Categories", categories, default=categories)
        
        # Filter Data
        mask = df[county_col].astype(str).isin(selected_county)
        
        if categories:
            mask = mask & df[cat_col].astype(str).isin(selected_cat)
            
        if search_query:
            mask = mask & (
                df[owner_col].astype(str).str.lower().str.contains(search_query) | 
                df[addr_col].astype(str).str.lower().str.contains(search_query)
            )
            
        filtered_df = df[mask]

        # 4. Download Button
        st.sidebar.markdown("---")
        csv = filtered_df.to_csv(index=False).encode('utf-8')
        st.sidebar.download_button(
            label="📥 Export Filtered Data (CSV)",
            data=csv,
            file_name="maryland_property_export.csv",
            mime="text/csv",
        )

        # Summary Metrics
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Filtered Properties", f"{len(filtered_df):,}")
        with col2:
            mapped_count = filtered_df[lat_col].notnull().sum() if lat_col in filtered_df.columns else 0
            st.metric("Mapped Geolocations", f"{mapped_count:,}")
        with col3:
            if not filtered_df.empty:
                top_cat = filtered_df[cat_col].value_counts().idxmax() if cat_col in filtered_df.columns else "N/A"
                st.metric("Top Category", top_cat)
            else:
                st.metric("Top Category", "N/A")

        # Map Section
        st.subheader("📍 Property Distribution & County Boundaries")

        map_df = filtered_df.dropna(subset=[lat_col, lon_col]) if lat_col in filtered_df.columns and lon_col in filtered_df.columns else pd.DataFrame()

        # Load County Boundaries
        COUNTY_GEOJSON = 'data/maryland-counties.geojson'
        geojson_data = None
        if os.path.exists(COUNTY_GEOJSON):
            import json
            with open(COUNTY_GEOJSON, 'r') as f:
                geojson_data = json.load(f)

        layers = []

        # 1. Base Layer: Standard OpenStreetMap (OSM) Tiles
        layers.append(pdk.Layer(
            "TileLayer",
            data="https://tile.openstreetmap.org/{z}/{x}/{y}.png",
            get_tile_data=None,
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
                opacity=0.2,
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
            # We no longer need jitter for house-level coordinates.
            # Only apply a tiny jitter for centroid matches if they overlap exactly.
            import numpy as np
            map_df = map_df.copy()
            
            # Tiny jitter only for centroids (Level 7) to separate overlapping points
            centroid_mask = map_df['Method'].str.contains('Level 7', na=False)
            if centroid_mask.any():
                map_df.loc[centroid_mask, '_plot_lat'] = map_df.loc[centroid_mask, lat_col] + np.random.uniform(-0.001, 0.001, centroid_mask.sum())
                map_df.loc[centroid_mask, '_plot_lon'] = map_df.loc[centroid_mask, lon_col] + np.random.uniform(-0.001, 0.001, centroid_mask.sum())
            
            # Direct matches use exact coordinates
            if (~centroid_mask).any():
                map_df.loc[~centroid_mask, '_plot_lat'] = map_df.loc[~centroid_mask, lat_col]
                map_df.loc[~centroid_mask, '_plot_lon'] = map_df.loc[~centroid_mask, lon_col]

            layers.append(pdk.Layer(
                "ScatterplotLayer",
                map_df,
                get_position=["_plot_lon", "_plot_lat"],
                get_color=[255, 75, 75, 230],
                get_radius=50,
                pickable=True,
                stroked=True,
                get_line_color=[255, 255, 255],
                get_line_width=2,
                radius_min_pixels=3,
                radius_max_pixels=8,
            ))

        # View State
        if not map_df.empty and lat_col in map_df.columns and lon_col in map_df.columns:
            v_lat, v_lon = map_df[lat_col].mean(), map_df[lon_col].mean()
            v_zoom = 13
        else:
            v_lat, v_lon = 39.0458, -76.6413
            v_zoom = 8

        view_state = pdk.ViewState(
            latitude=v_lat,
            longitude=v_lon,
            zoom=v_zoom,
            pitch=0,
        )

        # Tooltip logic
        tooltip_html = f"""
            <b>Owner:</b> {{{owner_col}}}<br/>
            <b>SDAT Address:</b> {{{addr_col}}}<br/>
            <b>Verified Address:</b> {{Geo_Address}}<br/>
            <b>County:</b> {{{county_col}}}<br/>
            <b>Match Quality:</b> {{Method}}
        """
        tooltip = {
            "html": tooltip_html,
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
            st.info("No coordinates available for the selected filters. The background geocoding script has processed some addresses, but none match your filters yet.")

        # Data Table
        st.subheader("📋 Property Details")

        # Display table with pretty headers
        display_cols = [owner_col, addr_col, county_col]
        if cat_col in df.columns:
            display_cols.append(cat_col)

        display_df = filtered_df[display_cols].copy()

        # Set column names based on how many columns we have
        if cat_col in df.columns:
            display_df.columns = ["Owner Name", "Property Address", "County", "Category"]
        else:
            display_df.columns = ["Owner Name", "Property Address", "County"]

        st.dataframe(display_df, use_container_width=True)

        # Refresh Button
        if st.button("🔄 Refresh Data"):
            st.cache_data.clear()
            st.rerun()

except Exception as e:
    st.error(f"Error loading dashboard: {e}")
    st.info("Make sure Hindu_Origin_Owners.xlsx exists in the project root.")

st.markdown("---")
st.caption("Data source: Maryland SDAT | Geocoding: OpenStreetMap (Nominatim)")

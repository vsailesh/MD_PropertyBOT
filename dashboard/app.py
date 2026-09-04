import streamlit as st
import pandas as pd
import folium
import re
import json
import sys
from streamlit_folium import st_folium
import os

# Streamlit puts cwd (not the script dir) on sys.path — make the sibling
# comments_store module importable both locally and on Streamlit Cloud
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from comments_store import CommentsStore, OUTREACH_TYPES

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


# ------------------------------------------------------------ outreach log
@st.cache_data(ttl=300, show_spinner=False)
def commented_keys():
    """(COUNTY, ADDRESS) pairs that already have outreach notes — cached so
    marker badging costs one request per 5 minutes, not one per render."""
    store = CommentsStore()
    if not store.enabled:
        return set()
    try:
        return store.all_commented()
    except Exception:
        return set()


def _norm(text):
    return " ".join(str(text or "").upper().split())


def _editor_password():
    """EDITOR_PASSWORD from st.secrets (cloud) or env/.env (local) —
    empty means commenting stays locked (fail closed)."""
    try:
        if "EDITOR_PASSWORD" in st.secrets:
            return st.secrets["EDITOR_PASSWORD"]
    except Exception:
        pass
    from comments_store import _load_env_file
    env = dict(os.environ)
    env.update(_load_env_file())
    return env.get("EDITOR_PASSWORD", "")

@st.cache_data
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

    # 3. FORMATTING: Title Case and Whitespace Normalization
    def clean_text(text):
        if not text or pd.isna(text): return text
        return re.sub(r'\s+', ' ', str(text).strip()).title()

    if 'owner_name' in col_mapping:
        df[col_mapping['owner_name']] = df[col_mapping['owner_name']].apply(clean_text)
    if 'address' in col_mapping:
        df[col_mapping['address']] = df[col_mapping['address']].apply(clean_text)
    if 'county' in col_mapping:
        c_col = col_mapping['county']
        df[c_col] = df[c_col].astype(str).str.replace(r'(?i)\s*County\s*', '', regex=True).str.strip().str.title()
        # Special case: ensure "Baltimore City" stays "Baltimore City"
        df[c_col] = df[c_col].str.replace('Baltimore City', 'Baltimore City', case=False)
        # Collapse spaces if any in county
        df[c_col] = df[c_col].str.replace(r'\s+', ' ', regex=True).str.strip()


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
    method_col = get_col('Method')

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

        # 4. Editor access gate — everyone reads outreach notes; only people
        # with the editor password can add them (secret lives server-side in
        # st.secrets; missing secret fails closed)
        st.sidebar.markdown("---")
        with st.sidebar.expander("✍️ Editor access", expanded=not st.session_state.get("editor_ok", False)):
            st.session_state.editor_name = st.text_input(
                "Your name", value=st.session_state.get("editor_name", ""))
            pw = st.text_input("Editor password", type="password")
            if st.button("Unlock commenting"):
                expected = _editor_password()
                if pw and expected and pw == expected:
                    st.session_state.editor_ok = True
                    st.rerun()
                else:
                    st.error("Wrong password (or no EDITOR_PASSWORD configured).")
        if st.session_state.get("editor_ok"):
            st.sidebar.success(f"Commenting as **{st.session_state.get('editor_name') or 'anonymous'}**")

        # 5. Download Button
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
        st.subheader("📍 Property Distribution & Viewport Filtering")

        map_df = filtered_df.dropna(subset=[lat_col, lon_col]) if lat_col in filtered_df.columns and lon_col in filtered_df.columns else pd.DataFrame()

        # Initialize map state in session state if not present
        if 'map_bounds' not in st.session_state:
            st.session_state.map_bounds = None

        # Determine initial center and zoom
        # Based ONLY on current filters so the baseline HTML stays perfectly consistent during pan/zoom
        if not map_df.empty:
            v_lat, v_lon = map_df[lat_col].mean(), map_df[lon_col].mean()
            v_zoom = 12
        else:
            v_lat, v_lon = 39.0458, -76.6413
            v_zoom = 8

        # We need a dynamic key that changes when filters change to force st_folium to re-render,
        # otherwise Leaflet tile layers sometimes vanish to a grey void on in-place update.
        filter_hash = f"{hash(frozenset(selected_county))}_{hash(frozenset(categories))}_{hash(search_query)}"
        map_key = f"property_map_{filter_hash}"


        # Prepare plotting coordinates (with jitter for centroids)
        if not map_df.empty:
            import numpy as np
            map_df = map_df.copy()
            
            # Use real lat/lon if Method is not Level 7
            map_df['_plot_lat'] = map_df[lat_col]
            map_df['_plot_lon'] = map_df[lon_col]
            
            # Tiny jitter only for centroids (Level 7) if the column exists
            if 'Method' in map_df.columns:
                centroid_mask = map_df['Method'].str.contains('Level 7', na=False)
                if centroid_mask.any():
                    # MUST be deterministic to prevent HTML changes & flickering on rerun
                    np.random.seed(42) 
                    map_df.loc[centroid_mask, '_plot_lat'] += np.random.uniform(-0.0005, 0.0005, centroid_mask.sum())
                    map_df.loc[centroid_mask, '_plot_lon'] += np.random.uniform(-0.0005, 0.0005, centroid_mask.sum())

        display_map_df = map_df

        # Create Folium Map
        _commented = commented_keys()

        def create_map(map_data_list, center_lat, center_lon, zoom):
            m = folium.Map(location=[center_lat, center_lon], zoom_start=zoom, control_scale=True)
            if map_data_list:
                from folium.plugins import FastMarkerCluster
                # Keys of properties that already have outreach notes —
                # marker gets a 💬 badge + green color so people see prior
                # outreach at a glance
                commented_js = json.dumps(
                    sorted(c + "|" + a for c, a in _commented))
                callback = """
                function (row) {
                    var COMMENTED = new Set(%s);
                    var key = String(row[4]).toUpperCase().replace(/\\s+/g, ' ')
                        + "|" + String(row[3]).toUpperCase().replace(/\\s+/g, ' ');
                    var hasNote = COMMENTED.has(key);
                    var marker = L.circleMarker(new L.LatLng(row[0], row[1]), {
                        color: hasNote ? '#2ecc71' : '#ff4b4b',
                        fillColor: hasNote ? '#2ecc71' : '#ff4b4b',
                        fillOpacity: 0.7,
                        radius: 5,
                        weight: 1
                    });
                    var popupContent = (hasNote ? "💬 outreach on record<br>" : "")
                        + "<b>Owner:</b> " + row[2] + "<br><b>Address:</b> " + row[3];
                    marker.bindPopup(popupContent, {maxWidth: 300});
                    return marker;
                }
                """ % commented_js
                FastMarkerCluster(map_data_list, callback=callback).add_to(m)
            return m

        # Prepare list for FastMarkerCluster (caching requires simple types)
        if not display_map_df.empty:
             marker_data = display_map_df[['_plot_lat', '_plot_lon', owner_col, addr_col, county_col]].values.tolist()
        else:
             marker_data = []

        # Render map with a patched deterministic UUID generator.
        # This guarantees perfectly identical HTML strings across Streamlit reruns,
        # which prevents st_folium from unmounting and redrawing the iframe (flickering)!
        import uuid
        original_uuid = uuid.uuid4
        counter = [0]
        def deterministic_uuid():
            counter[0] += 1
            return uuid.UUID(int=counter[0])
        uuid.uuid4 = deterministic_uuid
        
        try:
            m = create_map(marker_data, v_lat, v_lon, v_zoom)
    
            # Render Map 
            map_data = st_folium(
                m,
                key=map_key,
                height=500,
                width=700,
                use_container_width=True,
                returned_objects=["bounds"]
            )
        finally:
            uuid.uuid4 = original_uuid

        # Viewport Filtering Logic (Automatic)
        final_filtered_df = filtered_df
        if map_data and map_data.get("bounds"):
            bounds = map_data["bounds"]
            sw = bounds.get("_southWest")
            ne = bounds.get("_northEast")
            
            if sw and ne and sw.get("lat") is not None and ne.get("lat") is not None and sw.get("lng") is not None and ne.get("lng") is not None:
                lat_min, lat_max = sw["lat"], ne["lat"]
                lat_max, lat_min = max(lat_min, lat_max), min(lat_min, lat_max) # Handle inverted bounds
                lon_min, lon_max = sw["lng"], ne["lng"]
                lon_max, lon_min = max(lon_min, lon_max), min(lon_min, lon_max)
                
                # Apply viewport filter to the FULL dataset
                viewport_mask = (
                    (filtered_df[lat_col] >= lat_min) & 
                    (filtered_df[lat_col] <= lat_max) & 
                    (filtered_df[lon_col] >= lon_min) & 
                    (filtered_df[lon_col] <= lon_max)
                )
                final_filtered_df = filtered_df[viewport_mask]
                
                # Show dynamic status
                st.info(f"📍 Viewport active: showing {len(final_filtered_df):,} of {len(filtered_df):,} properties in this area.")

        if filtered_df.empty:
            st.info("No coordinates available for the selected filters.")

        # Data Table
        st.subheader("📋 Property Details")

        # Display table with pretty headers
        display_cols = [owner_col, addr_col, county_col]
        if cat_col in df.columns:
            display_cols.append(cat_col)
        if lat_col in filtered_df.columns and lon_col in filtered_df.columns:
             display_cols += [lat_col, lon_col]
        if method_col in filtered_df.columns:
             display_cols += [method_col]
        
        # Map columns to readable names
        column_config = {
            owner_col: st.column_config.TextColumn("Owner Name"),
            addr_col: st.column_config.TextColumn("Property Address"),
            county_col: st.column_config.TextColumn("County"),
            cat_col: st.column_config.TextColumn("Category"),
            lat_col: st.column_config.NumberColumn("Latitude", format="%.5f"),
            lon_col: st.column_config.NumberColumn("Longitude", format="%.5f"),
            method_col: st.column_config.TextColumn("Geocoding Method")
        }
        
        st.dataframe(
            final_filtered_df[display_cols],
            column_config=column_config,
            hide_index=True,
            use_container_width=True,
            on_select="rerun",
            selection_mode="single-row",
            key="property_table",
        )

        # ------------------------------------------------ Outreach log
        # Row selected in the table above → show its thread (+ form for editors)
        st.subheader("💬 Outreach Log")
        sel = st.session_state.get("property_table", {}).get("selection", {}).get("rows", [])
        store = CommentsStore()

        if not sel:
            st.caption("Select a row in the table above to view and add outreach notes.")
        else:
            row = final_filtered_df.iloc[sel[0]]
            sel_county = str(row[county_col])
            sel_address = str(row[addr_col])
            st.markdown(f"**🏠 {row[owner_col]}** — {sel_address}, {sel_county}")

            if not store.enabled:
                st.info("Comments backend not configured. Set SUPABASE_URL / SUPABASE_ANON_KEY (see README).")
            else:
                try:
                    comments = store.get_comments(sel_county, sel_address)
                except Exception as e:
                    comments = []
                    st.error(f"Could not load notes: {e}")

                if comments:
                    for c in comments:
                        when = (c.get("outreach_date") or "")[:10]
                        otype = c.get("outreach_type") or "note"
                        st.markdown(
                            f"**{c['author']}** · {otype} · {when} · "
                            f"`{(c.get('created_at') or '')[:10]}`\n\n{c['comment']}"
                        )
                        st.markdown("---")
                else:
                    st.caption("No outreach recorded for this address yet.")

                if st.session_state.get("editor_ok"):
                    with st.form("add_note", clear_on_submit=True):
                        c1, c2 = st.columns(2)
                        outreach_type = c1.selectbox("Type", OUTREACH_TYPES)
                        outreach_date = c2.date_input("Date").isoformat()
                        comment = st.text_area("Note — what was said, outcome, next step", height=100)
                        submitted = st.form_submit_button("➕ Add outreach note")
                    if submitted:
                        if not comment.strip():
                            st.warning("Note text is required.")
                        else:
                            try:
                                store.add_comment(
                                    county=sel_county, address=sel_address,
                                    author=st.session_state.get("editor_name", ""),
                                    comment=comment, outreach_type=outreach_type,
                                    outreach_date=outreach_date,
                                )
                                commented_keys.clear()
                                st.success("Note saved.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Save failed: {e}")
                else:
                    st.info("Read-only view. Unlock editor access in the sidebar to add notes.")

        # Refresh Button
        if st.button("🔄 Refresh Data"):
            st.cache_data.clear()
            st.rerun()

except Exception as e:
    st.error(f"Error loading dashboard: {e}")
    st.info("Make sure Hindu_Origin_Owners.xlsx exists in the project root.")

st.markdown("---")
st.caption("Data source: Maryland SDAT | Geocoding: OpenStreetMap (Nominatim)")

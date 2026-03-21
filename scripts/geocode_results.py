import pandas as pd
import time
import re
import os
import argparse
from multiprocessing import Pool, Manager, cpu_count
from geopy.geocoders import Nominatim, ArcGIS
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
from functools import partial
import random

# Surname to Region Mapping for Indian/Hindu Origins
SURNAME_REGION_MAPPING = {
    'punjabi': {
        'surnames': {'SINGH', 'KAUR', 'SHARMA', 'VERMA', 'PATEL', 'GILL', 'SIDHU', 'SANDHU', 'DHILLON', 'BAINS', 'BHULLAR', 'BRAR', 'GREWAL', 'BHATIA', 'CHOPRA', 'GUPTA', 'MALHOTRA', 'MEHTA', 'KAPOOR', 'KHANNA', 'TANDON', 'CHAWLA', 'AWASTHI', 'DUBEY', 'MISHRA', 'TIWARI', 'JOSHI', 'SHUKLA', 'PATHAK'},
        'region': 'Punjab/North India',
        'countries': ['India', 'Pakistan', 'Nepal']
    },
    'gujarati': {
        'surnames': {'PATEL', 'SHAH', 'DESAI', 'MEHTA', 'JAIN', 'PAREKH', 'KOTHARI', 'DAFTARY', 'GANDHI', 'TRIVEDI', 'PANDYA', 'ZAVERI', 'BUCH', 'CHAUHAN', 'SOLANKI', 'VORA', 'LAL', 'PARMAR', 'RATHOD', 'CHHEDA', 'KANANI', 'OZA'},
        'region': 'Gujarat',
        'countries': ['India', 'Kenya', 'Tanzania', 'Uganda', 'UK', 'USA', 'Canada']
    },
    'tamil': {
        'surnames': {'RAJAN', 'NARAYANAN', 'KRISHNAN', 'SUBRAMANIAM', 'SRINIVASAN', 'RAMAN', 'BALAN', 'KUMAR', 'CHANDRAN', 'PRAKASH', 'SHANKAR', 'NATARAJAN', 'SUNDARAM', 'VENKATESH', 'MURTHY', 'IYER', 'IYENGAR', 'PILLAI', 'NAMBIAR', 'MENON', 'RAO', 'REDDY', 'NADAR', 'THEVAR', 'KALYANASUNDARAM', 'CHETTIAR', 'MUDALIAR'},
        'region': 'Tamil Nadu',
        'countries': ['India', 'Sri Lanka', 'Malaysia', 'Singapore']
    },
    'telugu': {
        'surnames': {'REDDY', 'NAIDU', 'RAO', 'PRASAD', 'KUMAR', 'SHARMA', 'CHOWDHARY', 'PATIL', 'JOSHI', 'DESHMUKH', 'KULKARNI', 'GOWDA', 'SHETTY', 'BHAT', 'SHENOY', 'PAI', 'HEGDE', 'AYYANGAR', 'SASTRY', 'SARMA', 'MISHRA', 'PATHAK', 'TRIPATHI', 'UPADHYAY', 'DVIVEDI', 'CHAUBEY'},
        'region': 'Andhra Pradesh/Telangana',
        'countries': ['India', 'USA', 'Malaysia', 'Singapore']
    },
    'bengali': {
        'surnames': {'DAS', 'MUKHERJEE', 'BANERJEE', 'CHATTERJEE', 'GHOSH', 'BOSE', 'PAUL', 'MONDAL', 'SAHA', 'SARKAR', 'ROY', 'CHAKRABORTY', 'SEN', 'GUHA', 'DATTA', 'SINHA', 'DE', 'PAL', 'ADHIKARI', 'MALAKAR', 'KUNDU', 'BHATTACHARYA', 'ROCKEY', 'DEY', 'BISWAS', 'BAG', 'GHATAK', 'HAZRA', 'SANYAL', 'MITRA'},
        'region': 'Bengal (West Bengal/Bangladesh)',
        'countries': ['India', 'Bangladesh']
    },
    'marathi': {
        'surnames': {'PATIL', 'DESHMUKH', 'JOSHI', 'KULKARNI', 'SHARMA', 'SALVE', 'BHOSALE', 'GAIKWAD', 'PAWAR', 'SHINDE', 'JADHAV', 'KAMBLE', 'MHATRE', 'BHOIR', 'THORAT', 'GAWALI', 'MORE', 'SURVE', 'BIRADAR', 'MAHADIK', 'PATANKAR', 'DATE', 'KARMARKAR', 'DIXIT', 'KAPADNIS', 'OAK', 'PHATAK', 'BEDKAR', 'BARVE'},
        'region': 'Maharashtra',
        'countries': ['India']
    },
    'kannada': {
        'surnames': {'RAO', 'SHARMA', 'MURTHY', 'PRASAD', 'KUMAR', 'REDDY', 'NAIDU', 'PILLAI', 'IYER', 'IYENGAR', 'GOWDA', 'SHETTY', 'BHAT', 'PAI', 'HEGDE', 'SHENOY', 'AYYAR', 'ACHAR', 'UPPILI', 'PURI', 'SASTRY', 'SARMA'},
        'region': 'Karnataka',
        'countries': ['India']
    },
    'malayali': {
        'surnames': {'NAIR', 'MENON', 'PILLAI', 'NAMBIAR', 'THOMAS', 'PHILIP', 'MATHEW', 'ABRAHAM', 'GEORGE', 'JOSEPH', 'VARUGHESE', 'PALLOT', 'KOSHY', 'EAPEN', 'CHACKO', 'VARGHESE', 'THAMPY', 'PAI', 'SHENOY', 'BHAT', 'RAO', 'SHARMA', 'IYER', 'REDDY', 'PRASAD', 'KRISHNA', 'RAMAN', 'KUMAR', 'SUBRAMANIAM', 'SANKARAN', 'BALAN', 'CHANDRAN'},
        'region': 'Kerala',
        'countries': ['India', 'UAE', 'Saudi Arabia', 'Qatar', 'Kuwait', 'Bahrain', 'Oman']
    },
    'sindhi': {
        'surnames': {'ADVANI', 'MULCHANDANI', 'HINDUJA', 'THADANI', 'CHANDIRANI', 'KHUBCHANDANI', 'KOHLI', 'SAKHRANI', 'GANWANI', 'MANGHNANI', 'DESIANI', 'GURUMANI', 'LILARAMANI', 'BHAMBHANI', 'KISHNANI', 'MOHTARAMANI', 'DARANANI', 'JASHNANI', 'VASWANI', 'GURNANI', 'TELLANI', 'BHAGWANI', 'HARANI', 'MAHTANI', 'KALWANI', 'ROCHWANI', 'SABHNANI', 'WADHWANI', 'WASWANI', 'MISHRIANI', 'KISHWANI', 'RUPCHANDANI', 'CHOUGHULE', 'GULVANI', 'JASHANANI'},
        'region': 'Sindh (Pakistan/India)',
        'countries': ['India', 'Pakistan', 'UAE', 'UK', 'USA']
    },
    'nepali': {
        'surnames': {'SHARMA', 'SHRESTHA', 'NEUPANE', 'KARKI', 'THAPA', 'GAUTAM', 'KHADKA', 'ADORJEE', 'BASNET', 'PAUDEL', 'DHAKAL', 'BHATTARAI', 'TIMALSINA', 'POUDEL', 'OLI', 'REGMI', 'LAMICHHANE', 'GIRI', 'RAI', 'LIMBU', 'TAMANG', 'SHERPA', 'GURUNG', 'MAGAR', 'THAKURI', 'ACHARYA', 'JOSHI', 'PANT', 'BISTA', 'BHANDARI', 'RAWAL', 'KATWAL', 'MAHARJAN', 'DANGOL', 'RANA', 'SHAH', 'SINGH', 'KAUR', 'HAMAL'},
        'region': 'Nepal',
        'countries': ['Nepal', 'India', 'Bhutan', 'Myanmar']
    },
    'general_indian': {
        'surnames': {'KUMAR', 'SHARMA', 'VERMA', 'GUPTA', 'AGARWAL', 'AGRAWAL', 'JAIN', 'GOEL', 'BANSAL', 'KOCHAR', 'MALHOTRA', 'MEHTA', 'SHAH', 'PRAKASH', 'SONI', 'CHOPRA', 'SAXENA', 'VARMA', 'DUBEY', 'MISHRA', 'TIWARI', 'PATHAK', 'SHUKLA', 'JOSHI', 'RAO', 'NAIR', 'REDDY', 'PATIL', 'DESAI', 'IYER', 'PILLAI', 'MURTHY', 'PRASAD', 'CHANDRA', 'SWAMY', 'BALA', 'LAL', 'DAS', 'ROY', 'SEN', 'MAJUMDAR', 'CHAKRABORTY'},
        'region': 'Multiple Regions',
        'countries': ['India', 'Nepal', 'Bangladesh']
    }
}

# Compile all surnames into a set for fast lookup
ALL_SURNAMES = set()
for group_data in SURNAME_REGION_MAPPING.values():
    ALL_SURNAMES.update(group_data['surnames'])


def extract_surname(owner_name: str) -> str:
    """Extract surname from owner name (handles Indian name formats)"""
    if not owner_name or pd.isna(owner_name):
        return ""

    # Clean up the name
    name = str(owner_name).upper().strip()

    # Remove common suffixes
    name = re.sub(r'\b(ET AL|ET AL\.)\b$', '', name).strip()
    name = re.sub(r'\b(TRUST|LLC|INC|LTD|CORP)\b$', '', name).strip()

    # Handle "LASTNAME Firstname" format (common for Indian names in SDAT)
    parts = name.split()

    if not parts:
        return ""

    # Check if the name starts with a known surname - take that
    for surname in ALL_SURNAMES:
        if name.startswith(surname + ' '):
            return surname

    # Handle joint owners: "PATEL MUKESH & R" -> should extract PATEL
    if '&' in name:
        before_and = name.split('&')[0].strip()
        before_parts = before_and.split()
        if before_parts:
            first_word = before_parts[0]
            if first_word in ALL_SURNAMES:
                return first_word
            return first_word if len(first_word) > 1 else ""

    # Default: get first word (for "PATEL MUKESH" format)
    first_word = parts[0]

    # Skip single letters, initials
    if len(first_word) <= 1 and len(parts) > 1:
        first_word = parts[1]

    # Check if first word is a known surname
    if first_word in ALL_SURNAMES:
        return first_word

    # Try last word
    last_word = parts[-1]
    if len(last_word) > 1 and last_word in ALL_SURNAMES:
        return last_word

    # Return first word as fallback if it's longer than 2 chars
    if len(first_word) > 2:
        return first_word

    # Return last word as final fallback
    return last_word if len(last_word) > 1 else ""


def map_surname_to_origin(surname: str) -> dict:
    """Map surname to origin information (region, countries, confidence)"""
    if not surname:
        return {'region': 'Unknown', 'countries': [], 'confidence': 'None', 'group': 'unknown'}

    surname_upper = surname.upper().strip()

    # Check each group
    for group_name, group_data in SURNAME_REGION_MAPPING.items():
        if surname_upper in group_data['surnames']:
            return {
                'region': group_data['region'],
                'countries': ', '.join(group_data['countries']),
                'confidence': 'High' if group_name != 'general_indian' else 'Medium',
                'group': group_name
            }

    # Not found in specific lists
    return {
        'region': 'Unknown',
        'countries': 'India',
        'confidence': 'Low',
        'group': 'unknown'
    }


def clean_address_for_geocoder(address):
    """Deeply clean SDAT-style address artifacts for maximum geocoder compatibility."""
    if not address or pd.isna(address):
        return ""

    # Standardize to uppercase and strip whitespace
    clean = str(address).upper().strip()

    # 1. CRITICAL: Aggressive Unit/Apartment Stripping
    # Handle cases like "UNIT: 4-307", "UNIT 5", "#101"
    split_patterns = [
        r'\bUNIT[:\s]+\S+', r'\bAPT[:\s]+\S+', r'\b#\s*\S+', r'\bSTE[:\s]+\S+', 
        r'\bSUITE[:\s]+\S+', r'\bOFC[:\s]+\S+', r'\bOFFICE[:\s]+\S+', 
        r'\bSPACE[:\s]+\S+', r'\bBLDG[:\s]+\S+', r'\bBUILDING[:\s]+\S+', 
        r':'
    ]
    for pat in split_patterns:
        clean = re.sub(pat, '', clean, flags=re.IGNORECASE).strip()

    # 2. Map Written Ordinals to Numbers (e.g., TENTH -> 10TH)
    ordinals = {
        r'\bFIRST\b': '1ST', r'\bSECOND\b': '2ND', r'\bTHIRD\b': '3RD', r'\bFOURTH\b': '4TH',
        r'\bFIFTH\b': '5TH', r'\bSIXTH\b': '6TH', r'\bSEVENTH\b': '7TH', r'\bEIGHTH\b': '8TH',
        r'\bNINTH\b': '9TH', r'\bTENTH\b': '10TH', r'\bELEVENTH\b': '11TH', r'\bTWELFTH\b': '12TH'
    }
    for pat, rep in ordinals.items():
        clean = re.sub(pat, rep, clean)

    # 3. Robust Suffix and Directional Mapping
    mappings = {
        r'\bPIK\b': 'PIKE', 
        r'\bBLV\b': 'BOULEVARD', 
        r'\bBLVD\b': 'BOULEVARD',
        r'\bDRW\b': 'DRIVE', r'\bDRE\b': 'DRIVE',
        r'\bPK\b': 'PARKWAY', r'\bPKWY\b': 'PARKWAY', r'\bWY\b': 'WAY', r'\bLN\b': 'LANE',
        r'\bRD\b': 'ROAD', r'\bST\b': 'STREET', r'\bAVE\b': 'AVENUE', r'\bCT\b': 'COURT',
        r'\bPL\b': 'PLACE', r'\bCIR\b': 'CIRCLE', r'\bTER\b': 'TERRACE', r'\bOV\b': 'OVERLOOK',
        r'\bHWY\b': 'HIGHWAY', r'\bRT\b': 'ROUTE', r'\bMD RT\b': 'MD-ROUTE',
    }
    for pattern, replacement in mappings.items():
        clean = re.sub(pattern, replacement, clean)

    # 4. Standardize Cardinal Directions (Expand for geocoder)
    dirs = {
        r'\bW\b': 'WEST', r'\bE\b': 'EAST', r'\bN\b': 'NORTH', r'\bS\b': 'SOUTH',
        r'\bNW\b': 'NORTHWEST', r'\bNE\b': 'NORTHEAST', r'\bSW\b': 'SOUTHWEST', r'\bSE\b': 'SOUTHEAST'
    }
    for pat, rep in dirs.items():
        clean = re.sub(pat, rep, clean)

    return clean.strip()


def normalize_for_match(text):
    """Normalize text for consistent lookup matching."""
    if not text or pd.isna(text):
        return ""
    return str(text).upper().strip()


def geocode_single_row(task_data):
    """
    Geocode a single row of data.
    Returns a dict with the results.
    """
    idx, row, owner_col, addr_col, county_col, city_col, zip_col, worker_id, lookup_dict, provider = task_data

    owner_name = str(row[owner_col])
    raw_addr = str(row[addr_col])
    raw_county = str(row[county_col]).replace(" COUNTY", "").replace(" County", "").strip()
    raw_city = str(row[city_col]).strip() if city_col and pd.notnull(row[city_col]) else ""
    raw_zip = str(row[zip_col]).strip().split('-')[0] if zip_col and pd.notnull(row[zip_col]) else ""

    # Extract surname and map to origin
    surname = extract_surname(owner_name)
    origin_info = map_surname_to_origin(surname)

    # 1. Try Lookup first
    lookup_key = (normalize_for_match(raw_addr), normalize_for_match(raw_county))
    if lookup_dict and lookup_key in lookup_dict:
        cached = lookup_dict[lookup_key]
        return {
            'idx': idx,
            'owner_name': owner_name,
            'surname': surname,
            'region': origin_info['region'],
            'countries': origin_info['countries'],
            'origin_confidence': origin_info['confidence'],
            'origin_group': origin_info['group'],
            'latitude': cached['lat'],
            'longitude': cached['lon'],
            'geo_address': cached.get('address', 'Fetched from lookup'),
            'hit_method': 'Lookup',
            'raw_addr': raw_addr
        }

    cleaned = clean_address_for_geocoder(raw_addr)

    # Build queries - more specific to less specific
    queries = []
    
    # Level 0: Full Specific (Address, City, Zip)
    if raw_city and raw_zip:
        queries.append((f"{cleaned}, {raw_city}, Maryland, {raw_zip}, USA", "Level 0: Cleaned-City-Zip"))
    
    # Level 1: Address + Zip
    if raw_zip:
        queries.append((f"{cleaned}, Maryland, {raw_zip}, USA", "Level 1: Cleaned-Zip"))

    # Level 2: Address + City
    if raw_city:
        queries.append((f"{cleaned}, {raw_city}, Maryland, USA", "Level 2: Cleaned-City"))

    # Level 3: Original SDAT format
    queries.append((f"{raw_addr}, {raw_county} County, Maryland, USA", "Level 3: Direct-County"))
    
    # Level 4: Cleaned + County
    queries.append((f"{cleaned}, {raw_county} County, Maryland, USA", "Level 4: Cleaned-County"))

    # Level 5: Direction-Agnostic
    no_dir = re.sub(r'\b(NORTH|SOUTH|EAST|WEST|NORTHWEST|NORTHEAST|SOUTHWEST|SOUTHEAST)\b', '', cleaned).strip()
    if no_dir != cleaned:
        queries.append((f"{no_dir}, {raw_county} County, Maryland, USA", "Level 5: Agnostic"))

    # Level 6: Street-Only
    parts = cleaned.split()
    if len(parts) > 1 and parts[0][0].isdigit():
        street_only = " ".join(parts[1:])
        queries.append((f"{street_only}, {raw_city if raw_city else raw_county}, Maryland, USA", "Level 6: StreetOnly"))

    # Level 7: County Centroid (Absolute fallback)
    queries.append((f"{raw_county} County, Maryland, USA", "Level 7: County Centroid"))

    # Create geolocator based on provider
    user_agents = [
        f"maryland_property_explorer_{random.randint(1000,9999)}",
        f"md_prop_research_{random.randint(1000,9999)}",
        f"county_mapper_v2_{random.randint(10,99)}",
        f"property_lookup_tool_{random.randint(100,999)}"
    ]
    ua = random.choice(user_agents)
    
    if provider.lower() == 'arcgis':
        geolocator = ArcGIS(timeout=10)
    else:
        geolocator = Nominatim(user_agent=ua, timeout=10)

    location = None
    hit_method = ""
    retry_count = 0
    max_retries = 2

    for q_str, method in queries:
        try:
            # ArcGIS is faster, but Nominatim needs conservative delay
            delay = 0.5 if provider.lower() == 'nominatim' else 0.1
            time.sleep(delay + random.uniform(0, 0.1))
            
            location = geolocator.geocode(q_str)
            if location and ("Maryland" in location.address or "MD" in location.address):
                hit_method = method
                break
        except Exception:
            time.sleep(0.5)
            continue

    result = {
        'idx': idx,
        'owner_name': owner_name,
        'surname': surname,
        'region': origin_info['region'],
        'countries': origin_info['countries'],
        'origin_confidence': origin_info['confidence'],
        'origin_group': origin_info['group'],
        'latitude': location.latitude if location else None,
        'longitude': location.longitude if location else None,
        'geo_address': location.address if location else None,
        'hit_method': hit_method if location else 'Failed',
        'raw_addr': raw_addr
    }

    return result


def geocode_hindu_owners(input_file='data/Hindu_Origin_Owners.xlsx',
                         output_file='data/Hindu_Origin_Owners_Mapped.xlsx',
                         workers=4,
                         lookup_file=None,
                         force_redo_centroids=False,
                         provider='nominatim'):
    """
    Geocode Hindu owners with parallel processing.
    """
    print("=" * 70)
    print("🕉️  HINDU OWNER GEOCODING AND ORIGIN MAPPING")
    print("=" * 70)
    print(f"\n📖 Reading {input_file}...")

    if not os.path.exists(input_file):
        print(f"❌ Error: {input_file} not found!")
        return

    df = pd.read_excel(input_file)
    print(f"📊 Total rows in input: {len(df)}")
    print(f"📋 Columns: {list(df.columns)}")

    # Detect column names - support multiple formats
    owner_col = None
    addr_col = None
    county_col = None
    city_col = None
    zip_col = None

    for col in df.columns:
        col_lower = col.lower().strip()
        if 'owner' in col_lower and 'name' in col_lower:
            owner_col = col
        elif col_lower == 'address' or col_lower == 'addr':
            addr_col = col
        elif col_lower == 'county':
            county_col = col
        elif col_lower == 'city':
            city_col = col
        elif 'zip' in col_lower:
            zip_col = col

    print(f"🔍 Detected columns: owner={owner_col}, address={addr_col}, county={county_col}, city={city_col}, zip={zip_col}")

    if not all([owner_col, addr_col, county_col]):
        print(f"❌ Could not detect required columns!")
        return

    # Add geocoding columns
    for col in ['Latitude', 'Longitude', 'Surname', 'Region', 'Countries',
                'Origin_Confidence', 'Origin_Group', 'Geo_Address']:
        if col not in df.columns:
            df[col] = None

    # Load existing progress if any
    if os.path.exists(output_file):
        print(f"🔄 Loading existing progress from {output_file}...")
        try:
            df_existing = pd.read_excel(output_file)
            for _, row in df_existing.iterrows():
                mask = (df[owner_col] == row[owner_col]) & \
                       (df[addr_col] == row[addr_col]) & \
                       (df[county_col].str.replace(" COUNTY", "", case=False) == str(row.get(county_col, "")).replace(" COUNTY", "").replace(" County", ""))
                if mask.any():
                    if pd.notnull(row.get('Latitude')):
                        df.loc[mask, 'Latitude'] = row['Latitude']
                        df.loc[mask, 'Longitude'] = row['Longitude']
                    if pd.notnull(row.get('Surname')):
                        df.loc[mask, 'Surname'] = row['Surname']
                        df.loc[mask, 'Region'] = row['Region']
                        df.loc[mask, 'Countries'] = row['Countries']
                        df.loc[mask, 'Origin_Confidence'] = row['Origin_Confidence']
                        df.loc[mask, 'Origin_Group'] = row['Origin_Group']
                    if pd.notnull(row.get('Geo_Address')):
                        df.loc[mask, 'Geo_Address'] = row['Geo_Address']
            print(f"✅ Progress loaded")
        except Exception as e:
            print(f"⚠️ Could not load existing progress: {e}")

    # Load lookup data if provided
    lookup_data = {}
    if lookup_file and os.path.exists(lookup_file):
        print(f"🔍 Loading lookup cache from {lookup_file}...")
        try:
            df_lookup = pd.read_excel(lookup_file)
            # Find lat/lon/addr columns in lookup
            l_lat, l_lon, l_addr, l_county = None, None, None, None
            for col in df_lookup.columns:
                cl = col.lower()
                if 'lat' in cl: l_lat = col
                if 'lon' in cl or 'long' in cl: l_lon = col
                if cl == 'address' or cl == 'addr': l_addr = col
                if cl == 'county': l_county = col

            if all([l_lat, l_lon, l_addr, l_county]):
                for _, row in df_lookup.iterrows():
                    if pd.notnull(row[l_lat]) and pd.notnull(row[l_lon]):
                        key = (normalize_for_match(row[l_addr]), normalize_for_match(row[l_county]))
                        lookup_data[key] = {
                            'lat': row[l_lat],
                            'lon': row[l_lon],
                            'address': row.get('Geo_Address', 'Lookup Match')
                        }
                print(f"✅ Loaded {len(lookup_data)} coordinates from cache")
            else:
                print(f"⚠️ Lookup file missing required columns (lat, lon, address, county)")
        except Exception as e:
            print(f"⚠️ Could not load lookup file: {e}")

    # Find rows to process
    if force_redo_centroids:
        # Ensure Geo_Address is string to avoid .str accessor errors
        if 'Geo_Address' in df.columns:
            df['Geo_Address'] = df['Geo_Address'].fillna('').astype(str)
        
        # Legacy or Surname Pattern means it needs geocoding metadata
        is_legacy = (df['Method'] == 'Surname Pattern') | (df['Method'] == 'Failed') if 'Method' in df.columns else pd.Series(False, index=df.index)
        
        is_centroid = (
            (df['Geo_Address'].str.count(',') < 3) & 
            df['Geo_Address'].str.contains('Maryland', na=False)
        ) | (
            # Known Montgomery Centroid
            (df['Latitude'].round(4) == 39.1312) | (df['Latitude'].round(4) == 39.1550)
        )
        
        to_process_indices = df[(df['Latitude'].isnull()) | is_centroid | is_legacy].index.tolist()
        print(f"🔄 Force mode: Identified {len(to_process_indices)} rows to (re)process (including legacy metadata checks)")
    else:
        to_process_indices = df[df['Latitude'].isnull()].index.tolist()
    
    print(f"📍 Need to geocode: {len(to_process_indices)} rows")
    print(f"🔧 Using {workers} workers")

    if not to_process_indices:
        print("✅ All rows already geocoded!")
        df.to_excel(output_file, index=False)
        return

    start_time = time.time()
    success_count = 0

    # Prepare tasks for workers
    tasks = []
    for i, idx in enumerate(to_process_indices):
        # In force mode, we skip the lookup_data for these specific rows to ensure fresh API geocoding
        row_lookup = {} if force_redo_centroids else lookup_data
        
        tasks.append((
            idx, df.loc[idx], owner_col, addr_col, county_col, 
            city_col, zip_col, i % workers, row_lookup, provider
        ))

    # Process in batches
    batch_size = 20
    total_batches = (len(tasks) + batch_size - 1) // batch_size

    for batch_num in range(total_batches):
        batch_start = batch_num * batch_size
        batch_end = min(batch_start + batch_size, len(tasks))
        batch_tasks = tasks[batch_start:batch_end]

        print(f"\n📦 Processing batch {batch_num + 1}/{total_batches} (rows {batch_start + 1}-{batch_end})...")

        try:
            with Pool(workers) as pool:
                results = pool.map(geocode_single_row, batch_tasks)

            # Update DataFrame with results
            for result in results:
                idx = result['idx']
                df.loc[idx, 'Surname'] = result['surname']
                df.loc[idx, 'Region'] = result['region']
                df.loc[idx, 'Countries'] = result['countries']
                df.loc[idx, 'Origin_Confidence'] = result['origin_confidence']
                df.loc[idx, 'Origin_Group'] = result['origin_group']

                if result['latitude']:
                    df.loc[idx, 'Latitude'] = result['latitude']
                    df.loc[idx, 'Longitude'] = result['longitude']
                    df.loc[idx, 'Geo_Address'] = result['geo_address']
                    df.loc[idx, 'Method'] = result['hit_method']
                    success_count += 1
                else:
                    # If it failed to geocode, we still want to know
                    df.loc[idx, 'Method'] = 'Failed'

                # Print progress
                if result['latitude']:
                    elapsed = time.time() - start_time
                    rate = success_count / elapsed if elapsed > 0 else 0
                    eta = (len(to_process_indices) - batch_end) / rate if rate > 0 else 0
                    print(f"  ✅ [{batch_start + 1 + len([r for r in results if r['idx'] == idx])}/{len(to_process_indices)}] "
                          f"{result['hit_method']}: {result['owner_name'][:25]:25} | "
                          f"{result['surname']:15} | {result['region']:25} | "
                          f"{rate:.2f}/s | ETA: {eta/60:.1f}m")

            # Save progress after each batch
            df.to_excel(output_file, index=False)
            print(f"💾 Progress saved ({batch_end}/{len(to_process_indices)})")

        except Exception as e:
            print(f"❌ Error in batch: {e}")
            df.to_excel(output_file, index=False)
            raise

    # Final save
    df.to_excel(output_file, index=False)
    elapsed = time.time() - start_time
    print(f"\n✨ DONE!")
    print(f"   Geocoded: {success_count}/{len(to_process_indices)} addresses")
    print(f"   Time: {elapsed/60:.1f} minutes")
    print(f"   Rate: {success_count/elapsed:.2f} addresses/second")
    print(f"📁 Final output: {output_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='Geocode Hindu owners with origin mapping using parallel processing',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single process geocoding
  python geocode_results.py data/Radius_5mi_Hindu_Owners.xlsx -o data/Hindu_origin_Owners_Mapped.xlsx -w 1

  # Multi-process geocoding (4 workers)
  python geocode_results.py data/Radius_5mi_Hindu_Owners.xlsx -o data/Hindu_origin_Owners_Mapped.xlsx -w 4

  # Maximum parallelism
  python geocode_results.py data/Radius_5mi_Hindu_Owners.xlsx -o data/Hindu_origin_Owners_Mapped.xlsx -w 8
        """
    )
    parser.add_argument('input_file', help='Input Excel file with owner data')
    parser.add_argument('-o', '--output', default='data/Hindu_origin_Owners_Mapped.xlsx',
                        help='Output Excel file (default: data/Hindu_origin_Owners_Mapped.xlsx)')
    parser.add_argument('-w', '--workers', type=int, default=4,
                        help='Number of parallel workers (default: 4, max: 50)')
    parser.add_argument('-l', '--lookup', default='data/Hindu_Origin_Owners_Mapped.xlsx',
                        help='Existing mapped file to use as coordinate lookup (default: data/Hindu_Origin_Owners_Mapped.xlsx)')
    parser.add_argument('--force', action='store_true',
                        help='Force re-geocoding of addresses that only hit county centroids')
    parser.add_argument('--provider', default='nominatim', choices=['nominatim', 'arcgis'],
                        help='Geocoding provider (nominatim or arcgis, default: nominatim)')

    args = parser.parse_args()

    # Allow up to 50 workers
    num_workers = min(args.workers, 50)
    print(f"Using {num_workers} workers for parallel geocoding with {args.provider}\n")

    geocode_hindu_owners(args.input_file, args.output, workers=num_workers, 
                         lookup_file=args.lookup, 
                         force_redo_centroids=args.force,
                         provider=args.provider)

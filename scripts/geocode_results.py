import pandas as pd
import time
import re
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
import os

def clean_address_for_geocoder(address):
    """Deeply clean SDAT-style address artifacts for maximum geocoder compatibility."""
    if not address or pd.isna(address): return ""
    
    # Standardize to uppercase and strip whitespace
    clean = str(address).upper().strip()
    
    # 1. CRITICAL: Aggressive Unit/Apartment Stripping
    # These artifacts (like TR 19 519) completely break Nominatim
    # We find the first occurrence of these keywords and chop everything after
    split_patterns = [r'\bUNIT\b', r'\bAPT\b', r'\b#\b', r'\bSTE\b', r'\bSUITE\b', r'\bOFC\b', r'\bOFFICE\b', r'\bSPACE\b', r'\bBLDG\b', r'\bBUILDING\b', r':']
    for pat in split_patterns:
        clean = re.split(pat, clean, flags=re.IGNORECASE)[0].strip()
    
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
        r'\bPIK\b': 'PIKE', r'\bBLV\b': 'BLVD', r'\bDRW\b': 'DRIVE', r'\bDRE\b': 'DRIVE',
        r'\bPK\b': 'PARKWAY', r'\bPKWY\b': 'PARKWAY', r'\bWY\b': 'WAY', r'\bLN\b': 'LANE', 
        r'\bRD\b': 'ROAD', r'\bST\b': 'STREET', r'\bAVE\b': 'AVENUE', r'\bCT\b': 'COURT', 
        r'\bPL\b': 'PLACE', r'\bCIR\b': 'CIRCLE', r'\bTER\b': 'TERRACE', r'\bOV\b': 'OVERLOOK',
        r'\bHWY\b': 'HIGHWAY', r'\bBLVD\b': 'BOULEVARD', r'\bRT\b': 'ROUTE', r'\bMD RT\b': 'MD-ROUTE',
    }
    for pattern, replacement in mappings.items():
        clean = re.sub(pattern, replacement, clean)
    
    # 4. Standardize Cardinal Directions
    dirs = {r'\bW\b': 'WEST', r'\bE\b': 'EAST', r'\bN\b': 'NORTH', r'\bS\b': 'SOUTH'}
    for pat, rep in dirs.items():
        clean = re.sub(pat, rep, clean)

    return clean.strip()

def geocode_hindu_owners(input_file='data/Hindu_Origin_Owners.xlsx', output_file='data/Hindu_Origin_Owners_Mapped.xlsx'):
    print(f"📖 Reading {input_file}...")
    if not os.path.exists(input_file):
        print(f"❌ Error: {input_file} not found!")
        return

    df = pd.read_excel(input_file)
    if 'latitude' not in df.columns: df['latitude'] = None
    if 'longitude' not in df.columns: df['longitude'] = None

    if os.path.exists(output_file):
        print(f"🔄 Loading existing progress...")
        try:
            df_existing = pd.read_excel(output_file)
            for _, row in df_existing.iterrows():
                if pd.notnull(row['latitude']):
                    mask = (df['owner_name'] == row['owner_name']) & (df['address'] == row['address'])
                    df.loc[mask, 'latitude'] = row['latitude']
                    df.loc[mask, 'longitude'] = row['longitude']
        except Exception: pass

    geolocator = Nominatim(user_agent="maryland_property_zero_fail_v1")
    to_process = df[df['latitude'].isnull()].index.tolist()
    print(f"📍 Need to geocode: {len(to_process)}")

    success_count = 0
    
    for idx in to_process:
        raw_addr = str(df.loc[idx, 'address'])
        raw_county = str(df.loc[idx, 'county']).replace(" County", "")
        
        cleaned = clean_address_for_geocoder(raw_addr)
        
        # 7-STAGE PROGRESSIVE SEARCH STRATEGY
        queries = [
            (f"{raw_addr}, {raw_county} County, Maryland, USA", "Level 0: Direct"),
            (f"{cleaned}, {raw_county} County, Maryland, USA", "Level 1: Cleaned"),
            (f"{cleaned}, Maryland, USA", "Level 2: Statewide"),
        ]
        
        # Level 3: Direction-Agnostic
        no_dir = re.sub(r'\b(NORTH|SOUTH|EAST|WEST|NORTHWEST|NORTHEAST|SOUTHWEST|SOUTHEAST)\b', '', cleaned).strip()
        if no_dir != cleaned:
            queries.append((f"{no_dir}, {raw_county} County, Maryland, USA", "Level 3: Agnostic"))
        
        # Level 4: Street-Only (Remove house number)
        # e.g. "1234 MAIN ST" -> "MAIN ST"
        parts = cleaned.split()
        if len(parts) > 1 and parts[0][0].isdigit():
            street_only = " ".join(parts[1:])
            queries.append((f"{street_only}, {raw_county} County, Maryland, USA", "Level 4: StreetOnly"))
        
        # Level 5: County Centroid Fallback (Safety Net)
        queries.append((f"{raw_county} County, Maryland, USA", "Level 5: County Centroid"))

        location = None
        hit_method = ""

        # Recursive query execution
        for q_str, method in queries:
            try:
                time.sleep(1.2)
                location = geolocator.geocode(q_str, timeout=10)
                if location and ("Maryland" in location.address or "MD" in location.address):
                    hit_method = method
                    break
            except Exception:
                time.sleep(2)
                continue

        if location:
            df.loc[idx, 'latitude'] = location.latitude
            df.loc[idx, 'longitude'] = location.longitude
            success_count += 1
            print(f"✅ [{success_count}] {hit_method}: {raw_addr} -> {location.latitude}")
        else:
            print(f"❌ Total Failure: {raw_addr}")

        if success_count % 20 == 0:
            df.to_excel(output_file, index=False)

    df.to_excel(output_file, index=False)
    print(f"\n✨ DONE. Final mapping saved to {output_file}")

if __name__ == "__main__":
    geocode_hindu_owners()

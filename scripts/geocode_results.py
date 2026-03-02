import pandas as pd
import time
import re
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
import os

def geocode_hindu_owners(input_file='data/Hindu_Origin_Owners.xlsx', output_file='data/Hindu_Origin_Owners_Mapped.xlsx'):
    print(f"📖 Reading {input_file}...")
    if not os.path.exists(input_file):
        print(f"❌ Error: {input_file} not found!")
        return

    df = pd.read_excel(input_file)
    
    # Initialize coordinates if not present
    if 'latitude' not in df.columns:
        df['latitude'] = None
    if 'longitude' not in df.columns:
        df['longitude'] = None

    # Load existing progress if available
    if os.path.exists(output_file):
        print(f"🔄 Loading existing progress from {output_file}...")
        df_existing = pd.read_excel(output_file)
        # Update df with existing coordinates where available
        for idx, row in df_existing.iterrows():
            if pd.notnull(row['latitude']):
                mask = (df['owner_name'] == row['owner_name']) & (df['address'] == row['address'])
                df.loc[mask, 'latitude'] = row['latitude']
                df.loc[mask, 'longitude'] = row['longitude']

    geolocator = Nominatim(user_agent="maryland_property_mapper")
    
    total = len(df)
    to_process = df[df['latitude'].isnull()].index.tolist()
    print(f"📊 Total records: {total}")
    print(f"📍 Records needing geocoding: {len(to_process)}")

    count = 0
    start_time = time.time()

    for idx in to_process:
        address = str(df.loc[idx, 'address'])
        county = str(df.loc[idx, 'county'])
        
        # Format address for Nominatim
        full_query = f"{address}, {county} County, Maryland, USA"
        
        try:
            # Nominatim policy: max 1 request per second
            time.sleep(1.1) 
            location = geolocator.geocode(full_query, timeout=10)
            
            if not location:
                # Try more aggressive cleaning for Nominatim
                # 1. Map common SDAT typos/shortenings
                clean = address.upper()
                clean = re.sub(r'\bBLV\b', 'BLVD', clean)
                clean = re.sub(r'\bDRW\b', 'DR', clean)
                clean = re.sub(r'\bDRE\b', 'DR', clean)
                
                # 2. Strip UNIT/APT and everything after
                clean = re.split(r' UNIT| APT|#|STE| SUITE| OFC| OFFICE', clean, flags=re.IGNORECASE)[0].strip()
                
                if clean != address.upper():
                    retry_query = f"{clean}, {county} County, Maryland, USA"
                    location = geolocator.geocode(retry_query, timeout=10)
            
            if location:
                df.loc[idx, 'latitude'] = location.latitude
                df.loc[idx, 'longitude'] = location.longitude
                print(f"✅ [{count+1}/{len(to_process)}] Found: {address} -> ({location.latitude}, {location.longitude})")
            else:
                # Try a broader search if specific address fails
                broad_query = f"{address}, Maryland, USA"
                location = geolocator.geocode(broad_query, timeout=10)
                if location:
                    df.loc[idx, 'latitude'] = location.latitude
                    df.loc[idx, 'longitude'] = location.longitude
                    print(f"⚠️  [{count+1}/{len(to_process)}] broad match: {address}")
                else:
                    print(f"❌ [{count+1}/{len(to_process)}] Not found: {address}")
            
            count += 1
            
            # Save every 50 records
            if count % 50 == 0:
                print(f"💾 Saving progress... ({count} geocoded)")
                df.to_excel(output_file, index=False)
                
        except (GeocoderTimedOut, GeocoderServiceError) as e:
            print(f"⏳ Timeout/Service error at {address}: {e}")
            time.sleep(2)
        except Exception as e:
            print(f"❌ Unexpected error at {address}: {e}")

    # Final save
    df.to_excel(output_file, index=False)
    print(f"\n✨ Geocoding complete! Total geocoded: {count}")
    print(f"📂 Saved to {output_file}")

if __name__ == "__main__":
    geocode_hindu_owners()

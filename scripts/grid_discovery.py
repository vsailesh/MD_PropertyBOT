#!/usr/bin/env python3
"""
Grid-based Street Discovery for Maryland
Systematically sweeps a bounding box to find every unique street name in OSM.
"""

import os
import sys
import time
import pandas as pd
import requests
import sqlite3
from typing import Set, List, Dict

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.county_mapper import CountyMapper
from src.street_name_cleaner import StreetNameCleaner

def fetch_grid_streets(lat_min, lat_max, lon_min, lon_max, step=0.1):
    url = "https://overpass-api.de/api/interpreter"
    session = requests.Session()
    session.headers.update({'User-Agent': 'MD-Property-Discovery/1.0'})
    
    cleaner = StreetNameCleaner()
    mapper = CountyMapper()
    
    # Load known streets to avoid reporting them as "new"
    known_streets = set()
    if os.path.exists("data/MD_street_Names.xlsx"):
        df = pd.read_excel("data/MD_street_Names.xlsx")
        for _, row in df.iterrows():
            known_streets.add((str(row['Address']).upper().strip(), str(row['county']).upper().strip()))
    
    new_streets = []
    
    # Grid loop
    lat = lat_min
    while lat < lat_max:
        lon = lon_min
        while lon < lon_max:
            print(f"📡 Searching grid point: {lat:.2f}, {lon:.2f}")
            
            # Radius approx 8km (5 miles) to cover the 0.1 degree gap
            query = f"""
            [out:json][timeout:90];
            (
              way(around:8000,{lat},{lon})["highway"]["name"];
              node(around:8000,{lat},{lon})["addr:street"];
              way(around:8000,{lat},{lon})["addr:street"];
            );
            out tags center;
            """
            
            try:
                resp = session.post(url, data={'data': query}, timeout=100)
                if resp.status_code == 429:
                    print("🚦 Rate limit hit. Waiting 60s...")
                    time.sleep(60)
                    continue
                
                if resp.status_code == 200:
                    data = resp.json()
                    elements = data.get('elements', [])
                    
                    batch_new = 0
                    for el in elements:
                        tags = el.get('tags', {})
                        name = tags.get('addr:street') or tags.get('name')
                        if not name: continue
                        
                        center = el.get('center') or {'lat': el.get('lat'), 'lon': el.get('lon')}
                        if not center.get('lat'): continue
                        
                        cleaned = cleaner.clean_street_name(name.upper())
                        if not cleaned or len(cleaned) < 3: continue
                        
                        # Note: We aren't doing point-in-polygon here to save speed during discovery,
                        # but we can infer county later or do it in batches.
                        # For now, let's just collect raw candidates and their locations.
                        new_streets.append({
                            'name': cleaned,
                            'lat': center['lat'],
                            'lon': center['lon']
                        })
                        batch_new += 1
                    
                    print(f"   Success: Found {batch_new} street candidates at this point.")
                else:
                    print(f"   Error: {resp.status_code}")
                    
            except Exception as e:
                print(f"   Exception: {e}")
            
            lon += step
            time.sleep(2) # Politeness
            
        lat += step
        
    return new_streets

def main():
    # Test grid: Montgomery County area
    # Lat: 39.0 to 39.3, Lon: -77.3 to -77.0
    print("🚀 Starting discovery grid search...")
    candidates = fetch_grid_streets(39.0, 39.3, -77.3, -77.0, step=0.05)
    
    if candidates:
        df = pd.DataFrame(candidates)
        df.drop_duplicates(subset=['name'], inplace=True)
        df.to_csv("data/new_street_candidates.csv", index=False)
        print(f"✅ Discovery complete. Saved {len(df)} candidates to data/new_street_candidates.csv")
    else:
        print("🏁 No new candidates found.")

if __name__ == "__main__":
    main()

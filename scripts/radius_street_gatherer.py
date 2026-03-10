#!/usr/bin/env python3
"""
Radius Street Gatherer for Maryland SDAT (Exhaustive)

Fetches all streets within a specified radius or systematically across counties.
Integrates with property_search.db to avoid redundant searches.
"""

import pandas as pd
import requests
import json
import os
import sys
import argparse
import sqlite3
import time
from shapely.geometry import shape, Point
from typing import List, Dict, Optional, Set

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.county_mapper import CountyMapper
from src.street_name_cleaner import StreetNameCleaner
from src.community_pipeline import SDATFormatter


class RadiusStreetGatherer:
    """
    Fetches streets within a radius and assigns accurate counties.
    """

    def __init__(self, 
                 geojson_path: str = "data/maryland-counties.geojson",
                 db_path: str = "data/property_search.db",
                 lookup_path: str = "data/MD_street_Names.xlsx"):
        """
        Initialize the gatherer.
        """
        self.geojson_path = geojson_path
        self.db_path = db_path
        self.lookup_path = lookup_path
        self.polygons = []
        self.county_mapper = CountyMapper()
        self.street_cleaner = StreetNameCleaner()
        self.searched_streets: Set[tuple] = set() # (street, county)

        # Load data on init
        self._load_polygons()
        self._load_searched_streets()

    def _load_polygons(self) -> bool:
        """Load county polygons from GeoJSON."""
        if not os.path.exists(self.geojson_path):
            print(f"❌ Error: {self.geojson_path} not found.")
            return False

        print(f"📖 Loading county boundaries from {self.geojson_path}...")
        try:
            with open(self.geojson_path, 'r') as f:
                data = json.load(f)

            for feature in data.get('features', []):
                geojson_name = feature['properties'].get('name', '')
                sdat_county = self.county_mapper.normalize(geojson_name)
                if sdat_county:
                    self.polygons.append({
                        'geojson_name': geojson_name,
                        'sdat_name': sdat_county,
                        'shape': shape(feature['geometry'])
                    })

            print(f"✅ Loaded {len(self.polygons)} county boundaries")
            return True
        except Exception as e:
            print(f"❌ Error loading GeoJSON: {e}")
            return False

    def _load_searched_streets(self):
        """Load already searched streets from DB and lookup file."""
        # 1. From DB
        if os.path.exists(self.db_path):
            print(f"🗄️ Loading searched streets from {self.db_path}...")
            try:
                conn = sqlite3.connect(self.db_path)
                query = "SELECT DISTINCT street_name, county FROM properties"
                df_db = pd.read_sql_query(query, conn)
                conn.close()
                for _, row in df_db.iterrows():
                    s = str(row['street_name']).upper().strip()
                    c = str(row['county']).upper().strip()
                    self.searched_streets.add((s, c))
                print(f"   Fetched {len(df_db)} unique searched streets from Database")
            except Exception as e:
                print(f"⚠️ Warning: Could not read DB: {e}")

        # 2. From Existing Lookup
        if os.path.exists(self.lookup_path):
            print(f"📖 Loading streets from existing lookup {self.lookup_path}...")
            try:
                df_look = pd.read_excel(self.lookup_path)
                count = 0
                for _, row in df_look.iterrows():
                    s = str(row['Address']).upper().strip()
                    c = str(row['county']).upper().strip()
                    if (s, c) not in self.searched_streets:
                        # We don't necessarily want to "skip" them if they are in lookup but NOT in DB,
                        # but the user said "read property_search.db for already existing data", 
                        # so we treat BOTH as "known".
                        self.searched_streets.add((s, c))
                        count += 1
                print(f"   Matched {count} additional streets from Lookup file")
            except Exception as e:
                print(f"⚠️ Warning: Could not read lookup file: {e}")

    def get_county_for_point(self, lat: float, lon: float) -> Optional[str]:
        """Determine which SDAT county contains the given point."""
        point = Point(lon, lat)
        for poly in self.polygons:
            if poly['shape'].contains(point):
                return poly['sdat_name']
        return None

    def get_closest_county(self, lat: float, lon: float) -> Optional[str]:
        """Find the closest county if point is outside all polygons."""
        point = Point(lon, lat)
        closest = None
        min_distance = float('inf')
        for poly in self.polygons:
            distance = poly['shape'].distance(point)
            if distance < min_distance:
                min_distance = distance
                closest = poly['sdat_name']
        return closest

    def fetch_streets(self, lat: float, lon: float, 
                     radius_meters: float = 8046.72,
                     target_county: Optional[str] = None) -> List[Dict]:
        """Fetch streets from Overpass within radius."""
        highway_types = ["unclassified", "residential", "tertiary", "secondary", "primary", "living_street", "service"]
        highway_filter = "|".join(highway_types)
        
        query = f"""
        [out:json][timeout:180];
        (
          way(around:{radius_meters},{lat},{lon})[highway~"^({highway_filter})$"]["name"];
        );
        out center;
        """

        url = "https://overpass-api.de/api/interpreter"
        try:
            response = requests.post(url, data={'data': query}, timeout=180)
            response.raise_for_status()
            raw_elements = response.json().get('elements', [])
        except Exception as e:
            print(f"❌ Overpass Error: {e}")
            return []

        results = []
        for element in raw_elements:
            name = element.get('tags', {}).get('name')
            center = element.get('center')
            if not name or not center: continue
            
            cleaned_name = self.street_cleaner.clean_street_name(name.upper())
            if not cleaned_name: continue
            
            county = self.get_county_for_point(center['lat'], center['lon']) or self.get_closest_county(center['lat'], center['lon'])
            
            if target_county and county != target_county:
                continue 

            if (cleaned_name, county) not in self.searched_streets:
                results.append({
                    'Address': cleaned_name,
                    'county': county,
                    'lat': center['lat'],
                    'lon': center['lon']
                })
                # Add to set so we don't pick it up again in the same run/loop
                self.searched_streets.add((cleaned_name, county)) 

        return results

    def bulk_expand_all(self, step_km: int = 5):
        """
        Systematically search each county from center to borders.
        """
        all_new_streets = []
        
        # Sort polygons to have a consistent order
        sorted_polys = sorted(self.polygons, key=lambda x: x['sdat_name'])
        
        for poly_info in sorted_polys:
            county = poly_info['sdat_name']
            shape_obj = poly_info['shape']
            centroid = shape_obj.centroid
            
            # Calculate approx radius needed to cover the county
            minx, miny, maxx, maxy = shape_obj.bounds
            points = [Point(minx, miny), Point(minx, maxy), Point(maxx, miny), Point(maxx, maxy)]
            max_dist_deg = max(p.distance(centroid) for p in points)
            max_radius_m = max_dist_deg * 111000 
            
            print(f"\n🚀 Starting expansion for {county}")
            print(f"   Center: {centroid.y:.4f}, {centroid.x:.4f} | Max Radius: {max_radius_m/1000:.1f} km")
            
            current_radius = step_km * 1000
            county_new_count = 0
            
            while current_radius <= (max_radius_m + (step_km * 1000)):
                print(f"   📡 Searching radius: {current_radius/1000:.1f} km...", end="\r")
                new_batch = self.fetch_streets(centroid.y, centroid.x, current_radius, target_county=county)
                all_new_streets.extend(new_batch)
                county_new_count += len(new_batch)
                current_radius += step_km * 1000
                time.sleep(1) # Small pause
            
            print(f"   ✨ Found {county_new_count} NEW streets for {county}")
        
        return all_new_streets

    def save_results(self, streets: List[Dict], output_path: str):
        if not streets:
            print("\n🏁 No new streets found to save.")
            return
            
        df = pd.DataFrame(streets)
        # Ensure correct column order and names
        df = df[['Address', 'county', 'lat', 'lon']]
        
        if os.path.exists(output_path):
            try:
                existing = pd.read_excel(output_path)
                df = pd.concat([existing, df], ignore_index=True)
                # Drop duplicates by Address and county
                df = df.drop_duplicates(subset=['Address', 'county'], keep='first')
            except Exception as e:
                print(f"⚠️ Could not merge with existing file: {e}")
            
        df.to_excel(output_path, index=False)
        print(f"\n✅ Success! Saved total of {len(df)} streets to {output_path}")


def main():
    parser = argparse.ArgumentParser(description='Maryland Street Radius Gatherer (Exhaustive)')
    parser.add_argument('--mode', choices=['radius', 'bulk'], default='radius', help='Search for a specific radius or bulk expand all counties')
    parser.add_argument('--lat', type=float, default=39.145, help='Center Lat for radius mode')
    parser.add_argument('--lon', type=float, default=-76.860, help='Center Lon for radius mode')
    parser.add_argument('--radius', type=float, default=10000, help='Radius in meters for radius mode')
    parser.add_argument('--step', type=int, default=5, help='Expansion step in KM (default: 5)')
    parser.add_argument('--output', default='data/MD_street_Names.xlsx', help='Output file')
    
    args = parser.parse_args()
    gatherer = RadiusStreetGatherer()
    
    if args.mode == 'radius':
        print(f"🌐 Radius Search: {args.radius/1000:.1f}km around {args.lat}, {args.lon}")
        new_streets = gatherer.fetch_streets(args.lat, args.lon, args.radius)
    else:
        print("🚩 Bulk Expansion Mode: Processing all Maryland counties...")
        new_streets = gatherer.bulk_expand_all(step_km=args.step)
        
    gatherer.save_results(new_streets, args.output)

if __name__ == "__main__":
    main()

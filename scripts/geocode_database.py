#!/usr/bin/env python3
"""
High-performance batch geocoding for the property database.
Prioritizes Hindu owners and uses thread pools for I/O bound tasks.
"""

import sqlite3
import time
import argparse
import logging
import re
import sys
import os
import threading
from multiprocessing.pool import ThreadPool
from typing import List, Dict, Optional, Tuple

from geopy.geocoders import ArcGIS, Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

from geocode_results import clean_address_for_geocoder

try:
    import pandas as pd
except ImportError:
    pd = None

# Configure logging
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("logs/geocode_db.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Global thread-local storage for geocoder instances
_thread_local = threading.local()

class DatabaseGeocoder:
    def __init__(self, db_path: str, provider: str = 'arcgis', workers: int = 4,
                 cache_file: str = 'data/Hindu_Origin_Owners_Mapped.xlsx'):
        self.db_path = db_path
        self.provider_name = provider
        self.workers = workers
        self._cache = self._load_cache(cache_file)

    @staticmethod
    def _load_cache(cache_file: str) -> dict:
        """Load (address, county) -> lat/lon cache from a previously geocoded Excel file.
        Cache hits skip the geocoding API entirely."""
        if not cache_file or not os.path.exists(cache_file) or pd is None:
            return {}
        try:
            df = pd.read_excel(cache_file)
        except Exception as e:
            logger.warning(f"Could not load geocode cache {cache_file}: {e}")
            return {}

        lat_col = next((c for c in df.columns if c.lower() in ('latitude', 'lat')), None)
        lon_col = next((c for c in df.columns if c.lower() in ('longitude', 'lon', 'long')), None)
        addr_col = next((c for c in df.columns if c.lower() in ('address', 'addr')), None)
        county_col = next((c for c in df.columns if c.lower() == 'county'), None)
        if not all([lat_col, lon_col, addr_col, county_col]):
            logger.warning(f"Cache file {cache_file} missing lat/lon/address/county columns")
            return {}

        cache = {}
        for _, row in df.iterrows():
            if pd.notnull(row[lat_col]) and pd.notnull(row[lon_col]):
                key = (str(row[addr_col]).upper().strip(),
                       str(row[county_col]).upper().replace(" COUNTY", "").strip())
                cache[key] = (float(row[lat_col]), float(row[lon_col]))
        logger.info(f"🗂️  Loaded {len(cache):,} cached coordinates from {cache_file}")
        return cache
        
    def _get_geocoder(self):
        """Get or create a thread-local geocoder to avoid SSL/Pickle issues."""
        if not hasattr(_thread_local, "geocoder"):
            if self.provider_name == 'arcgis':
                _thread_local.geocoder = ArcGIS(timeout=10)
            else:
                _thread_local.geocoder = Nominatim(user_agent=f"md_property_search_{threading.get_ident()}", timeout=10)
        return _thread_local.geocoder

    def get_unprocessed_count(self) -> Tuple[int, int]:
        """Return (hindu_unprocessed, total_unprocessed)"""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        
        cur.execute("SELECT COUNT(*) FROM properties WHERE is_hindu=1 AND (latitude IS NULL OR longitude IS NULL)")
        hindu_count = cur.fetchone()[0]
        
        cur.execute("SELECT COUNT(*) FROM properties WHERE (latitude IS NULL OR longitude IS NULL)")
        total_count = cur.fetchone()[0]
        
        conn.close()
        return hindu_count, total_count

    def fetch_batch(self, limit: int = 500, hindu_only: bool = False) -> List[Dict]:
        """Fetch a batch of properties without geocodes."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        query = "SELECT id, address, city, state, zip_code, county FROM properties WHERE (latitude IS NULL OR longitude IS NULL) "
        if hindu_only:
            query += "AND is_hindu=1 "
        query += f"LIMIT {limit}"
        
        cur.execute(query)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    def geocode_worker(self, property_data: Dict) -> Dict:
        """Geocode a single property: cache first, then cleaned multi-level queries."""
        address = property_data['address']
        city = property_data['city'] or ""
        zip_code = str(property_data['zip_code'] or "").split('-')[0]
        county = (property_data['county'] or "").replace(" County", "").replace(" COUNTY", "").strip()

        # 1. Cache hit — no API call
        cache_key = (str(address).upper().strip(), str(county).upper().strip())
        if cache_key in self._cache:
            lat, lon = self._cache[cache_key]
            return {'id': property_data['id'], 'latitude': lat, 'longitude': lon,
                    'method': 'Cache', 'success': True}

        # 2. Build queries, most specific first (levels mirror geocode_results.py)
        cleaned = clean_address_for_geocoder(address)
        queries = []
        if city and zip_code:
            queries.append((f"{cleaned}, {city}, Maryland, {zip_code}, USA", "Level 0: Cleaned-City-Zip"))
        if zip_code:
            queries.append((f"{cleaned}, Maryland, {zip_code}, USA", "Level 1: Cleaned-Zip"))
        if city:
            queries.append((f"{cleaned}, {city}, Maryland, USA", "Level 2: Cleaned-City"))
        queries.append((f"{address}, {county} County, Maryland, USA", "Level 3: Direct-County"))
        queries.append((f"{cleaned}, {county} County, Maryland, USA", "Level 4: Cleaned-County"))
        no_dir = re.sub(r'\b(NORTH|SOUTH|EAST|WEST|NORTHWEST|NORTHEAST|SOUTHWEST|SOUTHEAST)\b', '', cleaned).strip()
        if no_dir != cleaned:
            queries.append((f"{no_dir}, {county} County, Maryland, USA", "Level 5: Agnostic"))
        parts = cleaned.split()
        if len(parts) > 1 and parts[0][0].isdigit():
            queries.append((f"{' '.join(parts[1:])}, {city or county}, Maryland, USA", "Level 6: StreetOnly"))
        queries.append((f"{county} County, Maryland, USA", "Level 7: County Centroid"))

        try:
            geocoder = self._get_geocoder()
            for q, method in queries:
                try:
                    location = geocoder.geocode(q)
                    if location and ("Maryland" in location.address or ", MD" in location.address or location.address.endswith(" MD")):
                        return {
                            'id': property_data['id'],
                            'latitude': location.latitude,
                            'longitude': location.longitude,
                            'method': method,
                            'success': True
                        }
                except Exception:
                    continue
        except Exception:
            pass

        return {'id': property_data['id'], 'success': False}

    def update_database(self, results: List[Dict]):
        """Save geocoding results back to SQLite."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        
        updates = []
        for res in results:
            if res['success']:
                updates.append((res['latitude'], res['longitude'], res.get('method', ''), res['id']))

        if updates:
            cur.executemany("UPDATE properties SET latitude=?, longitude=?, geo_method=? WHERE id=?", updates)
            conn.commit()
            
        conn.close()
        return len(updates)

    def run(self, max_rows: Optional[int] = None):
        """Execute the geocoding loop."""
        hindu_count, total_count = self.get_unprocessed_count()
        logger.info(f"📊 Status: {hindu_count} Hindu properties and {total_count} total properties pending geocoding.")
        
        to_process = max_rows if max_rows else total_count
        success_total = 0
        
        start_time = time.time()
        
        # Phase 1: Hindu Properties
        if hindu_count > 0:
            limit = min(to_process, hindu_count)
            logger.info(f"🎯 Phase 1: Prioritizing {limit} Hindu properties...")
            success_total += self._process_loop(limit=limit, hindu_only=True)
            
        # Refetch counts
        _, total_rem = self.get_unprocessed_count()
        rem_to_process = to_process - success_total if max_rows else total_rem
        
        # Phase 2: Rest of properties
        if rem_to_process > 0:
            logger.info(f"🌍 Phase 2: Geocoding next {rem_to_process} properties...")
            success_total += self._process_loop(limit=rem_to_process, hindu_only=False)
            
        elapsed = time.time() - start_time
        logger.info(f"✨ DONE. Processed total of {success_total} properties in {elapsed/60:.1f} minutes.")

    def _process_loop(self, limit: int, hindu_only: bool) -> int:
        batch_size = 100
        success_count = 0
        processed_count = 0
        
        start_time = time.time()
        
        while processed_count < limit:
            chunk_size = min(batch_size, limit - processed_count)
            rows = self.fetch_batch(limit=chunk_size, hindu_only=hindu_only)
            if not rows:
                break
                
            with ThreadPool(self.workers) as pool:
                results = pool.map(self.geocode_worker, rows)
                
            saved = self.update_database(results)
            success_count += saved
            processed_count += len(rows)
            
            elapsed = time.time() - start_time
            rate = success_count / elapsed if elapsed > 0 else 0
            logger.info(f"   🚀 Progress: {processed_count}/{limit} | Success: {success_count} | Rate: {rate:.2f}/s")
            
            # Respect rate limits for Nominatim
            if self.provider_name == 'nominatim':
                time.sleep(1)

        return success_count

def main():
    parser = argparse.ArgumentParser(description='Geocode properties in SQLite database')
    parser.add_argument('--db', default='data/property_search.db', help='Path to SQLite database')
    parser.add_argument('--workers', type=int, default=4, help='Number of parallel threads')
    parser.add_argument('--limit', type=int, help='Max properties to process in this run')
    parser.add_argument('--provider', default='arcgis', choices=['arcgis', 'nominatim'], help='Geocode provider')
    parser.add_argument('--cache', default='data/Hindu_Origin_Owners_Mapped.xlsx',
                        help='Previously geocoded Excel used as coordinate cache (skip API for known addresses)')

    args = parser.parse_args()

    geocoder = DatabaseGeocoder(args.db, provider=args.provider, workers=args.workers,
                                cache_file=args.cache)
    geocoder.run(max_rows=args.limit)

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
High-performance batch geocoding for the property database.
Prioritizes Hindu owners and uses thread pools for I/O bound tasks.
"""

import sqlite3
import time
import argparse
import logging
import sys
import os
import threading
from multiprocessing.pool import ThreadPool
from typing import List, Dict, Optional, Tuple

from geopy.geocoders import ArcGIS, Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

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
    def __init__(self, db_path: str, provider: str = 'arcgis', workers: int = 4):
        self.db_path = db_path
        self.provider_name = provider
        self.workers = workers
        
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
        """Geocode a single property."""
        address = property_data['address']
        city = property_data['city'] or ""
        zip_code = property_data['zip_code'] or ""
        county = property_data['county'] or ""
        
        full_address = f"{address}, {city}, MD {zip_code}".strip(", ")
        if county and county.lower() not in full_address.lower():
            full_address += f", {county}"
            
        try:
            geocoder = self._get_geocoder()
            location = geocoder.geocode(full_address)
            if location:
                return {
                    'id': property_data['id'],
                    'latitude': location.latitude,
                    'longitude': location.longitude,
                    'success': True
                }
        except Exception as e:
            # logger.debug(f"Failed to geocode {full_address}: {e}")
            pass
            
        return {'id': property_data['id'], 'success': False}

    def update_database(self, results: List[Dict]):
        """Save geocoding results back to SQLite."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        
        updates = []
        for res in results:
            if res['success']:
                updates.append((res['latitude'], res['longitude'], res['id']))
                
        if updates:
            cur.executemany("UPDATE properties SET latitude=?, longitude=? WHERE id=?", updates)
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
    
    args = parser.parse_args()
    
    geocoder = DatabaseGeocoder(args.db, provider=args.provider, workers=args.workers)
    geocoder.run(max_rows=args.limit)

if __name__ == "__main__":
    main()

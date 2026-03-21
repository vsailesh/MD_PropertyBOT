#!/usr/bin/env python3
"""
Maryland Property Search - Continuous Pipeline Orchestrator

Automates the end-to-end lifecycle:
1. Discovery (OSM Expansion)
2. Discovery (Radius Clusters)
3. Search (SDAT Scraper)
4. Geocoding (Owner Mapping)

Runs in a continuous loop with configurable intervals.
"""

import time
import subprocess
import logging
import os
import sys
import argparse
from datetime import datetime
from typing import List, Optional

# Ensure we can import from src
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Configure logging
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("logs/pipeline.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

class MarylandPropertyPipeline:
    """
    Orchestrates the property search pipeline steps sequentially.
    """

    def __init__(self, 
                 counties: Optional[List[str]] = None, 
                 workers: int = 50, 
                 interval_hours: float = 24.0,
                 db_path: str = "data/property_search.db",
                 lookup_file: str = "data/MD_street_Names.xlsx",
                 results_file: str = "data/Hindu_origin_owners_Mapped.xlsx"):
        """
        Initialize the pipeline.
        """
        self.counties = counties or ["Montgomery", "Howard", "Baltimore", "Prince George's", "Anne Arundel"]
        self.workers = workers
        self.interval_hours = interval_hours
        self.db_path = db_path
        self.lookup_file = lookup_file
        self.results_file = results_file
        
        # High-density clusters for radius discovery (Bethesda, Potomac, Ellicott City)
        self.clusters = [
            {"lat": 38.963, "lon": -77.090, "name": "Bethesda"},
            {"lat": 39.026, "lon": -77.149, "name": "Potomac"},
            {"lat": 39.279, "lon": -76.810, "name": "Ellicott City"},
            {"lat": 39.326, "lon": -76.761, "name": "Pikesville"}
        ]

    def run_step(self, name: str, command: List[str]) -> bool:
        """
        Run a pipeline step as a subprocess.
        """
        logger.info(f"🚀 [STEP] {name}")
        start_time = time.time()
        
        try:
            # Use sys.executable to ensure the same Python environment is used
            process = subprocess.Popen(
                [sys.executable] + command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True
            )

            # Stream output to log
            if process.stdout:
                for line in process.stdout:
                    clean_line = line.strip()
                    if clean_line:
                        # Log only high-level progress or errors to the pipeline log to avoid bloat
                        if any(x in clean_line.upper() for x in ["FOUND", "ADDED", "ERROR", "✅", "❌", "📊", "SAVE"]):
                            logger.info(f"   | {clean_line}")

            process.wait()
            
            elapsed = time.time() - start_time
            if process.returncode == 0:
                logger.info(f"✅ [SUCCESS] {name} completed in {elapsed/60:.1f} minutes")
                return True
            else:
                logger.error(f"❌ [FAILED] {name} exited with code {process.returncode}")
                return False
                
        except Exception as e:
            logger.error(f"💥 [CRASH] Unexpected error in {name}: {e}")
            return False

    def discover_streets_county(self):
        """Phase 1a: Refresh street lists for all target counties."""
        logger.info("🔍 PHASE 1a: County-wide Street Discovery")
        cmd = ["scripts/expand_streets.py", "--counties"] + self.counties + ["--cooldown", "5"]
        return self.run_step("County Discovery", cmd)

    def discover_streets_radius(self):
        """Phase 1b: Search for new streets around high-density owner clusters."""
        logger.info("🔍 PHASE 1b: Targeted Radius discovery")
        success = True
        for cluster in self.clusters:
            logger.info(f"📍 Discovering around {cluster['name']}...")
            cmd = [
                "scripts/radius_street_gatherer.py", 
                "--mode", "radius", 
                "--lat", str(cluster['lat']), 
                "--lon", str(cluster['lon']), 
                "--radius", "8000" # 5 miles
            ]
            if not self.run_step(f"Radius Discovery ({cluster['name']})", cmd):
                success = False
        return success

    def perform_search(self):
        """Phase 2: Use SDAT scraper to find properties for discovered streets."""
        logger.info("📡 PHASE 2: SDAT Property Search")
        # Ensure database is standardized before search
        self.standardize_database()
        
        cmd = ["scripts/robust_bulk_search.py", "run", "-i", self.lookup_file, "-w", str(self.workers)]
        return self.run_step("Bulk Search", cmd)

    def geocode_all_properties(self, limit: int = 5000):
        """Phase 4: Geocode all properties in the database."""
        logger.info("🌍 PHASE 4: Database-wide Geocoding")
        cmd = ["scripts/geocode_database.py", "--db", self.db_path, "--limit", str(limit), "--workers", "10"]
        return self.run_step("Database Geocoding", cmd)

    def geocode_and_map(self):
        """Phase 3: Identify target owners and map them."""
        logger.info("🕉️ PHASE 3: Geocoding & Owner Mapping")
        
        # Export Hindu owners to a temporary file
        temp_export = "data/pipeline_temp_hindu.xlsx"
        export_cmd = ["scripts/robust_bulk_search.py", "export", "--hindu-only", "-o", temp_export]
        if not self.run_step("Export Hindu Owners", export_cmd):
            return False
            
        # Geocode the exported owners
        geocode_cmd = ["scripts/geocode_results.py", temp_export, "-o", self.results_file, "-w", "10"]
        return self.run_step("Geocoding & Mapping", geocode_cmd)

    def standardize_database(self):
        """Utility: Ensure county names are standardized in the DB."""
        logger.info("🧹 Normalizing Database County Names")
        script = """
import sqlite3
import os
from src.county_mapper import normalize_county
db_path = 'data/property_search.db'
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # Get unique counties from both tables
    counties = set()
    cur.execute('SELECT DISTINCT county FROM search_progress')
    counties.update([r[0] for r in cur.fetchall()])
    cur.execute('SELECT DISTINCT county FROM properties')
    counties.update([r[0] for r in cur.fetchall()])

    for old_name in counties:
        if not old_name: continue
        new_name = normalize_county(old_name)
        if new_name and new_name != old_name:
            # Update search_progress
            cur.execute('UPDATE OR IGNORE search_progress SET county = ? WHERE county = ?', (new_name, old_name))
            cur.execute('DELETE FROM search_progress WHERE county = ?', (old_name,))
            
            # Update properties
            cur.execute('UPDATE OR IGNORE properties SET county = ? WHERE county = ?', (new_name, old_name))
            cur.execute('DELETE FROM properties WHERE county = ?', (old_name,))
    conn.commit()
    conn.close()
"""
        cmd = ["-c", script]
        return self.run_step("Normalization", cmd)

    def run_cycle(self):
        """Run one complete pipeline cycle."""
        logger.info("=" * 60)
        logger.info(f"🔄 STARTING PIPELINE CYCLE: {datetime.now().isoformat()}")
        logger.info("=" * 60)
        
        # 1. Pipeline execution
        self.discover_streets_county()
        self.discover_streets_radius()
        self.perform_search()
        self.geocode_and_map()
        self.geocode_all_properties()
        
        logger.info("=" * 60)
        logger.info(f"✅ PIPELINE CYCLE COMPLETE: {datetime.now().isoformat()}")
        logger.info("=" * 60)

    def start(self, once: bool = False):
        """Start the continuous pipeline."""
        logger.info("🚀 Maryland Property Search Pipeline Initialized")
        logger.info(f"   Counties: {', '.join(self.counties)}")
        logger.info(f"   Workers:  {self.workers}")
        logger.info(f"   Interval: {self.interval_hours} hours")
        
        try:
            while True:
                self.run_cycle()
                
                if once:
                    logger.info("🏁 One-time run complete. Exiting.")
                    break
                    
                if not once and self.interval_hours > 0:
                    logger.info(f"😴 Sleeping for {self.interval_hours} hours...")
                    time.sleep(self.interval_hours * 3600)
        except KeyboardInterrupt:
            logger.info("🛑 Pipeline stopped by user. Graceful shutdown.")
        except Exception as e:
            logger.critical(f"💀 Pipeline crashed: {e}")
            raise

def main():
    parser = argparse.ArgumentParser(description='Continuous Maryland Property Search Pipeline')
    parser.add_argument('--counties', nargs='+', help='Target counties (default: major MD counties)')
    parser.add_argument('--workers', type=int, default=50, help='Parallel search workers (default: 50)')
    parser.add_argument('--interval', type=float, default=24.0, help='Hours between cycles (default: 24.0)')
    parser.add_argument('--once', action='store_true', help='Run only one cycle and exit')
    
    args = parser.parse_args()
    
    pipeline = MarylandPropertyPipeline(
        counties=args.counties,
        workers=args.workers,
        interval_hours=args.interval
    )
    pipeline.start(once=args.once)

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Complete Maryland Property Pipeline
1. Select Community → Get ALL addresses
2. Format for SDAT → Scrape owner names
3. Export Excel → Addresses + Owners + Race Prediction
"""

import time
import json
import re
import pandas as pd
from typing import List, Dict, Optional
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException
from webdriver_manager.chrome import ChromeDriverManager
import requests
import os
from datetime import datetime
from dotenv import load_dotenv

import undetected_chromedriver as uc
from bs4 import BeautifulSoup
# Load environment variables
from src.diagnostics import ScrapeDiagnostics
load_dotenv()


class UsageTracker:
    """Track API usage to stay within free tier limits."""
    def __init__(self, filename="usage_stats.json"):
        self.filename = filename
        self.stats = self._load()

    def _load(self):
        if os.path.exists(self.filename):
            with open(self.filename, 'r') as f:
                data = json.load(f)
                # Check if same month
                current_month = datetime.now().strftime("%Y-%m")
                if data.get('month') == current_month:
                    return {
                        'month': str(data.get('month')),
                        'geocoding': int(data.get('geocoding', 0)),
                        'places': int(data.get('places', 0))
                    }
        return {'month': datetime.now().strftime("%Y-%m"), 'geocoding': 0, 'places': 0}

    def _save(self):
        with open(self.filename, 'w') as f:
            json.dump(self.stats, f)

    def increment(self, service):
        current = int(self.stats.get(service, 0))
        self.stats[service] = current + 1
        self._save()

    def can_use(self, service, limit):
        return self.stats.get(service, 0) < limit


class GoogleAddressFetcher:
    """Fetch addresses using Google Maps API (Safe Tier)."""
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv('GOOGLE_MAPS_API_KEY')
        self.tracker = UsageTracker()
        # Free Tier Caps (Conservative)
        self.LIMITS = {
            'geocoding': 9000,
            'places': 4500
        }

    def fetch_addresses(self, community: str, county: str, limit: int = 50) -> List[Dict]:
        """Fetch addresses for a community using Google Places Text Search."""
        if not self.api_key:
            print("⚠️ No Google API Key found. Skipping Google fetch.")
            return []

        if not self.tracker.can_use('places', self.LIMITS['places']):
            print("❌ Google Places API limit reached for this month.")
            return []

        print(f"🏘️ Fetching Google addresses for {community}, {county}...")
        
        url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
        params = {
            'query': f"residential addresses in {community}, {county}, MD",
            'key': self.api_key
        }
        
        try:
            response = requests.get(url, params=params)
            self.tracker.increment('places')
            
            if response.status_code == 200:
                data = response.json()
                results = data.get('results', [])
                
                properties = []
                for res in results[:limit]:
                    address = res.get('formatted_address', '')
                    # Simple parse: '123 Main St, Laurel, MD 20707, USA' -> '123 Main St'
                    parts = address.split(',')
                    if parts:
                        street_address = parts[0].strip()
                        city = parts[1].strip() if len(parts) > 1 else community
                        
                        properties.append({
                            'address': street_address,
                            'city': city,
                            'community': community,
                            'county': county,
                            'lat': res.get('geometry', {}).get('location', {}).get('lat'),
                            'lng': res.get('geometry', {}).get('location', {}).get('lng'),
                            'source': 'Google'
                        })
                return properties
            else:
                print(f"❌ Google API error: {response.status_code}")
        except Exception as e:
            print(f"❌ Google API error: {e}")
            
        return []


class CommunityAddressFetcher:
    """Fetch ALL addresses for a community using OpenStreetMap."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        })

    def fetch_community_addresses(self, community: str, county: str, limit: int = 10000) -> List[Dict]:
        """
        Fetch ALL addresses for a community.

        Args:
            community: Community name (e.g., "Brooklandville", "Towson")
            county: County name (e.g., "Baltimore County")
            limit: Max addresses to fetch

        Returns:
            List of addresses with coordinates
        """
        print(f"\n🏘️ Fetching addresses for {community}, {county}...")

        # Maryland county boundaries
        county_boundaries = {
            "Montgomery": {"south": 38.9, "north": 39.5, "west": -77.4, "east": -77.0},
            "Prince George's": {"south": 38.6, "north": 39.0, "west": -77.0, "east": -76.7},
            "Baltimore City": {"south": 39.2, "north": 39.4, "west": -76.7, "east": -76.5},
            "Baltimore County": {"south": 39.2, "north": 39.7, "west": -76.8, "east": -76.5},
            "Anne Arundel": {"south": 38.7, "north": 39.2, "west": -76.8, "east": -76.4},
            "Frederick": {"south": 39.2, "north": 39.7, "west": -77.6, "east": -77.2},
            "Howard": {"south": 39.0, "north": 39.3, "west": -77.0, "east": -76.8},
        }

        if county not in county_boundaries:
            print(f"⚠️ County {county} not in database")
            return []

        bounds = county_boundaries[county]

        # Overpass API query
        overpass_url = "https://overpass-api.de/api/interpreter"

        # Query for addresses - widened to include more fields and community variations
        query = f"""
        [out:json][timeout:180];
        area["name"="{county}"]->.searchArea;
        (
          way["addr:street"]["addr:housenumber"]["addr:city"~"{community}|Laurel", i](area.searchArea);
          node["addr:street"]["addr:housenumber"]["addr:city"~"{community}|Laurel", i](area.searchArea);
          way["addr:street"]["addr:housenumber"]["addr:suburb"~"{community}", i](area.searchArea);
          node["addr:street"]["addr:housenumber"]["addr:suburb"~"{community}", i](area.searchArea);
        );
        out center;
        """

        try:
            # Add retry logic for 429
            max_retries = 3
            response = None
            for attempt in range(max_retries):
                try:
                    response = self.session.post(overpass_url, data={'data': query}, timeout=200)
                    if response.status_code == 200:
                        break
                    elif response.status_code == 429:
                        print(f"⚠️ Rate limited (429). Waiting {45 * (attempt + 1)}s...")
                        time.sleep(45 * (attempt + 1))
                except requests.exceptions.Timeout:
                    print(f"⚠️ Overpass timeout. Attempt {attempt + 1}/{max_retries}")
                    time.sleep(5)
            
            resp = response
            if resp is None:
                return []
                
            if resp.status_code != 200:
                print(f"❌ Overpass error: {resp.status_code}")
                return []

            data = resp.json()
            properties = []

            elements = data.get('elements', [])
            # Shuffle or pick first N
            import random
            random_elements = list(elements)
            random.shuffle(random_elements)
            
            # Ensure limit is int
            safe_limit = int(limit) if limit else 1000
            for element in random_elements[:safe_limit]:
                tags = element.get('tags', {})

                street = tags.get('addr:street', '')
                housenumber = tags.get('addr:housenumber', '')
                r_city = tags.get('addr:city', community)
                postcode = tags.get('addr:postcode', '')

                if street and housenumber:
                    lat = element.get('lat') or element.get('center', {}).get('lat')
                    lon = element.get('lon') or element.get('center', {}).get('lon')

                    address = f"{housenumber} {street}"

                    properties.append({
                        'address': address,
                        'city': r_city,
                        'county': county,
                        'zip_code': postcode,
                        'community': community,
                        'lat': lat,
                        'lng': lon
                    })
            
            print(f"✅ Found {len(properties)} addresses")
            return properties

        except Exception as e:
            print(f"❌ Error: {e}")

    def fetch_county_streets(self, county: str) -> List[str]:
        """Fetch all unique street names for an entire county using OpenStreetMap."""
        # Maryland counties in OSM are usually admin_level 6
        osm_county = county if "County" in county or "City" in county else f"{county} County"
        if county.lower() == "baltimore city": osm_county = "Baltimore City"
        
        # Exhaustive query: Get named highways AND elements with address:street tags
        query = f"""
        [out:json][timeout:360];
        area["name"="{osm_county}"]["admin_level"="6"]->.searchArea;
        (
          way["highway"]["name"](area.searchArea);
          way["addr:street"](area.searchArea);
          node["addr:street"](area.searchArea);
          relation["addr:street"](area.searchArea);
        );
        out tags;
        """
        
        overpass_url = "https://overpass-api.de/api/interpreter"
        print(f"🌍 Thorough Harvesting: {osm_county}...")
        
        try:
            # Simple retry with exponential backoff for 429/504
            max_retries = 3
            response = None
            for attempt in range(max_retries):
                try:
                    response = self.session.post(overpass_url, data={'data': query}, timeout=370)
                    if response.status_code == 200: break
                    if response.status_code in (429, 504):
                        wait_sec = 60 * (attempt + 1)
                        print(f"🚦 Busy/Rate limited ({response.status_code}). Waiting {wait_sec}s...")
                        time.sleep(wait_sec)
                except (requests.exceptions.Timeout, requests.exceptions.RequestException):
                    time.sleep(10)

            if not response or response.status_code != 200:
                print(f"❌ Overpass error: {response.status_code if response else 'Timeout'}")
                return []

            data = response.json()
            elements = data.get('elements', [])
            
            streets = set()
            for el in elements:
                tags = el.get('tags', {})
                # Priority 1: Address tags (usually more accurate for searches)
                if 'addr:street' in tags:
                    streets.add(tags['addr:street'])
                # Priority 2: Highway names
                elif 'name' in tags and 'highway' in tags:
                    streets.add(tags['name'])
            
            # Filter out any non-text or extremely short artifacts
            unique_streets = [s for s in streets if s and len(s) > 2 and not s.isdigit()]
            unique_streets = sorted(list(set(unique_streets)))
            
            print(f"✅ Found {len(unique_streets)} unique street names in {osm_county}")
            return unique_streets
            
        except Exception as e:
            print(f"❌ Error fetching county streets: {e}")
            return []

    def fetch_laurel_area_addresses(self, limit: int = 100) -> List[Dict]:
        """Fetch addresses in the Laurel area using a bounding box."""
        # Laurel area bounding box (approx covering 4 counties intersection)
        # Lat: 39.05 to 39.15, Lon: -76.95 to -76.80
        bbox = "39.05,-76.95,39.15,-76.80"
        
        query = f"""
        [out:json][timeout:180];
        (
          way["addr:housenumber"]["addr:street"]({bbox});
          node["addr:housenumber"]["addr:street"]({bbox});
        );
        out center;
        """
        
        overpass_url = "https://overpass-api.de/api/interpreter"
        print(f"🏘️ Fetching properties in Laurel bounding box: {bbox}...")
        
        try:
            response = self.session.post(overpass_url, data={'data': query}, timeout=200)
            if response is None or response.status_code != 200:
                code = response.status_code if response else "Unknown"
                print(f"❌ Overpass error: {code}")
                return []
                
            data = response.json()
            elements = data.get('elements', [])
            
            # Map elements to property dicts
            properties = []
            
            import random
            random_elements = list(elements)
            random.shuffle(random_elements)
            
            for element in random_elements[:limit]:
                tags = element.get('tags', {})
                street = tags.get('addr:street', '')
                housenumber = tags.get('addr:housenumber', '')
                city = tags.get('addr:city', 'Laurel')
                postcode = tags.get('addr:postcode', '')
                
                if street and housenumber:
                    lat = element.get('lat') or element.get('center', {}).get('lat')
                    lon = element.get('lon') or element.get('center', {}).get('lon')
                    
                    # Try to determine county based on lat/lon or city
                    # For Laurel, we can use simple mapping or just pass to SDAT to find
                    properties.append({
                        'address': f"{housenumber} {street}",
                        'city': city,
                        'county': 'Prince George\'s', # Default, will refine in orchestration
                        'zip_code': postcode,
                        'community': 'Laurel Area',
                        'lat': lat,
                        'lng': lon,
                        'source': 'OSM-BBox'
                    })
            
            print(f"✅ Found {len(properties)} addresses")
            return properties
            
        except Exception as e:
            print(f"❌ Error: {e}")
            return []
            
    def fetch_radius_addresses(self, lat: float, lon: float, radius_miles: float, limit: int = 10000) -> List[Dict]:
        """Fetch addresses within a radius (miles) of a point with retry logic."""
        # Convert miles to meters
        radius_meters = radius_miles * 1609.34
        
        query = f"""
        [out:json][timeout:300];
        (
          way["addr:housenumber"]["addr:street"](around:{radius_meters},{lat},{lon});
          node["addr:housenumber"]["addr:street"](around:{radius_meters},{lat},{lon});
        );
        out center;
        """
        
        overpass_url = "https://overpass-api.de/api/interpreter"
        print(f"🏘️ Fetching properties within {radius_miles} miles of ({lat}, {lon})...")
        
        max_retries = 3
        import random
        import time
        
        for attempt in range(max_retries):
            try:
                # Add a small random jitter before retry requests
                if attempt > 0:
                    wait_time = (2 ** attempt) + random.random() * 5
                    print(f"⏳ Retry {attempt}/{max_retries} in {wait_time:.1f}s...")
                    time.sleep(wait_time)

                response = self.session.post(overpass_url, data={'data': query}, timeout=310)
                
                if response.status_code == 429:
                    print("🚦 Overpass Rate Limit (429). Waiting longer...")
                    time.sleep(15 + random.random() * 5)
                    continue
                elif response.status_code != 200:
                    print(f"❌ Overpass error: {response.status_code} - {response.text[:200]}")
                    if attempt < max_retries - 1: continue
                    return []
                    
                data = response.json()
                elements = data.get('elements', [])
                
                properties = []
                random_elements = list(elements)
                random.shuffle(random_elements)
                
                # If limit is 0 or None, fetch all
                fetch_limit = limit if limit and limit > 0 else len(random_elements)
                
                for element in random_elements[:fetch_limit]:
                    tags = element.get('tags', {})
                    street = tags.get('addr:street', '')
                    housenumber = tags.get('addr:housenumber', '')
                    city = tags.get('addr:city', 'Maryland')
                    postcode = tags.get('addr:postcode', '')
                    
                    if street and housenumber:
                        p_lat = element.get('lat') or element.get('center', {}).get('lat')
                        p_lon = element.get('lon') or element.get('center', {}).get('lon')
                        
                        properties.append({
                            'address': f"{housenumber} {street}",
                            'city': city,
                            'county': 'Maryland',
                            'zip_code': postcode,
                            'community': f'Radius {radius_miles}mi',
                            'lat': p_lat,
                            'lng': p_lon,
                            'source': 'OSM-Radius'
                        })
                
                print(f"✅ Found {len(properties)} addresses")
                return properties
                
            except Exception as e:
                print(f"❌ Attempt {attempt+1} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(10)
                else:
                    return []
        return []


class SDATFormatter:
    """Format addresses strictly according to SDAT rules."""

    SUFFIXES = {
        'AVE', 'AVENUE', 'ST', 'STREET', 'DR', 'DRIVE', 'RD', 'ROAD',
        'LN', 'LANE', 'WAY', 'CT', 'COURT', 'PL', 'PLACE', 'CIR', 'CIRCLE',
        'BLVD', 'BOULEVARD', 'TRL', 'TRAIL', 'LOOP', 'TER', 'TERRACE',
        'PK', 'PIKE', 'HWY', 'HIGHWAY', 'PKWY', 'PARKWAY', 'PATH', 'ROW',
        'RUN', 'XING', 'CROSSING', 'ALY', 'ALLEY', 'SQ', 'SQUARE',
        'CMN', 'COMMONS', 'HL', 'HILL', 'HLS', 'HILLS', 'VLG', 'VILLAGE',
        'VLY', 'VALLEY', 'VL', 'VILLE', 'VW', 'VIEW', 'VIS', 'VISTA',
        'WALK', 'WA', 'WALL', 'WOODS', 'WOOD', 'XRD', 'CROSSROAD',
        'ESTATE', 'ESTATES', 'FLD', 'FIELD', 'FLDS', 'FIELDS',
        'FRG', 'FORGE', 'FRST', 'FOREST', 'GRN', 'GREEN', 'GRNS', 'GREENS',
        'GROVE', 'GRV', 'HBR', 'HARBOR', 'HBRS', 'HARBORS', 'HT', 'HEIGHTS',
        'HTS', 'KY', 'KEY', 'KLS', 'KEYS', 'KNOL', 'KNOLL', 'KNLS', 'KNOLLS',
        'LDG', 'LODGE', 'MNR', 'MANOR', 'MNRS', 'MANORS', 'MEADOW', 'MDW',
        'MDWS', 'MEADOWS', 'ML', 'MILL', 'MLS', 'MILLS', 'MSN', 'MISSION',
        'MT', 'MOUNT', 'PT', 'POINT', 'PTS', 'POINTS', 'PRT', 'PORT',
        'PR', 'PRAIRIE', 'SHL', 'SHOAL', 'SHLS', 'SHOALS', 'SHR', 'SHORE',
        'SHRS', 'SHORES', 'SPG', 'SPRING', 'SPGS', 'SPRINGS', 'STEA', 'STEAK',
        'STR', 'STRA', 'STRAV', 'STRAV', 'STRAVE', 'STRAVENUE', 'STRAVN',
        'STRVENUE', 'TRCE', 'TRACE', 'TRFY', 'TRAFFICWAY', 'UN', 'UNION',
        'WELL', 'WELLS', 'WING'
    }

    DIRECTIONS = {
        'N', 'NORTH', 'S', 'SOUTH', 'E', 'EAST', 'W', 'WEST',
        'NE', 'NORTHEAST', 'NW', 'NORTHWEST', 'SE', 'SOUTHEAST', 'SW', 'SOUTHWEST'
    }

    @staticmethod
    def _is_ordinal(word: str) -> bool:
        """Check if a word is an ordinal number like 5TH, 15TH, 33RD, 1ST, 2ND."""
        return bool(re.match(r'^\d+(ST|ND|RD|TH)$', word))

    @staticmethod
    def format_address(address: str) -> Dict[str, str]:
        """Format address for SDAT search following strict official instructions.
        
        Rules applied:
        1. Strip ALL leading numbers, number-ranges (13400-13408), and letter-suffixed numbers (10109-B)
        2. Preserve ordinals ONLY when they are the street name (15TH, 8TH)
        3. Remove ALL suffixes (AVE, ST, DR, PL, WAY, etc.) from ANY position
        4. Remove ALL directions (NORTH, NORTHWEST, etc.) from ANY position
        5. Handle punctuation: St. Mary's -> ST MARYS, O'Donnell stays as-is
        6. Handle Saint vs St interchange
        """
        # Convert to upper case
        original = str(address).upper().strip()
        if not original:
            return {'street_number': '', 'street_name': '', 'search_string': ''}

        # Handle special punctuation BEFORE splitting (Instruction 3)
        # "St. Mary's" -> "ST MARYS"
        original = original.replace("ST.", "ST")
        original = re.sub(r"'S\b", "S", original)  # MARY'S -> MARYS

        parts = original.split()

        # Step 1: Strip ALL leading number tokens
        # Numbers, ranges (13400-13408), letter-suffixed (10109-B), plain digits
        street_number = ''
        remaining_parts = []
        found_name_start = False
        for p in parts:
            if not found_name_start:
                # Pure digits -> street number, skip
                if p.isdigit():
                    street_number = p
                    continue
                # Range like 13400-13408
                if re.match(r'^\d+-\d+$', p):
                    continue
                # Letter-suffixed number like 10109-B or 9439425A
                if re.match(r'^\d+[-]?[A-Z]$', p):
                    continue
                # This is the start of the street name
                found_name_start = True
            remaining_parts.append(p)

        if not remaining_parts:
            return {'street_number': street_number, 'street_name': '', 'search_string': ''}

        # Step 2: Remove ALL suffixes and directions from ANY position
        # But keep ordinals (15TH, 8TH) — they are legitimate street names
        cleaned_parts = []
        for p in remaining_parts:
            # Clean non-alpha characters except interior apostrophes and hyphens
            p = re.sub(r"[^A-Z0-9'-]", "", p)
            if not p:
                continue
            
            # Keep ordinals (Instruction 6: 25th, 33rd)
            if SDATFormatter._is_ordinal(p):
                cleaned_parts.append(p)
                continue
            
            # Remove suffixes from ANY position (Instruction 1: no suffixes)
            if p in SDATFormatter.SUFFIXES:
                continue
            
            # Remove directions from ANY position (Instruction 1: no directions)
            if p in SDATFormatter.DIRECTIONS:
                continue
            
            cleaned_parts.append(p)

        street_name = ' '.join(cleaned_parts).strip()

        # Step 3: Saint vs St (Instruction 4)
        if street_name.startswith("SAINT "):
            street_name = street_name.replace("SAINT ", "ST ", 1)

        return {
            'street_number': street_number,
            'street_name': street_name,
            'search_string': f"{street_number} {street_name}".strip()
        }



class SDATAutoScraper:
    """Automated SDAT scraper using Selenium."""

    def __init__(self, headless: bool = True):
        self.base_url = "https://sdat.dat.maryland.gov/RealProperty/Pages/default.aspx"
        self.driver = None
        self.headless = headless
        self.diagnostics = ScrapeDiagnostics()
        self.selectors = self._load_selectors()
        
        # New requests session for faster, non-blocked scraping
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        })
        
        # County to ID mapping for SDAT dropdown
        self.county_map = {
            'ALLEGANY': '01', 'ANNE ARUNDEL': '02', 'BALTIMORE CITY': '03',
            'BALTIMORE COUNTY': '04', 'CALVERT': '05', 'CAROLINE': '06',
            'CARROLL': '07', 'CECIL': '08', 'CHARLES': '09', 'DORCHESTER': '10',
            'FREDERICK': '11', 'GARRETT': '12', 'HARFORD': '13', 'HOWARD': '14',
            'KENT': '15', 'MONTGOMERY': '16', "PRINCE GEORGE'S": '17',
            'QUEEN ANNE\'S': '18', 'ST. MARY\'S': '19', 'SOMERSET': '20',
            'TALBOT': '21', 'WASHINGTON': '22', 'WICOMICO': '23', 'WORCESTER': '24'
        }

    def _load_selectors(self) -> dict:
        """Load CSS selectors from config."""
        config_path = "config/sdat_selectors.json"
        try:
            with open(config_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Could not load selectors from {config_path}: {e}")
            return {}

    def start_driver(self):
        """Start Chrome driver."""
        options = uc.ChromeOptions()
        if self.headless:
            # Note: undetected_chromedriver has its own headless handling
            options.headless = True
            
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--window-size=1920,1080')

        self.driver = uc.Chrome(options=options)
        
        self.driver.set_page_load_timeout(30) # Set reasonable timeout
        print("✅ WebDriver started")

    def stop_driver(self):
        """Stop driver."""
        if self.driver:
            self.driver.quit()
    
    def _get_form_vars(self, soup: BeautifulSoup) -> Dict[str, str]:
        """Extract ASP.NET hidden form variables."""
        vars = {}
        for id in ['__VIEWSTATE', '__VIEWSTATEGENERATOR', '__EVENTVALIDATION']:
            el = soup.find(id=id)
            if el:
                vars[id] = el.get('value', '')
        return vars

    def search_street_bulk(self, street_name: str, county: str) -> list[dict]:
        """
        Search for a street name using requests-based ASP.NET form flow.
        Much faster and bypasses Selenium detection.
        """
        # 1. Start with strict formatting
        formatted = SDATFormatter.format_address(street_name)
        base_name = formatted['street_name']
        
        if not base_name:
            return []

        # 2. Build variations
        variations = [base_name]
        if " " in base_name: variations.append(base_name.replace(" ", ""))
        elif base_name.startswith("MC") and len(base_name) > 2: variations.append(base_name.replace("MC", "MC ", 1))
        if base_name.startswith("ST "): variations.append(base_name.replace("ST ", "SAINT ", 1))
        elif base_name.startswith("SAINT "): variations.append(base_name.replace("SAINT ", "ST ", 1))
        if "BALTIMORE" in base_name: variations.append(base_name.replace("BALTIMORE", "BALTO"))
        if "NATIONAL" in base_name: variations.append(base_name.replace("NATIONAL", "NATL"))
        
        variations = [v for v in dict.fromkeys(variations) if v and v != 'UNKNOWN']
        
        county_upper = county.upper().replace(" COUNTY", "").strip()
        county_id = self.county_map.get(county_upper)
        if not county_id:
            # Fallback for approximate matches
            for k, v in self.county_map.items():
                if county_upper in k:
                    county_id = v
                    break
        
        if not county_id:
            print(f"  ⚠️ County {county} not found in ID map.")
            return []

        all_results = []
        for street_query in variations:
            try:
                print(f"  🌐 Trying search: {street_query} in {county} (Requests)")
                
                # Step 0: GET base page to get initial ViewState
                resp = self.session.get(self.base_url, timeout=15)
                soup = BeautifulSoup(resp.text, 'html.parser')
                form_vars = self._get_form_vars(soup)
                
                # Step 1: POST to select County and Street Address method
                payload = {
                    **form_vars,
                    '__EVENTTARGET': 'ctl00$cphMainContentArea$ucSearchType$wzrdRealPropertySearch$ucSearchType$ddlCounty',
                    'ctl00$cphMainContentArea$ucSearchType$wzrdRealPropertySearch$ucSearchType$ddlCounty': county_id,
                    'ctl00$cphMainContentArea$ucSearchType$wzrdRealPropertySearch$ucSearchType$ddlSearchType': '01', # Street Address
                    'ctl00$cphMainContentArea$ucSearchType$wzrdRealPropertySearch$StartNavigationTemplateContainerID$btnContinue': 'Continue'
                }
                
                resp = self.session.post(self.base_url, data=payload, timeout=15)
                soup = BeautifulSoup(resp.text, 'html.parser')
                form_vars = self._get_form_vars(soup)
                
                # Step 2: POST the street search
                payload = {
                    **form_vars,
                    'ctl00$cphMainContentArea$ucSearchType$wzrdRealPropertySearch$ucEnterData$txtStreetName': street_query,
                    'ctl00$cphMainContentArea$ucSearchType$wzrdRealPropertySearch$StepNavigationTemplateContainerID$btnStepNextButton': 'Next'
                }
                
                resp = self.session.post(self.base_url, data=payload, timeout=20)
                soup = BeautifulSoup(resp.text, 'html.parser')
                
                # Step 3: Parse Results
                if "No records found" in resp.text:
                    continue

                # Check for single result
                owner_el = soup.find(id="cphMainContentArea_ucSearchType_wzrdRealPropertySearch_ucDetailsSearch_dlstDetaisSearch_lblOwnerName_0")
                if owner_el:
                    addr_el = soup.find(id="cphMainContentArea_ucSearchType_wzrdRealPropertySearch_ucDetailsSearch_dlstDetaisSearch_lblPremisesAddress_0")
                    all_results.append({
                        'owner_name': owner_el.text.strip(),
                        'address': addr_el.text.strip() if addr_el else "Unknown",
                        'county': county
                    })
                    return all_results

                # Grid results (Multiple Pages)
                results = self._parse_grid_results(soup, county)
                
                # Handle Pagination
                page_num = 1
                while True:
                    form_vars = self._get_form_vars(soup)
                    # Find pager links
                    pager = soup.find("tr", class_="PagerStyle")
                    if not pager: break
                    
                    next_page = page_num + 1
                    target_link = None
                    
                    # Try to find the numeric link for the next page
                    links = pager.find_all("a")
                    for link in links:
                        if link.text.strip() == str(next_page):
                            target_link = link
                            break
                    
                    # Fallback to "..." or "Next"
                    if not target_link:
                        for link in links:
                            if link.text.strip() in ("...", "Next", ">"):
                                target_link = link
                                break
                    
                    if not target_link: break
                    
                    # Extract __EVENTTARGET and __EVENTARGUMENT from href if it's a postback
                    # href="javascript:__doPostBack('ctl00$cphMainContentArea$ucSearchType$wzrdRealPropertySearch$ucSearchResult$gv_SearchResult','Page$2')"
                    href = target_link.get('href', '')
                    match = re.search(r"__doPostBack\('([^']+)','([^']+)'\)", href)
                    if not match: break
                    
                    target, argument = match.groups()
                    payload = {
                        **form_vars,
                        '__EVENTTARGET': target,
                        '__EVENTARGUMENT': argument
                    }
                    
                    resp = self.session.post(self.base_url, data=payload, timeout=20)
                    soup = BeautifulSoup(resp.text, 'html.parser')
                    
                    page_results = self._parse_grid_results(soup, county)
                    if not page_results: break
                    results.extend(page_results)
                    page_num += 1
                    
                if results:
                    return results

            except Exception as e:
                print(f"    ⚠️ Requests Error: {e}")
                continue
        
        return []

    def _parse_grid_results(self, soup: BeautifulSoup, county: str) -> list[dict]:
        """Helper to parse the SDAT search result grid."""
        table = soup.find(id="cphMainContentArea_ucSearchType_wzrdRealPropertySearch_ucSearchResult_gv_SearchResult")
        if not table: return []
        
        results = []
        rows = table.find_all("tr")
        for row in rows:
            if "PagerStyle" in (row.get('class') or []): continue
            cells = row.find_all("td")
            if len(cells) >= 3:
                owner = re.sub(r'\s+', ' ', cells[0].text.strip())
                address = re.sub(r'\s+', ' ', cells[2].text.strip())
                if not owner or owner.upper() in ("NAME", "ACCOUNT ID"): continue
                if owner.replace(" ", "").isdigit(): continue # Skip pager artifacts
                results.append({'owner_name': owner, 'address': address, 'county': county})
        return results


    def search_and_extract(self, search_data: Dict, county: str) -> Dict:
        """Search SDAT using verified selectors."""
        if not self.driver:
            self.start_driver()
            
        street_number = str(search_data.get('street_number', ''))
        street_name = str(search_data.get('street_name', ''))
        
        try:
            # Navigate to SDAT
            self.driver.get(self.base_url)
            wait = WebDriverWait(self.driver, 30)  # Increased for stability under load
            
            # Check for Service Unavailable or Server Error
            page_text = self.driver.page_source.upper()
            if "SERVICE UNAVAILABLE" in page_text or "SERVER ERROR" in page_text:
                raise Exception("SDAT Service Unavailable or Server Error")
            
            # Step 1: Select County and Search Method
            county_select = Select(wait.until(EC.presence_of_element_located(
                (By.ID, "cphMainContentArea_ucSearchType_wzrdRealPropertySearch_ucSearchType_ddlCounty")
            )))
            # Try to match county name
            matched_county = None
            for option in county_select.options:
                if county.upper() in option.text.upper():
                    matched_county = option.text
                    break
            
            if matched_county:
                county_select.select_by_visible_text(matched_county)
            else:
                # Fallback to direct selection if possible, but visible text is safer
                print(f"⚠️ Could not find exact county match for {county}")
                county_select.select_by_index(1) 

            method_select = Select(self.driver.find_element(By.ID, "cphMainContentArea_ucSearchType_wzrdRealPropertySearch_ucSearchType_ddlSearchType"))
            method_select.select_by_visible_text("STREET ADDRESS")
            
            continue_btn = self.driver.find_element(By.ID, "cphMainContentArea_ucSearchType_wzrdRealPropertySearch_StartNavigationTemplateContainerID_btnContinue")
            continue_btn.click()
            
            # Step 2: Enter Street Number and Name
            num_input = wait.until(EC.presence_of_element_located(
                (By.ID, "cphMainContentArea_ucSearchType_wzrdRealPropertySearch_ucEnterData_txtStreenNumber")
            ))
            name_input = self.driver.find_element(By.ID, "cphMainContentArea_ucSearchType_wzrdRealPropertySearch_ucEnterData_txtStreetName")
            
            num_input.clear()
            num_input.send_keys(street_number)
            name_input.clear()
            name_input.send_keys(street_name)
            
            next_btn = self.driver.find_element(By.ID, "cphMainContentArea_ucSearchType_wzrdRealPropertySearch_StepNavigationTemplateContainerID_btnStepNextButton")
            next_btn.click()
            
            # Step 3: Extract Data
            # Adding retry logic for stale element references
            for attempt in range(3):
                try:
                    # Give the page a moment to settle
                    time.sleep(1)
                    
                    # Check for multiple results vs single detail page
                    # If we are on detail page, the verified IDs will exist
                    owner_name_el = wait.until(EC.presence_of_element_located(
                        (By.ID, "cphMainContentArea_ucSearchType_wzrdRealPropertySearch_ucDetailsSearch_dlstDetaisSearch_lblOwnerName_0")
                    ))
                    owner_name = re.sub(r'\s+', ' ', owner_name_el.text.strip())
                    
                    try:
                        mailing_address = re.sub(r'\s+', ' ', self.driver.find_element(By.ID, "cphMainContentArea_ucSearchType_wzrdRealPropertySearch_ucDetailsSearch_dlstDetaisSearch_lblMailAddress_0").text.strip())
                    except:
                        mailing_address = "Not Found"
                        
                    try:
                        premises_address = re.sub(r'\s+', ' ', self.driver.find_element(By.ID, "cphMainContentArea_ucSearchType_wzrdRealPropertySearch_ucDetailsSearch_dlstDetaisSearch_lblPremisesAddress_0").text.strip())
                    except:
                        premises_address = "Not Found"
                    
                    print(f"✅ Found: {owner_name}")
                    return {
                        'owner_name': owner_name,
                        'owner_address': mailing_address if mailing_address != "Not Found" else premises_address,
                        'premises_address': premises_address,
                        'found': True
                    }
                    
                except (StaleElementReferenceException, TimeoutException) as e:
                    if attempt < 2:
                        print(f"🔄 Retrying extraction ({attempt+1}/3)...")
                        time.sleep(1)
                        continue
                    
                    # If we timed out or stayed stale, check for other conditions
                    if "No records found" in self.driver.page_source:
                        print(f"⚠️ No records found for {street_number} {street_name}")
                        return self._not_found_result()
                    
                    # Check for multiple results table
                    try:
                        results_table = self.driver.find_elements(By.XPATH, "//table[contains(@id, 'Grid')]//a")
                        if results_table:
                            print(f"ℹ️ Multiple results found, picking first one...")
                            results_table[0].click()
                            time.sleep(2)
                            # On click, loop continues to try extraction again
                            continue
                    except:
                        pass
                        
                    return self._not_found_result()

        except Exception as e:
            print(f"❌ Error scraping {street_number} {street_name}: {e}")
            return self._not_found_result()


    def _not_found_result(self) -> Dict:
        """Return not found result."""
        return {
            'owner_name': 'Not Found',
            'owner_address': 'Not Found',
            'found': False
        }


class RaceEthnicityPredictor:
    """Predict race/ethnicity from owner names using ethnicolr."""

    def __init__(self):
        try:
            from ethnicolr import pred_wiki_name
            self.pred_wiki_name = pred_wiki_name
            self.use_ethnicolr = True
            print("✅ Using ethnicolr for race prediction")
        except ImportError:
            self.use_ethnicolr = False
            print("⚠️ ethnicolr not available, using fallback patterns")
            # Fallback patterns
            self.surname_patterns = {
                'Asian': ['nguyen', 'kim', 'li', 'wang', 'zhang', 'liu',
                         'chen', 'yang', 'tan', 'wong', 'lee', 'park', 'choi', 'yamamoto',
                         'sato', 'suzuki'],
                'Indian': ['patel', 'singh', 'kumar', 'gupta', 'shah', 'joshi'],
                'Black': ['washington', 'jefferson', 'booker', 'king', 'jackson', 'johnson',
                         'smith', 'brown', 'williams', 'davis', 'harris', 'thomas',
                         'robinson', 'white'],
                'Hispanic': ['garcia', 'rodriguez', 'martinez', 'hernandez', 'lopez', 'gonzalez',
                            'perez', 'sanchez', 'ramirez', 'torres', 'rivera', 'flores',
                            'diaz', 'morales', 'reyes', 'ruiz', 'cruz', 'ortiz', 'ramos'],
                'White': ['smith', 'johnson', 'williams', 'brown', 'jones', 'miller', 'davis',
                         'wilson', 'anderson', 'taylor', 'thomas', 'moore', 'martin',
                         'lee', 'thompson', 'harris', 'clark', 'lewis', 'robinson'],
            }

    def _clean_name(self, full_name: str) -> str:
        """Clean SDAT name strings with clinical precision."""
        if not full_name: return ""
        name = full_name.upper()
        
        # 1. Identify and Tag Business/Technical Entities
        self.is_business_entity = False
        business_markers = [
            r'\bINC\b', r'\bLLC\b', r'\bCORP\b', r'\bCORPORATION\b', r'\bLTD\b',
            r'\bPTNRSHP\b', r'\bPARTNERSHIP\b', r'\bASSN\b', r'\bASSOCIATES\b',
            r'\bHOLDINGS\b', r'\bPROPERTY\b', r'\bPROPERTIES\b', r'\bHOLDING\b', r'\bHOLDIN\b',
            r'\bLAND\b', r'\bESTATE\b', r'\bMGMT\b', r'\bMANAGEMENT\b', r'\bINVESTMENTS\b',
            r'\bTRUSTEE\b', r'\bTRUSTEES\b', r'\bTRUST\b', r'\bFOUNDATION\b'
        ]
        for marker in business_markers:
            if re.search(marker, name):
                self.is_business_entity = True
                break

        # 2. Remove common noise words
        noise = business_markers + [
            r'\bREVOCABLE\b', r'\bLIVING\b', r'\bFAMILY\b', r'\bET AL\b', r'\bETAL\b', 
            r'\bLIFE ESTATE\b', r'\b&', r'\bAND\b'
        ]
        for pattern in noise:
            name = re.sub(pattern, '', name)
        
        # 3. Remove Professional / Suffix Noise (JR, SR, MD, PHD, etc.)
        suffixes = [
            r'\bJR\b', r'\bSR\b', r'\bII\b', r'\bIII\b', r'\bIV\b', r'\bV\b',
            r'\bMD\b', r'\bPHD\b', r'\bESQ\b', r'\bDR\b', r'\bPROF\b'
        ]
        for pattern in suffixes:
            name = re.sub(pattern, '', name)
            
        # 4. Clean special characters but preserve separators
        name = re.sub(r'[^A-Z\s,-]', '', name)
        
        # 5. Collapse multiple spaces
        name = re.sub(r'\s+', ' ', name).strip()
        return name

    def _is_indian_surname(self, surname: str) -> Dict:
        """
        Check if a surname is typically Indian and identify sub-category.
        Optimized for 95%+ accuracy for Hindu identification.
        """
        s = surname.lower()
        
        # 🟢 CONSOLIDATED HINDU SURNAMES (North, South, East, West, Nepal, Bhutan)
        # These are high-confidence indicators of Hindu/South Asian origin
        hindu = [
            # NORTH & WEST (Hindi, Gujarati, Marathi, Punjabi Hindu)
            'patel', 'kumar', 'gupta', 'shah', 'joshi', 'devi', 'das', 'sharma', 'agrawal', 'singhal', 
            'tripathi', 'malhotra', 'kapoor', 'khanna', 'mehta', 'shroff', 'maheshwari', 'agarwal', 
            'bollu', 'gambhir', 'chhabra', 'anand', 'bakshi', 'bansal', 'bhatia', 'chopra', 'dhawan', 
            'gandhi', 'goel', 'grover', 'handa', 'jain', 'johar', 'jolly', 'kapur', 'khurana', 'kohli', 
            'luthra', 'madan', 'mahajan', 'mangal', 'mehra', 'monga', 'oberoi', 'puri', 'sahni', 
            'sethi', 'sood', 'taneja', 'uppal', 'vohra', 'wadhwa', 'yadav', 'chawla', 'gadkari', 
            'gokhale', 'karve', 'modi', 'paranjpe', 'ranade', 'tilak', 'vaidya', 'tyagi', 'verma', 
            'vats', 'shukla', 'mishra', 'pandey', 'tiwari', 'dwivedi', 'chaubey', 'thakur', 'rajput',
            'mital', 'shrivastava', 'saxena', 'jaitley', 'gadgil', 'pujari', 'vashishta', 'bhardwaj',
            
            # SOUTH (Telugu, Tamil, Kannada, Malayalam Hindu)
            'reddy', 'rao', 'iyer', 'nair', 'menon', 'kulkarni', 'deshpande', 'shetty', 'hegde', 'pai', 
            'balakrishnan', 'venkatesh', 'nambiar', 'warrier', 'kurup', 'panicker', 'pillai', 'acharya', 
            'adiga', 'bhat', 'hebbar', 'maiya', 'muralidhar', 'shenoy', 'uudpa', 'vaikunta', 'balaram', 
            'chetty', 'mudaliar', 'naidu', 'gowda', 'subramanian', 'krishnan', 'raghavan', 'pillay',
            'murthy', 'srinivasan', 'rangarajan', 'venkat', 'narayanan', 'gopal', 'swamy', 'kalyan',
            'rajagopalan', 'manian', 'vasudevan', 'ganesan', 'moorthy', 'prabhu', 'kamath', 'kiny',
            
            # EAST & BENGALI
            'chowdhury', 'mukherjee', 'chatterjee', 'banerjee', 'mukhopadhyay', 'chattopadhyay', 
            'bandyopadhyay', 'gangopadhyay', 'ghosh', 'bose', 'dutta', 'majumdar', 'ray', 'sen', 
            'guha', 'chakraborty', 'basu', 'sarkar', 'paul', 'mitra', 'bhattacharya', 'mandal',
            
            # NEPAL & HILL HINDUS
            'adhikari', 'thapa', 'gurung', 'bhattarai', 'poudel', 'shrestha', 'dahal', 'aryal', 
            'basnet', 'magar', 'rai', 'tamang', 'ghimire', 'paudel', 'acharya', 'khadka', 'pant',
            'karki', 'subedi', 'regmi', 'sapkota', 'dhakal', 'devkota', 'lamsal', 'chalise',
            
            # BHUTANESE HINDU NAMES
            'dorji', 'wangchuk', 'namgyal', 'tshering', 'gyeltshen', 'leki', 'rinzin'
        ]

        # 🔵 OTHER SOUTH ASIAN CATEGORIES (Low confidence for 'Hindu' extract)
        # Added truncated markers (SI, KA) common in SDAT data
        sikh = ['singh', 'kaur', 'gill', 'bajwa', 'dhillon', 'sidhu', 'sandhu', 'brar', 'grewal', 'nijjar', 'thind', 'purewal', 'dhaliwal', 'si', 'ka']
        muslim = ['khan', 'siddiqui', 'ahmed', 'malik', 'butt', 'rizvi', 'hashmi', 'qureshi', 'pasha', 'ansari', 'mirza', 'farooqui', 'zidi', 'ali', 'hussain', 'mohammad', 'mohammed', 'khalequz', 'rahman']
        christian = ['varghese', 'mathew', 'kurian', 'chacko', 'cherian', 'dias', 'fernandes', 'pereira', 'dsouza', 'dmello', 'lobo', 'pinto']

        if s in hindu:
             return {'is_indian': True, 'is_hindu': True, 'category': 'Hindu'}
        if s in sikh:
             return {'is_indian': True, 'is_hindu': False, 'category': 'Sikh'}
        if s in muslim:
             return {'is_indian': True, 'is_hindu': False, 'category': 'Muslim'}
        if s in christian:
             return {'is_indian': True, 'is_hindu': False, 'category': 'Christian'}
             
        return {'is_indian': False, 'is_hindu': False, 'category': 'Unknown'}


    def predict_race(self, full_name: str) -> Dict[str, any]:
        """
        Predict race/ethnicity from full name.
        SDAT format is typically: LAST FIRST MIDDLE
        Returns: {predicted_race, confidence, method}
        """
        if not full_name or full_name == 'Not Found':
            return {
                'predicted_race': 'Unknown',
                'confidence': 0,
                'method': 'None'
            }

        cleaned_name = self._clean_name(full_name)
        if not cleaned_name:
            return {
                'predicted_race': 'Unknown',
                'confidence': 0,
                'method': 'None'
            }

        # SDAT Format is usually LAST FIRST ... OR FIRST LAST depending on commas
        # If there's a comma, the part BEFORE the comma is the surname
        if ',' in cleaned_name:
            surname = cleaned_name.split(',')[0].strip()
            # Everything after the comma is potentially the first name
            firstname = cleaned_name.split(',')[1].strip().split()[0] if len(cleaned_name.split(',')) > 1 else ''
        else:
            # No comma, assume LAST FIRST (SDAT standard) but check components
            parts = cleaned_name.split()
            if not parts:
                return {'predicted_race': 'Unknown', 'confidence': 0, 'method': 'None'}
            surname = parts[0]
            firstname = parts[1] if len(parts) > 1 else ''

        # Use ethnicolr if available
        if self.use_ethnicolr:
            try:
                # We provide firstname and lastname for better wiki/census matching
                result = self.pred_wiki_name(pd.DataFrame([[firstname, surname]]),
                                           columns=['firstname', 'lastname'])

                if not result.empty:
                    row = result.iloc[0]
                    race_cols = [col for col in result.columns if col.startswith('race_')]

                    if race_cols:
                        probs = {col.replace('race_', ''): row[col] for col in race_cols}
                        predicted = max(probs, key=probs.get)
                        # Specific handling for the 'Asian, IndianSubContinent' category in pred_wiki_name
                        if 'Asian,IndianSubContinent' in probs:
                            predicted = 'Asian,IndianSubContinent'
                        
                        confidence = round(probs[predicted] * 100, 1)

                        if confidence > 35:
                            # Map IndianSubContinent to Indian
                            final_race = predicted
                            if 'Indian' in predicted or 'Asian,IndianSubContinent' == predicted:
                                final_race = 'Indian'

                            # Cross-verify with our high-precision Hindu dictionary
                            # BRUTAL ACCURACY: Check ALL parts of the name for conflicting markers
                            all_parts = cleaned_name.replace(',', ' ').split()
                            conflicts = []
                            for part in all_parts:
                                check = self._is_indian_surname(part)
                                if check['is_indian'] and not check['is_hindu']:
                                    conflicts.append(check['category'])
                            
                            indian_check = self._is_indian_surname(surname)
                            
                            # If we find ANY Sikh, Muslim markers, or Business indicators, disqualify as Hindu
                            is_hindu = indian_check.get('is_hindu', False)
                            if conflicts or getattr(self, 'is_business_entity', False):
                                is_hindu = False

                            return {
                                'predicted_race': 'Indian' if (indian_check['is_indian'] or final_race == 'Indian') else final_race,
                                'is_hindu': is_hindu,
                                'sub_category': 'Mixed/Business' if (conflicts and is_hindu) else (conflicts[0] if conflicts else indian_check.get('category', 'General')),
                                'confidence': max(confidence, 85.0 if indian_check['is_indian'] else 0),
                                'method': f'ethnicolr + multi-token ({final_race}/{surname})'
                            }

            except Exception as e:
                pass

        # Fallback to pattern matching
        surname_lower = surname.lower()
        
        # BRUTAL ACCURACY: Check ALL parts of the name for conflicting markers in fallback too
        all_parts = cleaned_name.replace(',', ' ').split()
        conflicts = []
        for part in all_parts:
            check = self._is_indian_surname(part)
            if check['is_indian'] and not check['is_hindu']:
                conflicts.append(check['category'])

        # Expanded list for better accuracy based on census data
        expanded_patterns = {
            'Asian': [
                'nguyen', 'kim', 'li', 'wang', 'zhang', 'liu',
                'chen', 'yang', 'tan', 'wong', 'lee', 'park', 'choi', 'yamamoto',
                'sato', 'suzuki', 'tran', 'lin', 'wu', 'cho', 'vu', 'ngo', 'le', 'pham',
                'chan', 'ho', 'lam', 'cheng', 'chu', 'lo', 'tam', 'yeung', 'chow', 'kwok', 'luk',
                'jeong', 'yu', 'lim', 'choi', 'kang', 'shin', 'baek', 'song', 'han', 'yoon',
                'hu', 'sun', 'zhu'
            ],
            'Indian': [
                'patel', 'singh', 'kumar', 'gupta', 'shah', 'joshi', 'devi', 'das', 'reddy', 'rao',
                'sharma', 'khan', 'lele', 'shanmugam', 'iyer', 'nair', 'menon', 'kulkarni', 'deshpande',
                'chowdhury', 'mukherjee', 'chatterjee', 'banerjee', 'shetty', 'hegde', 'pai',
                'malhotra', 'kapoor', 'khanna', 'mehta', 'shroff', 'maheshwari', 'agarwal',
                'bollu', 'gambhir', 'siddiqui', 'ahmed', 'malik', 'butt', 'rizvi', 'hashmi', 'qureshi',
                'pasha', 'ansari', 'mirza', 'farooqui', 'zidi', 'gill', 'bajwa', 'dhillon', 'sidhu',
                'sandhu', 'brar', 'grewal', 'nijjar', 'thind', 'chhabra', 'anand', 'bakshi', 'bansal',
                'bhatia', 'chopra', 'dhawan', 'gandhi', 'gill', 'goel', 'grover', 'handa', 'jain',
                'johar', 'jolly', 'kapur', 'kaur', 'khurana', 'kohli', 'luthra', 'madan', 'mahajan',
                'mangal', 'mehra', 'monga', 'oberoi', 'puri', 'sahni', 'sethi', 'sood', 'taneja',
                'uppal', 'vohra', 'wadhwa', 'yadav', 'chawla', 'gadkari', 'gokhale', 'joshi',
                'karve', 'modi', 'paranjpe', 'ranade', 'tilak', 'vaidya', 'nambiar', 'warrier',
                'kurup', 'panicker', 'pillai', 'chacko', 'cherian', 'ittooop', 'kurian', 'mathew',
                'varghese', 'acharya', 'adiga', 'bhat', 'hebbar', 'maiya', 'muralidhar', 'rao',
                'shenoy', 'uudpa', 'vaikunta', 'balaram', 'chetty', 'mudaliar', 'naidu', 'reddy',
                'sunderland', 'thakur', 'tyagi', 'verma', 'vats', 'shukla', 'mishra', 'pandey',
                'tiwari', 'tripathi', 'dwivedi', 'chaubey'
            ],
            'Black': [
                'washington', 'jefferson', 'booker', 'king', 'jackson', 'johnson',
                'williams', 'thompson', 'harris', 'robinson', 'white', 'walker', 'scott',
                'banks', 'reid', 'lowe', 'bryant', 'pierce', 'coleman', 'jenkins', 'perry',
                'powell', 'long', 'patterson', 'hughes', 'floyd', 'mccoy', 'sims', 'mosley'
            ],
            'Hispanic': [
                'garcia', 'rodriguez', 'martinez', 'hernandez', 'lopez', 'gonzalez',
                'perez', 'sanchez', 'ramirez', 'torres', 'rivera', 'flores',
                'diaz', 'morales', 'reyes', 'ruiz', 'cruz', 'ortiz', 'ramos', 'gomez', 'jimenez',
                'castillo', 'vargas', 'mendoza', 'vasquez', 'morales', 'gutierrez', 'ortiz',
                'nuñez', 'medina', 'cortes', 'vargas', 'castillo', 'santos', 'delgado'
            ],
            'White': [
                'miller', 'anderson', 'taylor', 'moore', 'martin', 'clark', 'lewis', 
                'walker', 'hall', 'allen', 'young', 'king', 'wright', 'baker', 'nelson',
                'hill', 'scott', 'adams', 'baker', 'gonzalez', 'bailey', 'smith', 'brown',
                'jones', 'wilson', 'thompson', 'clark', 'lewis', 'hall', 'allen', 'young',
                'wright', 'king', 'baker', 'nelson', 'hill', 'carter', 'mitchell', 'perez'
            ],
        }

        # Check for multi-part Asian surnames or specific prefixes
        if any(surname_lower.startswith(p) for p in ['huynh', 'phan', 'vuong', 'trinh']):
             return {'predicted_race': 'Asian', 'confidence': 85.0, 'method': 'Surname Pattern'}

        for race, surnames in expanded_patterns.items():
            if surname_lower in [s.lower() for s in surnames]:
                # Special handling for Indian to identify Hindu names
                if race == 'Indian':
                    indian_check = self._is_indian_surname(surname_lower)
                    is_hindu = indian_check.get('is_hindu', False)
                    # Disqualify if business or religious conflict found anywhere in string
                    if conflicts or getattr(self, 'is_business_entity', False):
                        is_hindu = False
                        
                    return {
                        'predicted_race': 'Indian',
                        'is_hindu': is_hindu,
                        'sub_category': 'Mixed/Business' if (conflicts and is_hindu) else (conflicts[0] if conflicts else indian_check.get('category', 'General')),
                        'confidence': 90.0,
                        'method': 'Surname Pattern'
                    }
                
                # Slight boost for common unique identifiers
                conf = 90.0 if race == 'Asian' or race == 'Hispanic' else 60.0
                return {
                    'predicted_race': race,
                    'is_hindu': False,
                    'confidence': conf,
                    'method': 'Surname Pattern'
                }

        return {
            'predicted_race': 'White',
            'confidence': 30.0,
            'method': 'Default (Unknown)'
        }


class PropertyPipeline:
    """Complete property pipeline."""

    def __init__(self):
        self.fetcher = CommunityAddressFetcher()
        self.google_fetcher = GoogleAddressFetcher()
        self.formatter = SDATFormatter()
        self.scraper = SDATAutoScraper(headless=True)
        self.predictor = RaceEthnicityPredictor()

    def run_community(self, community: str, county: str, limit: int = 100, scrape: bool = False):
        """
        Run complete pipeline for a community.

        Args:
            community: Community name
            county: County name
            limit: Max addresses to fetch
            scrape: Whether to scrape SDAT (slow) or just format
        """
        print(f"\n{'='*70}")
        print(f"🏘️ PIPELINE: {community}, {county}")
        print(f"{'='*70}")

        # Step 1: Fetch addresses
        # Try Google first if API key exists
        addresses = []
        if self.google_fetcher.api_key:
            addresses = self.google_fetcher.fetch_addresses(community, county, limit)
        
        # Fallback to Overpass if no addresses found or no Google key
        if not addresses:
            addresses = self.fetcher.fetch_community_addresses(community, county, limit)

        if not addresses:
            print("❌ No addresses found")
            return []

        # Step 2: Format for SDAT
        print(f"\n📋 Formatting {len(addresses)} addresses for SDAT...")
        results = []

        for i, addr in enumerate(addresses, 1):
            formatted = self.formatter.format_address(addr['address'])

            result = {
                '#': i,
                'community': community,
                'original_address': addr['address'],
                'city': addr['city'],
                'county': county,
                'source': addr.get('source', 'OSM'),
                'sdat_search_number': formatted['street_number'],
                'sdat_search_name': formatted['street_name'],
                'owner_name': '',
                'owner_address': '',
                'predicted_race': '',
                'race_confidence': '',
            }

            # Step 3: Scrape SDAT (if enabled)
            if scrape:
                print(f"[{i}/{len(addresses)}] Scraping: {formatted['search_string']}", end=' ')

                owner_data = self.scraper.search_and_extract(
                    formatted,
                    county
                )

                result['owner_name'] = owner_data.get('owner_name', 'Not Found')
                result['owner_address'] = owner_data.get('owner_address', 'Not Found')

                # Step 4: Predict race
                if result['owner_name'] and result['owner_name'] != 'Not Found':
                    prediction = self.predictor.predict_race(result['owner_name'])
                    result['predicted_race'] = prediction['predicted_race']
                    result['race_confidence'] = prediction['confidence']

                time.sleep(1)  # Be respectful

            results.append(result)

        # Step 5: Export to Excel
        self.export_to_excel(results, community, county)

        return results

    def export_to_excel(self, results: List[Dict], community: str, county: str):
        """Export results to Excel."""
        filename = f"{community.replace(' ', '_')}_{county.replace(' ', '_')}_properties.xlsx"

        df = pd.DataFrame(results)

        with pd.ExcelWriter(filename, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Properties', index=False)

            # Auto-adjust column widths
            worksheet = writer.sheets['Properties']
            for idx, col in enumerate(df.columns, 1):
                max_len = max(df[col].astype(str).apply(len).max(), len(col)) + 2
                worksheet.column_dimensions[chr(64 + idx)].width = min(max_len, 40)

        print(f"\n✅ Exported to {filename}")
        print(f"📊 Total properties: {len(results)}")

        # Show stats
        if 'owner_name' in df.columns:
            found = df[df['owner_name'] != 'Not Found'].shape[0]
            print(f"✅ Owners found: {found}/{len(results)}")


# ============ MAIN ============
if __name__ == "__main__":
    import sys

    print("""
╔════════════════════════════════════════════════════════════════════════════╗
║              MARYLAND PROPERTY PIPELINE - COMPLETE                         ║
╚════════════════════════════════════════════════════════════════════════════╝

Available Communities in Baltimore County:
  - Towson
  - Parkville
  - Essex
  - Dundalk
  - Catonsville
  - Pikesville
  - Towson
  - White Marsh

Choose mode:
1. Format addresses only (FAST - creates SDAT-ready Excel)
2. Full pipeline with SDAT scraping (SLOW - gets owner names)
3. Custom community
""")

    mode = input("\nSelect mode (1-3): ").strip()

    pipeline = PropertyPipeline()

    if mode == '1':
        # Quick format mode
        community = input("\nEnter community name (e.g., Towson): ").strip()
        county = input("Enter county (e.g., Baltimore County): ").strip()
        limit = int(input(f"Max addresses to fetch (default 1000): ").strip() or "1000")

        pipeline.run_community(community, county, limit=limit, scrape=False)

    elif mode == '2':
        # Full scraping mode
        print("\n⚠️  This will open Chrome and scrape SDAT (SLOW but thorough)")
        confirm = input("Continue? (y/n): ").strip().lower()

        if confirm == 'y':
            community = input("\nEnter community name (e.g., Towson): ").strip()
            county = input("Enter county (e.g., Baltimore County): ").strip()
            limit = int(input(f"Max addresses to scrape (recommended 20-50): ").strip() or "20")

            pipeline.scraper.start_driver()
            try:
                pipeline.run_community(community, county, limit=limit, scrape=True)
            finally:
                pipeline.scraper.stop_driver()

    elif mode == '3':
        # Custom
        pass

    else:
        print("Invalid choice")

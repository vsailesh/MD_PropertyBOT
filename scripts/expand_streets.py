#!/usr/bin/env python3
"""
Expand Street Names Database

Fetches comprehensive street names for all Maryland counties from
OpenStreetMap and expands the street names database.

Features:
- Fetches all street names for each Maryland county
- Integrates with CountyMapper for accurate county names
- Integrates with StreetNameCleaner for SDAT formatting
- Filters out already-searched streets
- Handles rate limiting gracefully
"""

import pandas as pd
import os
import sys
import time
import argparse
from typing import Set, List, Dict

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.community_pipeline import CommunityAddressFetcher, SDATFormatter
from src.county_mapper import CountyMapper, normalize_county
from src.street_name_cleaner import StreetNameCleaner


# All 24 Maryland jurisdictions (23 counties + 1 city) in display format
# These match the format commonly used by OpenStreetMap
MARYLAND_JURISDICTIONS = [
    "Allegany",
    "Anne Arundel",
    "Baltimore City",
    "Baltimore",
    "Calvert",
    "Caroline",
    "Carroll",
    "Cecil",
    "Charles",
    "Dorchester",
    "Frederick",
    "Garrett",
    "Harford",
    "Howard",
    "Kent",
    "Montgomery",
    "Prince George's",
    "Queen Anne's",
    "Saint Mary's",
    "Somerset",
    "Talbot",
    "Washington",
    "Wicomico",
    "Worcester",
]


class StreetDatabaseExpander:
    """
    Expands the street names database with comprehensive coverage.

    Features:
    - Fetches streets from OpenStreetMap for all counties
    - Normalizes county names to SDAT format
    - Cleans street names per SDAT rules
    - Filters out already-searched streets
    - Deduplicates across counties
    """

    def __init__(self,
                 database_path: str = "data/MD_street_Names.xlsx",
                 results_path: str = "data/Final_Owner_Results.xlsx"):
        """
        Initialize the expander.

        Args:
            database_path: Path to street names database Excel file
            results_path: Path to results file for filtering
        """
        self.database_path = database_path
        self.results_path = results_path
        self.county_mapper = CountyMapper()
        self.street_cleaner = StreetNameCleaner()
        self.fetcher = CommunityAddressFetcher()

        self.searched_streets: Set[str] = set()
        self._load_searched_streets()

    def _load_searched_streets(self):
        """Load already searched streets from results file."""
        if not os.path.exists(self.results_path):
            print("📝 No existing results file found - starting fresh")
            return

        print(f"📖 Loading searched streets from {self.results_path}...")
        try:
            results_df = pd.read_excel(self.results_path)
            if 'address' in results_df.columns:
                for addr in results_df['address']:
                    if addr and not pd.isna(addr):
                        normalized = SDATFormatter.format_address(str(addr))['street_name']
                        if normalized:
                            self.searched_streets.add(normalized)

                self.searched_streets.discard("")
                print(f"📊 Found {len(self.searched_streets)} unique streets already processed")
        except Exception as e:
            print(f"⚠️ Warning: Could not load results file: {e}")

    def load_existing_database(self) -> pd.DataFrame:
        """Load existing street database."""
        if os.path.exists(self.database_path):
            df = pd.read_excel(self.database_path)
            print(f"📖 Existing database: {len(df)} entries")
            return df
        else:
            print("🆕 Creating new street name database")
            return pd.DataFrame(columns=['Address', 'county'])

    def normalize_county_for_sdat(self, osm_county: str) -> str:
        """
        Convert OSM county name to SDAT format.

        Args:
            osm_county: County name from OpenStreetMap

        Returns:
            SDAT-compliant county name
        """
        normalized = self.county_mapper.normalize(osm_county, source="osm")
        if normalized:
            return normalized

        # Fallback: add "COUNTY" if not present
        county_upper = osm_county.upper().strip()
        if not county_upper.endswith(" COUNTY") and not county_upper.endswith(" CITY"):
            if county_upper == "BALTIMORE CITY":
                return "BALTIMORE CITY"
            else:
                return f"{county_upper} COUNTY"

        return county_upper

    def clean_street_name(self, raw_street: str) -> str:
        """
        Clean street name per SDAT rules.

        Args:
            raw_street: Raw street name from OSM

        Returns:
            Cleaned street name
        """
        # First use SDATFormatter for basic formatting
        formatted = SDATFormatter.format_address(raw_street)
        basic_cleaned = formatted['street_name']

        # Then apply street name cleaner for more thorough cleaning
        thoroughly_cleaned = self.street_cleaner.clean_street_name(basic_cleaned)

        return thoroughly_cleaned if thoroughly_cleaned else basic_cleaned

    def fetch_county_streets(self, jurisdiction: str) -> List[str]:
        """
        Fetch all streets for a jurisdiction.

        Args:
            jurisdiction: Jurisdiction name (e.g., "Montgomery", "Baltimore City")

        Returns:
            List of unique street names
        """
        print(f"\n🌍 Fetching: {jurisdiction}...")

        try:
            streets = self.fetcher.fetch_county_streets(jurisdiction)
            print(f"   Fetched {len(streets)} raw street names")
            return streets
        except Exception as e:
            print(f"❌ Error fetching {jurisdiction}: {e}")
            return []

    def process_county(self, jurisdiction: str) -> List[Dict]:
        """
        Process a single jurisdiction and return new streets.

        Args:
            jurisdiction: Jurisdiction name

        Returns:
            List of new street dictionaries
        """
        # Fetch streets
        raw_streets = self.fetch_county_streets(jurisdiction)
        if not raw_streets:
            return []

        # Normalize county name
        sdat_county = self.normalize_county_for_sdat(jurisdiction)

        # Process streets
        new_streets = []
        filtered_count = 0

        for raw_street in raw_streets:
            # Clean street name
            cleaned = self.clean_street_name(raw_street)

            if not cleaned or len(cleaned) < 2:
                filtered_count += 1
                continue

            # Skip if already searched
            if cleaned in self.searched_streets:
                filtered_count += 1
                continue

            new_streets.append({
                'Address': cleaned,
                'county': sdat_county
            })

        print(f"   Added: {len(new_streets)} unique streets")
        print(f"   Filtered: {filtered_count} (already searched or invalid)")

        return new_streets

    def expand_all(self, jurisdictions: List[str] = None,
                   cooldown: int = 15) -> pd.DataFrame:
        """
        Expand database for all jurisdictions.

        Args:
            jurisdictions: List of jurisdictions (default: all Maryland)
            cooldown: Seconds to wait between requests

        Returns:
            Updated database DataFrame
        """
        if jurisdictions is None:
            jurisdictions = MARYLAND_JURISDICTIONS

        # Load existing database
        existing_df = self.load_existing_database()

        # Collect new streets
        all_new_streets = []
        total_jurisdictions = len(jurisdictions)

        for i, jurisdiction in enumerate(jurisdictions, 1):
            print(f"\n{'='*60}")
            print(f"Progress: {i}/{total_jurisdictions} ({i/total_jurisdictions*100:.1f}%)")

            new_streets = self.process_county(jurisdiction)
            all_new_streets.extend(new_streets)

            # Cooldown between requests
            if i < total_jurisdictions:
                print(f"⏳ Cooling down for {cooldown}s...")
                time.sleep(cooldown)

        if not all_new_streets:
            print("\n⚠️ No new streets found")
            return existing_df

        # Combine with existing
        new_df = pd.DataFrame(all_new_streets)
        combined_df = pd.concat([existing_df, new_df], ignore_index=True)

        # Standardize and deduplicate
        combined_df['Address'] = combined_df['Address'].str.upper().str.strip()
        combined_df['county'] = combined_df['county'].str.upper().str.strip()

        original_len = len(combined_df)
        combined_df = combined_df.drop_duplicates(subset=['Address', 'county'])
        final_len = len(combined_df)

        duplicates_removed = original_len - final_len
        print(f"\n{'='*60}")
        print(f"📊 SUMMARY:")
        print(f"   New streets added: {len(new_df)}")
        print(f"   Duplicates removed: {duplicates_removed}")
        print(f"   Previous total: {len(existing_df)}")
        print(f"   Final total: {final_len}")

        return combined_df

    def save_database(self, df: pd.DataFrame):
        """Save database to Excel."""
        os.makedirs(os.path.dirname(self.database_path), exist_ok=True)
        df.to_excel(self.database_path, index=False, engine='openpyxl')
        print(f"✅ Saved to {self.database_path}")


def main():
    """CLI interface for street database expansion."""
    parser = argparse.ArgumentParser(
        description='Expand street names database with comprehensive coverage',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Expand all Maryland counties
  python scripts/expand_streets.py

  # Expand specific counties only
  python scripts/expand_streets.py --counties Montgomery Howard

  # Use custom database path
  python scripts/expand_streets.py --database data/custom_streets.xlsx

  # Faster expansion (less cooldown)
  python scripts/expand_streets.py --cooldown 5

Available Counties:
  Allegany, Anne Arundel, Baltimore City, Baltimore, Calvert,
  Caroline, Carroll, Cecil, Charles, Dorchester, Frederick, Garrett,
  Harford, Howard, Kent, Montgomery, Prince George's, Queen Anne's,
  Saint Mary's, Somerset, Talbot, Washington, Wicomico, Worcester
        """
    )

    parser.add_argument('--counties', nargs='+',
                       help='Specific counties to process (default: all)')
    parser.add_argument('--database', default='data/MD_street_Names.xlsx',
                       help='Path to street database Excel file')
    parser.add_argument('--results', default='data/Final_Owner_Results.xlsx',
                       help='Path to results file for filtering')
    parser.add_argument('--cooldown', type=int, default=15,
                       help='Seconds to wait between requests (default: 15)')
    parser.add_argument('--no-save', action='store_true',
                       help='Do not save results (dry run)')

    args = parser.parse_args()

    # Create expander
    expander = StreetDatabaseExpander(
        database_path=args.database,
        results_path=args.results
    )

    # Expand
    df = expander.expand_all(
        jurisdictions=args.counties,
        cooldown=args.cooldown
    )

    # Save
    if not args.no_save:
        expander.save_database(df)
    else:
        print("\n🏁 Dry run complete - no files saved")

    return 0


if __name__ == "__main__":
    sys.exit(main())

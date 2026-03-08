#!/usr/bin/env python3
"""
Pull Streets from Maryland ArcGIS Dataset

Downloads and processes the official Maryland Roadway Names dataset from
ArcGIS Open Data portal and adds it to the street names database.

Features:
- Downloads from data-maryland.opendata.arcgis.com
- Integrates with CountyMapper for accurate county names
- Integrates with StreetNameCleaner for SDAT formatting
- Filters out already-searched streets
- Handles CSV parsing robustly
"""

import pandas as pd
import os
import sys
import requests
import io
import argparse
from typing import Set, List, Dict

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.community_pipeline import SDATFormatter
from src.county_mapper import CountyMapper, normalize_county
from src.street_name_cleaner import StreetNameCleaner


# ArcGIS Open Data URL for Maryland Roadway Names
ARCGIS_CSV_URL = "https://data-maryland.opendata.arcgis.com/datasets/maryland::maryland-roadway-names.csv?where=1=1"


class ArcGISStreetImporter:
    """
    Imports streets from Maryland ArcGIS dataset.

    Features:
    - Downloads official roadway names from ArcGIS
    - Normalizes county names to SDAT format
    - Cleans street names per SDAT rules
    - Filters out already-searched streets
    - Merges with existing database
    """

    def __init__(self,
                 database_path: str = "data/MD_street_Names.xlsx",
                 results_path: str = "data/Final_Owner_Results.xlsx"):
        """
        Initialize the importer.

        Args:
            database_path: Path to street names database Excel file
            results_path: Path to results file for filtering
        """
        self.database_path = database_path
        self.results_path = results_path
        self.county_mapper = CountyMapper()
        self.street_cleaner = StreetNameCleaner()

        self.searched_streets: Set[str] = set()
        self._load_searched_streets()

    def _load_searched_streets(self):
        """Load already searched streets from results file."""
        if not os.path.exists(self.results_path):
            print("📝 No existing results file found")
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

    def download_arcgis_data(self, url: str = None) -> pd.DataFrame:
        """
        Download Maryland Roadway Names dataset.

        Args:
            url: ArcGIS CSV URL (default: official Maryland dataset)

        Returns:
            DataFrame with raw roadway data
        """
        if url is None:
            url = ARCGIS_CSV_URL

        print(f"🌐 Downloading Maryland Roadway Names dataset...")
        print(f"   URL: {url[:80]}...")

        try:
            response = requests.get(url, timeout=120)
            response.raise_for_status()
            df = pd.read_csv(io.StringIO(response.text))
            print(f"✅ Downloaded {len(df)} raw roadway entries")
            return df

        except requests.exceptions.Timeout:
            print(f"❌ Timeout downloading from ArcGIS")
            return None
        except requests.exceptions.RequestException as e:
            print(f"❌ Error downloading from ArcGIS: {e}")
            return None
        except Exception as e:
            print(f"❌ Error processing CSV: {e}")
            return None

    def identify_columns(self, df: pd.DataFrame) -> Dict[str, str]:
        """
        Identify the relevant columns in the ArcGIS dataset.

        The ArcGIS dataset may have varying column names over time.
        We try to identify ROAD_NAME and COUNTY_NAME columns.

        Args:
            df: Raw ArcGIS DataFrame

        Returns:
            Dictionary with column names
        """
        # Known column name variations
        road_name_variants = ['ROAD_NAME', 'Road Name', 'ROADNAME', 'road_name',
                             'Name', 'STREET_NAME', 'Street Name']
        county_name_variants = ['COUNTY_NAME', 'County Name', 'COUNTYNAME',
                               'county_name', 'County', 'JURISDICTION']

        road_col = None
        county_col = None

        # Try to find road name column
        for variant in road_name_variants:
            if variant in df.columns:
                road_col = variant
                break

        # Try to find county name column
        for variant in county_name_variants:
            if variant in df.columns:
                county_col = variant
                break

        # Fallback: use positional if needed
        # Based on historical data: ROAD_NAME is usually index 3, COUNTY_NAME is index 19
        if road_col is None and len(df.columns) > 3:
            road_col = df.columns[3]
            print(f"⚠️ Using positional column for road name: {road_col}")

        if county_col is None and len(df.columns) > 19:
            county_col = df.columns[19]
            print(f"⚠️ Using positional column for county name: {county_col}")

        if not road_col or not county_col:
            print(f"❌ Could not identify required columns")
            print(f"   Available columns: {df.columns.tolist()[:10]}...")
            return None

        print(f"✅ Identified columns: road_name='{road_col}', county_name='{county_col}'")
        return {'road_name': road_col, 'county_name': county_col}

    def normalize_county_for_sdat(self, raw_county: str) -> str:
        """
        Convert ArcGIS county name to SDAT format.

        Args:
            raw_county: County name from ArcGIS

        Returns:
            SDAT-compliant county name
        """
        if not raw_county or pd.isna(raw_county):
            return None

        normalized = self.county_mapper.normalize(str(raw_county), source="arcgis")
        return normalized

    def clean_street_name(self, raw_street: str) -> str:
        """
        Clean street name per SDAT rules.

        Args:
            raw_street: Raw street name from ArcGIS

        Returns:
            Cleaned street name
        """
        if not raw_street or pd.isna(raw_street):
            return None

        # First use SDATFormatter for basic formatting
        formatted = SDATFormatter.format_address(str(raw_street))
        basic_cleaned = formatted['street_name']

        # Then apply street name cleaner for more thorough cleaning
        thoroughly_cleaned = self.street_cleaner.clean_street_name(basic_cleaned)

        return thoroughly_cleaned if thoroughly_cleaned else basic_cleaned

    def process_arcgis_data(self, df: pd.DataFrame) -> List[Dict]:
        """
        Process ArcGIS data and return new streets.

        Args:
            df: Raw ArcGIS DataFrame

        Returns:
            List of new street dictionaries
        """
        # Identify columns
        columns = self.identify_columns(df)
        if not columns:
            return []

        road_col = columns['road_name']
        county_col = columns['county_name']

        # Basic cleaning
        df = df.dropna(subset=[road_col, county_col])

        print(f"🧹 Processing {len(df)} valid rows...")

        # Process streets
        new_streets = []
        seen_in_batch = set()
        filtered_count = 0
        invalid_county_count = 0

        for _, row in df.iterrows():
            raw_name = str(row[road_col])
            raw_county = str(row[county_col])

            # Normalize county
            sdat_county = self.normalize_county_for_sdat(raw_county)
            if not sdat_county:
                invalid_county_count += 1
                continue

            # Clean street name
            cleaned_street = self.clean_street_name(raw_name)
            if not cleaned_street or len(cleaned_street) < 2:
                filtered_count += 1
                continue

            # Skip if already searched
            if cleaned_street in self.searched_streets:
                filtered_count += 1
                continue

            # Deduplicate within batch
            key = (cleaned_street, sdat_county)
            if key in seen_in_batch:
                filtered_count += 1
                continue

            new_streets.append({
                'Address': cleaned_street,
                'county': sdat_county
            })
            seen_in_batch.add(key)

        print(f"✨ Found {len(new_streets)} unique, new search targets")
        print(f"   Filtered: {filtered_count} (duplicates or already searched)")
        if invalid_county_count > 0:
            print(f"   Invalid counties: {invalid_county_count}")

        return new_streets

    def load_existing_database(self) -> pd.DataFrame:
        """Load existing street database."""
        if os.path.exists(self.database_path):
            df = pd.read_excel(self.database_path)
            print(f"📖 Existing database: {len(df)} entries")
            return df
        else:
            return pd.DataFrame(columns=['Address', 'county'])

    def merge_and_save(self, new_streets: List[Dict]) -> bool:
        """
        Merge new streets with existing database and save.

        Args:
            new_streets: List of new street dictionaries

        Returns:
            True if successful
        """
        if not new_streets:
            print("⚠️ No new streets to add")
            return True

        # Load existing
        existing_df = self.load_existing_database()

        # Combine
        new_df = pd.DataFrame(new_streets)
        combined_df = pd.concat([existing_df, new_df], ignore_index=True)

        # Standardize
        combined_df['Address'] = combined_df['Address'].str.upper().str.strip()
        combined_df['county'] = combined_df['county'].str.upper().str.strip()

        # Deduplicate
        original_len = len(combined_df)
        combined_df = combined_df.drop_duplicates(subset=['Address', 'county'])
        final_len = len(combined_df)

        print(f"\n📊 MERGE SUMMARY:")
        print(f"   New streets: {len(new_df)}")
        print(f"   Previous total: {len(existing_df)}")
        print(f"   Duplicates removed: {original_len - final_len}")
        print(f"   Final total: {final_len}")

        # Save
        try:
            os.makedirs(os.path.dirname(self.database_path), exist_ok=True)
            combined_df.to_excel(self.database_path, index=False, engine='openpyxl')
            print(f"✅ Saved to {self.database_path}")
            return True
        except Exception as e:
            print(f"❌ Error saving: {e}")
            return False

    def run(self, url: str = None) -> bool:
        """
        Run the full import process.

        Args:
            url: ArcGIS CSV URL (optional)

        Returns:
            True if successful
        """
        # Download
        df = self.download_arcgis_data(url)
        if df is None:
            return False

        # Process
        new_streets = self.process_arcgis_data(df)
        if not new_streets:
            print("⚠️ No new streets found in ArcGIS dataset")
            return True

        # Merge and save
        return self.merge_and_save(new_streets)


def main():
    """CLI interface for ArcGIS street import."""
    parser = argparse.ArgumentParser(
        description='Import streets from Maryland ArcGIS dataset',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Import from default ArcGIS dataset
  python scripts/pull_arcgis_streets.py

  # Use custom database path
  python scripts/pull_arcgis_streets.py --database data/custom_streets.xlsx

  # Use custom ArcGIS URL
  python scripts/pull_arcgis_streets.py --url https://...

  # Dry run (don't save)
  python scripts/pull_arcgis_streets.py --dry-run

The ArcGIS dataset contains official Maryland roadway names
from data-maryland.opendata.arcgis.com
        """
    )

    parser.add_argument('--database', default='data/MD_street_Names.xlsx',
                       help='Path to street database Excel file')
    parser.add_argument('--results', default='data/Final_Owner_Results.xlsx',
                       help='Path to results file for filtering')
    parser.add_argument('--url', help='Custom ArcGIS CSV URL')
    parser.add_argument('--dry-run', action='store_true',
                       help='Download and process but do not save')

    args = parser.parse_args()

    # Create importer
    importer = ArcGISStreetImporter(
        database_path=args.database,
        results_path=args.results
    )

    if args.dry_run:
        print("🏁 DRY RUN MODE - No files will be saved\n")

    # Run import
    if args.dry_run:
        df = importer.download_arcgis_data(args.url)
        if df is not None:
            new_streets = importer.process_arcgis_data(df)
            if new_streets:
                print(f"\n✅ Would add {len(new_streets)} streets (dry run)")
        return 0
    else:
        success = importer.run(args.url)
        return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())

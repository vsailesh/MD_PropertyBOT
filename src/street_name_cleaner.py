#!/usr/bin/env python3
"""
Street Name Cleaner and Duplicate Detector

Implements SDAT (Maryland Department of Assessments and Taxation) official
street name formatting rules with duplicate detection and normalization.

Based on official SDAT Search Help documentation:
https://dat.maryland.gov/realproperty/Documents/SearchHelp.pdf

Features:
- SDAT-compliant street name normalization
- Duplicate detection (after normalization)
- Alternate name handling (McHenry vs Mc Henry, Saint vs St, etc.)
- Punctuation and special character handling
- Comprehensive suffix and direction removal
"""

import re
import pandas as pd
from typing import List, Dict, Set, Tuple, Optional
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)


class StreetNameCleaner:
    """
    SDAT-compliant street name cleaner with duplicate detection.

    Implements official SDAT search rules:
    1. Remove ALL street suffixes (AVE, ST, DR, PL, WAY, etc.)
    2. Remove ALL directions (NORTH, SOUTH, EAST, WEST, etc.)
    3. Handle punctuation: St. Mary's -> ST MARYS, O'Donnell stays
    4. Handle Saint vs St interchangeably
    5. Handle Mc/Mac prefixes (McHenry vs Mc Henry)
    6. Handle numbered streets (use ordinals: 25TH, 33RD)
    7. Handle alternate names (Baltimore National Pike -> Balto Natl)
    """

    # Comprehensive street suffixes from SDAT
    SUFFIXES = {
        'AVE', 'AVENUE', 'AV', 'ST', 'STREET', 'DR', 'DRIVE', 'RD', 'ROAD',
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
        'HTS', 'KY', 'KEY', 'KLS', 'KEYS', 'Knl', 'KNOL', 'KNOLL', 'KNLS',
        'KNOLLS', 'LDG', 'LODGE', 'MNR', 'MANOR', 'MNRS', 'MANORS',
        'MEADOW', 'MDW', 'MDWS', 'MEADOWS', 'ML', 'MILL', 'MLS', 'MILLS',
        'MSN', 'MISSION', 'MT', 'MOUNT', 'PT', 'POINT', 'PTS', 'POINTS',
        'PRT', 'PORT', 'PR', 'PRAIRIE', 'SHL', 'SHOAL', 'SHLS', 'SHOALS',
        'SHR', 'SHORE', 'SHRS', 'SHORES', 'SPG', 'SPRING', 'SPGS', 'SPRINGS',
        'STEA', 'STEAK', 'STR', 'STRA', 'STRAV', 'STRAVE', 'STRAVENUE',
        'STRAVN', 'STRVENUE', 'TRCE', 'TRACE', 'TRFY', 'TRAFFICWAY',
        'UN', 'UNION', 'VLY', 'VALLEY', 'VL', 'VILLE', 'VW', 'VIEW',
        'VLY', 'VALLEY', 'VL', 'VILLE', 'VW', 'VIEW', 'VIS', 'VISTA',
        'WALK', 'WALL', 'WAY', 'WELL', 'WELLS', 'WING', 'STR', 'STRA', 'STRAV',
        'STRAVE', 'STRAVENUE', 'STRAVN', 'STRVENUE'
    }

    # Unit indicators to be stripped (along with everything after them)
    UNIT_INDICATORS = {
        'STE', 'SUITE', 'UNIT', 'APT', 'APARTMENT', 'BSMT', 'BASEMENT',
        'FL', 'FLOOR', 'RM', 'ROOM', 'BLDG', 'BUILDING', 'REAR', 'LOBBY',
        'OFFICE', 'PENTHOUSE', 'PH'
    }

    # Route prefixes (numbers following these are part of the street name)
    ROUTE_PREFIXES = {
        'MD', 'US', 'RT', 'ROUTE', 'STATE', 'SR', 'I', 'HWY', 'HIGHWAY'
    }

    # Direction indicators
    DIRECTIONS = {
        'N', 'NORTH', 'S', 'SOUTH', 'E', 'EAST', 'W', 'WEST',
        'NE', 'NORTHEAST', 'NW', 'NORTHWEST', 'SE', 'SOUTHEAST', 'SW', 'SOUTHWEST'
    }

    # Known alternate names (based on SDAT documentation and common MD streets)
    # Format: normalized_name -> [variant1, variant2, ...]
    ALTERNATE_NAMES = {
        'BALTIMORE NATIONAL': ['BALTIMORE NATL', 'BALTO NATIONAL', 'BALTO NATL', 'BALTIMORE NAT PIKE'],
        'FREDERICK ROAD': ['FREDERICK RD', 'FREDK RD', 'FREDERICK'],
        'GEORGIA AVENUE': ['GEORGIA AVE', 'GEORGIA'],
        'ROCKVILLE PIKE': ['ROCKVILLE PIKE', 'ROCKVILLE PK', 'ROCKVILLE'],
        'WISCONSIN AVENUE': ['WISCONSIN AVE', 'WISCONSIN'],
        'CONNECTICUT AVENUE': ['CONNECTICUT AVE', 'CONNECTICUT', 'CONN AVE'],
        'MASSACHUSETTS AVENUE': ['MASSACHUSETTS AVE', 'MASSACHUSETTS', 'MASS AVE'],
        'COLUMBIA PIKE': ['COLUMBIA PIKE', 'COLUMBIA PK'],
        'ARLINGTON BOULEVARD': ['ARLINGTON BLVD', 'ARLINGTON'],
        'RITCHIE ROAD': ['RITCHIE RD', 'RITCHIE'],
        'BALTIMORE ANnapolis': ['B&A', 'BALTIMORE ANNAPOLIS'],
        'WASHINGTON BOULEVARD': ['WASHINGTON BLVD', 'WASH BLVD'],
    }

    # Ordinal patterns (25TH, 33RD, 1ST, 2ND)
    ORDINAL_PATTERN = re.compile(r'^\d+(ST|ND|RD|TH)$')

    def __init__(self, strict: bool = True):
        """
        Initialize the street name cleaner.

        Args:
            strict: If True, apply strict SDAT rules. If False, be more lenient.
        """
        self.strict = strict
        self._build_alternate_map()

    def _build_alternate_map(self):
        """Build reverse lookup map for alternate names."""
        self._alternate_to_canonical = {}
        for canonical, alternates in self.ALTERNATE_NAMES.items():
            for alt in alternates:
                self._alternate_to_canonical[self._normalize_key(alt)] = canonical

    def _normalize_key(self, name: str) -> str:
        """Create a normalized key for comparison."""
        return re.sub(r'[^A-Z0-9]', '', name.upper())

    def clean_street_name(self, street: str) -> str:
        """
        Clean a street name according to SDAT rules.

        Args:
            street: Raw street name

        Returns:
            Cleaned street name
        """
        if not street or pd.isna(street):
            return ""

        # Convert to uppercase and strip
        street = str(street).upper().strip()

        # Remove leading/trailing punctuation
        street = street.strip('.,;:-_\'"()[]{}')

        # Special handling: St. -> SAINT, but keep ST if already there
        street = re.sub(r'\bst\.', 'SAINT ', street + ' ')[:-1]
        street = street.replace('ST.', 'SAINT ')

        # Handle apostrophes (SDAT rule: remove 'S but keep O' type)
        street = re.sub(r"'S\b", "S", street)  # MARY'S -> MARYS

        # Split into parts
        parts = street.split()

        # Remove leading numbers (address numbers)
        cleaned_parts = []
        skip_leading_numbers = True

        for part in parts:
            # Clean each part of non-alpha characters (except hyphens, apostrophes)
            clean_part = re.sub(r"[^A-Z0-9'-]", "", part)

            if not clean_part:
                continue

            # Skip leading address numbers
            if skip_leading_numbers:
                if clean_part.isdigit():
                    continue
                # Skip number ranges like 13400-13408
                if re.match(r'^\d+-\d+$', clean_part):
                    continue
                # Skip letter-suffixed numbers like 10109-B
                if re.match(r'^\d+[-]?[A-Z]$', clean_part):
                    continue
                skip_leading_numbers = False

            # Keep ordinal numbers (25TH, 33RD) - these are street names
            if self.ORDINAL_PATTERN.match(clean_part):
                cleaned_parts.append(clean_part)
                continue

            # NEW: Stop if we hit a unit indicator
            if clean_part in self.UNIT_INDICATORS:
                break

            # NEW: Strip trailing numbers unless they follow a route prefix
            if clean_part.isdigit() and not self.ORDINAL_PATTERN.match(clean_part):
                prev_part = cleaned_parts[-1] if cleaned_parts else ""
                if prev_part not in self.ROUTE_PREFIXES:
                    # If this is a trailing number after a non-route name, it's likely a unit
                    # We only skip it if it's not the ONLY word so far (e.g., "1" is handled later)
                    if cleaned_parts:
                        break

            # Remove suffixes
            if clean_part in self.SUFFIXES:
                continue

            # Remove directions
            if clean_part in self.DIRECTIONS:
                continue

            cleaned_parts.append(clean_part)

        # Reassemble
        cleaned = ' '.join(cleaned_parts).strip()

        # Handle Saint vs St (SDAT prefers ST)
        cleaned = re.sub(r'\bSAINT\b', 'ST', cleaned)

        # Handle Mc/Mac prefixes (McHenry vs MC HENRY)
        # SDAT says try both, we normalize to one form
        cleaned = self._normalize_mc_mac(cleaned)

        # Final cleanup of multiple spaces
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()

        # SDAT Rule: Search term must be descriptive. 
        # Reject single letters or purely numeric names that aren't ordinals.
        if len(cleaned) <= 1:
            return ""
        if cleaned.isdigit():
            return ""

        return cleaned

    def _normalize_mc_mac(self, name: str) -> str:
        """Normalize Mc/Mac prefixes (McHenry -> MC HENRY)."""
        # For SDAT, we keep them together for consistency
        # McHENRY stays MC HENRY, not MC H E N R Y
        return name

    def normalize_for_search(self, street: str, county: str = '') -> str:
        """
        Normalize street name for SDAT search.

        This creates a canonical form used for duplicate detection.

        Args:
            street: Raw street name
            county: County name (optional)

        Returns:
            Normalized street key
        """
        cleaned = self.clean_street_name(street)

        # Check against alternate names
        key = self._normalize_key(cleaned)
        if key in self._alternate_to_canonical:
            cleaned = self._alternate_to_canonical[key]

        # Create search key (remove all spaces for maximum matching)
        search_key = re.sub(r'\s+', '', cleaned)

        # Add county if provided
        if county:
            search_key += f"_{self._normalize_key(county)}"

        return search_key

    def find_duplicates(self, streets: List[Dict[str, str]],
                       county_col: str = 'county',
                       street_col: str = 'street_name') -> Dict[str, List[Dict]]:
        """
        Find duplicate street names in a list.

        Args:
            streets: List of dictionaries with street information
            county_col: Name of county column
            street_col: Name of street column

        Returns:
            Dictionary with duplicate groups:
            {
                'canonical_name': [
                    {original street 1 info},
                    {original street 2 info},
                    ...
                ],
                ...
            }
        """
        # Group by normalized key
        groups = defaultdict(list)

        for idx, street_info in enumerate(streets):
            street = street_info.get(street_col, '')
            county = street_info.get(county_col, '')

            normalized = self.normalize_for_search(street, county)

            # Store with original info
            groups[normalized].append({
                'index': idx,
                'original': street,
                'county': county,
                'cleaned': self.clean_street_name(street),
                **street_info
            })

        # Find groups with more than one entry (duplicates)
        duplicates = {
            key: group
            for key, group in groups.items()
            if len(group) > 1
        }

        return duplicates

    def remove_duplicates(self, streets: List[Dict[str, str]],
                         county_col: str = 'county',
                         street_col: str = 'street_name',
                         keep: str = 'first') -> Tuple[List[Dict], Dict]:
        """
        Remove duplicates from a list of streets.

        Args:
            streets: List of dictionaries with street information
            county_col: Name of county column
            street_col: Name of street column
            keep: Which duplicate to keep ('first', 'last', 'shortest', 'longest')

        Returns:
            Tuple of (deduplicated_list, removed_duplicates_info)
        """
        duplicates = self.find_duplicates(streets, county_col, street_col)

        if not duplicates:
            return streets, {'removed': 0, 'groups': 0}

        # Track indices to remove
        indices_to_remove = set()
        removed_info = []

        for canonical_key, group in duplicates.items():
            # Determine which to keep
            if keep == 'first':
                keep_idx = 0
            elif keep == 'last':
                keep_idx = -1
            elif keep == 'shortest':
                keep_idx = min(range(len(group)), key=lambda i: len(group[i]['original']))
            elif keep == 'longest':
                keep_idx = max(range(len(group)), key=lambda i: len(group[i]['original']))
            else:
                keep_idx = 0

            # Mark others for removal
            for i, item in enumerate(group):
                if i != keep_idx:
                    indices_to_remove.add(item['index'])
                    removed_info.append({
                        'canonical_key': canonical_key,
                        'original': item['original'],
                        'county': item.get('county', ''),
                        'cleaned': item['cleaned'],
                        'kept_original': group[keep_idx]['original']
                    })

        # Create deduplicated list
        deduplicated = [
            street for idx, street in enumerate(streets)
            if idx not in indices_to_remove
        ]

        return deduplicated, {
            'removed': len(removed_info),
            'groups': len(duplicates),
            'details': removed_info
        }

    def clean_dataframe(self, df: pd.DataFrame,
                       street_col: str = 'street_name',
                       county_col: str = 'county',
                       remove_dups: bool = True) -> Tuple[pd.DataFrame, Dict]:
        """
        Clean a pandas DataFrame of street names.

        Args:
            df: DataFrame with street information
            street_col: Name of street column
            county_col: Name of county column
            remove_dups: Whether to remove duplicates

        Returns:
            Tuple of (cleaned_dataframe, cleaning_stats)
        """
        # Make a copy to avoid modifying original
        df_cleaned = df.copy()

        # Add cleaned street name column
        df_cleaned['cleaned_street'] = df_cleaned[street_col].apply(self.clean_street_name)

        # Remove invalid entries
        before_count = len(df_cleaned)
        df_cleaned = df_cleaned[df_cleaned['cleaned_street'] != '']
        df_cleaned = df_cleaned[df_cleaned['cleaned_street'] != 'UNKNOWN']
        after_count = len(df_cleaned)

        # Convert to list for duplicate detection
        streets_list = df_cleaned.to_dict('records')

        stats = {
            'original_count': before_count,
            'invalid_removed': before_count - after_count,
            'before_dedup': after_count
        }

        if remove_dups:
            streets_list, dup_info = self.remove_duplicates(
                streets_list,
                county_col=county_col,
                street_col='cleaned_street',
                keep='first'
            )

            stats.update({
                'duplicates_removed': dup_info['removed'],
                'duplicate_groups': dup_info['groups'],
                'final_count': len(streets_list)
            })

            # Store removed duplicates for review
            df_cleaned = pd.DataFrame(streets_list)
            df_removed = pd.DataFrame(dup_info['details'])

            stats['removed_dataframe'] = df_removed

        return df_cleaned, stats


class StreetNameVariationGenerator:
    """
    Generate variations of street names for SDAT search.

    SDAT recommends trying variations when initial search fails.
    """

    @staticmethod
    def generate_variations(street: str, county: str = '') -> List[str]:
        """
        Generate search variations for a street name.

        Args:
            street: Cleaned street name
            county: County name (optional)

        Returns:
            List of variation strings to try
        """
        variations = [street]

        # Variation 1: Try with Mc/Mac split (McHenry -> Mc Henry)
        if 'MC' in street and not street.startswith('MC '):
            split_mc = re.sub(r'MC([A-Z])', r'MC \1', street)
            if split_mc != street:
                variations.append(split_mc)

        # Variation 2: Try SAINT instead of ST
        if street.startswith('ST '):
            variations.append(re.sub(r'^ST ', 'SAINT ', street))

        # Variation 3: Try without hyphens
        if '-' in street:
            variations.append(street.replace('-', ''))
            variations.append(street.replace('-', ' '))

        # Variation 4: Try with different spacing
        # (if multiple spaces exist, try single space)
        if '  ' in street:
            variations.append(re.sub(r'\s+', ' ', street))

        # Remove duplicates while preserving order
        seen = set()
        unique_variations = []
        for v in variations:
            if v not in seen:
                seen.add(v)
                unique_variations.append(v)

        return unique_variations


def export_duplicates_report(duplicates: Dict, output_path: str):
    """
    Export a duplicates report to Excel.

    Args:
        duplicates: Duplicates dictionary from find_duplicates
        output_path: Path to output Excel file
    """
    rows = []

    for canonical_key, group in duplicates.items():
        for item in group:
            rows.append({
                'Canonical Key': canonical_key,
                'Original Street': item['original'],
                'Cleaned Street': item['cleaned'],
                'County': item.get('county', ''),
                'Index': item['index']
            })

    if rows:
        df = pd.DataFrame(rows)
        df.to_excel(output_path, index=False)
        print(f"📄 Duplicates report exported to: {output_path}")
    else:
        print("✅ No duplicates found to report.")


# ============ CLI INTERFACE ============

def main():
    """CLI interface for street name cleaning."""
    import argparse

    parser = argparse.ArgumentParser(
        description='Clean and Deduplicate Street Names per SDAT Rules',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Clean an Excel file
  python -m src.street_name_cleaner clean data/MD_street_Names.xlsx \\
      --output data/cleaned_streets.xlsx

  # Show duplicates without removing them
  python -m src.street_name_cleaner dedupe data/MD_street_Names.xlsx \\
      --report data/duplicates_report.xlsx

  # Clean and remove duplicates in one step
  python -m src.street_name_cleaner clean data/MD_street_Names.xlsx \\
      --output data/cleaned_streets.xlsx --remove-dups

  # Clean a single street name
  python -m src.street_name_cleaner test "Main Street" "Montgomery"
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # Clean command
    clean_parser = subparsers.add_parser('clean', help='Clean street names in Excel file')
    clean_parser.add_argument('input', help='Input Excel file')
    clean_parser.add_argument('--output', required=True, help='Output Excel file')
    clean_parser.add_argument('--street-col', default='street_name', help='Street column name')
    clean_parser.add_argument('--county-col', default='county', help='County column name')
    clean_parser.add_argument('--remove-dups', action='store_true', help='Remove duplicates')
    clean_parser.add_argument('--no-dups', action='store_true', help='Skip duplicate detection')

    # Dedupe command
    dedupe_parser = subparsers.add_parser('dedupe', help='Find and report duplicates')
    dedupe_parser.add_argument('input', help='Input Excel file')
    dedupe_parser.add_argument('--report', help='Output report file')
    dedupe_parser.add_argument('--street-col', default='street_name', help='Street column name')
    dedupe_parser.add_argument('--county-col', default='county', help='County column name')

    # Test command
    test_parser = subparsers.add_parser('test', help='Test cleaning on a single street')
    test_parser.add_argument('street', help='Street name to test')
    test_parser.add_argument('county', nargs='?', default='', help='County name')

    args = parser.parse_args()

    cleaner = StreetNameCleaner()

    if args.command == 'clean':
        # Load Excel
        df = pd.read_excel(args.input)
        print(f"📖 Loaded {len(df)} rows from {args.input}")

        # Clean
        remove_dups = args.remove_dups and not args.no_dups
        df_cleaned, stats = cleaner.clean_dataframe(
            df,
            street_col=args.street_col,
            county_col=args.county_col,
            remove_dups=remove_dups
        )

        # Report stats
        print(f"\n📊 Cleaning Statistics:")
        print(f"   Original rows: {stats['original_count']:,}")
        print(f"   Invalid removed: {stats['invalid_removed']:,}")
        print(f"   Before dedup: {stats['before_dedup']:,}")
        if remove_dups:
            print(f"   Duplicates removed: {stats['duplicates_removed']:,}")
            print(f"   Duplicate groups: {stats['duplicate_groups']:,}")
        print(f"   Final rows: {stats['final_count']:,}")

        # Export
        df_cleaned.to_excel(args.output, index=False)
        print(f"\n✅ Cleaned data exported to: {args.output}")

        if remove_dups and 'removed_dataframe' in stats:
            # Export removed duplicates for review
            removed_path = args.output.replace('.xlsx', '_removed.xlsx')
            stats['removed_dataframe'].to_excel(removed_path, index=False)
            print(f"📄 Removed duplicates saved to: {removed_path}")

    elif args.command == 'dedupe':
        # Load Excel
        df = pd.read_excel(args.input)
        print(f"📖 Loaded {len(df)} rows from {args.input}")

        # Find duplicates
        streets_list = df.to_dict('records')
        duplicates = cleaner.find_duplicates(
            streets_list,
            county_col=args.county_col,
            street_col=args.street_col
        )

        if duplicates:
            print(f"\n🔍 Found {len(duplicates)} duplicate groups:")
            for key, group in list(duplicates.items())[:10]:
                print(f"\n  Group: {key}")
                for item in group:
                    print(f"    - {item['original']} ({item.get('county', 'N/A')})")

            if len(duplicates) > 10:
                print(f"\n  ... and {len(duplicates) - 10} more groups")

            # Export report if requested
            if args.report:
                export_duplicates_report(duplicates, args.report)
        else:
            print("✅ No duplicates found!")

    elif args.command == 'test':
        cleaned = cleaner.clean_street_name(args.street)
        normalized = cleaner.normalize_for_search(args.street, args.county)
        variations = StreetNameVariationGenerator.generate_variations(cleaned, args.county)

        print(f"\n🧪 Street Name Test:")
        print(f"   Original: {args.street}")
        print(f"   Cleaned: {cleaned}")
        print(f"   Normalized: {normalized}")
        print(f"\n   Search Variations:")
        for i, v in enumerate(variations, 1):
            print(f"      {i}. {v}")


if __name__ == "__main__":
    main()

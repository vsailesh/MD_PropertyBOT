#!/usr/bin/env python3
"""
County Name Mapper for Maryland SDAT

Provides standardized mapping between various county name formats used by:
- OpenStreetMap (OSM)
- Maryland ArcGIS datasets
- SDAT (official format)
- GeoJSON files
"""

from typing import Dict, Optional, Set
import re

# Official SDAT county names (from dropdown)
SDAT_COUNTIES = {
    "ALLEGANY COUNTY",
    "ANNE ARUNDEL COUNTY",
    "BALTIMORE CITY",
    "BALTIMORE COUNTY",
    "CALVERT COUNTY",
    "CAROLINE COUNTY",
    "CARROLL COUNTY",
    "CECIL COUNTY",
    "CHARLES COUNTY",
    "DORCHESTER COUNTY",
    "FREDERICK COUNTY",
    "GARRETT COUNTY",
    "HARFORD COUNTY",
    "HOWARD COUNTY",
    "KENT COUNTY",
    "MONTGOMERY COUNTY",
    "PRINCE GEORGE'S COUNTY",
    "QUEEN ANNE'S COUNTY",
    "ST. MARY'S COUNTY",
    "SOMERSET COUNTY",
    "TALBOT COUNTY",
    "WASHINGTON COUNTY",
    "WICOMICO COUNTY",
    "WORCESTER COUNTY",
}

# Common variations and their mappings to SDAT format
COUNTY_MAPPINGS: Dict[str, str] = {
    # Base name -> SDAT format
    "allegany": "ALLEGANY COUNTY",
    "anne arundel": "ANNE ARUNDEL COUNTY",
    "baltimore city": "BALTIMORE CITY",
    "baltimore": "BALTIMORE COUNTY",  # Default to county for just "Baltimore"
    "calvert": "CALVERT COUNTY",
    "caroline": "CAROLINE COUNTY",
    "carroll": "CARROLL COUNTY",
    "cecil": "CECIL COUNTY",
    "charles": "CHARLES COUNTY",
    "dorchester": "DORCHESTER COUNTY",
    "frederick": "FREDERICK COUNTY",
    "garrett": "GARRETT COUNTY",
    "harford": "HARFORD COUNTY",
    "howard": "HOWARD COUNTY",
    "kent": "KENT COUNTY",
    "montgomery": "MONTGOMERY COUNTY",
    "prince george": "PRINCE GEORGE'S COUNTY",
    "prince george's": "PRINCE GEORGE'S COUNTY",
    "prince georges": "PRINCE GEORGE'S COUNTY",
    "queen anne": "QUEEN ANNE'S COUNTY",
    "queen anne's": "QUEEN ANNE'S COUNTY",
    "queen annes": "QUEEN ANNE'S COUNTY",
    "saint mary": "ST. MARY'S COUNTY",
    "st. mary": "ST. MARY'S COUNTY",
    "st mary": "ST. MARY'S COUNTY",
    "st. mary's": "ST. MARY'S COUNTY",
    "somerset": "SOMERSET COUNTY",
    "talbot": "TALBOT COUNTY",
    "washington": "WASHINGTON COUNTY",
    "wicomico": "WICOMICO COUNTY",
    "worcester": "WORCESTER COUNTY",
}

# OSM-specific formats
OSM_COUNTY_MAPPINGS: Dict[str, str] = {
    "Allegany": "ALLEGANY COUNTY",
    "Allegany County": "ALLEGANY COUNTY",
    "Anne Arundel": "ANNE ARUNDEL COUNTY",
    "Anne Arundel County": "ANNE ARUNDEL COUNTY",
    "Baltimore City": "BALTIMORE CITY",
    "Baltimore": "BALTIMORE COUNTY",
    "Baltimore County": "BALTIMORE COUNTY",
    "Calvert": "CALVERT COUNTY",
    "Calvert County": "CALVERT COUNTY",
    "Caroline": "CAROLINE COUNTY",
    "Caroline County": "CAROLINE COUNTY",
    "Carroll": "CARROLL COUNTY",
    "Carroll County": "CARROLL COUNTY",
    "Cecil": "CECIL COUNTY",
    "Cecil County": "CECIL COUNTY",
    "Charles": "CHARLES COUNTY",
    "Charles County": "CHARLES COUNTY",
    "Dorchester": "DORCHESTER COUNTY",
    "Dorchester County": "DORCHESTER COUNTY",
    "Frederick": "FREDERICK COUNTY",
    "Frederick County": "FREDERICK COUNTY",
    "Garrett": "GARRETT COUNTY",
    "Garrett County": "GARRETT COUNTY",
    "Harford": "HARFORD COUNTY",
    "Harford County": "HARFORD COUNTY",
    "Howard": "HOWARD COUNTY",
    "Howard County": "HOWARD COUNTY",
    "Kent": "KENT COUNTY",
    "Kent County": "KENT COUNTY",
    "Montgomery": "MONTGOMERY COUNTY",
    "Montgomery County": "MONTGOMERY COUNTY",
    "Prince George's": "PRINCE GEORGE'S COUNTY",
    "Prince George's County": "PRINCE GEORGE'S COUNTY",
    "Queen Anne's": "QUEEN ANNE'S COUNTY",
    "Queen Anne's County": "QUEEN ANNE'S COUNTY",
    "St. Mary's": "ST. MARY'S COUNTY",
    "St. Mary's County": "ST. MARY'S COUNTY",
    "Somerset": "SOMERSET COUNTY",
    "Somerset County": "SOMERSET COUNTY",
    "Talbot": "TALBOT COUNTY",
    "Talbot County": "TALBOT COUNTY",
    "Washington": "WASHINGTON COUNTY",
    "Washington County": "WASHINGTON COUNTY",
    "Wicomico": "WICOMICO COUNTY",
    "Wicomico County": "WICOMICO COUNTY",
    "Worcester": "WORCESTER COUNTY",
    "Worcester County": "WORCESTER COUNTY",
}

# ArcGIS dataset specific mappings (these often have different formats)
ARCgis_COUNTY_MAPPINGS: Dict[str, str] = {
    "Allegany County": "ALLEGANY COUNTY",
    "Anne Arundel County": "ANNE ARUNDEL COUNTY",
    "Baltimore City": "BALTIMORE CITY",
    "Baltimore County": "BALTIMORE COUNTY",
    "Calvert County": "CALVERT COUNTY",
    "Caroline County": "CAROLINE COUNTY",
    "Carroll County": "CARROLL COUNTY",
    "Cecil County": "CECIL COUNTY",
    "Charles County": "CHARLES COUNTY",
    "Dorchester County": "DORCHESTER COUNTY",
    "Frederick County": "FREDERICK COUNTY",
    "Garrett County": "GARRETT COUNTY",
    "Harford County": "HARFORD COUNTY",
    "Howard County": "HOWARD COUNTY",
    "Kent County": "KENT COUNTY",
    "Montgomery County": "MONTGOMERY COUNTY",
    "Prince George's County": "PRINCE GEORGE'S COUNTY",
    "Queen Anne's County": "QUEEN ANNE'S COUNTY",
    "St. Mary's County": "ST. MARY'S COUNTY",
    "Somerset County": "SOMERSET COUNTY",
    "Talbot County": "TALBOT COUNTY",
    "Washington County": "WASHINGTON COUNTY",
    "Wicomico County": "WICOMICO COUNTY",
    "Worcester County": "WORCESTER COUNTY",
}


class CountyMapper:
    """
    Mapper for normalizing county names to SDAT format.

    Handles various input formats:
    - "Montgomery" -> "MONTGOMERY COUNTY"
    - "Montgomery County" -> "MONTGOMERY COUNTY"
    - "MONTGOMERY" -> "MONTGOMERY COUNTY"
    - "Prince George's" -> "PRINCE GEORGE'S COUNTY"
    """

    def __init__(self):
        # Build reverse mapping for quick lookup
        self._lookup: Dict[str, str] = {}

        # Add all mappings, case-insensitive
        for mapping in [COUNTY_MAPPINGS, OSM_COUNTY_MAPPINGS, ARCgis_COUNTY_MAPPINGS]:
            for key, value in mapping.items():
                self._lookup[key.lower()] = value
                self._lookup[key] = value

        # Add SDAT counties themselves
        for county in SDAT_COUNTIES:
            self._lookup[county.lower()] = county
            self._lookup[county] = county

    def normalize(self, county: str, source: str = "auto") -> Optional[str]:
        """
        Normalize a county name to SDAT format.

        Args:
            county: Raw county name
            source: Source type ("osm", "arcgis", "sdat", "auto")

        Returns:
            Normalized county name in SDAT format, or None if invalid
        """
        if not county or pd.isna(county):
            return None

        county = str(county).strip()

        # Direct lookup (case-insensitive)
        if county.lower() in self._lookup:
            return self._lookup[county.lower()]

        # Try with " County" suffix
        if not county.lower().endswith(" county") and not county.lower().endswith(" city"):
            with_suffix = f"{county} county"
            if with_suffix.lower() in self._lookup:
                return self._lookup[with_suffix.lower()]

        # Try without " County" suffix
        cleaned = re.sub(r'\s+(county|city)\s*$', '', county, flags=re.IGNORECASE)
        if cleaned.lower() in self._lookup:
            return self._lookup[cleaned.lower()]

        # Handle special cases
        normalized = self._handle_special_cases(county)
        if normalized:
            return normalized

        # Could not find a match
        return None

    def _handle_special_cases(self, county: str) -> Optional[str]:
        """Handle special county name cases."""
        county_lower = county.lower()

        # Saint/St variations
        if "st." in county_lower or "st " in county_lower or "saint" in county_lower:
            # St. Mary's / Saint Mary's
            if "mary" in county_lower:
                return "ST. MARY'S COUNTY"

        # Prince George variations
        if "prince" in county_lower and "george" in county_lower:
            return "PRINCE GEORGE'S COUNTY"

        # Queen Anne variations
        if "queen" in county_lower and "anne" in county_lower:
            return "QUEEN ANNE'S COUNTY"

        return None

    def is_valid_sdat_county(self, county: str) -> bool:
        """Check if a county name is in SDAT format."""
        if not county:
            return False
        return county.upper() in SDAT_COUNTIES

    def get_all_sdat_counties(self) -> Set[str]:
        """Get all SDAT county names."""
        return set(SDAT_COUNTIES)

    def get_sdat_county_code(self, county: str) -> Optional[str]:
        """
        Get the SDAT county code (01-24).

        Returns:
            Two-digit code or None if not found
        """
        normalized = self.normalize(county)
        if not normalized:
            return None

        # SDAT codes are in order
        sdat_list = sorted(SDAT_COUNTIES)
        try:
            index = sdat_list.index(normalized)
            return f"{index + 1:02d}"
        except ValueError:
            return None


# Singleton instance
_mapper = None

def get_mapper() -> CountyMapper:
    """Get the singleton CountyMapper instance."""
    global _mapper
    if _mapper is None:
        _mapper = CountyMapper()
    return _mapper


# Convenience functions
def normalize_county(county: str, source: str = "auto") -> Optional[str]:
    """Normalize a county name to SDAT format."""
    return get_mapper().normalize(county, source)


def is_valid_county(county: str) -> bool:
    """Check if a county name is valid."""
    return get_mapper().is_valid_sdat_county(county)


# Import pandas for type checking
try:
    import pandas as pd
except ImportError:
    pd = None


if __name__ == "__main__":
    # Test the mapper
    mapper = CountyMapper()

    test_cases = [
        "Montgomery",
        "Montgomery County",
        "MONTGOMERY COUNTY",
        "Howard",
        "Prince George's",
        "Prince George's County",
        "St. Mary's",
        "Baltimore",
        "Baltimore City",
        "Queen Anne's",
        "Invalid County",
    ]

    print("🧪 Testing County Mapper:")
    print("-" * 60)
    for test in test_cases:
        normalized = mapper.normalize(test)
        status = "✅" if normalized else "❌"
        print(f'{status} "{test}" -> "{normalized}"')

    print()
    print("📋 All SDAT Counties:")
    for county in sorted(SDAT_COUNTIES):
        code = mapper.get_sdat_county_code(county)
        print(f"  {code}: {county}")

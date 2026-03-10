# Street Name Cleaning and Duplicate Detection

## Overview

The Street Name Cleaner module implements official SDAT (Maryland Department of Assessments and Taxation) formatting rules for street name standardization and duplicate detection.

This module is integrated into `robust_bulk_search.py` and runs **before** the ML filter, providing a preprocessing pipeline:

1. **Load streets from Excel**
2. **Clean street names** (SDAT standard) ← New
3. **Remove duplicates** ← New
4. **Apply ML filter** (No Result predictions)
5. **Run searches** on remaining streets

## SDAT Rules Implemented

Based on official SDAT Search Help documentation:
https://dat.maryland.gov/realproperty/Documents/SearchHelp.pdf

### 1. Remove Street Suffixes
All street suffixes are removed from any position in the street name:
- AVE, AVENUE
- ST, STREET
- DR, DRIVE
- RD, ROAD
- LN, LANE
- WAY, CT, COURT
- PL, PLACE
- CIR, CIRCLE
- BLVD, BOULEVARD
- And 30+ more suffixes

**Examples:**
- `Main Street` → `MAIN`
- `Washington Boulevard` → `WASHINGTON`
- `25th Avenue` → `25TH`

### 2. Remove Directions
All cardinal and ordinal directions are removed:
- N, NORTH, S, SOUTH
- E, EAST, W, WEST
- NE, NORTHEAST, NW, NORTHWEST
- SE, SOUTHEAST, SW, SOUTHWEST

**Example:**
- `North Main Street` → `MAIN`

### 3. Handle Punctuation
- `St. Mary's` → `ST MARYS` (possessive S removed)
- `O'Donnell` → `O'DONNELL` (apostrophe preserved)

### 4. Handle Saint vs St
- `Saint John` → `ST JOHN`
- `St. Mary's` → `ST MARYS`

### 5. Remove Leading Numbers
All address numbers are stripped:
- `123 Main Street` → `MAIN`
- `13400-13408 Balto Natl` → `BALTO NATL`
- `10109-B Industrial` → `INDUSTRIAL`

### 6. Preserve Ordinals
Ordinal street names are preserved:
- `25TH Street` → `25TH`
- `33RD Avenue` → `33RD`
- `1ST Street` → `1ST`

## Duplicate Detection

### How It Works

Duplicates are detected by:
1. Cleaning each street name using SDAT rules
2. Normalizing (removing spaces)
3. Grouping by county + normalized street name

**Example:**
```
Main Street, Montgomery → MAIN_MONTGOMERY
MAIN ST, Montgomery     → MAIN_MONTGOMERY  (Duplicate!)
Main St, Montgomery     → MAIN_MONTGOMERY  (Duplicate!)
```

### Duplicate Removal Strategy

By default, the **first occurrence** is kept when duplicates are found. You can change this behavior:

- `keep='first'` - Keep the first occurrence (default)
- `keep='last'` - Keep the last occurrence
- `keep='shortest'` - Keep the shortest original street name
- `keep='longest'` - Keep the longest original street name

## Usage

### As Part of Bulk Search (Recommended)

The cleaner is automatically enabled when running bulk searches:

```bash
# Run with all optimizations enabled (default)
python scripts/robust_bulk_search.py run -i data/MD_street_Names.xlsx

# Run with 50 workers
python scripts/robust_bulk_search.py run -i data/MD_street_Names.xlsx -w 50

# Disable street name cleaning
python scripts/robust_bulk_search.py run -i data/MD_street_Names.xlsx --no-clean

# Disable duplicate removal
python scripts/robust_bulk_search.py run -i data/MD_street_Names.xlsx --no-dedup
```

### Standalone CLI

You can also use the cleaner as a standalone tool:

```bash
# Clean an Excel file and remove duplicates
python -m src.street_name_cleaner clean data/MD_street_Names.xlsx \
    --output data/cleaned_streets.xlsx --remove-dups

# Find and report duplicates without removing them
python -m src.street_name_cleaner dedupe data/MD_street_Names.xlsx \
    --report data/duplicates_report.xlsx

# Test a single street name
python -m src.street_name_cleaner test "Main Street" "Montgomery"
```

### Python API

```python
from src.street_name_cleaner import StreetNameCleaner

cleaner = StreetNameCleaner()

# Clean a single street name
cleaned = cleaner.clean_street_name("123 Main Street")
# Returns: "MAIN"

# Check for duplicates in a list
streets = [
    {'street_name': 'Main Street', 'county': 'Montgomery'},
    {'street_name': 'MAIN ST', 'county': 'Montgomery'},
]

duplicates = cleaner.find_duplicates(streets, 'county', 'street_name')
# Returns: {'MAIN_MONTGOMERY': [...]}

# Remove duplicates from a list
deduplicated, info = cleaner.remove_duplicates(streets, keep='first')
```

## Output Reports

When duplicates are removed, a detailed report is automatically saved:

- **File**: `data/duplicates_YYYYMMDD_HHMMSS.xlsx`
- **Contents**: All removed duplicates with:
  - Original street name
  - Cleaned street name
  - County
  - Which street was kept instead

## Performance Impact

Based on typical datasets:

- **Duplicate removal**: ~10-20% reduction in street count
- **Invalid entries**: ~1-5% additional reduction
- **Combined with ML filter**: ~40-50% total reduction

This means:
- **Faster searches** - Fewer streets to process
- **Reduced SDAT load** - Fewer API calls
- **Cleaner data** - Consistent formatting

## Troubleshooting

**Too many streets removed:**
- Check the duplicates report to see what was removed
- Use `--no-dedup` to disable duplicate removal
- Review cleaned street names with `--no-clean`

**Street not found after cleaning:**
- SDAT may require variations (McHenry vs Mc Henry)
- The cleaner generates variations automatically
- Check the rejected streets report

**Apostrophes handled inconsistently:**
- This is correct per SDAT rules
- `O'Donnell` keeps apostrophe (name)
- `Mary's` loses apostrophe (possessive)

## Integration with ML Filter

The cleaning pipeline works synergistically with the ML filter:

```
Raw Streets (10,000)
    ↓ Clean & Dedupe
Cleaned Streets (8,500)  ← Removed: 1,500 (15%)
    ↓ ML Filter
Searchable Streets (5,500)  ← Filtered: 3,000 (35%)
    ↓ Run Searches
```

The ML filter is trained on **cleaned** street names, so it benefits from the normalization performed by the cleaner.

## Advanced: Custom Suffixes

To add custom suffixes, modify `src/street_name_cleaner.py`:

```python
class StreetNameCleaner:
    SUFFIXES = {
        # ... existing suffixes ...
        'MYPATH',  # Add custom suffix here
    }
```

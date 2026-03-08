# Maryland Property Explorer

A robust toolset to scrape, analyze, and visualize Maryland property ownership data with crash-proof SQLite storage and resume capability.

## Project Structure

```
maryland-property-agent/
├── src/
│   ├── community_pipeline.py   # SDAT scraper, race prediction, address fetching
│   └── robust_database.py      # SQLite engine with atomic writes & resume
├── scripts/
│   ├── robust_bulk_search.py   # Main search CLI (run, resume, export)
│   ├── geocode_results.py      # Add coordinates via Nominatim
│   ├── radius_street_gatherer.py  # Fetch streets by radius
│   ├── pull_arcgis_streets.py  # ArcGIS street integration
│   ├── expand_streets.py       # Expand street coverage via OSM
│   └── audit_ethnicolr_accuracy.py  # Validate race predictions
├── data/
│   ├── property_search.db      # Main SQLite database (crash-proof)
│   ├── Radius_5mi_Streets.xlsx # Streets to search
│   └── *.xlsx                  # Source data files
├── dashboard/                  # Streamlit visualization
└── docs/
    └── ROBUST_SYSTEM_GUIDE.md  # Detailed usage guide
```

## Quick Start

### 1. Setup
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Run Bulk Search (Crash-Proof)
```bash
# Start new search
python scripts/robust_bulk_search.py run -i data/Radius_5mi_Streets.xlsx

# Resume after interruption (automatic)
python scripts/robust_bulk_search.py run
```

### 3. Export Results
```bash
# Export all properties
python scripts/robust_bulk_search.py export -o data/Results.xlsx

# Export only Hindu owners
python scripts/robust_bulk_search.py export --hindu-only -o data/Hindu_Owners.xlsx
```

### 4. Monitor Progress
```bash
# Show statistics
python scripts/robust_bulk_search.py stats

# List all batches
python scripts/robust_bulk_search.py list

# Run integrity check
python scripts/robust_bulk_search.py check
```

### 5. View Dashboard
```bash
streamlit run dashboard/app.py
```

## Key Features

| Feature | Description |
|---------|-------------|
| **Atomic Writes** | SQLite transactions prevent data corruption |
| **Resume Capability** | Continue from exact interruption point |
| **Auto Backups** | Periodic snapshots every 100 streets |
| **Race Prediction** | ethnicolr ML-based ethnicity detection |
| **Hindu Identification** | Specialized Hindu owner extraction |
| **Geocoding** | Nominatim (OpenStreetMap) coordinates |

## Data Sources

- **SDAT**: Maryland State Department of Assessments & Taxation
- **OpenStreetMap**: Street names and address data
- **ArcGIS**: Geographic boundaries and street data
- **ethnicolr**: Race/ethnicity prediction from names

## CLI Commands

```bash
# Run or resume search
python scripts/robust_bulk_search.py run [OPTIONS]
  -i, --input FILE         Excel file with streets
  --resume ID              Resume specific batch
  --save-interval N        Save every N streets (default: 10)
  --backup-interval N      Backup every N streets (default: 100)

# Export to Excel
python scripts/robust_bulk_search.py export [OPTIONS]
  -o, --output FILE        Output path (default: data/Final_Owner_Results.xlsx)
  --hindu-only             Export only Hindu owners

# Status commands
python scripts/robust_bulk_search.py stats    # Show statistics
python scripts/robust_bulk_search.py list     # List batches
python scripts/robust_bulk_search.py check    # Integrity check
```

## Database Schema

| Table | Purpose |
|-------|---------|
| `properties` | Property records with checksums |
| `search_progress` | Per-street search status |
| `batches` | Job metadata and statistics |
| `integrity_checks` | Validation history |

See [docs/ROBUST_SYSTEM_GUIDE.md](docs/ROBUST_SYSTEM_GUIDE.md) for detailed documentation.

## License

MIT

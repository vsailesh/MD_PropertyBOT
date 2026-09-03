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

# Export geocoded Hindu owners (dashboard format, single source of truth = DB)
python scripts/export_mapped.py -o data/Hindu_Origin_Owners_Mapped.xlsx
```

### 4. Geocode (single DB-side pass)
```bash
# ArcGIS default; uses data/Hindu_Origin_Owners_Mapped.xlsx as coordinate cache
python scripts/geocode_database.py --workers 8

# Hindu owners are prioritized automatically, then remaining properties
```

### 5. Monitor Progress
```bash
# Show statistics
python scripts/robust_bulk_search.py stats

# List all batches
python scripts/robust_bulk_search.py list

# Run integrity check
python scripts/robust_bulk_search.py check
```

### 6. View Dashboard
```bash
streamlit run dashboard/app.py
```

### 7. Upload to Turso (remote DB)
```bash
export TURSO_TOKEN=<your token>   # never hardcode; revoked tokens stay revoked
python data/async_upload_to_turso.py
```
Resumable — progress saved to `data/.upload_progress_async.json`. Token/URL also read from `.env` (gitignored). Note: the uploader runs on ANY argv — there is no `--help`.

### 8. Full Pipeline (one command)
```bash
python scripts/pipeline.py                # audit → scrape gaps → backfill → export
python scripts/pipeline.py --statewide    # also queue never-searched streets first
python scripts/pipeline.py --skip-scrape  # no SDAT traffic (blocked hours)
python scripts/pipeline.py --upload       # also sync to Turso (off by default — rate-limited)
```
Scrape step drains through Cloudflare blocks via the watchdog (10-min backoff loop).

## Keeping Data Fresh

- **Weekly rotation** (launchd `com.marylandproperty.refresh`, Sun 02:07): re-scrapes the 2,000 stalest streets with **replace semantics** — a street's old rows are deleted before the new ones land, so owner changes and vanished parcels actually update. Full state cycles every ~3 months.
- **Refresh a batch manually**: `python scripts/robust_bulk_search.py run -i FILE --force --replace`
- **Statewide completeness**: `python scripts/statewide_streets.py --create-job` — queues MDP-bulk streets never searched (batches drain oldest-first).
- **Coverage audit**: `python scripts/coverage_audit.py --gaps data/coverage_gaps.xlsx` — address-level coverage vs MDP bulk ground truth; the gap file feeds straight back into a refresh scrape.
- **Watchdog**: `./scripts/sdat_watchdog.sh [-i FILE --force --replace]` — single-instance (lock-guarded); retries every 10 min until the pending queue drains.
- **Bulk data refresh** (coords/city/zip): `python scripts/bulk_backfill.py --download` — Socrata re-download, resumable.

## Key Features

| Feature | Description |
|---------|-------------|
| **Atomic Writes** | SQLite transactions prevent data corruption |
| **Resume Capability** | Continue from exact interruption point |
| **Auto Backups** | Periodic snapshots every 100 streets |
| **Race Prediction** | ethnicolr ML-based ethnicity detection |
| **Hindu Identification** | Specialized Hindu owner extraction |
| **Geocoding** | Single DB-side pass (ArcGIS/Nominatim) with Excel coordinate cache — no double geocoding |

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
  --force                  Re-scrape streets even if already completed
  --replace                Refresh semantics: delete a street's old rows before
                           saving new ones (pair with --force)
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

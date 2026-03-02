# 🏠 Maryland Property Explorer (Scraper & Dashboard)

A robust toolset to scrape, analyze, and visualize Maryland property ownership data with a focus on demographic identification.

## 📁 Project Structure

| Folder | Contents |
|--------|----------|
| `src/` | Core logic and pipeline classes (`community_pipeline.py`) |
| `scripts/` | Execution scripts for bulk searching, filtering, and geocoding |
| `data/` | Excel databases and scraped property results |
| `dashboard/` | Streamlit-based interactive map and analytics |
| `tests/` | Verification suite for SDAT formatting rules |
| `docs/` | Debug logs and HTML snapshots |

## 🚀 Getting Started

### 1. Setup Environment
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Search for Properties
To run a bulk search for streets defined in `data/MD_street Names.xlsx`:
```bash
python3 scripts/bulk_street_search_final.py
```

### 3. Geocode Results (Free/OpenSource)
To add coordinates to your results using Nominatim (OpenStreetMap):
```bash
python3 scripts/geocode_results.py
```

### 4. Launch Dashboard
```bash
streamlit run dashboard/app.py
```

## 🛠️ Key Scripts

- `scripts/filter_streets.py`: Cleans and deduplicates street names using strict SDAT rules.
- `scripts/extract_hindu_owners.py`: Identifies owners of Hindu/Indian origin using ML predictors.
- `scripts/expand_streets.py`: Fetches more street names from OpenStreetMap for broader coverage.

---
**Data Source**: Maryland SDAT | **Geoding**: Nominatim | **Dashboard**: Streamlit

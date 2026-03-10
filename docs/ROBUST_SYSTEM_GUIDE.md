# Robust Bulk Search System - User Guide

## Overview

The new **SQLite-based robust search system** eliminates data corruption risks and provides:

- **Atomic writes** - No partial saves or corrupted Excel files
- **Resume capability** - Continue from exactly where you left off after interruption
- **Batch processing** - Organized, trackable search jobs
- **Automatic backups** - Periodic snapshots for safety
- **Data integrity** - Checksums and validation

## Architecture

```
data/
├── property_search.db          # Main SQLite database (crash-proof)
├── property_search.db.backup_*  # Automatic backups
└── Final_Owner_Results.xlsx    # Only created on successful export
```

### Database Schema

| Table | Purpose |
|-------|---------|
| `properties` | All found property records with checksums |
| `search_progress` | Track each street's search status |
| `batches` | Job metadata and statistics |
| `integrity_checks` | Validation history |

## Quick Start

### 1. Start a Fresh Search

```bash
# Activate virtual environment
source venv/bin/activate

# Run search (will create new job)
python scripts/robust_bulk_search.py run -i data/Radius_5mi_Streets.xlsx
```

### 2. Resume After Interruption

```bash
# Automatically resume latest incomplete job
python scripts/robust_bulk_search.py run

# Or resume specific batch ID
python scripts/robust_bulk_search.py run --resume 1
```

### 3. Export Results

```bash
# Export all properties
python scripts/robust_bulk_search.py export -o data/Results.xlsx

# Export only Hindu owners
python scripts/robust_bulk_search.py export --hindu-only -o data/Hindu_Owners.xlsx
```

### 4. Check Progress

```bash
# Show statistics
python scripts/robust_bulk_search.py stats

# List all batches
python scripts/robust_bulk_search.py list

# Run integrity check
python scripts/robust_bulk_search.py check
```

## Command Reference

### `run` - Start/Resume Search

| Option | Description |
|--------|-------------|
| `-i FILE` | Excel file with streets to search |
| `--resume ID` | Resume specific batch ID |
| `--name NAME` | Custom job name |
| `--save-interval N` | Save every N streets (default: 10) |
| `--backup-interval N` | Backup every N streets (default: 100) |

### `export` - Export to Excel

| Option | Description |
|--------|-------------|
| `-o FILE` | Output file path (default: `data/Final_Owner_Results.xlsx`) |
| `--hindu-only` | Export only Hindu owners |

### `stats` - Show Statistics

Displays total properties, unique streets, race/county breakdowns.

### `list` - List Batches

Shows all search batches with status and progress.

### `check` - Integrity Check

Validates database integrity and reports any issues.

## How It Prevents Data Loss

### Problem 1: Excel Corruption
**Old system**: Writing directly to Excel, interruption = corrupted file

**New system**:
- SQLite with WAL (Write-Ahead Logging) mode
- Atomic transactions - all-or-nothing writes
- Excel export only happens on explicit request

### Problem 2: Lost Progress
**Old system**: Restart = search from beginning

**New system**:
- Each street tracked individually
- `search_progress` table records status per street
- Resume continues from last completed street

### Problem 3: No Recovery
**Old system**: 197K records lost, no backup

**New system**:
- Automatic backups every N streets
- Manual backup command available
- Multiple `.backup_*` files retained

## Recovery Scenarios

### Scenario 1: System Crash
```bash
# Just run resume - system picks up where it left off
python scripts/robust_bulk_search.py run
```

### Scenario 2: Database Corruption
```bash
# Restore from most recent backup
ls data/property_search.db.backup*
# Copy the latest backup over the main database
cp data/property_search.db.backup_20260304_012345 data/property_search.db
```

### Scenario 3: Want to Restart Fresh
```bash
# Delete database and start over
rm data/property_search.db
rm data/property_search.db-*
python scripts/robust_bulk_search.py run -i data/Radius_5mi_Streets.xlsx
```

## Monitoring Progress

During execution, you'll see:

```
🚀 RUNNING BATCH: Search_20260304_012345
📊 Batch ID: 1

🔍 Searching: MAIN ST in Prince George's
  ✅ Found 45 properties
📊 Progress: 10/2519 streets | 450 properties | 0 failed
💾 Checkpoint: 10 streets processed
```

Press `Ctrl+C` to gracefully stop - current street will finish and progress saves.

## Data Export Format

Exported Excel includes:

| Column | Description |
|--------|-------------|
| Street | Formatted street name |
| County | Maryland county |
| Owner Name | Property owner from SDAT |
| Address | Full property address |
| City | Property city |
| Predicted Race | Ethnicity prediction |
| Confidence % | Prediction confidence |
| Is Hindu | Hindu owner flag |
| Sub-Category | Indian sub-category (Hindu/Sikh/etc) |

## Performance Expectations

- **2,519 streets** to process
- ~1-3 seconds per street (variable)
- **Estimated time**: 1-3 hours total
- Progress saves every 10 streets
- Backups every 100 streets

## Troubleshooting

### "Database locked" error
- Another process is using the database
- Close all connections and retry

### "Scraper restart failed"
- WebDriver issue
- Script will attempt automatic restart
- If persistent, restart the script

### Export shows no data
- Run `stats` to verify database has records
- Check batch status with `list`
- Ensure batch completed successfully

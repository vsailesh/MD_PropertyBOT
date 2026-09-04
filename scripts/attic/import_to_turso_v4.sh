#!/bin/bash

DB="/Users/midas/Documents/AI Agents/maryland-property-agent/data/property_search.db"
TURSO="/Users/midas/.local/bin/turso"
DB_NAME="property-search"
BATCH=2000

# Fresh start
echo "Creating fresh database..."
$TURSO db destroy "$DB_NAME" -y 2>/dev/null || true
sleep 1
$TURSO db create "$DB_NAME" >/dev/null 2>&1
sleep 2  # Wait for DB to be fully ready

echo "Importing to: $DB_NAME"

# Create all tables first
echo "Creating tables..."
for table in search_progress properties batches integrity_checks schema_info; do
    echo "  - $table"
    schema=$(sqlite3 "$DB" ".schema $table" | sed 's/AUTOINCREMENT//g' | sed 's/ --.*//' | grep -v "CREATE INDEX" | head -1)
    echo "$schema" | $TURSO db shell "$DB_NAME" 2>&1 | grep -v Connecting
done

echo "Tables created. Starting import..."
sleep 1

import_table() {
    local table=$1
    echo "=== $table ==="

    local total=$(sqlite3 "$DB" "SELECT COUNT(*) FROM $table")
    echo "Rows: $total"

    if [ "$total" = "0" ]; then
        echo "Skipping empty table"
        return
    fi

    local offset=0
    while [ $offset -lt $total ]; do
        sqlite3 "$DB" ".mode insert $table" "SELECT * FROM $table LIMIT $BATCH OFFSET $offset;" 2>/dev/null | \
        $TURSO db shell "$DB_NAME" 2>&1 | grep -v Connecting | grep -i error || true

        offset=$((offset + BATCH))
        echo -ne "\rProgress: $((offset > total ? total : offset))/$total    "
    done
    echo ""
}

# Import all tables
import_table "batches"
import_table "integrity_checks"
import_table "schema_info"
import_table "search_progress"
import_table "properties"

echo ""
echo "=== Creating indexes ==="
for table in search_progress properties; do
    sqlite3 "$DB" ".schema $table" | grep "CREATE INDEX" | while read idx; do
        echo "$idx" | $TURSO db shell "$DB_NAME" 2>&1 | grep -v Connecting || true
    done
done

echo ""
echo "=== Verification ==="
$TURSO db shell "$DB_NAME" "SELECT 'search_progress', (SELECT COUNT(*) FROM search_progress)
UNION ALL SELECT 'properties', (SELECT COUNT(*) FROM properties)
UNION ALL SELECT 'batches', (SELECT COUNT(*) FROM batches);" 2>&1 | grep -v Connecting

#!/usr/bin/env python3
"""
Robust SQLite Database Manager for Maryland Property Search
Provides atomic writes, batch processing, and crash recovery.
"""

import sqlite3
import json
import os
import re
import hashlib
import threading
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from contextlib import contextmanager
import pandas as pd

from src.community_pipeline import SDATFormatter


class PropertyDatabase:
    """
    Robust SQLite-based storage for property search results.

    Features:
    - Atomic writes (transactions)
    - Batch processing with checkpoints
    - Progress tracking for resume capability
    - Data integrity validation
    - Automatic backups
    - Concurrent write protection
    """

    SCHEMA_VERSION = 1

    def __init__(self, db_path: str = "data/property_search.db"):
        """
        Initialize the database.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self.lock = threading.Lock()
        self._ensure_directories()
        self._initialize_schema()

    def _ensure_directories(self):
        """Ensure database directory exists."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

    @contextmanager
    def _transaction(self):
        """
        Context manager for safe transactions with automatic rollback on error.
        Uses threading lock for safe concurrent operations.
        """
        with self.lock:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA journal_mode=WAL")  # Write-Ahead Logging for better crash recovery
            conn.execute("PRAGMA wal_autocheckpoint=1000")  # Checkpoint frequency for IO stability
            conn.execute("PRAGMA journal_size_limit=104857600")  # Keep journal size under control (100MB)
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=NORMAL")  # Balance safety and performance

            try:
                yield conn
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                conn.close()

    def _initialize_schema(self):
        """Create database schema if it doesn't exist."""
        with self._transaction() as conn:
            # Properties table - main data storage
            conn.execute("""
                CREATE TABLE IF NOT EXISTS properties (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    street_name TEXT NOT NULL,
                    county TEXT NOT NULL,
                    owner_name TEXT,
                    address TEXT,
                    city TEXT,
                    state TEXT DEFAULT 'MD',
                    zip_code TEXT,
                    source_street TEXT,  -- Original input street name
                    searched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    -- Race prediction fields
                    predicted_race TEXT,
                    race_confidence REAL,
                    race_method TEXT,
                    is_hindu BOOLEAN DEFAULT 0,
                    sub_category TEXT,
                    -- Geocoding fields
                    latitude REAL,
                    longitude REAL,
                    -- Metadata
                    batch_id INTEGER,
                    checksum TEXT,
                    UNIQUE(county, owner_name, address)
                )
            """)

            # Search progress table - for resume capability
            conn.execute("""
                CREATE TABLE IF NOT EXISTS search_progress (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    street_name TEXT NOT NULL,
                    county TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',  -- pending, in_progress, completed, failed
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    error_message TEXT,
                    properties_found INTEGER DEFAULT 0,
                    batch_id INTEGER,
                    UNIQUE(street_name, county)
                )
            """)

            # Batch metadata table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_name TEXT NOT NULL,
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP,
                    status TEXT DEFAULT 'in_progress',  -- in_progress, completed, failed
                    total_streets INTEGER DEFAULT 0,
                    completed_streets INTEGER DEFAULT 0,
                    properties_found INTEGER DEFAULT 0,
                    metadata TEXT
                )
            """)

            # Integrity check table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS integrity_checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    check_type TEXT NOT NULL,
                    passed BOOLEAN DEFAULT 1,
                    details TEXT,
                    checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create indexes for performance
            conn.execute("CREATE INDEX IF NOT EXISTS idx_properties_street ON properties(street_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_properties_county ON properties(county)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_properties_race ON properties(predicted_race)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_properties_is_hindu ON properties(is_hindu)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_progress_status ON search_progress(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_progress_street_county ON search_progress(street_name, county)")

            # Migration: Add latitude/longitude if they don't exist
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(properties)")
            columns = [info[1] for info in cursor.fetchall()]
            if 'latitude' not in columns:
                conn.execute("ALTER TABLE properties ADD COLUMN latitude REAL")
            if 'longitude' not in columns:
                conn.execute("ALTER TABLE properties ADD COLUMN longitude REAL")

            # Set schema version
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_info (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            conn.execute("""
                INSERT OR IGNORE INTO schema_info (key, value)
                VALUES ('version', ?)
            """, (self.SCHEMA_VERSION,))

    @staticmethod
    def normalize_text(text: str) -> str:
        """Collapse multiple spaces and trim."""
        if not text or not isinstance(text, str):
            return text
        return re.sub(r'\s+', ' ', text).strip()

    @staticmethod
    def normalize_county(name: str) -> str:
        """
        Normalize county names to a consistent Title Case format
        without the redundant " County" suffix.
        """
        if not name or not isinstance(name, str):
            return name
        
        name = name.strip()
        
        # Special case for Baltimore City
        if "BALTIMORE CITY" in name.upper():
            return "Baltimore City"
            
        # Remove " COUNTY" suffix if present
        name = re.sub(r'(?i)\s*COUNTY\s*', '', name).strip()
        
        return name.title()

    # ============ BATCH MANAGEMENT ============

    def create_batch(self, batch_name: str, metadata: Optional[Dict] = None) -> int:
        """
        Create a new batch for tracking a group of searches.

        Args:
            batch_name: Name/identifier for this batch
            metadata: Optional metadata dictionary

        Returns:
            Batch ID
        """
        with self._transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO batches (batch_name, metadata)
                VALUES (?, ?)
                """,
                (batch_name, json.dumps(metadata) if metadata else None)
            )
            return cursor.lastrowid

    def update_batch_progress(self, batch_id: int, streets_completed: int = None,
                            properties_found: int = None, status: str = None):
        """Update batch progress."""
        with self._transaction() as conn:
            updates = []
            params = []

            if streets_completed is not None:
                updates.append("completed_streets = ?")
                params.append(streets_completed)

            if properties_found is not None:
                updates.append("properties_found = ?")
                params.append(properties_found)

            if status is not None:
                updates.append("status = ?")
                params.append(status)
                if status in ('completed', 'failed'):
                    updates.append("completed_at = ?")
                    params.append(datetime.now().isoformat())

            if updates:
                params.append(batch_id)
                conn.execute(
                    f"UPDATE batches SET {', '.join(updates)} WHERE id = ?",
                    params
                )

    def get_batch_status(self, batch_id: int) -> Optional[Dict]:
        """Get batch status and statistics."""
        with self._transaction() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,))
            row = cursor.fetchone()

            if row:
                return dict(row)
            return None

    def get_all_batches(self) -> List[Dict]:
        """Get all batches."""
        with self._transaction() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT id, batch_name, started_at, completed_at, status,
                       total_streets, completed_streets, properties_found
                FROM batches
                ORDER BY started_at DESC
            """)
            return [dict(row) for row in cursor.fetchall()]

    # ============ STREET SEARCH MANAGEMENT ============

    def add_streets_to_batch(self, streets: List[Tuple[str, str]], batch_id: int,
                             requeue_completed: bool = False):
        """
        Add streets to a batch for processing.

        Args:
            streets: List of (street_name, county) tuples
            batch_id: Batch ID to associate with
            requeue_completed: also re-queue streets whose current status is
                'completed' (refresh re-scrape). Default keeps completed rows
                untouched.
        """
        with self._transaction() as conn:
            # Insert pending search tasks, reassigning them to the new batch if not completed
            for street_name, county in streets:
                norm_street = self.normalize_text(street_name).upper()
                norm_county = self.normalize_county(county)
                upsert = """
                    INSERT INTO search_progress (street_name, county, batch_id)
                    VALUES (?, ?, ?)
                    ON CONFLICT(street_name, county) DO UPDATE SET
                        batch_id = EXCLUDED.batch_id,
                        status = 'pending',
                        started_at = NULL,
                        completed_at = NULL,
                        error_message = NULL,
                        properties_found = 0
                """
                if not requeue_completed:
                    # Never clobber a completed search (plain queue append)
                    upsert += " WHERE search_progress.status != 'completed'"
                conn.execute(upsert, (norm_street, norm_county, batch_id))
            
            # Update correct batch total
            true_total = conn.execute("SELECT COUNT(*) FROM search_progress WHERE batch_id = ?", (batch_id,)).fetchone()[0]
            conn.execute(
                "UPDATE batches SET total_streets = ? WHERE id = ?",
                (true_total, batch_id)
            )

    def get_next_pending_street(self, batch_id: Optional[int] = None) -> Optional[Dict]:
        """
        Get the next pending street to search.

        Args:
            batch_id: Optional batch ID to filter by

        Returns:
            Dictionary with street info or None
        """
        with self._transaction() as conn:
            conn.row_factory = sqlite3.Row

            query = """
                SELECT id, street_name, county, batch_id
                FROM search_progress
                WHERE status = 'pending'
            """
            params = []

            if batch_id is not None:
                query += " AND batch_id = ?"
                params.append(batch_id)

            query += " ORDER BY id ASC LIMIT 1"

            cursor = conn.execute(query, params)
            row = cursor.fetchone()

            if row:
                # Mark as in_progress
                street_id = row['id']
                conn.execute("""
                    UPDATE search_progress
                    SET status = 'in_progress', started_at = ?
                    WHERE id = ?
                """, (datetime.now().isoformat(), street_id))

                return {
                    'id': street_id,
                    'street_name': row['street_name'],
                    'county': row['county'],
                    'batch_id': row['batch_id']
                }

            return None

    def mark_street_completed(self, street_name: str, county: str,
                            properties_found: int = 0, error: str = None):
        """
        Mark a street search as completed or failed.

        Args:
            street_name: The street that was searched
            county: County of the street
            properties_found: Number of properties found
            error: Error message if failed
        """
        with self._transaction() as conn:
            status = 'failed' if error else 'completed'

            if error:
                conn.execute("""
                    UPDATE search_progress
                    SET status = ?, completed_at = ?, properties_found = ?, error_message = ?
                    WHERE street_name = ? AND county = ?
                """, (status, datetime.now().isoformat(), properties_found,
                      error, street_name.upper(), county))
            else:
                conn.execute("""
                    UPDATE search_progress
                    SET status = ?, completed_at = ?, properties_found = ?, error_message = NULL
                    WHERE street_name = ? AND county = ?
                """, (status, datetime.now().isoformat(), properties_found,
                      street_name.upper(), county))

            # Update batch progress directly in same transaction (no nested transaction)
            batch_result = conn.execute("""
                SELECT batch_id FROM search_progress
                WHERE street_name = ? AND county = ?
            """, (street_name.upper(), county)).fetchone()

            if batch_result:
                batch_id = batch_result[0]
                # Count completed streets in this batch
                completed = conn.execute("""
                    SELECT COUNT(*) FROM search_progress
                    WHERE batch_id = ? AND status = 'completed'
                """, (batch_id,)).fetchone()[0]

                # Count total properties found
                total_props = conn.execute("""
                    SELECT SUM(properties_found) FROM search_progress
                    WHERE batch_id = ? AND status = 'completed'
                """, (batch_id,)).fetchone()[0] or 0

                # Update directly without calling update_batch_progress (avoid nested transaction)
                updates = ["completed_streets = ?", "properties_found = ?"]
                params = [completed, total_props]

                conn.execute(
                    f"UPDATE batches SET {', '.join(updates)} WHERE id = ?",
                    params + [batch_id]
                )

    def get_search_progress(self, batch_id: int) -> Dict[str, int]:
        """Get search progress statistics for a batch."""
        with self._transaction() as conn:
            stats = conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed,
                    SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
                    SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) as in_progress,
                    SUM(properties_found) as total_properties
                FROM search_progress
                WHERE batch_id = ?
            """, (batch_id,)).fetchone()

            return {
                'total': stats[0] or 0,
                'completed': stats[1] or 0,
                'failed': stats[2] or 0,
                'pending': stats[3] or 0,
                'in_progress': stats[4] or 0,
                'total_properties': stats[5] or 0
            }

    def reset_in_progress_streets(self, batch_id: Optional[int] = None):
        """
        Reset streets that are stuck in 'in_progress' status back to 'pending'.
        Useful for recovery after a crash.
        """
        with self._transaction() as conn:
            query = "UPDATE search_progress SET status = 'pending', started_at = NULL WHERE status = 'in_progress'"
            params = []

            if batch_id is not None:
                query += " AND batch_id = ?"
                params.append(batch_id)

            conn.execute(query, params)

    # ============ PROPERTY STORAGE ============

    def _calculate_checksum(self, record: Dict) -> str:
        """Calculate checksum for a property record."""
        key_fields = f"{record.get('county', '')}{record.get('owner_name', '')}{record.get('address', '')}"
        return hashlib.md5(key_fields.encode()).hexdigest()

    def add_properties(self, properties: List[Dict], batch_id: int,
                       replace: bool = False) -> int:
        """
        Add property records to the database.

        Args:
            properties: List of property dictionaries
            batch_id: Associated batch ID
            replace: delete prior rows for each (county, source_street) before
                inserting — refresh semantics, so stale owner names disappear
                instead of lingering next to the new rows

        Returns:
            Number of properties added
        """
        added = 0

        rows = []
        for prop in properties:
            checksum = self._calculate_checksum(prop)
            rows.append((
                self.normalize_text(prop.get('street_name', '')).upper(),
                self.normalize_county(prop.get('county', '')),
                self.normalize_text(prop.get('owner_name')),
                self.normalize_text(prop.get('address')),
                self.normalize_text(prop.get('city')),
                prop.get('state', 'MD'),
                self.normalize_text(prop.get('zip_code')),
                self.normalize_text(prop.get('source_street', '')).upper(),
                prop.get('predicted_race'),
                prop.get('race_confidence'),
                prop.get('race_method'),
                prop.get('is_hindu', 0),
                prop.get('sub_category'),
                batch_id,
                checksum
            ))

        with self._transaction() as conn:
            if replace:
                # One DELETE per distinct searched street in this save.
                # county matched suffix-agnostically — legacy rows store
                # 'MONTGOMERY COUNTY', current rows 'Montgomery'
                conn.executemany("""
                    DELETE FROM properties
                    WHERE source_street = ?
                      AND upper(replace(county, ' COUNTY', '')) = ?
                """, sorted({
                    (row[7], row[1].upper().replace(' COUNTY', ''))
                    for row in rows
                }))
            conn.executemany("""
                INSERT OR REPLACE INTO properties
                (street_name, county, owner_name, address, city, state, zip_code,
                 source_street, predicted_race, race_confidence, race_method,
                 is_hindu, sub_category, batch_id, checksum)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)

        added = len(rows)
        return added

    def delete_street_results(self, street_name: str, county: str) -> int:
        """
        Delete all property rows that came from one (street, county) search.

        Used by refresh mode when a re-scrape returns zero results — the old
        rows are stale (owners moved, parcels merged) and must not survive.

        Returns:
            Number of rows deleted
        """
        with self._transaction() as conn:
            # county matched suffix-agnostically — legacy rows store
            # 'MONTGOMERY COUNTY', current rows 'Montgomery'
            cur = conn.execute(
                """DELETE FROM properties
                   WHERE source_street = ?
                     AND upper(replace(county, ' COUNTY', '')) = ?""",
                (self.normalize_text(street_name).upper(),
                 self.normalize_county(county).upper().replace(' COUNTY', '')),
            )
            return cur.rowcount

    def get_properties(self, county: Optional[str] = None,
                      is_hindu: Optional[bool] = None,
                      race: Optional[str] = None,
                      limit: Optional[int] = None) -> pd.DataFrame:
        """
        Query properties with optional filters.

        Args:
            county: Filter by county
            is_hindu: Filter by Hindu flag
            race: Filter by predicted race
            limit: Maximum records to return

        Returns:
            Pandas DataFrame with results
        """
        query = "SELECT * FROM properties WHERE 1=1"
        params = []

        if county:
            query += " AND county = ?"
            params.append(county)

        if is_hindu is not None:
            query += " AND is_hindu = ?"
            params.append(1 if is_hindu else 0)

        if race:
            query += " AND predicted_race = ?"
            params.append(race)

        if limit:
            query += " LIMIT ?"
            params.append(limit)

        with self._transaction() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def get_all_properties(self) -> pd.DataFrame:
        """Get all properties as a DataFrame."""
        with self._transaction() as conn:
            return pd.read_sql_query("SELECT * FROM properties", conn)

    # ============ INTEGRITY CHECKS ============

    def run_integrity_check(self) -> Dict[str, Any]:
        """
        Run database integrity checks.

        Returns:
            Dictionary with check results
        """
        results = {
            'passed': True,
            'checks': []
        }

        with self._transaction() as conn:
            # Check 1: Verify properties table
            try:
                count = conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0]
                results['checks'].append({
                    'type': 'properties_count',
                    'passed': True,
                    'details': f'{count} properties in database'
                })
            except Exception as e:
                results['checks'].append({
                    'type': 'properties_count',
                    'passed': False,
                    'details': str(e)
                })
                results['passed'] = False

            # Check 2: Verify search progress
            try:
                stats = conn.execute("""
                    SELECT status, COUNT(*) as count FROM search_progress
                    GROUP BY status
                """).fetchall()
                results['checks'].append({
                    'type': 'search_progress',
                    'passed': True,
                    'details': dict(stats)
                })
            except Exception as e:
                results['checks'].append({
                    'type': 'search_progress',
                    'passed': False,
                    'details': str(e)
                })
                results['passed'] = False

            # Check 3: Check for orphaned records
            try:
                orphaned = conn.execute("""
                    SELECT COUNT(*) FROM properties p
                    LEFT JOIN search_progress sp ON p.street_name = sp.street_name AND p.county = sp.county
                    WHERE sp.id IS NULL
                """).fetchone()[0]
                results['checks'].append({
                    'type': 'orphaned_properties',
                    'passed': orphaned == 0,
                    'details': f'{orphaned} orphaned properties'
                })
            except Exception as e:
                results['checks'].append({
                    'type': 'orphaned_properties',
                    'passed': False,
                    'details': str(e)
                })

            # Store results
            conn.execute("""
                INSERT INTO integrity_checks (check_type, passed, details)
                VALUES (?, ?, ?)
            """, ('full_check', results['passed'], json.dumps(results['checks'])))

        return results

    # ============ EXPORT/BACKUP ============

    def export_to_excel(self, output_path: str, filters: Optional[Dict] = None) -> str:
        """
        Export properties to Excel file.

        Args:
            output_path: Path for output Excel file
            filters: Optional filters dictionary

        Returns:
            Path to exported file
        """
        # Get data
        df = self.get_properties(**(filters or {}))

        # Standardize column names
        column_map = {
            'street_name': 'Street',
            'county': 'County',
            'owner_name': 'Owner Name',
            'address': 'Address',
            'city': 'City',
            'state': 'State',
            'zip_code': 'Zip Code',
            'predicted_race': 'Predicted Race',
            'race_confidence': 'Confidence %',
            'race_method': 'Method',
            'is_hindu': 'Is Hindu',
            'sub_category': 'Sub-Category'
        }

        # Rename columns for display
        export_df = df.rename(columns=column_map)

        # Select only relevant columns
        export_cols = [c for c in ['Street', 'County', 'Owner Name', 'Address', 'City',
                                   'State', 'Zip Code', 'Predicted Race', 'Confidence %',
                                   'Method', 'Is Hindu', 'Sub-Category'] if c in export_df.columns]

        # Use atomic write: write to temp file first
        temp_path = f"{output_path}.tmp.xlsx"

        with pd.ExcelWriter(temp_path, engine='openpyxl') as writer:
            export_df[export_cols].to_excel(writer, index=False, sheet_name='Properties')

            # Auto-adjust column widths
            worksheet = writer.sheets['Properties']
            for idx, col in enumerate(export_cols, 1):
                max_len = max(
                    export_df[col].astype(str).apply(len).max(),
                    len(col)
                ) + 2
                worksheet.column_dimensions[chr(64 + idx)].width = min(max_len, 50)

        # Atomic rename
        if os.path.exists(output_path):
            backup_path = f"{output_path}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            os.rename(output_path, backup_path)

        os.rename(temp_path, output_path)

        return output_path

    def backup_database(self, backup_path: Optional[str] = None) -> str:
        """
        Create a backup of the database.

        Args:
            backup_path: Optional custom backup path

        Returns:
            Path to backup file
        """
        if backup_path is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_path = f"{self.db_path}.backup_{timestamp}"

        # SQLite backup API
        source = sqlite3.connect(self.db_path)
        dest = sqlite3.connect(backup_path)

        try:
            source.backup(dest)
            source.close()
            dest.close()
            
            # Rotate old backups to save disk space
            self._rotate_backups()
            
            return backup_path
        except Exception as e:
            if os.path.exists(backup_path):
                os.remove(backup_path)
            raise e
        finally:
            if source: source.close()
            if dest: dest.close()

    def _rotate_backups(self, max_backups: int = 5):
        """
        Keep only the most recent N backups of the database.
        """
        try:
            db_dir = os.path.dirname(self.db_path) or "."
            db_name = os.path.basename(self.db_path)
            
            # Find all backup files for this database
            backups = []
            for f in os.listdir(db_dir):
                if f.startswith(f"{db_name}.backup_"):
                    full_path = os.path.join(db_dir, f)
                    if os.path.isfile(full_path):
                        backups.append((full_path, os.path.getmtime(full_path)))
            
            # Sort by modification time (newest first)
            backups.sort(key=lambda x: x[1], reverse=True)
            
            # Delete backups beyond the limit
            if len(backups) > max_backups:
                for old_backup_path, _ in backups[max_backups:]:
                    try:
                        os.remove(old_backup_path)
                    except Exception as e:
                        print(f"⚠️ Could not remove old backup {old_backup_path}: {e}")
        except Exception as e:
            print(f"⚠️ Error during backup rotation: {e}")

    def get_statistics(self) -> Dict[str, Any]:
        """Get overall database statistics."""
        with self._transaction() as conn:
            # Property stats
            prop_stats = conn.execute("""
                SELECT
                    COUNT(*) as total_properties,
                    COUNT(DISTINCT street_name) as unique_streets,
                    COUNT(DISTINCT county) as unique_counties,
                    SUM(CASE WHEN is_hindu = 1 THEN 1 ELSE 0 END) as hindu_count,
                    SUM(CASE WHEN predicted_race = 'Indian' THEN 1 ELSE 0 END) as indian_count
                FROM properties
            """).fetchone()

            # Race breakdown
            race_breakdown = conn.execute("""
                SELECT predicted_race, COUNT(*) as count
                FROM properties
                GROUP BY predicted_race
                ORDER BY count DESC
            """).fetchall()

            # County breakdown
            county_breakdown = conn.execute("""
                SELECT county, COUNT(*) as count
                FROM properties
                GROUP BY county
                ORDER BY count DESC
            """).fetchall()

            return {
                'total_properties': prop_stats[0] or 0,
                'unique_streets': prop_stats[1] or 0,
                'unique_counties': prop_stats[2] or 0,
                'hindu_count': prop_stats[3] or 0,
                'indian_count': prop_stats[4] or 0,
                'race_breakdown': dict(race_breakdown),
                'county_breakdown': dict(county_breakdown)
            }


class SearchJobManager:
    """
    High-level manager for search jobs with automatic recovery.
    """

    def __init__(self, db: PropertyDatabase):
        self.db = db

    def load_streets_from_excel(self, excel_path: str) -> List[Tuple[str, str]]:
        """
        Load streets from Excel file for processing.

        Args:
            excel_path: Path to Excel file with Address/county columns

        Returns:
            List of (street_name, county) tuples
        """
        df = pd.read_excel(excel_path)

        # Normalize column names
        df.columns = [c.lower().strip() for c in df.columns]

        # Get the address column
        addr_col = None
        for col in ['address', 'street', 'street_name']:
            if col in df.columns:
                addr_col = col
                break

        if addr_col is None:
            raise ValueError("No address/street column found in Excel file")

        county_col = 'county' if 'county' in df.columns else None

        streets = []
        for _, row in df.iterrows():
            street = str(row[addr_col]).strip()
            county = str(row[county_col]).strip() if county_col else "Unknown"

            if street and street.lower() not in ('nan', 'none', ''):
                # Apply new strict StreetNameCleaner logic
                from src.street_name_cleaner import StreetNameCleaner
                cleaner = StreetNameCleaner()
                canonical_street = cleaner.clean_street_name(street)
                if canonical_street:
                    streets.append((canonical_street, county))

        return streets

    def create_search_job(self, streets: List[Tuple[str, str]],
                         job_name: str, force: bool = False) -> int:
        """
        Create a new search job (batch).

        Args:
            streets: List of (street_name, county) tuples
            job_name: Name for this job
            force: Skip the already-completed filter (re-scrape gaps where a
                previous run truncated results, e.g. pre-pagination-fix)

        Returns:
            Batch ID
        """
        batch_id = self.db.create_batch(
            job_name,
            metadata={'total_streets': len(streets)}
        )

        # Filter out already-searched streets efficiently using search_progress
        with self.db._transaction() as conn:
            # Get existing completions with county for accurate cross-county coverage
            completed_df = pd.read_sql_query("SELECT street_name, county FROM search_progress WHERE status = 'completed'", conn) if not force else pd.DataFrame()
            
        if not completed_df.empty:
            # Use (street, county) tuple as key to allow same street name in different counties
            existing_keys = set()
            for _, r in completed_df.iterrows():
                s = str(r['street_name']).upper().strip()
                c = self.db.normalize_county(str(r['county'])).upper()
                existing_keys.add((s, c))
            
            new_streets = []
            for item in streets:
                # Handle both tuple and dict formats
                if isinstance(item, tuple):
                    s, c = str(item[0]).upper().strip(), self.db.normalize_county(str(item[1])).upper()
                else:
                    s = str(item.get('street_name', '')).upper().strip()
                    c = self.db.normalize_county(str(item.get('county', 'Unknown'))).upper()
                
                if (s, c) not in existing_keys:
                    new_streets.append(item)

            print(f"Filtered out {len(streets) - len(new_streets)} already-completed street/county pairs")
            streets = new_streets

        # Additional de-duplication within the incoming batch itself (always,
        # even with force=True)
        unique_streets = []
        seen = set()
        for s, c in streets:
            sk = str(s).upper().strip()
            ck = self.db.normalize_county(str(c)).upper()
            key = (sk, ck)
            if key not in seen:
                seen.add(key)
                unique_streets.append((s, c))

        print(f"Filtered out {len(streets) - len(unique_streets)} duplicates within the input file itself")
        streets = unique_streets

        self.db.add_streets_to_batch(streets, batch_id, requeue_completed=force)

        # Ensure correct batch totals are initialized
        self.db.update_batch_progress(batch_id, streets_completed=0)

        return batch_id

    def resume_job(self, batch_id: int) -> int:
        """
        Resume a job that was interrupted.

        Args:
            batch_id: Batch ID to resume

        Returns:
            Number of remaining streets
        """
        # Reset any stuck in_progress streets
        self.db.reset_in_progress_streets(batch_id)

        progress = self.db.get_search_progress(batch_id)
        return progress['pending'] + progress['in_progress']


# ============ CLI INTERFACE ============

def main():
    """CLI interface for database operations."""
    import argparse

    parser = argparse.ArgumentParser(description='Robust Property Database Manager')
    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # Stats command
    subparsers.add_parser('stats', help='Show database statistics')

    # Export command
    export_parser = subparsers.add_parser('export', help='Export to Excel')
    export_parser.add_argument('-o', '--output', default='data/Final_Owner_Results.xlsx',
                               help='Output file path')
    export_parser.add_argument('--hindu-only', action='store_true',
                               help='Export only Hindu owners')

    # Backup command
    backup_parser = subparsers.add_parser('backup', help='Backup database')
    backup_parser.add_argument('-o', '--output', help='Backup file path')

    # Check command
    subparsers.add_parser('check', help='Run integrity checks')

    # Resume command
    resume_parser = subparsers.add_parser('resume', help='Resume a batch')
    resume_parser.add_argument('batch_id', type=int, help='Batch ID to resume')

    args = parser.parse_args()

    db = PropertyDatabase()

    if args.command == 'stats':
        stats = db.get_statistics()
        print("\n=== DATABASE STATISTICS ===")
        print(f"Total Properties: {stats['total_properties']:,}")
        print(f"Unique Streets: {stats['unique_streets']:,}")
        print(f"Unique Counties: {stats['unique_counties']}")
        print(f"Hindu Owners: {stats['hindu_count']:,}")
        print(f"Indian Owners: {stats['indian_count']:,}")

        print("\n--- Race Breakdown ---")
        for race, count in stats['race_breakdown'].items():
            print(f"  {race}: {count:,}")

        print("\n--- County Breakdown ---")
        for county, count in stats['county_breakdown'].items():
            print(f"  {county}: {count:,}")

    elif args.command == 'export':
        filters = {'is_hindu': True} if args.hindu_only else {}
        output = db.export_to_excel(args.output, filters)
        print(f"Exported to: {output}")

    elif args.command == 'backup':
        backup = db.backup_database(args.output)
        print(f"Backup created: {backup}")

    elif args.command == 'check':
        results = db.run_integrity_check()
        print(f"\nIntegrity Check: {'PASSED' if results['passed'] else 'FAILED'}")
        for check in results['checks']:
            status = '✓' if check['passed'] else '✗'
            print(f"  {status} {check['type']}: {check['details']}")

    elif args.command == 'resume':
        manager = SearchJobManager(db)
        remaining = manager.resume_job(args.batch_id)
        progress = db.get_search_progress(args.batch_id)
        print(f"Batch {args.batch_id}: {progress['completed']} completed, {remaining} remaining")


if __name__ == "__main__":
    main()

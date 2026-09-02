#!/usr/bin/env python3
"""
Async batch upload SQLite data to Turso using the HTTP API.
Uses asyncio and aiohttp to achieve high throughput via concurrent batches.
"""
import sqlite3
import json
import time
import sys
import os
import asyncio
import aiohttp
import base64

TURSO_URL = "https://property-search-vsailesh.aws-us-east-1.turso.io"
TURSO_TOKEN = os.environ["TURSO_TOKEN"]  # export TURSO_TOKEN=... before running

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_DB = os.path.join(DATA_DIR, "property_search.db")
BATCH_SIZE = 1000  # rows per request (1000 rows x 20 cols = 20k params, under SQLite's 32k limit)
CONCURRENCY = 5    # concurrent requests
PIPELINE_URL = f"{TURSO_URL}/v2/pipeline"
PROGRESS_FILE = os.path.join(DATA_DIR, ".upload_progress_async.json")
MAX_REQUEUE = 2    # times a failed batch is re-queued before giving up

HEADERS = {
    "Authorization": f"Bearer {TURSO_TOKEN}",
    "Content-Type": "application/json",
}

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {}

def save_progress(progress):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f)

def convert_value(val):
    if val is None:
        return {"type": "null"}
    elif isinstance(val, int):
        return {"type": "integer", "value": str(val)}
    elif isinstance(val, float):
        return {"type": "float", "value": val}
    elif isinstance(val, bytes):
        return {"type": "blob", "base64": base64.b64encode(val).decode()}
    else:
        return {"type": "text", "value": str(val)}

async def execute_batch(session, statements, max_retries=5):
    requests_body = []
    for stmt in statements:
        requests_body.append({"type": "execute", "stmt": stmt})
    requests_body.append({"type": "close"})

    payload = {"requests": requests_body}
    
    for attempt in range(max_retries):
        try:
            async with session.post(PIPELINE_URL, json=payload, headers=HEADERS, timeout=60) as resp:
                if resp.status == 200:
                    return True
                else:
                    text = await resp.text()
                    if attempt == max_retries - 1:
                        print(f"\n  ⚠ HTTP {resp.status}: {text[:200]}")
                    await asyncio.sleep(min(2 ** attempt, 10))
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"\n  ⚠ Request error: {type(e).__name__}")
            await asyncio.sleep(min(2 ** attempt, 10))
    return False

def build_batch_statement(table_name, columns, rows):
    """One multi-row INSERT statement for the whole batch — server parses 1 stmt
    instead of BATCH_SIZE stmts per request."""
    n_cols = len(columns)
    col_list = ", ".join([f'"{c}"' for c in columns])
    row_placeholder = "(" + ", ".join(["?"] * n_cols) + ")"
    values = ", ".join([row_placeholder] * len(rows))
    sql = f'INSERT OR IGNORE INTO "{table_name}" ({col_list}) VALUES {values}'
    args = [convert_value(val) for row in rows for val in row]
    return {"sql": sql, "args": args}

async def worker(worker_id, session, queue, total_rows, table_name, columns, progress, start_time, start_offset, state):
    while True:
        task = await queue.get()
        if task is None:
            queue.task_done()
            break

        rows_data, attempts = task

        statement = build_batch_statement(table_name, columns, rows_data)
        success = await execute_batch(session, [statement])

        if success:
            state['uploaded'] += len(rows_data)
            uploaded = state['uploaded']
            progress[table_name] = uploaded

            # Print progress only from one worker to avoid mess
            if worker_id == 0 or uploaded % (BATCH_SIZE * 5) == 0:
                pct = (uploaded / total_rows) * 100
                elapsed = time.time() - start_time
                rate = (uploaded - start_offset) / elapsed if elapsed > 0 else 0
                eta_s = (total_rows - uploaded) / rate if rate > 0 else 0
                sys.stdout.write(f"\r  ✅ {uploaded:,}/{total_rows:,} ({pct:.1f}%) | {rate:.0f} rows/s | ETA: {eta_s/60:.1f}m  ")
                sys.stdout.flush()
        elif attempts < MAX_REQUEUE:
            await queue.put((rows_data, attempts + 1))
        else:
            print(f"\n  ❌ Failed batch of {len(rows_data)} rows in worker {worker_id} after {MAX_REQUEUE + 1} attempts — rows lost, re-run script to pick up (INSERT OR IGNORE)")
            state['failures'] += 1

        queue.task_done()

async def upload_table_async(table_name, columns, local_conn, progress):
    cursor = local_conn.cursor()
    cursor.execute(f'SELECT COUNT(*) FROM "{table_name}"')
    total = cursor.fetchone()[0]
    
    start_offset = progress.get(table_name, 0)
    if start_offset >= total and total > 0:
        print(f"\n✅ '{table_name}': already uploaded ({total:,} rows)")
        return
        
    print(f"\n📦 Uploading '{table_name}': {total:,} rows (resuming from {start_offset:,})")
    
    if total == 0:
        progress[table_name] = 0
        save_progress(progress)
        return
        
    col_list = ", ".join([f'"{c}"' for c in columns])
    cursor.execute(f'SELECT {col_list} FROM "{table_name}" LIMIT -1 OFFSET {start_offset}')

    queue = asyncio.Queue(maxsize=CONCURRENCY * 2)
    state = {'uploaded': start_offset, 'failures': 0}

    start_time = time.time()

    async with aiohttp.ClientSession() as session:
        workers = [
            asyncio.create_task(worker(i, session, queue, total, table_name, columns, progress, start_time, start_offset, state))
            for i in range(CONCURRENCY)
        ]

        batches_queued = 0
        while True:
            rows = cursor.fetchmany(BATCH_SIZE)
            if not rows:
                break

            await queue.put((rows, 0))
            batches_queued += 1

            if batches_queued % 10 == 0:
                save_progress(progress)
                
        # Send stop signal
        for _ in range(CONCURRENCY):
            await queue.put(None)
            
        await queue.join()
        
        for w in workers:
            w.cancel()
            
    save_progress(progress)
    print(f"\n  📊 Result: {state['uploaded']:,}/{total:,} rows uploaded, {state['failures']} failed batches")

def main():
    if "TURSO_TOKEN" not in os.environ:
        sys.exit("Error: TURSO_TOKEN not set. Run: export TURSO_TOKEN=<your token>")
    print("🚀 Turso Database Upload (Async HTTP)")
    progress = load_progress()
    conn = sqlite3.connect(LOCAL_DB)
    
    tables = {
        "batches": ["id", "batch_name", "started_at", "completed_at", "status", "total_streets", "completed_streets", "properties_found", "metadata"],
        "search_progress": ["id", "street_name", "county", "status", "started_at", "completed_at", "error_message", "properties_found", "batch_id"],
        "integrity_checks": ["id", "check_type", "passed", "details", "checked_at"],
        "schema_info": ["key", "value"],
        "properties": ["id", "street_name", "county", "owner_name", "address", "city", "state", "zip_code", "source_street", "searched_at", "predicted_race", "race_confidence", "race_method", "is_hindu", "sub_category", "batch_id", "checksum", "latitude", "longitude", "geo_method"],
    }

    # Ensure remote schema has geo_method (no-op if already present)
    async def ensure_geo_method():
        import aiohttp
        async with aiohttp.ClientSession() as session:
            await execute_batch(session, [{"sql": "ALTER TABLE properties ADD COLUMN geo_method TEXT", "args": []}])
    asyncio.run(ensure_geo_method())

    for table_name, columns in tables.items():
        asyncio.run(upload_table_async(table_name, columns, conn, progress))

if __name__ == "__main__":
    main()

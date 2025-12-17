#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from dotenv import load_dotenv
load_dotenv(override=True)

from integration_coworker.persistence.postgres import get_connection
with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT table_schema, table_name FROM information_schema.tables WHERE table_schema IN ('spec_silver', 'integration_gold') ORDER BY table_schema, table_name")
        for row in cur.fetchall():
            print(f'{row[0]}.{row[1]}')

import sys, os
sys.path.insert(0, os.path.abspath("."))
from database import get_postgres_connection

with get_postgres_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns 
            WHERE table_name = 'reports'
            ORDER BY ordinal_position
        """)
        for col in cur.fetchall():
            print("Col:", col)
            
        cur.execute("SELECT * FROM reports LIMIT 2")
        print("Sample:", cur.fetchall())

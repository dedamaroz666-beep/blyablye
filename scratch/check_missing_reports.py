import sys, os, sqlite3
sys.path.insert(0, os.path.abspath("."))
from database import get_postgres_connection, CACHE_DB_PATH

with sqlite3.connect(CACHE_DB_PATH) as sconn:
    scur = sconn.cursor()
    scur.execute("SELECT DISTINCT ticker FROM reports_extra")
    extra_tickers = [r[0] for r in scur.fetchall()]
    print("Tickers in reports_extra:", len(extra_tickers))

with get_postgres_connection() as conn:
    with conn.cursor() as cur:
        for t in extra_tickers:
            cur.execute("""
                SELECT COUNT(*) 
                FROM reports r 
                JOIN ticker t_tab ON t_tab.id = r.id_ticker 
                WHERE UPPER(t_tab.ticker) = %s
            """, (t,))
            cnt = cur.fetchone()[0]
            if cnt == 0:
                print(f"Ticker {t} has 0 reports in PostgreSQL but has reports_extra!")
                scur.execute("SELECT * FROM reports_extra WHERE ticker = ?", (t,))
                print("  Extra rows:", scur.fetchall())

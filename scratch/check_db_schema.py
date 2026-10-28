import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import psycopg
from config import DB_CONFIG

def check():
    try:
        with psycopg.connect(**DB_CONFIG, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT column_name, data_type 
                    FROM information_schema.columns 
                    WHERE table_name = 'intraday_chart';
                """)
                cols = cur.fetchall()
                print('Columns in intraday_chart:', cols)
                
                cur.execute("""
                    SELECT t.ticker, count(*) 
                    FROM intraday_chart i 
                    JOIN ticker t ON t.id = i.id_ticker 
                    WHERE t.ticker IN ('MCD', 'AAPL', 'SPY', 'AKAM', 'ARM') 
                    GROUP BY t.ticker;
                """)
                print('Sample counts:', cur.fetchall())
    except Exception as e:
        print('Error:', e)

if __name__ == '__main__':
    check()

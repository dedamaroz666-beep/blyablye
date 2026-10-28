import sys, os, sqlite3, glob
sys.path.insert(0, os.path.abspath("."))
from config import CACHE_DB_PATH

print("Checking SQLite cache:", CACHE_DB_PATH)
if os.path.exists(CACHE_DB_PATH):
    with sqlite3.connect(CACHE_DB_PATH) as sconn:
        cur = sconn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [t[0] for t in cur.fetchall()]
        print("SQLite tables:", tables)
        for tbl in tables:
            try:
                cur.execute(f"SELECT * FROM {tbl} WHERE ticker LIKE '%SPCX%'")
                rows = cur.fetchall()
                if rows:
                    print(f"Table {tbl} has {len(rows)} rows for SPCX:")
                    for r in rows[:5]:
                        print("  ", r)
            except Exception as e:
                pass

print("\nSearching all files in workspace for 'SPCX' and '08-04' or '4 августа' or 'Aug 4'...")
for p in glob.glob("**/*.*", recursive=True):
    if any(p.startswith(x) for x in ["venv", ".git", ".gemini", "node_modules", ".idea"]):
        continue
    if not p.endswith(('.py', '.json', '.sql', '.csv', '.txt', '.md')):
        continue
    try:
        with open(p, "r", encoding="utf-8", errors="ignore") as f:
            c = f.read()
            if "SPCX" in c and ("08-04" in c or "2026-08" in c or "Aug" in c or "report" in c.lower()):
                print(f"Match in {p}:")
                for line in c.splitlines():
                    if "SPCX" in line and any(x in line for x in ["08", "Aug", "report", "earning"]):
                        print("  ", line[:120])
    except Exception:
        pass

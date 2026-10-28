import urllib.request
import json

payload = {
    "benchmark": "MU",
    "benchmark_shares": 5,
    "lookback_days": 20,
    "rounding": "1",
    "tickers": ["NVDA", "AAPL", "AMD", "TSLA", "PLTR", "SOUN"]
}

try:
    req = urllib.request.Request(
        "http://127.0.0.1:8000/api/tier/calculate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        print("API STATUS:", resp.status)
        print("BENCHMARK:", data.get("benchmark"))
        print("FIRST RESULT:", data.get("results", [])[0] if data.get("results") else "No results")

    # Test Excel Export
    req_excel = urllib.request.Request(
        "http://127.0.0.1:8000/api/tier/export/excel",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req_excel, timeout=15) as resp_ex:
        content = resp_ex.read()
        print("EXCEL STATUS:", resp_ex.status, "SIZE:", len(content), "bytes")
except Exception as e:
    print("TEST FAILED:", e)

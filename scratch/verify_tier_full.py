import urllib.request
import urllib.parse
import json

BASE_URL = "http://127.0.0.1:8000"

def test_html():
    with urllib.request.urlopen(f"{BASE_URL}/", timeout=10) as resp:
        html = resp.read().decode("utf-8")
        assert "data-target-view=\"viewTier\"" in html, "Tier tab button missing in index.html"
        assert "id=\"viewTier\"" in html, "viewTier container missing in index.html"
        assert "id=\"tierResultsTable\"" in html, "tierResultsTable missing in index.html"
        assert "id=\"tierBulkModalBackdrop\"" in html, "tierBulkModalBackdrop missing in index.html"
        assert "app.js?v=" in html, "app.js version query string not updated"
        print("[PASS] HTML structure has Tier tab, view, table, bulk modal, and cache-busting.")

def test_api_calc_post():
    payload = {
        "benchmark": "MU",
        "benchmark_shares": 5,
        "lookback_days": 20,
        "rounding": "1",
        "tickers": ["NVDA", "AAPL", "AMD", "TSLA", "PLTR", "SOUN", "MSFT", "AMZN", "GOOGL", "META", "AVGO", "QCOM", "INTC", "ARM"]
    }
    req = urllib.request.Request(
        f"{BASE_URL}/api/tier/calculate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        assert data.get("status") == "ok", "API status not ok"
        bench = data.get("benchmark", {})
        assert bench.get("ticker") == "MU", "Benchmark ticker mismatch"
        assert bench.get("price") > 0, "Benchmark price non-positive"
        assert bench.get("dollar_risk") > 0, "Benchmark dollar risk non-positive"
        
        items = data.get("items", [])
        assert len(items) > 0, "Items list is empty"
        print(f"[PASS] API POST calculation returned {len(items)} items.")
        print(f"       Benchmark: {bench['ticker']} | Price: ${bench['price']} | GK Vol: {bench['gk_daily_pct']}% | Risk: ${bench['dollar_risk']}")
        for it in items[:3]:
            print(f"       Stock: {it['ticker']:<5} | Price: ${it['price']:<7} | GK Vol: {it['gk_daily_pct']:<5}% | Tier 1: {it['tier1_shares']:<4} shares | Risk: ${it['actual_risk']:<6} | Parity: {it['parity_pct']}%")

def test_api_calc_get():
    params = urllib.parse.urlencode({
        "benchmark": "MU",
        "benchmark_shares": 5,
        "lookback_days": 20,
        "rounding": "1",
        "tickers": "NVDA,AAPL,AMD"
    })
    with urllib.request.urlopen(f"{BASE_URL}/api/tier/calculate?{params}", timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        assert data.get("status") == "ok"
        assert len(data.get("items", [])) == 3
        print(f"[PASS] API GET calculation returned 3 items successfully.")

def test_api_export_excel():
    payload = {
        "benchmark": "MU",
        "benchmark_shares": 5,
        "lookback_days": 20,
        "rounding": "1",
        "tickers": ["NVDA", "AAPL", "AMD", "TSLA", "PLTR"]
    }
    req = urllib.request.Request(
        f"{BASE_URL}/api/tier/export/excel",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        content = resp.read()
        assert content.startswith(b"PK\x03\x04"), "Excel file does not have valid zip/xlsx signature"
        print(f"[PASS] Excel export succeeded with valid xlsx binary stream ({len(content)} bytes).")

if __name__ == "__main__":
    test_html()
    test_api_calc_post()
    test_api_calc_get()
    test_api_export_excel()
    print("\nALL VERIFICATION CHECKS PASSED PERFECTLY!")

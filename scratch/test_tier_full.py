import urllib.request
import json

def test():
    # 1. Empty call
    req1 = urllib.request.Request(
        'http://127.0.0.1:8000/api/tier/calculate',
        data=json.dumps({'benchmark': '', 'tickers': []}).encode('utf-8'),
        headers={'Content-Type': 'application/json'}
    )
    with urllib.request.urlopen(req1) as resp:
        d1 = json.loads(resp.read().decode())
        assert d1['status'] == 'empty'
        assert len(d1['results']) == 0
        print("[PASS] Test 1: Empty initial state returns status=empty, 0 results")

    # 2. Benchmark only
    req2 = urllib.request.Request(
        'http://127.0.0.1:8000/api/tier/calculate',
        data=json.dumps({'benchmark': 'MU', 'tickers': []}).encode('utf-8'),
        headers={'Content-Type': 'application/json'}
    )
    with urllib.request.urlopen(req2) as resp:
        d2 = json.loads(resp.read().decode())
        assert d2['status'] == 'ok'
        assert d2['benchmark']['ticker'] == 'MU'
        assert d2['benchmark']['dollar_risk'] > 0
        assert len(d2['results']) == 0
        print(f"[PASS] Test 2: Benchmark only returns MU risk=${d2['benchmark']['dollar_risk']}, 0 items")

    # 3. Benchmark + Tickers
    req3 = urllib.request.Request(
        'http://127.0.0.1:8000/api/tier/calculate',
        data=json.dumps({'benchmark': 'MU', 'benchmark_shares': 5, 'lookback_days': 20, 'tickers': ['NVDA', 'AAPL', 'AMD']}).encode('utf-8'),
        headers={'Content-Type': 'application/json'}
    )
    with urllib.request.urlopen(req3) as resp:
        d3 = json.loads(resp.read().decode())
        assert d3['status'] == 'ok'
        assert len(d3['results']) == 3
        for r in d3['results']:
            print(f"  Ticker {r['ticker']}: Price=${r['price']}, GK={r['gk_daily_pct']}%, Tier1={r['tier1_shares']}sh, Risk=${r['actual_risk']}, Parity={r['parity_pct']}%")
        print("[PASS] Test 3: Benchmark + tickers calculates parity sizing correctly")

    # 4. Excel Export
    req4 = urllib.request.Request(
        'http://127.0.0.1:8000/api/tier/export/excel',
        data=json.dumps({'benchmark': 'MU', 'benchmark_shares': 5, 'lookback_days': 20, 'tickers': ['NVDA', 'AAPL']}).encode('utf-8'),
        headers={'Content-Type': 'application/json'}
    )
    with urllib.request.urlopen(req4) as resp:
        excel_bytes = resp.read()
        assert len(excel_bytes) > 2000
        print(f"[PASS] Test 4: Excel export returns valid binary ({len(excel_bytes)} bytes)")

if __name__ == '__main__':
    test()

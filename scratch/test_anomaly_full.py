import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from starlette.testclient import TestClient
from app_fastapi import app

client = TestClient(app)

print("=== Test 1: Standard Calculation ===")
r1 = client.post("/api/calculate-groups", json={
    "groups": {"Group 1": ["AAPL", "NVDA"]},
    "benchmarks": ["SPY"],
    "period": "1y",
    "metric": "ctc"
})
assert r1.status_code == 200, f"Status {r1.status_code}: {r1.text}"
d1 = r1.json()
print("Normal days analyzed:", d1.get("total_analyzed"), "Corr AAPL-SPY:", d1["correlation"]["AAPL"]["SPY"])

print("=== Test 2: Anomaly Filter SPY GAP > 0.5% ===")
r2 = client.post("/api/calculate-groups", json={
    "groups": {"Group 1": ["AAPL", "NVDA"]},
    "benchmarks": ["SPY"],
    "period": "1y",
    "metric": "ctc",
    "anomaly_mode": "only_anomaly",
    "anomaly_metric": "gap",
    "anomaly_op": "gt",
    "anomaly_threshold": 0.5,
    "anomaly_target": "benchmark"
})
assert r2.status_code == 200, f"Status {r2.status_code}: {r2.text}"
d2 = r2.json()
print("Anomaly label:", d2.get("anomaly_label_ru"))
print("Anomaly days count:", d2.get("anomaly_days_count"))
print("Anomaly Corr AAPL-SPY:", d2["correlation"]["AAPL"]["SPY"])
assert d2.get("anomaly_days_count") == 42, f"Expected 42 days, got {d2.get('anomaly_days_count')}"

print("=== Test 3: No-Anomaly Filter SPY GAP <= 0.5% ===")
r3 = client.post("/api/calculate-groups", json={
    "groups": {"Group 1": ["AAPL", "NVDA"]},
    "benchmarks": ["SPY"],
    "period": "1y",
    "metric": "ctc",
    "anomaly_mode": "no_anomaly",
    "anomaly_metric": "gap",
    "anomaly_op": "gt",
    "anomaly_threshold": 0.5,
    "anomaly_target": "benchmark"
})
assert r3.status_code == 200, f"Status {r3.status_code}: {r3.text}"
d3 = r3.json()
print("No-Anomaly label:", d3.get("anomaly_label_ru"))
print("No-Anomaly days count:", d3.get("anomaly_days_count"))
print("No-Anomaly Corr AAPL-SPY:", d3["correlation"]["AAPL"]["SPY"])
assert d3.get("anomaly_days_count") == 207, f"Expected 207 days, got {d3.get('anomaly_days_count')}"

print("=== Test 4: Anomaly Filter Per Asset |GAP| >= 2.0% ===")
r4 = client.post("/api/calculate-groups", json={
    "groups": {"Group 1": ["AAPL", "NVDA"]},
    "benchmarks": ["SPY"],
    "period": "1y",
    "metric": "ctc",
    "anomaly_mode": "only_anomaly",
    "anomaly_metric": "gap",
    "anomaly_op": "abs_gte",
    "anomaly_threshold": 2.0,
    "anomaly_target": "asset"
})
assert r4.status_code == 200, f"Status {r4.status_code}: {r4.text}"
d4 = r4.json()
print("Per asset anomaly label:", d4.get("anomaly_label_ru"))
print("Per asset anomaly days count:", d4.get("anomaly_days_count"))

print("=== Test 5: Verify Button Name in HTML ===")
with open("static/index.html", "r", encoding="utf-8") as f:
    html = f.read()
assert "Рассчитать модели" not in html, "Found 'Рассчитать модели' in static/index.html!"
assert "Расчёт" in html, "Button 'Расчёт' not found!"

print("ALL 5 VERIFICATION TESTS PASSED SUCCESSFULLY!")

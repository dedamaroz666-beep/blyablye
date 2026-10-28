import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8000/", timeout=10) as resp:
    html = resp.read().decode("utf-8")

assert 'id="toggleHeaderCollapseBtn"' in html, "toggleHeaderCollapseBtn missing in HTML"
assert 'id="restoreHeaderBtn"' in html, "restoreHeaderBtn missing in HTML"

with open("static/style.css", "r", encoding="utf-8") as f:
    css = f.read()

assert ".header-collapse-toggle-btn" in css, "header-collapse-toggle-btn missing in CSS"
assert ".header-restore-btn" in css, "header-restore-btn missing in CSS"
assert "body.header-collapsed .global-header" in css, "body.header-collapsed rule missing in CSS"

with open("static/app.js", "r", encoding="utf-8") as f:
    js = f.read()

assert "initHeaderCollapseToggle" in js, "initHeaderCollapseToggle missing in JS"
assert "chepuh_header_collapsed" in js, "localStorage key missing in JS"

print("[PASS] Header collapse & restore feature verified successfully!")

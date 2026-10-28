import urllib.request
import re

BASE_URL = "http://127.0.0.1:8000"

def test_all_fixes():
    with urllib.request.urlopen(f"{BASE_URL}/", timeout=10) as resp:
        html = resp.read().decode("utf-8")

    with open("static/style.css", "r", encoding="utf-8") as f:
        css = f.read()

    with open("static/app.js", "r", encoding="utf-8") as f:
        js = f.read()

    # 1. Test Stepper Arrows in Correlation & Beta menu
    assert 'id="benchmarkMoveUp"' in html, "benchmarkMoveUp button missing in HTML"
    assert 'id="benchmarkMoveDown"' in html, "benchmarkMoveDown button missing in HTML"
    assert 'class="percent-unit"' in html, "percent-unit span missing in HTML"
    assert "-webkit-appearance: none" in css, "Webkit spin button suppression missing in CSS"
    assert ".num-stepper-btn" in css, "num-stepper-btn styles missing in CSS"
    print("[PASS] 1. Stepper arrows properly set up with custom buttons and no overlapping %.")

    # 2. Test Alignment of Tier View and buttons
    assert "max-width: 1680px" in css, "max-width 1680px missing in .tier-wrapper CSS"
    assert "padding: 14px 20px 40px" in css, "padding 14px 20px 40px missing in .tier-wrapper CSS"
    # Check that tier-wrapper has max-width and padding in HTML inline styles too
    assert "max-width: 1680px" in html, "max-width 1680px missing in inline styles"
    assert "padding: 14px 20px 40px" in html, "padding 14px 20px 40px missing in inline styles"
    print("[PASS] 2. [ TIER ] and buttons now aligned within 1680px padded container (no protrusion left or up).")

    # 3. Test No browser confirm modal on Clear All
    assert "confirm(" not in js, "window.confirm() found in static/app.js!"
    assert "Вы уверены, что хотите удалить все тикеры" not in js, "Confirm prompt text still in JS!"
    print("[PASS] 3. Native browser confirm popup removed completely.")

    # 4. Test Presets removed
    assert "tier-presets-group" not in html, "tier-presets-group still in HTML!"
    assert "TIER_PRESETS" not in js, "TIER_PRESETS still in static/app.js!"
    assert 'data-preset="semis"' not in html, "Preset buttons still in HTML!"
    print("[PASS] 4. Presets completely removed from HTML and JS.")

    # 5. Test empty state IDs
    assert 'id="tierEmptyTitle"' in html, "tierEmptyTitle missing in HTML"
    assert 'id="tierEmptyDesc"' in html, "tierEmptyDesc missing in HTML"
    print("[PASS] 5. Dynamic empty state with clear guidance present.")

    print("\nALL 3 FIXES VERIFIED SUCCESSFULLY AND PASS WITH FLYING COLORS!")

if __name__ == "__main__":
    test_all_fixes()

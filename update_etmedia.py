import os
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE_URL = "https://tvplayer.etmedia.tv/web-player/#/tv"
DEBUG_DIR = Path("debug")
DEBUG_DIR.mkdir(exist_ok=True)

token = os.environ.get("ET_AUTH_TOKEN")
profile_uid = os.environ.get("ET_PROFILE_UID")
device_uid = os.environ.get("ET_DEVICE_UID")

if not all([token, profile_uid, device_uid]):
    raise RuntimeError("Не заданы ET_AUTH_TOKEN / ET_PROFILE_UID / ET_DEVICE_UID")

def clean(s, limit=5000):
    s = (s or "").replace("\n", " ").replace("\r", " ")
    return " ".join(s.split())[:limit]

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        viewport={"width": 1440, "height": 1000},
        user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    )

    def add_auth(route, request):
        headers = dict(request.headers)
        if "tvplayer.etmedia.tv" in request.url:
            headers["auth-token"] = token
            headers["profile-uid"] = profile_uid
            headers["device-uid"] = device_uid
        route.continue_(headers=headers)

    context.route("**/*", add_auth)
    page = context.new_page()

    stream_requests = []
    def on_request(request):
        if "etm.etmedia.tv" in request.url and ".m3u8" in request.url:
            stream_requests.append(request.url.split("?", 1)[0])
    page.on("request", on_request)

    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=120000)
    page.wait_for_timeout(8000)

    page.screenshot(path=str(DEBUG_DIR / "01_before_accessibility.png"), full_page=True)

    info = [
        f"INITIAL_URL={page.url}",
        f"INITIAL_TITLE={clean(page.title(), 500)}",
    ]

    # Flutter Web initially exposes only a tiny "Enable accessibility"
    # semantics placeholder. Clicking it makes the Flutter semantics tree
    # available to Playwright.
    accessibility = page.get_by_role(
        "button", name="Enable accessibility", exact=True
    )

    info.append(f"ACCESSIBILITY_PLACEHOLDER_COUNT={accessibility.count()}")

    if accessibility.count():
        try:
            accessibility.click(force=True, timeout=10000)
            page.wait_for_timeout(3000)
            info.append("ACCESSIBILITY_CLICK=SUCCESS")
        except Exception as e:
            info.append(f"ACCESSIBILITY_CLICK=FAIL: {type(e).__name__}: {e}")
    else:
        info.append("ACCESSIBILITY_CLICK=NOT_FOUND")

    page.screenshot(path=str(DEBUG_DIR / "02_after_accessibility.png"), full_page=True)

    # Dump the now-exposed Flutter semantics tree.
    semantics = page.locator("flt-semantics")
    info.append(f"SEMANTICS_COUNT={semantics.count()}")

    info.append("")
    info.append("=== FLUTTER SEMANTICS ===")
    for i in range(min(500, semantics.count())):
        el = semantics.nth(i)
        try:
            box = el.bounding_box()
            aria = el.get_attribute("aria-label") or ""
            role = el.get_attribute("role") or ""
            text = clean(el.inner_text(timeout=1000), 300)
            info.append(
                f"{i+1}. text={text!r} aria={aria!r} role={role!r} box={box}"
            )
        except Exception:
            pass

    # Try to activate the DOM profile through the newly exposed semantics.
    dom_candidates = [
        page.get_by_text("DOM", exact=True),
        page.locator('flt-semantics[aria-label="DOM"]'),
    ]

    clicked = False
    for loc in dom_candidates:
        try:
            if loc.count():
                info.append(f"DOM_CANDIDATE_COUNT={loc.count()}")
                loc.first.click(force=True, timeout=10000)
                clicked = True
                info.append("DOM_PROFILE_CLICK=SUCCESS")
                break
        except Exception as e:
            info.append(f"DOM_PROFILE_CLICK_ATTEMPT=FAIL: {type(e).__name__}: {e}")

    if not clicked:
        # Fallback: click the center of the visible DOM profile tile.
        info.append("DOM_PROFILE_CLICK=FALLBACK_COORDINATE")
        page.mouse.click(570, 550)

    page.wait_for_timeout(10000)
    page.screenshot(path=str(DEBUG_DIR / "03_after_dom_profile.png"), full_page=True)

    info.append(f"AFTER_PROFILE_URL={page.url}")
    info.append(f"AFTER_PROFILE_TITLE={clean(page.title(), 500)}")

    # Correctly parse the actual API structure: response.channels.
    catalog = page.request.get(
        "https://tvplayer.etmedia.tv/tvipapi/json/channels.json",
        headers={
            "auth-token": token,
            "profile-uid": profile_uid,
            "device-uid": device_uid,
        },
        timeout=60000,
    )

    channel_count = 0
    catalog_preview = ""
    try:
        data = catalog.json()
        if isinstance(data, dict):
            response = data.get("response")
            if isinstance(response, dict) and isinstance(response.get("channels"), list):
                channel_count = len(response["channels"])
            elif isinstance(data.get("channels"), list):
                channel_count = len(data["channels"])
        elif isinstance(data, list):
            channel_count = len(data)
        catalog_preview = json.dumps(data, ensure_ascii=False)[:1500]
    except Exception:
        catalog_preview = clean(catalog.text(), 1500)

    info += [
        "",
        f"CATALOG_HTTP_STATUS={catalog.status}",
        f"CATALOG_CHANNEL_COUNT={channel_count}",
        "",
        "=== BODY TEXT ===",
        clean(page.locator("body").inner_text(timeout=15000), 5000),
        "",
        "=== M3U8 REQUESTS ===",
        *(sorted(set(stream_requests))[:100] or ["NONE"]),
        "",
        "=== CATALOG PREVIEW ===",
        catalog_preview,
    ]

    (DEBUG_DIR / "debug_elements.txt").write_text(
        "\n".join(info), encoding="utf-8"
    )

    print("\n".join(info[:180]))
    print("\nSaved diagnostic files to debug/")
    browser.close()

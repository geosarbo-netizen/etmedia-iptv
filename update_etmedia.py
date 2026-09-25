import os
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_URL = "https://tvplayer.etmedia.tv/web-player/#/tv"
DEBUG_DIR = Path("debug")
DEBUG_DIR.mkdir(exist_ok=True)

token = os.environ.get("ET_AUTH_TOKEN")
profile_uid = os.environ.get("ET_PROFILE_UID")
device_uid = os.environ.get("ET_DEVICE_UID")

if not all([token, profile_uid, device_uid]):
    raise RuntimeError("Не заданы ET_AUTH_TOKEN / ET_PROFILE_UID / ET_DEVICE_UID")

def safe_text(s, limit=5000):
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

    # The previous diagnostic proved that the app stops at the profile chooser.
    # The page is Flutter-rendered, so normal DOM text selectors do not expose
    # the profile tile. The screenshot shows the "DOM" profile at this fixed
    # viewport near x=570, y=550. Click that tile once.
    page.screenshot(path=str(DEBUG_DIR / "01_profiles_before.png"), full_page=True)
    before_url = page.url

    page.mouse.click(570, 550)
    page.wait_for_timeout(8000)

    after_url = page.url
    page.screenshot(path=str(DEBUG_DIR / "02_after_profile_click.png"), full_page=True)

    # Authenticated catalog request, for diagnostics only.
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
        if isinstance(data, list):
            channel_count = len(data)
        elif isinstance(data, dict):
            for key in ("channels", "data", "items"):
                if isinstance(data.get(key), list):
                    channel_count = len(data[key])
                    break
        catalog_preview = json.dumps(data, ensure_ascii=False)[:1000]
    except Exception:
        catalog_preview = safe_text(catalog.text(), 1000)

    info = [
        f"BEFORE_URL={before_url}",
        f"AFTER_PROFILE_CLICK_URL={after_url}",
        f"PAGE_TITLE={safe_text(page.title(), 500)}",
        f"CATALOG_HTTP_STATUS={catalog.status}",
        f"CATALOG_CHANNEL_COUNT={channel_count}",
        "",
        "=== BODY TEXT ===",
        safe_text(page.locator("body").inner_text(timeout=15000), 5000),
        "",
        "=== VISIBLE SEMANTICS / CLICKABLE ELEMENTS ===",
    ]

    # Flutter's accessibility/semantics tree may expose elements after the
    # profile click. Collect only visible text/labels, not page HTML or headers.
    js = r"""
    () => {
      const selectors = [
        'flt-semantics', 'flt-semantics-placeholder',
        'button', 'a', '[role="button"]', '[role="link"]',
        '[role="option"]', '[role="listitem"]'
      ];
      const nodes = Array.from(document.querySelectorAll(selectors.join(',')));
      const out = [];
      for (const el of nodes) {
        const r = el.getBoundingClientRect();
        const cs = getComputedStyle(el);
        if (!r.width || !r.height || cs.display === 'none' ||
            cs.visibility === 'hidden' || cs.opacity === '0') continue;
        out.push({
          tag: el.tagName.toLowerCase(),
          text: (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 200),
          aria: (el.getAttribute('aria-label') || '').slice(0, 200),
          role: (el.getAttribute('role') || ''),
          x: Math.round(r.x), y: Math.round(r.y),
          w: Math.round(r.width), h: Math.round(r.height)
        });
      }
      return out;
    }
    """
    elements = page.evaluate(js)
    for i, e in enumerate(elements[:500]):
        info.append(
            f"{i+1}. <{e['tag']}> text={e['text']!r} "
            f"aria={e['aria']!r} role={e['role']!r} "
            f"box=({e['x']},{e['y']},{e['w']},{e['h']})"
        )

    info += [
        "",
        "=== CATALOG PREVIEW (first 1000 chars) ===",
        catalog_preview,
        "",
        "=== M3U8 REQUESTS ===",
        *(sorted(set(stream_requests))[:100] or ["NONE"]),
    ]

    (DEBUG_DIR / "debug_elements.txt").write_text(
        "\n".join(info), encoding="utf-8"
    )

    print("\n".join(info[:120]))
    print("\nSaved diagnostic files to debug/")
    browser.close()

import os
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

def safe_text(s, limit=300):
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

    # Do not save request headers, URLs with query strings, or page HTML:
    # those may contain authentication material.
    frames_seen = []
    stream_requests = []

    def on_request(request):
        if "etm.etmedia.tv" in request.url and ".m3u8" in request.url:
            stream_requests.append(request.url.split("?", 1)[0])

    page.on("request", on_request)

    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=120000)
    page.wait_for_timeout(10000)

    # Load the public channel catalog through the authenticated context.
    catalog = page.request.get(
        "https://tvplayer.etmedia.tv/tvipapi/json/channels.json",
        headers={
            "auth-token": token,
            "profile-uid": profile_uid,
            "device-uid": device_uid,
        },
        timeout=60000,
    )
    catalog_status = catalog.status
    catalog_text = catalog.text()
    channel_count = 0
    try:
        import json
        data = json.loads(catalog_text)
        if isinstance(data, list):
            channel_count = len(data)
        elif isinstance(data, dict):
            for key in ("channels", "data", "items"):
                if isinstance(data.get(key), list):
                    channel_count = len(data[key])
                    break
    except Exception:
        pass

    page.screenshot(path=str(DEBUG_DIR / "debug_page.png"), full_page=True)

    info = []
    info.append(f"PAGE_URL={page.url}")
    info.append(f"PAGE_TITLE={safe_text(page.title(), 500)}")
    info.append(f"CATALOG_HTTP_STATUS={catalog_status}")
    info.append(f"CATALOG_CHANNEL_COUNT={channel_count}")
    info.append(f"FRAME_COUNT={len(page.frames)}")
    info.append("")

    info.append("=== FRAMES ===")
    for i, frame in enumerate(page.frames):
        info.append(f"[FRAME {i}] URL={frame.url}")

    info.append("")
    info.append("=== BODY TEXT (first 5000 chars) ===")
    body_text = safe_text(page.locator("body").inner_text(timeout=15000), 5000)
    info.append(body_text)

    info.append("")
    info.append("=== VISIBLE CLICKABLE ELEMENTS ===")

    js = r"""
    () => {
      const selectors = [
        'button', 'a', '[role="button"]', '[role="link"]',
        '[role="option"]', '[role="listitem"]',
        '[ng-click]', '[onclick]',
        '[tabindex]:not([tabindex="-1"])'
      ];
      const nodes = Array.from(document.querySelectorAll(selectors.join(',')));
      const out = [];
      for (const el of nodes) {
        const r = el.getBoundingClientRect();
        const cs = getComputedStyle(el);
        if (!r.width || !r.height || cs.display === 'none' ||
            cs.visibility === 'hidden' || cs.opacity === '0') continue;

        const attrs = {};
        for (const a of el.attributes) {
          if (/^(class|id|role|aria-label|title|href|data-|ng-)/i.test(a.name)) {
            attrs[a.name] = a.value.slice(0, 250);
          }
        }

        out.push({
          tag: el.tagName.toLowerCase(),
          text: (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 200),
          aria: (el.getAttribute('aria-label') || '').slice(0, 200),
          title: (el.getAttribute('title') || '').slice(0, 200),
          cls: (el.className && typeof el.className === 'string') ? el.className.slice(0, 300) : '',
          attrs,
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
            f"{i+1}. <{e['tag']}> "
            f"text={e['text']!r} aria={e['aria']!r} title={e['title']!r} "
            f"class={e['cls']!r} box=({e['x']},{e['y']},{e['w']},{e['h']}) "
            f"attrs={e['attrs']}"
        )

    info.append("")
    info.append("=== IFRAMES IN DOM ===")
    iframe_info = page.evaluate("""
      () => Array.from(document.querySelectorAll('iframe')).map((x, i) => ({
        i,
        src: x.getAttribute('src') || '',
        title: x.getAttribute('title') || '',
        name: x.getAttribute('name') || '',
        cls: typeof x.className === 'string' ? x.className : ''
      }))
    """)
    for x in iframe_info:
        info.append(str(x))

    info.append("")
    info.append("=== M3U8 REQUESTS SEEN WHILE IDLE ===")
    info.extend(sorted(set(stream_requests))[:100] or ["NONE"])

    (DEBUG_DIR / "debug_elements.txt").write_text(
        "\n".join(info), encoding="utf-8"
    )

    print("\n".join(info[:80]))
    print(f"\nSaved diagnostics to {DEBUG_DIR}/")
    print("The diagnostic intentionally does NOT print auth headers or tokenized URLs.")

    browser.close()

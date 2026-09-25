import os
import json
import hashlib
import re
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from playwright.sync_api import sync_playwright

BASE_URL = "https://tvplayer.etmedia.tv/web-player/#/tv"
CATALOG_URL = "https://tvplayer.etmedia.tv/tvipapi/json/channels.json"
DEBUG_DIR = Path("debug")
DEBUG_DIR.mkdir(exist_ok=True)

AUTH = os.environ.get("ET_AUTH_TOKEN")
PROFILE = os.environ.get("ET_PROFILE_UID")
DEVICE = os.environ.get("ET_DEVICE_UID")

if not all([AUTH, PROFILE, DEVICE]):
    raise RuntimeError("Не заданы ET_AUTH_TOKEN / ET_PROFILE_UID / ET_DEVICE_UID")

AUTH_HEADERS = {
    "auth-token": AUTH,
    "profile-uid": PROFILE,
    "device-uid": DEVICE,
}

def token_info(url):
    try:
        q = parse_qs(urlsplit(url).query)
        vals = q.get("token", [])
        if not vals:
            return "NO_TOKEN"
        t = vals[0]
        return f"TOKEN_LEN={len(t)} TOKEN_SHA256_12={hashlib.sha256(t.encode()).hexdigest()[:12]}"
    except Exception:
        return "TOKEN_PARSE_ERROR"

def redact(url):
    return re.sub(r"([?&]token=)[^&]+", r"\1<REDACTED>", url)

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
            headers.update(AUTH_HEADERS)
        route.continue_(headers=headers)

    context.route("**/*", add_auth)
    page = context.new_page()

    seen = []

    def on_request(req):
        if "etm.etmedia.tv" in req.url and ".m3u8" in req.url:
            seen.append(req.url)

    page.on("request", on_request)

    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=120000)
    page.wait_for_timeout(5000)

    # The Flutter page initially lands on the profile chooser.
    # Use the same working coordinate fallback that reached #/tv.
    if "#/profiles" in page.url:
        page.mouse.click(570, 550)
        page.wait_for_timeout(10000)

    report = [
        f"PLAYER_URL={page.url}",
        f"PLAYER_TITLE={page.title()}",
        "",
        "=== NETWORK M3U8 REQUESTS ===",
    ]

    for u in sorted(set(seen)):
        report.append(f"{redact(u)} | {token_info(u)}")

    # Fetch the first few catalog stream URLs directly.
    catalog_resp = page.request.get(CATALOG_URL, headers=AUTH_HEADERS, timeout=60000)
    report += [
        "",
        f"CATALOG_STATUS={catalog_resp.status}",
    ]

    data = catalog_resp.json()
    channels = data.get("response", {}).get("channels", []) if isinstance(data, dict) else []
    report.append(f"CHANNELS={len(channels)}")

    report.append("")
    report.append("=== DIRECT VIDEO.M3U8 PROBES ===")

    for ch in channels[:5]:
        title = ch.get("title", "")
        url = ch.get("url", "")
        try:
            r = page.request.get(
                url,
                headers={
                    **AUTH_HEADERS,
                    "Referer": "https://tvplayer.etmedia.tv/web-player/",
                    "Origin": "https://tvplayer.etmedia.tv",
                },
                timeout=30000,
            )
            body = r.text()
            # Never save a live token in the artifact.
            safe_body = re.sub(
                r"([?&]token=)[^&\s]+",
                r"\1<REDACTED>",
                body,
            )
            # Also redact token-like values in JSON/string contexts.
            safe_body = re.sub(
                r'("token"\s*:\s*")[^"]+(")',
                r'\1<REDACTED>\2',
                safe_body,
            )
            report.append(
                f"[{title}] HTTP={r.status} URL={redact(url)} "
                f"CONTENT_TYPE={r.headers.get('content-type','')}"
            )
            report.append(f"BODY_HEAD={safe_body[:2000]}")
            report.append(f"BODY_TOKEN_INFO={token_info(body if '://' in body else url)}")
        except Exception as e:
            report.append(f"[{title}] ERROR={type(e).__name__}: {e}")

    report += [
        "",
        "=== UNIQUE REQUEST TOKEN FINGERPRINTS ===",
    ]
    fingerprints = sorted(set(token_info(u) for u in seen if "token=" in u))
    report.extend(fingerprints or ["NONE"])

    (DEBUG_DIR / "stream_probe.txt").write_text(
        "\n".join(report), encoding="utf-8"
    )

    print("\n".join(report))
    print("\nSaved debug/stream_probe.txt (tokens redacted).")
    browser.close()

import os
import re
import json
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

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

def redact(text):
    return re.sub(r'([?&]token=)[^&\s]+', r'\1<REDACTED>', text)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context()

    # Auth is used ONLY to obtain the channel catalog.
    catalog_resp = context.request.get(
        CATALOG_URL,
        headers=AUTH_HEADERS,
        timeout=60000,
    )

    report = [
        f"CATALOG_WITH_AUTH_STATUS={catalog_resp.status}",
    ]

    data = catalog_resp.json()
    channels = data.get("response", {}).get("channels", []) if isinstance(data, dict) else []
    report.append(f"CHANNELS={len(channels)}")
    report.append("")
    report.append("=== MASTER PLAYLISTS WITHOUT AUTH ===")

    for ch in channels[:10]:
        title = ch.get("title", "")
        master = ch.get("url", "")
        if not master:
            continue

        try:
            # Deliberately NO ET Media auth headers here.
            r = context.request.get(
                master,
                headers={
                    "Referer": "https://tvplayer.etmedia.tv/web-player/",
                    "Origin": "https://tvplayer.etmedia.tv",
                },
                timeout=30000,
            )
            body = r.text()
            report.append(
                f"[{title}] MASTER_HTTP={r.status} "
                f"URL={redact(master)} CONTENT_TYPE={r.headers.get('content-type','')}"
            )

            lines = [
                x.strip()
                for x in body.splitlines()
                if x.strip() and not x.startswith("#")
            ]
            report.append(f"MASTER_VARIANTS={len(lines)}")

            # Test the first media playlist referenced by the master.
            if lines:
                child = urljoin(master, lines[0])
                cr = context.request.get(
                    child,
                    headers={
                        "Referer": "https://tvplayer.etmedia.tv/web-player/",
                        "Origin": "https://tvplayer.etmedia.tv",
                    },
                    timeout=30000,
                )
                report.append(
                    f"CHILD_HTTP={cr.status} "
                    f"URL={redact(child)} CONTENT_TYPE={cr.headers.get('content-type','')}"
                )
                child_text = redact(cr.text()[:1000])
                report.append(f"CHILD_BODY_HEAD={child_text}")

        except Exception as e:
            report.append(f"[{title}] ERROR={type(e).__name__}: {e}")

    (DEBUG_DIR / "public_probe.txt").write_text(
        "\n".join(report), encoding="utf-8"
    )
    print("\n".join(report))

    browser.close()

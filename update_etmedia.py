import os, json, re
from pathlib import Path
from playwright.sync_api import sync_playwright

CATALOG_URL="https://tvplayer.etmedia.tv/tvipapi/json/channels.json"
DEBUG=Path("debug"); DEBUG.mkdir(exist_ok=True)

with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    context=browser.new_context()
    page=context.new_page()

    # No ET Media auth headers are added anywhere in this test.
    r=page.request.get(CATALOG_URL, timeout=60000)
    report=[f"CATALOG_WITHOUT_AUTH_STATUS={r.status}"]
    try:
        data=r.json()
        channels=data.get("response",{}).get("channels",[])
    except Exception:
        channels=[]
    report.append(f"CHANNELS={len(channels)}")
    report.append("")
    report.append("=== MASTER PLAYLISTS WITHOUT AUTH ===")

    for ch in channels[:5]:
        title=ch.get("title","")
        url=ch.get("url","")
        try:
            rr=page.request.get(url, timeout=30000)
            body=rr.text()
            report.append(f"[{title}] MASTER_HTTP={rr.status} CONTENT_TYPE={rr.headers.get('content-type','')}")
            lines=[x.strip() for x in body.splitlines() if x.strip() and not x.startswith("#")]
            child=lines[0] if lines else ""
            report.append(f"MASTER_CHILD={child}")
            if child:
                from urllib.parse import urljoin
                child_url=urljoin(url,child)
                cr=page.request.get(child_url, timeout=30000)
                report.append(f"CHILD_URL={child_url}")
                report.append(f"CHILD_HTTP={cr.status} CONTENT_TYPE={cr.headers.get('content-type','')}")
                report.append(f"CHILD_HEAD={cr.text()[:500].replace(chr(10),' | ')}")
        except Exception as e:
            report.append(f"[{title}] ERROR={type(e).__name__}: {e}")
        report.append("")

    (DEBUG/"public_probe.txt").write_text("\n".join(report),encoding="utf-8")
    print("\n".join(report))
    browser.close()

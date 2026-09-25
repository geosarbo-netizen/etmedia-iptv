import asyncio
import json
import os
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

PLAYER = "https://tvplayer.etmedia.tv/web-player/#/tv"
CHANNELS_API = "https://tvplayer.etmedia.tv/tvipapi/json/channels.json"
OUTPUT = Path("ETMedia.m3u")

AUTH = os.environ["ET_AUTH_TOKEN"]
PROFILE = os.environ["ET_PROFILE_UID"]
DEVICE = os.environ["ET_DEVICE_UID"]

TVPLAYER_HOST = "tvplayer.etmedia.tv"

async def main():
    headers = {
        "auth-token": AUTH,
        "profile-uid": PROFILE,
        "device-uid": DEVICE,
        "Referer": "https://tvplayer.etmedia.tv/web-player/",
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            viewport={"width": 1440, "height": 1000},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/140.0.0.0 Safari/537.36"
            ),
        )

        # Add ET Media API authorization only to requests going to tvplayer.etmedia.tv.
        async def route_handler(route):
            req = route.request
            u = req.url
            if urlparse(u).netloc == TVPLAYER_HOST:
                h = dict(req.headers)
                h.update(headers)
                await route.continue_(headers=h)
            else:
                await route.continue_()

        await context.route("**/*", route_handler)

        page = await context.new_page()
        await page.goto(PLAYER, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)

        # Get the same catalogue used by the web player.
        result = await page.evaluate("""
        async (url) => {
            const r = await fetch(url, {headers: {
              'auth-token': undefined
            }});
            if (!r.ok) throw new Error('channels.json HTTP ' + r.status);
            return await r.json();
        }
        """, CHANNELS_API)

        channels = result["response"]["channels"]
        print(f"Каталог: {len(channels)} каналов")

        captured = {}
        current_id = None

        def on_request(req):
            nonlocal current_id
            u = req.url
            if (
                current_id is not None
                and "etm.etmedia.tv/" in u
                and ".m3u8" in u
                and "token=" in u
            ):
                captured[current_id] = u.split("#", 1)[0]

        page.on("request", on_request)

        async def click_channel(ch):
            title = ch.get("title", "")
            number = str(ch.get("number", ""))

            # Exact visible text is preferred; ET Media currently exposes
            # channel titles in the player UI.
            selectors = [
                f'text="{title}"',
                f'[aria-label="{title}"]',
                f'[title="{title}"]',
            ]

            for sel in selectors:
                try:
                    loc = page.locator(sel).first
                    if await loc.count():
                        await loc.scroll_into_view_if_needed(timeout=1500)
                        await loc.click(timeout=3000)
                        return True
                except Exception:
                    pass

            # DOM fallback: click a leaf element whose visible text is exactly
            # the channel title.
            try:
                ok = await page.evaluate("""
                (title) => {
                  const els = [...document.querySelectorAll('button,a,[role="button"],div,span')];
                  const el = els.find(e =>
                    e.children.length === 0 &&
                    e.textContent.trim() === title
                  );
                  if (!el) return false;
                  el.scrollIntoView({block:'center'});
                  el.click();
                  return true;
                }
                """, title)
                if ok:
                    return True
            except Exception:
                pass

            return False

        # First pass: trigger each channel and capture its real authorized HLS URL.
        for i, ch in enumerate(channels, 1):
            if not ch.get("url", "").endswith("video.m3u8"):
                continue

            current_id = ch["id"]
            before = captured.get(current_id)
            print(f"[{i}/{len(channels)}] {ch.get('number')} {ch.get('title')} ...", flush=True)

            ok = await click_channel(ch)
            if not ok:
                print("  click: FAIL")
                continue

            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                if captured.get(current_id) and captured.get(current_id) != before:
                    break
                await page.wait_for_timeout(200)

            if captured.get(current_id):
                print("  stream: OK")
            else:
                print("  stream: FAIL")

        page.remove_listener("request", on_request)
        await browser.close()

    # Build the public playlist. Only channels for which the current run
    # obtained an authorized tokenized URL are published.
    lines = ["#EXTM3U"]
    written = 0
    for ch in channels:
        u = captured.get(ch["id"])
        if not u:
            continue

        title = str(ch.get("title", "")).replace('"', "'")
        number = str(ch.get("number", ""))
        logo = ""
        li = ch.get("logo_image")
        if isinstance(li, dict):
            logo = li.get("url") or ""

        attrs = [f'tvg-name="{title}"']
        if number:
            attrs.append(f'channel-number="{number}"')
        if logo:
            attrs.append(f'tvg-logo="{logo}"')

        lines.append("#EXTINF:-1 " + " ".join(attrs) + "," + title)
        lines.append(u)
        written += 1

    if written < 150:
        raise RuntimeError(
            f"Получено только {written} каналов из {len(channels)}. "
            "Плейлист не публикую, чтобы не заменить рабочий файл неполным."
        )

    OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Записано: {written} каналов -> {OUTPUT}")

if __name__ == "__main__":
    asyncio.run(main())

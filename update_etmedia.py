import json
import os
import urllib.request
from pathlib import Path

CATALOG_URL = "https://tvplayer.etmedia.tv/tvipapi/json/channels.json"
OUTPUT = Path("ETMedia.m3u")

AUTH = os.environ.get("ET_AUTH_TOKEN")
PROFILE = os.environ.get("ET_PROFILE_UID")
DEVICE = os.environ.get("ET_DEVICE_UID")

if not all([AUTH, PROFILE, DEVICE]):
    raise RuntimeError("Не заданы ET_AUTH_TOKEN / ET_PROFILE_UID / ET_DEVICE_UID")

headers = {
    "auth-token": AUTH,
    "profile-uid": PROFILE,
    "device-uid": DEVICE,
    "User-Agent": "ETMedia-GitHub-Updater/1.0",
    "Referer": "https://tvplayer.etmedia.tv/web-player/",
}

req = urllib.request.Request(CATALOG_URL, headers=headers)
with urllib.request.urlopen(req, timeout=60) as response:
    if response.status != 200:
        raise RuntimeError(f"channels.json HTTP {response.status}")
    data = json.load(response)

channels = data.get("response", {}).get("channels", [])
if not isinstance(channels, list) or len(channels) < 150:
    raise RuntimeError(
        f"Получено подозрительно мало каналов: {len(channels) if isinstance(channels, list) else 0}"
    )

lines = [
    "#EXTM3U",
    "#PLAYLIST:ET Media",
]

seen_ids = set()
seen_urls = set()

for ch in channels:
    channel_id = ch.get("id")
    title = str(ch.get("title") or "").strip()
    number = ch.get("number")
    url = str(ch.get("url") or "").strip()

    # We intentionally publish only the stable master playlist URL.
    # No auth token is embedded in ETMedia.m3u.
    if not title or not url:
        continue
    if "etm.etmedia.tv/" not in url or not url.endswith(".m3u8"):
        continue
    if channel_id in seen_ids or url in seen_urls:
        continue

    seen_ids.add(channel_id)
    seen_urls.add(url)

    attrs = [f'tvg-id="{channel_id}"', f'tvg-name="{title.replace(chr(34), "&quot;")}"']
    if number is not None:
        attrs.append(f'tvg-chno="{number}"')

    lines.append(f'#EXTINF:-1 {" ".join(attrs)},{title}')
    lines.append(url)

if len(seen_urls) < 150:
    raise RuntimeError(f"После фильтрации осталось только {len(seen_urls)} каналов")

OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")

print(f"Catalog channels: {len(channels)}")
print(f"Playlist entries: {len(seen_urls)}")
print(f"Written: {OUTPUT}")
print("Authentication headers were used only for channels.json and are not written to the playlist.")

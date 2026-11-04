"""Filter proxy for the neueroeffnung.info feed.

The upstream feed covers all of DACH (~130 entries per day) and cannot be
filtered server-side. This proxy polls it, checks every new entry against a
postcode list (taken from the detail page), and serves the matches as a
JSON Feed for the maubot rss plugin.

Stdlib only. Configuration through environment variables, see README.
"""

import json
import logging
import os
import re
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

FEED_URL = os.environ.get("FEED_URL", "https://www.neueroeffnung.info/feed")
# Postcodes of the target area. Default: Landkreis Fulda.
PLZ = set(os.environ.get("PLZ", "").split())
# Fallback when a detail page has no postcode: first path segment of the link.
SLUGS = set(os.environ.get("SLUGS", "").split())
FEED_TITLE = os.environ.get("FEED_TITLE", "neueroeffnung.info (gefiltert)")
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "600"))
KEEP_DAYS = int(os.environ.get("KEEP_DAYS", "14"))
REQUEST_DELAY = float(os.environ.get("REQUEST_DELAY", "1"))
CACHE_FILE = os.environ.get("CACHE_FILE", "/cache/cache.json")
USER_AGENT = os.environ.get(
    "USER_AGENT", "osm-fulda-feeds/nf-filter (+https://github.com/osm-fulda/osm-fulda-feeds)"
)
# Upstream pubDate has no zone ("2026-11-04 08:44:08"); assume local time.
UPSTREAM_TZ = ZoneInfo(os.environ.get("UPSTREAM_TZ", "Europe/Berlin"))

# "Kontaktdaten Am Aspe 2, 36148 Kalbach" -> 36148
PLZ_RE = re.compile(r"Kontaktdaten.{0,200}?\b(\d{5})\s+\w", re.S)
TAG_RE = re.compile(r"<script.*?</script>|<style.*?</style>|<[^>]+>", re.S)

log = logging.getLogger("nf-filter")
lock = threading.Lock()
# link -> {"match": bool, "plz": str|None, "seen": iso, "item": {...}}
cache: dict[str, dict] = {}
last_poll_ok: datetime | None = None


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def find_plz(page: str) -> str | None:
    text = re.sub(r"\s+", " ", TAG_RE.sub(" ", page))
    m = PLZ_RE.search(text)
    return m.group(1) if m else None


def slug_of(link: str) -> str:
    path = link.split("://", 1)[-1].split("/", 1)[-1]
    return path.split("/", 1)[0]


def parse_date(raw: str) -> str:
    try:
        dt = datetime.strptime(raw.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=UPSTREAM_TZ)
    except ValueError:
        dt = datetime.now(timezone.utc)
    return dt.isoformat()


def classify(link: str) -> tuple[bool, str | None]:
    """Return (match, plz). The postcode decides; the slug is only a fallback."""
    try:
        plz = find_plz(fetch(link).decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001 -- retry on next poll
        raise RuntimeError(f"detail page {link}: {e}") from e
    if plz:
        return plz in PLZ, plz
    return slug_of(link) in SLUGS, None


def poll() -> None:
    global last_poll_ok
    root = ET.fromstring(fetch(FEED_URL))
    now = datetime.now(timezone.utc)
    new = 0
    for item in root.iter("item"):
        link = (item.findtext("link") or "").strip()
        if not link:
            continue
        with lock:
            known = cache.get(link)
            if known:
                known["seen"] = now.isoformat()
                continue
        # Every new entry needs the detail page: the site also uses district
        # slugs (e.g. "mittelkalbach"), and names like "kalbach" are ambiguous.
        try:
            match, plz = classify(link)
        except RuntimeError as e:
            log.warning("%s", e)
            continue
        finally:
            time.sleep(REQUEST_DELAY)
        new += 1
        entry = {
            "match": match,
            "plz": plz,
            "seen": now.isoformat(),
            # Keep the item body only for matches; the rest only marks "checked".
            "item": {
                "id": link,
                "url": link,
                "title": (item.findtext("title") or "").strip(),
                "content_text": (item.findtext("description") or "").strip(),
                "date_published": parse_date(item.findtext("pubDate") or ""),
            }
            if match
            else None,
        }
        if match:
            log.info("match %s (plz %s)", link, plz or "-, slug")
        with lock:
            cache[link] = entry
    cutoff = (now - timedelta(days=KEEP_DAYS)).isoformat()
    with lock:
        for link in [k for k, v in cache.items() if v["seen"] < cutoff]:
            del cache[link]
        save()
        last_poll_ok = now
    log.info("poll done: %d new checked, %d cached", new, len(cache))


def load() -> None:
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            cache.update(json.load(f))
        log.info("loaded %d cached entries", len(cache))
    except FileNotFoundError:
        pass


def save() -> None:
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)
    os.replace(tmp, CACHE_FILE)


def poller() -> None:
    while True:
        try:
            poll()
        except Exception:  # noqa: BLE001 -- keep the thread alive
            log.exception("poll failed")
        time.sleep(POLL_SECONDS)


def build_feed() -> dict:
    with lock:
        items = [v["item"] for v in cache.values() if v["match"]]
    items.sort(key=lambda i: i["date_published"], reverse=True)
    return {
        "version": "https://jsonfeed.org/version/1.1",
        "title": FEED_TITLE,
        "home_page_url": "https://www.neueroeffnung.info/",
        "items": items,
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/feed.json":
            body = json.dumps(build_feed(), ensure_ascii=False).encode()
            ctype = "application/feed+json"
            status = 200
        elif self.path == "/health":
            ok = last_poll_ok and datetime.now(timezone.utc) - last_poll_ok < timedelta(
                seconds=3 * POLL_SECONDS
            )
            body = b"ok\n" if ok else b"stale\n"
            ctype = "text/plain"
            status = 200 if ok else 503
        else:
            self.send_error(404)
            return
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        log.debug(fmt, *args)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not PLZ:
        raise SystemExit("PLZ is empty -- set the postcode list")
    load()
    threading.Thread(target=poller, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()


if __name__ == "__main__":
    main()

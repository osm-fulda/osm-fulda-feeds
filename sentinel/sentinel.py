"""QA feed for OSM data in Landkreis Fulda.

Once per night, one Overpass query gets the current state. The script
compares the result with the previous run and serves only new findings as a
JSON Feed for the maubot rss plugin.

Check 1 (the only check at the moment): a road has sidewalk=both/yes/left/right
on a side that also has sidewalk:<side>=separate. Then routers see the
sidewalk two times. A typical cause is the StreetComplete or iD question
"Is there a sidewalk here?".

Stdlib only. Configuration through environment variables, see README.
Run "python sentinel.py --once" to print the findings and exit.
"""

import json
import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

# Tried in order. A busy server answers fast with 429/502/504.
ENDPOINTS = os.environ.get(
    "OVERPASS",
    "https://overpass-api.de/api/interpreter https://overpass.private.coffee/api/interpreter",
).split()
# Landkreis Fulda.
AREA_RELATION = int(os.environ.get("AREA_RELATION", "62700"))
FEED_TITLE = os.environ.get("FEED_TITLE", "Auffälligkeiten – Landkreis Fulda")
# Local time of the nightly run. At night the Overpass servers have less load.
RUN_AT = os.environ.get("RUN_AT", "04:15")
TZ = ZoneInfo(os.environ.get("TZ", "Europe/Berlin"))
RETRY_MINUTES = int(os.environ.get("RETRY_MINUTES", "30"))
# More new findings than this in one run: one summary item instead.
MAX_ITEMS_PER_RUN = int(os.environ.get("MAX_ITEMS_PER_RUN", "20"))
KEEP_DAYS = int(os.environ.get("KEEP_DAYS", "30"))
STATE_FILE = os.environ.get("STATE_FILE", "/state/state.json")
USER_AGENT = os.environ.get(
    "USER_AGENT", "osm-fulda-feeds/sentinel (+https://github.com/osm-fulda/osm-fulda-feeds)"
)

log = logging.getLogger("sentinel")
lock = threading.Lock()
STARTED = datetime.now(timezone.utc)
# open:  finding key -> ISO time of first sight. A key leaves "open" when the
#        problem is fixed. If it comes back later, it is new again.
# items: JSON Feed items, newest first
# last_ok: ISO time of the last good run
state: dict = {"open": {}, "items": [], "last_ok": None}

# One query for all checks. Only ways with a sidewalk tag and a "separate"
# side come back, so the result stays small (a few hundred ways at most).
QUERY = f"""[out:json][timeout:120];
rel({AREA_RELATION});map_to_area->.a;
way[highway][sidewalk](area.a)->.s;
(
  way.s["sidewalk:both"="separate"];
  way.s["sidewalk:left"="separate"];
  way.s["sidewalk:right"="separate"];
);
out tags meta;
"""

SIDES = {"both": {"left", "right"}, "yes": {"left", "right"}, "left": {"left"}, "right": {"right"}}


def check_sidewalk_twice(way: dict) -> str | None:
    """Return a short description if a side has a sidewalk on the road and a separate one."""
    tags = way.get("tags", {})
    on_road = SIDES.get(tags.get("sidewalk", ""), set())
    separate = set()
    if tags.get("sidewalk:both") == "separate":
        separate |= {"left", "right"}
    for side in ("left", "right"):
        if tags.get(f"sidewalk:{side}") == "separate":
            separate.add(side)
    if not on_road & separate:
        return None
    sep = "; ".join(
        f"{k}={v}" for k, v in sorted(tags.items()) if k.startswith("sidewalk:") and v == "separate"
    )
    return f"sidewalk={tags['sidewalk']} trotz {sep}"


CHECKS = [("gehweg-doppelt", "Gehweg doppelt", check_sidewalk_twice)]


def overpass(query: str) -> dict:
    body = urllib.parse.urlencode({"data": query}).encode()
    errors = []
    for url in ENDPOINTS:
        req = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.load(resp)
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            errors.append(f"{url}: {e}")
            continue
        # Overpass sends HTTP 200 with a "remark" when the query fails.
        if "error" in data.get("remark", ""):
            errors.append(f"{url}: {data['remark']}")
            continue
        return data
    raise RuntimeError("; ".join(errors))


def find(data: dict) -> dict[str, dict]:
    """Return finding key -> details for all current problems."""
    found = {}
    for way in data["elements"]:
        for check_id, label, fn in CHECKS:
            text = fn(way)
            if text:
                found[f"{check_id}:way/{way['id']}"] = {
                    "label": label,
                    "text": text,
                    "way": way["id"],
                    "name": way["tags"].get("name") or way["tags"].get("ref") or "",
                    "user": way.get("user", "?"),
                    "changeset": way.get("changeset"),
                }
    return found


def make_item(key: str, f: dict, now: datetime) -> dict:
    where = f"„{f['name']}“" if f["name"] else f"Weg {f['way']}"
    way_url = f"https://www.openstreetmap.org/way/{f['way']}"
    return {
        # The time in the id makes a problem that comes back a new entry.
        "id": f"{key}@{now.strftime('%Y-%m-%dT%H:%M')}",
        "url": way_url,
        "title": f"{f['label']}: {where} – {f['text']} (zuletzt bearbeitet von {f['user']})",
        "content_text": (
            f"{f['text']}\n\nWeg: {way_url}\n"
            f"Letztes Changeset: https://www.openstreetmap.org/changeset/{f['changeset']}"
        ),
        "date_published": now.isoformat(),
    }


def run() -> None:
    now = datetime.now(timezone.utc)
    found = find(overpass(QUERY))
    with lock:
        new = [k for k in found if k not in state["open"]]
        fixed = [k for k in state["open"] if k not in found]
        state["open"] = {k: state["open"].get(k, now.isoformat()) for k in found}
        items = [make_item(k, found[k], now) for k in new[:MAX_ITEMS_PER_RUN]]
        if len(new) > MAX_ITEMS_PER_RUN:
            items.append(
                {
                    "id": f"overflow@{now.isoformat()}",
                    "url": "https://wiki.openstreetmap.org/wiki/Fulda",
                    "title": f"… und {len(new) - MAX_ITEMS_PER_RUN} weitere neue Funde "
                    "(nicht einzeln gemeldet, Limit pro Lauf erreicht)",
                    "content_text": "Wahrscheinlich gab es eine große Bearbeitung.",
                    "date_published": now.isoformat(),
                }
            )
        cutoff = (now - timedelta(days=KEEP_DAYS)).isoformat()
        state["items"] = items + [i for i in state["items"] if i["date_published"] >= cutoff]
        state["last_ok"] = now.isoformat()
        save()
    log.info("run done: %d open, %d new, %d fixed", len(found), len(new), len(fixed))


def next_run_after(t: datetime) -> datetime:
    hh, mm = map(int, RUN_AT.split(":"))
    local = t.astimezone(TZ)
    nxt = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if nxt <= local:
        nxt += timedelta(days=1)
    return nxt.astimezone(timezone.utc)


def scheduler() -> None:
    now = datetime.now(timezone.utc)
    last = state["last_ok"]
    # Missed a night: run at once. Else, and also on the very first start,
    # wait for RUN_AT. So there is time to subscribe before the first run:
    # maubot posts nothing for items that are in the feed at subscribe time.
    if last and now - datetime.fromisoformat(last) > timedelta(days=1):
        due = now
    else:
        due = next_run_after(now)
    while True:
        wait = (due - datetime.now(timezone.utc)).total_seconds()
        if wait > 0:
            log.info("next run %s", due.astimezone(TZ).strftime("%Y-%m-%d %H:%M"))
            time.sleep(wait)
        try:
            run()
            due = next_run_after(datetime.now(timezone.utc))
            continue
        except RuntimeError as e:
            # All Overpass servers busy or down. This is normal, no traceback.
            log.warning("overpass failed, retry in %d minutes: %s", RETRY_MINUTES, e)
        except Exception:  # noqa: BLE001 -- retry later, keep the thread alive
            log.exception("run failed, retry in %d minutes", RETRY_MINUTES)
        due = datetime.now(timezone.utc) + timedelta(minutes=RETRY_MINUTES)


def load() -> None:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            state.update(json.load(f))
        log.info("loaded state: %d open findings", len(state["open"]))
    except FileNotFoundError:
        pass


def save() -> None:
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/feed.json":
            with lock:
                feed = {
                    "version": "https://jsonfeed.org/version/1.1",
                    "title": FEED_TITLE,
                    "home_page_url": "https://wiki.openstreetmap.org/wiki/Fulda",
                    "items": state["items"],
                }
            body = json.dumps(feed, ensure_ascii=False).encode()
            ctype, status = "application/feed+json", 200
        elif self.path == "/health":
            now = datetime.now(timezone.utc)
            last = state["last_ok"]
            # Two nights without a good run: Overpass is down for long, or we are broken.
            # Before the first run: ok while the first night is still to come.
            if last:
                ok = now - datetime.fromisoformat(last) < timedelta(hours=50)
            else:
                ok = now - STARTED < timedelta(hours=26)
            body = b"ok\n" if ok else b"stale\n"
            ctype, status = "text/plain", 200 if ok else 503
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
    if "--once" in sys.argv:
        for key, f in sorted(find(overpass(QUERY)).items()):
            print(key, "|", make_item(key, f, datetime.now(timezone.utc))["title"])
        return
    load()
    threading.Thread(target=scheduler, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()


if __name__ == "__main__":
    main()

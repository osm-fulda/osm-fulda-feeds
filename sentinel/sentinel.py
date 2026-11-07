"""QA feed for OSM data in Landkreis Fulda.

Once per night, one Overpass query gets the current state. The script
compares the result with the previous run and serves only new findings as a
JSON Feed for the maubot rss plugin.

The checks are in CHECKS. Each check is a function that gets one OSM element
from the query result and returns a short text if the element has a problem.

The script uses only the standard library. Environment variables set the
configuration, see README. Run "python sentinel.py --once" to print the
findings and stop.
"""

import http.client
import json
import logging
import os
import re
import sys
import threading
import time
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
# A finding that disappears and then appears again within this time gets no
# second message. This stops repeated posts after a bad or old Overpass result.
GRACE_DAYS = int(os.environ.get("GRACE_DAYS", "7"))
STATE_FILE = os.environ.get("STATE_FILE", "/state/state.json")
USER_AGENT = os.environ.get(
    "USER_AGENT", "osm-fulda-feeds/sentinel (+https://github.com/osm-fulda/osm-fulda-feeds)"
)

log = logging.getLogger("sentinel")
lock = threading.Lock()
STARTED = datetime.now(timezone.utc)
# open:    finding key -> ISO time of first sight. A key leaves "open" when a
#          mapper repairs the problem.
# closed:  finding key -> ISO time when the key left "open" (see GRACE_DAYS)
# items:   JSON Feed items, newest first
# last_ok: ISO time of the last good run
# base:    data time (timestamp_osm_base) of the last good Overpass result
state: dict = {"open": {}, "closed": {}, "items": [], "last_ok": None, "base": None}

# This item is always in the feed. At subscribe time, maubot marks it as seen
# and does not post it. Because the feed is never empty, the rss plugin does
# not start its backoff (it counts an empty feed as an error).
STATUS_ITEM = {
    "id": "sentinel-status",
    "url": "https://github.com/osm-fulda/osm-fulda-feeds#qualitätssicherung",
    "title": "sentinel ist aktiv. Neue Auffälligkeiten erscheinen hier.",
    "content_text": "Dieser Eintrag ist immer im Feed. Er ist kein Fund.",
    "date_published": "2026-11-06T00:00:00+00:00",
}

# Names that only tell the type of the object. Compared without case.
GENERIC_NAMES = {
    "apotheke", "arztpraxis", "bäckerei", "bank", "bushaltestelle", "café",
    "cafe", "friedhof", "friseur", "geldautomat", "grillhütte", "imbiss",
    "kindergarten", "kiosk", "kirche", "metzgerei", "parkplatz", "restaurant",
    "schule", "spielplatz", "sportplatz", "supermarkt", "tankstelle", "zahnarzt",
}
_NAME_RE = "|".join(sorted(GENERIC_NAMES))

# One query for all checks. Each line returns only candidates for one check,
# so the result stays small. The checks in Python make the final decision.
QUERY = f"""[out:json][timeout:180];
rel({AREA_RELATION});map_to_area->.a;
way[highway][sidewalk](area.a)->.s;
(
  way.s["sidewalk:both"="separate"];
  way.s["sidewalk:left"="separate"];
  way.s["sidewalk:right"="separate"];
  way[footway=sidewalk][highway!~"^(footway|path|construction|proposed)$"](area.a);
  way[bicycle_road=yes][bicycle!=designated](area.a);
  way[traffic_sign~"DE:(237|239|242|244|254|260)"](area.a);
  nwr[name~"^({_NAME_RE})$",i](area.a);
);
out meta;
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


def check_sidewalk_without_highway(el: dict) -> str | None:
    """footway=sidewalk must be on a highway=footway (or path) way."""
    tags = el.get("tags", {})
    if el["type"] != "way" or tags.get("footway") != "sidewalk":
        return None
    hw = tags.get("highway")
    if hw in ("footway", "path", "construction", "proposed"):
        return None
    return f"footway=sidewalk an highway={hw}" if hw else "footway=sidewalk ohne highway"


def check_bicycle_road(el: dict) -> str | None:
    """A Fahrradstraße (bicycle_road=yes) needs bicycle=designated."""
    tags = el.get("tags", {})
    if tags.get("bicycle_road") != "yes" or tags.get("bicycle") == "designated":
        return None
    return f"bicycle_road=yes, aber bicycle={tags['bicycle']}" if "bicycle" in tags else (
        "bicycle_road=yes ohne bicycle=designated"
    )


def is_transit_stop(tags: dict) -> bool:
    """Stops often have names like "Kirche" or "Sportplatz". These are real names."""
    return (
        "public_transport" in tags
        or tags.get("highway") == "bus_stop"
        or tags.get("amenity") == "bus_station"
        or tags.get("railway") in ("halt", "station", "stop", "tram_stop")
    )


def check_generic_name(el: dict) -> str | None:
    """The name only tells the type, for example name=Spielplatz."""
    tags = el.get("tags", {})
    name = tags.get("name", "")
    if name.lower() not in GENERIC_NAMES or is_transit_stop(tags):
        return None
    return f"name={name} beschreibt nur den Typ"


_SIGN_RE = re.compile(r"\d{3,4}(?:[.-]\d+)*")
BIKE_ALLOWED = ("yes", "designated", "permissive")


def sign_numbers(value: str) -> set[str]:
    """'DE:239,1022-10' -> {'239', '1022-10'}. Works with ',' and ';'.

    Values in brackets are parameters, not signs: 1004-30[400] means "in 400 m".
    """
    value = re.sub(r"\[[^\]]*\]", "", value)
    return set(_SIGN_RE.findall(value.replace("DE:", "")))


def check_sign_conflict(el: dict) -> str | None:
    """The tags allow what the German traffic sign on the way forbids (or the reverse).

    Only signs in traffic_sign on the way itself. End signs (242.2, 244.2)
    do not count.
    """
    tags = el.get("tags", {})
    if el["type"] != "way" or "traffic_sign" not in tags:
        return None
    sg = sign_numbers(tags["traffic_sign"])
    bike = tags.get("bicycle")
    bike_free = "1022-10" in sg  # Zusatzzeichen "Radfahrer frei"
    problems = []
    if "239" in sg and not bike_free and bike in BIKE_ALLOWED:
        problems.append(f"Gehweg (Z. 239) ohne „Radfahrer frei“, aber bicycle={bike}")
    if "254" in sg and bike in BIKE_ALLOWED:
        problems.append(f"Verbot für Radverkehr (Z. 254), aber bicycle={bike}")
    if "237" in sg and bike != "designated":
        problems.append(f"Radweg (Z. 237), aber bicycle={bike or 'fehlt'}")
    if "237" in sg and tags.get("foot") == "designated":
        problems.append("Radweg (Z. 237), aber foot=designated")
    if sg & {"242", "242.1"}:
        if tags.get("highway") != "pedestrian":
            problems.append(f"Fußgängerzone (Z. 242.1), aber highway={tags.get('highway')}")
        if not bike_free and bike in BIKE_ALLOWED:
            problems.append(f"Fußgängerzone (Z. 242.1) ohne „Radfahrer frei“, aber bicycle={bike}")
    if sg & {"244", "244.1"} and tags.get("bicycle_road") != "yes":
        problems.append("Fahrradstraße (Z. 244.1), aber bicycle_road fehlt")
    # Only without any Zusatzzeichen: "260,1020-30" (Anlieger frei) can allow some.
    if sg == {"260"} and tags.get("motor_vehicle") in ("yes", "designated"):
        problems.append(f"Verbot für Kfz (Z. 260), aber motor_vehicle={tags['motor_vehicle']}")
    return "; ".join(problems) if problems else None


# (id, label in the message, function)
CHECKS = [
    ("gehweg-doppelt", "Gehweg doppelt", check_sidewalk_twice),
    ("gehweg-ohne-highway", "Gehweg falsch getaggt", check_sidewalk_without_highway),
    ("fahrradstrasse", "Fahrradstraße unvollständig", check_bicycle_road),
    ("name-ist-typ", "Name ist nur der Typ", check_generic_name),
    ("schild-widerspruch", "Schild widerspricht Tags", check_sign_conflict),
]
TYPE_DE = {"node": "Knoten", "way": "Weg", "relation": "Relation"}


def overpass(query: str, min_base: str | None = None) -> dict:
    """Return the first good answer. Skip servers with older data than min_base."""
    body = urllib.parse.urlencode({"data": query}).encode()
    errors = []
    for url in ENDPOINTS:
        req = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.load(resp)
        # OSError includes URLError, timeouts and dropped connections.
        except (OSError, http.client.HTTPException, ValueError) as e:
            errors.append(f"{url}: {e}")
            continue
        # Overpass sends HTTP 200 with a "remark" when the query fails.
        if "error" in data.get("remark", ""):
            errors.append(f"{url}: {data['remark']}")
            continue
        base = data.get("osm3s", {}).get("timestamp_osm_base")
        # A server with old data can show a repaired problem again.
        if min_base and base and base < min_base:
            errors.append(f"{url}: data from {base} is older than {min_base}")
            continue
        return data
    raise RuntimeError("; ".join(errors))


def find(data: dict) -> dict[str, dict]:
    """Return finding key -> details for all current problems."""
    found = {}
    for el in data["elements"]:
        for check_id, label, fn in CHECKS:
            text = fn(el)
            if text:
                found[f"{check_id}:{el['type']}/{el['id']}"] = {
                    "label": label,
                    "text": text,
                    "type": el["type"],
                    "id": el["id"],
                    "name": el.get("tags", {}).get("name") or el.get("tags", {}).get("ref") or "",
                    "user": el.get("user", "?"),
                    "changeset": el.get("changeset"),
                }
    return found


def make_item(key: str, f: dict, now: datetime) -> dict:
    kind = TYPE_DE.get(f["type"], f["type"])
    # A generic name says nothing about the place, so show the id instead.
    if f["name"] and f["name"].lower() not in GENERIC_NAMES:
        where = f"„{f['name']}“"
    else:
        where = f"{kind} {f['id']}"
    url = f"https://www.openstreetmap.org/{f['type']}/{f['id']}"
    return {
        # The time in the id gives a problem that appears again a new entry.
        "id": f"{key}@{now.strftime('%Y-%m-%dT%H:%M')}",
        "url": url,
        "title": f"{f['label']}: {where} – {f['text']} (zuletzt bearbeitet von {f['user']})",
        "content_text": (
            f"{f['text']}\n\n{kind}: {url}\n"
            f"Letztes Changeset: https://www.openstreetmap.org/changeset/{f['changeset']}"
        ),
        "date_published": now.isoformat(),
    }


def run() -> None:
    now = datetime.now(timezone.utc)
    data = overpass(QUERY, state["base"])
    found = find(data)
    with lock:
        grace = (now - timedelta(days=GRACE_DAYS)).isoformat()
        closed = {k: t for k, t in state["closed"].items() if t >= grace}
        new = [k for k in found if k not in state["open"] and k not in closed]
        fixed = [k for k in state["open"] if k not in found]
        for k in fixed:
            closed[k] = now.isoformat()
        for k in found:
            closed.pop(k, None)
        state["closed"] = closed
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
        state["base"] = data.get("osm3s", {}).get("timestamp_osm_base") or state["base"]
        save()
    log.info("run done: %d open, %d new, %d fixed", len(found), len(new), len(fixed))


def feed_date(iso: str) -> str:
    """Return the time as UTC without a zone, for example 2026-11-06T22:21:48.

    The maubot rss plugin stores date_published in SQLite. A value with a zone
    ("+00:00") breaks the read of the stored entries, and then the plugin
    stops to poll all feeds. So the feed must not contain a zone.
    """
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat(timespec="seconds")


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
    last_slot = next_run_after(now) - timedelta(days=1)
    # No good run since the last RUN_AT: run at once. Else, and also on the
    # very first start, wait for RUN_AT. So there is time to subscribe before
    # the first run: maubot posts nothing for items that are in the feed at
    # subscribe time.
    if last and datetime.fromisoformat(last) < last_slot:
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
            # No Overpass server gave a good answer. This is normal, no traceback.
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
    except ValueError:
        # A broken file must not stop the container. Keep it for analysis and
        # start with an empty state. The next run reports all findings again.
        os.replace(STATE_FILE, STATE_FILE + ".broken")
        log.warning("state file is broken, moved to %s.broken", STATE_FILE)


def save() -> None:
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, STATE_FILE)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/feed.json":
            with lock:
                feed = {
                    "version": "https://jsonfeed.org/version/1.1",
                    "title": FEED_TITLE,
                    "home_page_url": "https://wiki.openstreetmap.org/wiki/Fulda",
                    "items": [
                        {**i, "date_published": feed_date(i["date_published"])}
                        for i in state["items"] + [STATUS_ITEM]
                    ],
                }
            body = json.dumps(feed, ensure_ascii=False).encode()
            ctype, status = "application/feed+json", 200
        elif self.path == "/health":
            now = datetime.now(timezone.utc)
            last = state["last_ok"]
            # Two nights without a good run: Overpass is down for a long time,
            # or the script has a problem. Before the first run: ok until the
            # first night is over.
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

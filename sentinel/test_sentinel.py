"""Tests for sentinel.py. Run: python -m unittest discover sentinel

The tests do not use the network. A fake overpass() gives the data.
"""

import datetime as dt
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

TMP = tempfile.mkdtemp()
os.environ["STATE_FILE"] = os.path.join(TMP, "state.json")
sys.path.insert(0, os.path.dirname(__file__))
import sentinel as s  # noqa: E402

UTC = dt.timezone.utc


def el(typ="way", id_=1, **tags):
    return {"type": typ, "id": id_, "user": "u", "changeset": 9, "version": 1, "tags": tags}


def road(id_, sidewalk):
    return el("way", id_, highway="residential", sidewalk=sidewalk, **{"sidewalk:both": "separate"})


def answer(elements, base="2026-11-07T04:00:00Z"):
    return {"elements": elements, "osm3s": {"timestamp_osm_base": base}}


class Rules(unittest.TestCase):
    def hit(self, fn, element):
        return fn(element) is not None

    def test_gehweg_doppelt(self):
        f = s.check_sidewalk_twice
        self.assertTrue(self.hit(f, road(1, "both")))
        self.assertTrue(self.hit(f, el(highway="residential", sidewalk="yes", **{"sidewalk:right": "separate"})))
        self.assertTrue(self.hit(f, el(highway="residential", sidewalk="left", **{"sidewalk:both": "separate"})))
        # Mixed mapping is valid: left on the road, right as its own way.
        self.assertFalse(self.hit(f, el(highway="residential", sidewalk="left", **{"sidewalk:right": "separate"})))
        self.assertFalse(self.hit(f, road(1, "separate")))
        self.assertFalse(self.hit(f, el(highway="residential", sidewalk="no", **{"sidewalk:left": "separate"})))

    def test_gehweg_ohne_highway(self):
        f = s.check_sidewalk_without_highway
        self.assertFalse(self.hit(f, el(footway="sidewalk", highway="footway")))
        self.assertFalse(self.hit(f, el(footway="sidewalk", highway="path")))
        self.assertTrue(self.hit(f, el(footway="sidewalk", highway="cycleway")))
        self.assertTrue(self.hit(f, el(footway="sidewalk")))

    def test_fahrradstrasse(self):
        f = s.check_bicycle_road
        self.assertFalse(self.hit(f, el(bicycle_road="yes", bicycle="designated")))
        self.assertTrue(self.hit(f, el(bicycle_road="yes", bicycle="yes")))
        self.assertTrue(self.hit(f, el(bicycle_road="yes")))

    def test_name_ist_typ(self):
        f = s.check_generic_name
        self.assertTrue(self.hit(f, el("node", name="Spielplatz", leisure="playground")))
        self.assertTrue(self.hit(f, el("node", name="grillhütte", building="hut")))
        self.assertFalse(self.hit(f, el("node", name="Spielplatz Am Hang")))

    def test_name_ist_typ_haltestellen(self):
        # Stops named "Kirche" or "Sportplatz" are real names (PR #12).
        f = s.check_generic_name
        self.assertFalse(self.hit(f, el("node", name="Kirche", highway="bus_stop", public_transport="platform")))
        self.assertFalse(self.hit(f, el("node", name="Kirche", public_transport="stop_position")))
        self.assertFalse(self.hit(f, el("relation", name="Sportplatz", public_transport="stop_area")))
        self.assertFalse(self.hit(f, el("node", name="Kirche", railway="halt")))


class SignConflict(unittest.TestCase):
    def hit(self, **tags):
        return s.check_sign_conflict(el("way", **tags)) is not None

    def test_sign_numbers(self):
        self.assertEqual(s.sign_numbers("DE:239,1022-10"), {"239", "1022-10"})
        self.assertEqual(s.sign_numbers("DE:240;DE:1022-10"), {"240", "1022-10"})
        self.assertEqual(s.sign_numbers("DE:260,1026-32,1004-30[400]"), {"260", "1026-32", "1004-30"})

    def test_gehweg(self):
        self.assertTrue(self.hit(traffic_sign="DE:239", bicycle="yes"))
        self.assertTrue(self.hit(traffic_sign="DE:239", bicycle="permissive"))
        self.assertFalse(self.hit(traffic_sign="DE:239", bicycle="no"))
        self.assertFalse(self.hit(traffic_sign="DE:239"))
        self.assertFalse(self.hit(traffic_sign="DE:239,1022-10", bicycle="yes"))

    def test_radverbot(self):
        self.assertTrue(self.hit(traffic_sign="DE:254", bicycle="yes"))
        self.assertFalse(self.hit(traffic_sign="DE:254", bicycle="no"))

    def test_radweg(self):
        self.assertFalse(self.hit(traffic_sign="DE:237", bicycle="designated"))
        self.assertTrue(self.hit(traffic_sign="DE:237"))
        self.assertTrue(self.hit(traffic_sign="DE:237", bicycle="designated", foot="designated"))

    def test_fussgaengerzone(self):
        self.assertFalse(self.hit(traffic_sign="DE:242.1", highway="pedestrian"))
        self.assertTrue(self.hit(traffic_sign="DE:242.1", highway="footway"))
        self.assertTrue(self.hit(traffic_sign="DE:242.1", highway="pedestrian", bicycle="yes"))
        self.assertFalse(self.hit(traffic_sign="DE:242.1,1022-10", highway="pedestrian", bicycle="yes"))
        # End sign: no rule.
        self.assertFalse(self.hit(traffic_sign="DE:242.2", highway="residential"))

    def test_fahrradstrasse_schild(self):
        self.assertTrue(self.hit(traffic_sign="DE:244.1", highway="residential"))
        self.assertFalse(self.hit(traffic_sign="DE:244.1", bicycle_road="yes"))
        self.assertFalse(self.hit(traffic_sign="DE:244.2", highway="residential"))

    def test_kfz_verbot(self):
        self.assertTrue(self.hit(traffic_sign="DE:260", motor_vehicle="yes"))
        self.assertFalse(self.hit(traffic_sign="DE:260,1020-30", motor_vehicle="yes"))
        self.assertFalse(self.hit(traffic_sign="DE:260", motor_vehicle="no"))

    def test_node_and_no_sign(self):
        self.assertIsNone(s.check_sign_conflict(el("node", traffic_sign="DE:239", bicycle="yes")))
        self.assertIsNone(s.check_sign_conflict(el("way", bicycle="yes")))


class Runs(unittest.TestCase):
    def setUp(self):
        s.state.clear()
        s.state.update({"open": {}, "closed": {}, "items": [], "last_ok": None, "base": None})

    def run_with(self, elements, base="2026-11-07T04:00:00Z"):
        with mock.patch.object(s, "overpass", return_value=answer(elements, base)):
            s.run()

    def ids(self):
        return [i["id"].split("@")[0] for i in s.state["items"]]

    def test_new_then_quiet(self):
        self.run_with([road(1, "both"), road(2, "both")])
        self.assertEqual(self.ids(), ["gehweg-doppelt:way/1", "gehweg-doppelt:way/2"])
        self.run_with([road(1, "both"), road(2, "both")])
        self.assertEqual(len(s.state["items"]), 2, "same state must not post again")

    def test_grace_after_empty_answer(self):
        self.run_with([road(1, "both")])
        self.run_with([])  # bad or empty answer: finding closes
        self.assertEqual(s.state["open"], {})
        self.run_with([road(1, "both")])  # back within GRACE_DAYS
        self.assertEqual(len(s.state["items"]), 1, "no second post within the grace time")

    def test_repost_after_grace(self):
        self.run_with([road(1, "both")])
        self.run_with([])
        old = (dt.datetime.now(UTC) - dt.timedelta(days=s.GRACE_DAYS + 1)).isoformat()
        s.state["closed"]["gehweg-doppelt:way/1"] = old
        self.run_with([road(1, "both")])
        self.assertEqual(len(s.state["items"]), 2)

    def test_limit_per_run(self):
        with mock.patch.object(s, "MAX_ITEMS_PER_RUN", 2):
            self.run_with([road(i, "both") for i in range(1, 6)])
        self.assertEqual(len(s.state["items"]), 3)
        self.assertTrue(s.state["items"][-1]["id"].startswith("overflow@"))

    def test_state_survives_restart(self):
        self.run_with([road(1, "both")])
        s.state.update({"open": {}, "items": []})
        s.load()
        self.assertIn("gehweg-doppelt:way/1", s.state["open"])

    def test_broken_state_file(self):
        with open(s.STATE_FILE, "w") as f:
            f.write("{broken")
        s.load()
        self.assertTrue(os.path.exists(s.STATE_FILE + ".broken"))
        os.remove(s.STATE_FILE + ".broken")


class Overpass(unittest.TestCase):
    def test_fallback_and_old_data(self):
        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        def fake(req, timeout):
            if "a.example" in req.full_url:
                raise ConnectionResetError("reset by peer")
            if "b.example" in req.full_url:
                return Resp(json.dumps(answer([], "2026-11-01T00:00:00Z")).encode())
            return Resp(json.dumps(answer([road(9, "both")], "2026-11-12T00:00:00Z")).encode())

        urls = ["http://a.example/i", "http://b.example/i", "http://c.example/i"]
        with mock.patch.object(s, "ENDPOINTS", urls), mock.patch.object(s.urllib.request, "urlopen", fake):
            data = s.overpass("x", "2026-11-11T00:00:00Z")
        self.assertEqual(data["elements"][0]["id"], 9)


class FeedDates(unittest.TestCase):
    """Regression for PR #11: a zone in date_published stopped ALL maubot feeds."""

    def roundtrip(self, value):
        # Same path as the maubot rss plugin: fromisoformat -> SQLite timestamp -> read.
        db = sqlite3.connect(":memory:", detect_types=sqlite3.PARSE_DECLTYPES)
        db.execute("create table entry (date timestamp)")
        db.execute("insert into entry values (?)", (dt.datetime.fromisoformat(value),))
        return db.execute("select date from entry").fetchall()

    def test_zone_breaks_sqlite(self):
        with self.assertRaises(ValueError):
            self.roundtrip("2026-11-06T00:00:00+00:00")

    def test_feed_date_has_no_zone(self):
        for v in ("2026-11-06T00:00:00+00:00", "2026-11-06T22:21:48.123+00:00", "2026-11-04T08:44:08+01:00"):
            out = s.feed_date(v)
            self.assertNotIn("+", out)
            self.roundtrip(out)

    def test_served_feed_has_no_zone(self):
        s.state["items"] = [s.make_item("gehweg-doppelt:way/1", s.find(answer([road(1, "both")]))["gehweg-doppelt:way/1"], dt.datetime.now(UTC))]
        handler = s.Handler.__new__(s.Handler)
        handler.path = "/feed.json"
        handler.wfile = io.BytesIO()
        handler.send_response = handler.send_header = handler.end_headers = lambda *a, **k: None
        handler.do_GET()
        feed = json.loads(handler.wfile.getvalue())
        self.assertTrue(feed["items"])
        for item in feed["items"]:
            self.assertNotIn("+", item["date_published"])
            self.roundtrip(item["date_published"])


class Schedule(unittest.TestCase):
    def test_next_run_is_in_future(self):
        now = dt.datetime.now(UTC)
        nxt = s.next_run_after(now)
        self.assertGreater(nxt, now)
        self.assertLessEqual(nxt - now, dt.timedelta(days=1, hours=1))


if __name__ == "__main__":
    unittest.main()

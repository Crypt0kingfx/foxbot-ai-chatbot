"""Runs the real /overlay/plinko script in Node (tests/overlay_harness/)
against a stubbed DOM, canvas and fetch with a controlled clock, and checks
the overlay is a faithful, display-only replay of server results:

  * it starts at "now" (first poll since=-1), then fetches only new drops;
  * the ball actually travels and lands on the SERVER's slot -- including
    when the server's slot disagrees with the path it was sent;
  * the banner shows the viewer, multiplier and payout the server sent;
  * slot labels come from the server's multiplier list;
  * it never touches window.speechSynthesis.

Skipped when `node` isn't on PATH. Run with:
    python -m unittest tests.test_plinko_overlay_js -v
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS = os.path.join(ROOT, "tests", "overlay_harness", "plinko_overlay_harness.js")
OVERLAY = os.path.join(ROOT, "static", "foxbot-plinko-overlay.html")

W = 640
SPACING = W / 10.5


def slot_x(k):
    return W / 2 + (k - 4) * SPACING


DROPS = [
    {"id": 1, "viewer": "NeonFox", "slot": 1, "path": [0, 0, 1, 0, 0, 0, 0, 0], "multiplier": "3x", "payout": 30, "drop_index": 1, "auto": False},
    {"id": 2, "viewer": "Vixen", "slot": 8, "path": [1, 1, 1, 1, 1, 1, 1, 1], "multiplier": "10x", "payout": 100, "drop_index": 2, "auto": False},
    {"id": 3, "viewer": "Kit", "slot": 4, "path": [1, 0, 1, 0, 0, 1, 1, 0], "multiplier": "0.4x", "payout": 4, "drop_index": 3, "auto": True},
    # Inconsistent on purpose: the path sums to 0 but the server says slot 2.
    # The overlay must land where the server says.
    {"id": 4, "viewer": "Trust", "slot": 2, "path": [0, 0, 0, 0, 0, 0, 0, 0], "multiplier": "1.4x", "payout": 14, "drop_index": 4, "auto": False},
]


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class PlinkoOverlayScriptTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(DROPS, f)
            drops_path = f.name
        try:
            out = subprocess.run(["node", HARNESS, OVERLAY, drops_path], capture_output=True, text=True,
                                 timeout=60, check=True)
        finally:
            os.unlink(drops_path)
        cls.report = json.loads(out.stdout)

    def test_starts_at_now_then_fetches_new_drops(self):
        self.assertEqual(self.report["fetches"][:2], [
            "/overlay/plinko-data?handle=demo&since=-1",
            "/overlay/plinko-data?handle=demo&since=0",
        ])

    def test_every_drop_lands_on_the_server_slot(self):
        landings = self.report["landings"]
        self.assertEqual(len(landings), len(DROPS))
        for drop, landing in zip(DROPS, landings):
            with self.subTest(drop=drop["id"]):
                self.assertEqual(landing["viewer"], "@" + drop["viewer"])
                self.assertEqual(landing["multiplier"], drop["multiplier"])
                self.assertEqual(landing["pay"], f"+{drop['payout']} FoxCoins")
                self.assertLess(abs(landing["lastBallX"] - slot_x(drop["slot"])), SPACING * 0.1)
                self.assertGreater(landing["distinctXs"], 20, "the ball should visibly travel")

    def test_drops_play_in_order(self):
        frames = [landing["frame"] for landing in self.report["landings"]]
        self.assertEqual(frames, sorted(frames))

    def test_slot_labels_come_from_server(self):
        self.assertEqual(self.report["slotLabels"], ["10x", "3x", "1.4x", "0.6x", "0.4x", "0.6x", "1.4x", "3x", "10x"])

    def test_no_speech_api(self):
        self.assertFalse(self.report["speechTouched"])


if __name__ == "__main__":
    unittest.main()

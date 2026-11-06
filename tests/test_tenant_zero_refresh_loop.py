"""Scheduled tenant-zero Blaze token refresh loop.

Regression for 80c58be: the loop called foxbot_blaze_oauth_refresh_v1()
with no arguments after that route started requiring `request`, so every
scheduled cycle raised TypeError and tenant zero was never refreshed on
schedule.

The loop runs synchronously here against a fake clock (time.sleep advances
it instead of blocking) and a fake Blaze that rotates refresh tokens (each
one valid exactly once). The REAL save path (_foxbot_blaze_oauth_save_tokens_v1)
writes to a temp token file; only its network identity check is stubbed.

No pytest dependency. Run with:
    python -m unittest tests.test_tenant_zero_refresh_loop -v
"""

import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402
from services import blaze_tokens  # noqa: E402

TZ_ID = "tz-creator"
BOT_ID = "bot-user-1"
ALERT = "FOXBOT_ALERT oauth_refresh"


class _FakeClock:
    """time.time()/time.sleep() stand-in. sleep() advances the clock and
    stops the worker loop after `max_cycles` sleeps."""

    def __init__(self, max_cycles):
        self.now = 1_000_000.0
        self.start = self.now
        self.sleeps = []
        self.max_cycles = max_cycles

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        if len(self.sleeps) >= self.max_cycles:
            app.blaze_oauth_refresh_status["running"] = False


class _RotatingBlaze:
    def __init__(self, clock):
        self.clock = clock
        self.valid = "rt-0"
        self.issued = 0
        self.calls_at = []
        self.lock_held = []
        self.fail_plan = {}  # call index -> Exception to raise

    def post_json(self, url, payload, timeout=15):
        index = len(self.calls_at)
        self.calls_at.append(self.clock.now - self.clock.start)
        self.lock_held.append(app._FOXBOT_TENANT_ZERO_REFRESH_LOCK_V1.locked())
        assert url.endswith("/bapi/oauth2/refresh"), url
        if index in self.fail_plan:
            raise self.fail_plan[index]
        if payload.get("refreshToken") != self.valid:
            raise RuntimeError("Blaze refresh failed: invalid_grant (stale refresh token)")
        self.issued += 1
        self.valid = f"rt-{self.issued}"
        return {"accessToken": f"at-{self.issued}", "refreshToken": self.valid, "expiresIn": 3600}


class TenantZeroRefreshLoopTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.token_file = Path(self.tmpdir) / "blaze_oauth_tokens.json"
        self.token_file.write_text(json.dumps({"accessToken": "at-0", "refreshToken": "rt-0"}), encoding="utf-8")
        self._orig_status = json.loads(json.dumps(app.blaze_oauth_refresh_status))

    def tearDown(self):
        app.blaze_oauth_refresh_status.clear()
        app.blaze_oauth_refresh_status.update(self._orig_status)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _patches(self, clock, blaze, interval="3600"):
        path_fn = lambda *a, **k: self.token_file  # noqa: E731
        return [
            mock.patch.dict(os.environ, {
                "BLAZE_CLIENT_ID": "cid",
                "BLAZE_CLIENT_SECRET": "secret",
                "FOXBOT_BLAZE_TOKEN_REFRESH_INTERVAL_SECONDS": interval,
            }),
            mock.patch.object(app.time, "time", side_effect=clock.time),
            mock.patch.object(app.time, "sleep", side_effect=clock.sleep),
            mock.patch.object(app, "_foxbot_storage_path_v1", path_fn),
            mock.patch.object(blaze_tokens, "storage_path", path_fn),
            mock.patch.object(app, "_foxbot_blaze_oauth_post_json_v1", side_effect=blaze.post_json),
            # The save path's only network call: proving the token's identity
            # against Blaze's profile endpoint. Everything else in the save is real.
            mock.patch.object(app, "_foxbot_blaze_oauth_verify_identity_v1", return_value=BOT_ID),
            mock.patch.object(app, "_foxbot_blaze_bot_expected_id_v1", return_value=BOT_ID),
            mock.patch.object(app, "_tenant_zero_id", return_value=TZ_ID),
        ]

    def _run_worker(self, cycles, blaze=None, extra_patches=(), interval="3600"):
        clock = _FakeClock(max_cycles=cycles)
        blaze = blaze or _RotatingBlaze(clock)
        blaze.clock = clock
        app.blaze_oauth_refresh_status.update({"running": False, "cycles": 0, "last_ok": None, "last_error": None, "per_creator": {}})
        patches = self._patches(clock, blaze, interval) + list(extra_patches)
        buffer = io.StringIO()
        for p in patches:
            p.start()
        try:
            with redirect_stdout(buffer):
                app._foxbot_blaze_oauth_refresh_worker_v1()  # returns once clock stops it
        finally:
            for p in reversed(patches):
                p.stop()
        return clock, blaze, buffer.getvalue()

    def _saved(self):
        return json.loads(self.token_file.read_text(encoding="utf-8"))

    def _assert_no_token_values(self, output, blaze):
        for n in range(blaze.issued + 1):
            self.assertNotIn(f"rt-{n}", output)
            self.assertNotIn(f"at-{n}", output)

    # --- schedule ---------------------------------------------------------

    def test_loop_refreshes_on_schedule_without_crashing(self):
        clock, blaze, output = self._run_worker(cycles=3)

        self.assertEqual(blaze.calls_at, [0.0, 3600.0, 7200.0], "one refresh per interval")
        self.assertEqual(clock.sleeps, [3600.0, 3600.0, 3600.0])
        self.assertEqual(blaze.issued, 3, "every scheduled cycle actually refreshed")
        self.assertNotIn("TypeError", output)
        self.assertNotIn(ALERT, output)
        self.assertEqual(output.count("scheduled refresh succeeded"), 3)

        saved = self._saved()
        self.assertEqual(saved["refreshToken"], blaze.valid, "saved token is the live one")
        self.assertEqual(saved["by_creator"][TZ_ID]["refreshToken"], blaze.valid, "real save path mirrors the tz slot")

        status = app.blaze_oauth_refresh_status
        self.assertEqual(status["cycles"], 3)
        self.assertTrue(status["last_ok"])
        self.assertIsNone(status["last_error"])
        self.assertEqual(status["per_creator"][TZ_ID]["cycles"], 3)
        self.assertFalse(status["running"])
        self._assert_no_token_values(output, blaze)

    def test_interval_env_is_respected_with_60s_floor(self):
        clock, _blaze, _out = self._run_worker(cycles=2, interval="900")
        self.assertEqual(clock.sleeps, [900.0, 900.0])
        clock, _blaze, _out = self._run_worker(cycles=1, interval="5")
        self.assertEqual(clock.sleeps, [60.0])

    def test_loop_refresh_holds_the_shared_lock(self):
        _clock, blaze, _out = self._run_worker(cycles=2)
        self.assertEqual(blaze.lock_held, [True, True])
        self.assertIs(app._FOXBOT_TENANT_ZERO_REFRESH_LOCK_V1, blaze_tokens.TENANT_ZERO_REFRESH_LOCK)
        self.assertFalse(blaze_tokens.TENANT_ZERO_REFRESH_LOCK.locked(), "lock released after each cycle")

    def test_loop_waits_for_a_manual_refresh_holding_the_lock(self):
        """A manual refresh in progress blocks the scheduled one until it
        finishes, and the scheduled one then sends the NEW refresh token."""
        clock = _FakeClock(max_cycles=1)
        blaze = _RotatingBlaze(clock)
        patches = self._patches(clock, blaze)
        for p in patches:
            p.start()
        try:
            app.blaze_oauth_refresh_status.update({"running": False, "cycles": 0, "per_creator": {}})
            with blaze_tokens.TENANT_ZERO_REFRESH_LOCK:
                worker = threading.Thread(target=app._foxbot_blaze_oauth_refresh_worker_v1, daemon=True)
                with redirect_stdout(io.StringIO()):
                    worker.start()
                    worker.join(timeout=0.5)
                    self.assertTrue(worker.is_alive(), "scheduled refresh must wait for the lock")
                    self.assertEqual(blaze.issued, 0)
                    # Simulate the manual refresh finishing: rotate + save.
                    blaze.post_json("https://blaze.stream/bapi/oauth2/refresh", {"refreshToken": "rt-0"})
                    app._foxbot_blaze_oauth_save_tokens_v1({"accessToken": "at-1", "refreshToken": "rt-1"})
            worker.join(timeout=5)
        finally:
            for p in reversed(patches):
                p.stop()
        self.assertFalse(worker.is_alive())
        self.assertEqual(blaze.issued, 2, "scheduled refresh used the token the manual one saved")
        self.assertTrue(app.blaze_oauth_refresh_status["last_ok"])

    # --- route shares the function ---------------------------------------

    def test_route_and_loop_share_one_refresh_function(self):
        clock = _FakeClock(max_cycles=1)
        blaze = _RotatingBlaze(clock)
        patches = self._patches(clock, blaze) + [mock.patch.object(app, "_foxbot_require_admin_v1", return_value=None)]
        for p in patches:
            p.start()
        try:
            with mock.patch.object(app, "_foxbot_blaze_oauth_refresh_tenant_zero_v1",
                                   wraps=app._foxbot_blaze_oauth_refresh_tenant_zero_v1) as shared:
                result = app.foxbot_blaze_oauth_refresh_v1(request=None)
                self.assertTrue(result["ok"])
                with redirect_stdout(io.StringIO()):
                    app._foxbot_blaze_oauth_refresh_cycle_v1()
                self.assertEqual(shared.call_count, 2)
        finally:
            for p in reversed(patches):
                p.stop()
        self.assertEqual(blaze.issued, 2)

    def test_route_admin_guard_still_blocks_before_any_refresh(self):
        clock = _FakeClock(max_cycles=1)
        blaze = _RotatingBlaze(clock)
        denied = {"ok": False, "error": "admin only"}
        patches = self._patches(clock, blaze) + [mock.patch.object(app, "_foxbot_require_admin_v1", return_value=denied)]
        for p in patches:
            p.start()
        try:
            result = app.foxbot_blaze_oauth_refresh_v1(request=None)
        finally:
            for p in reversed(patches):
                p.stop()
        self.assertIs(result, denied)
        self.assertEqual(blaze.calls_at, [])

    # --- failures are loud and survivable --------------------------------

    def test_failures_are_logged_loudly_and_do_not_kill_the_loop(self):
        clock = _FakeClock(max_cycles=3)
        blaze = _RotatingBlaze(clock)
        # cycle 1: Blaze rejects the refresh (handled -> kind=failed)
        blaze.fail_plan[0] = RuntimeError("Blaze refresh HTTP 500: upstream unavailable")
        real_refresh = app._foxbot_blaze_oauth_refresh_tenant_zero_v1
        calls = {"n": 0}

        def flaky_refresh():
            calls["n"] += 1
            if calls["n"] == 2:  # cycle 2: unexpected exception -> kind=crashed
                raise OSError("disk full")
            return real_refresh()

        _clock, blaze, output = self._run_worker(
            cycles=3, blaze=blaze,
            extra_patches=[mock.patch.object(app, "_foxbot_blaze_oauth_refresh_tenant_zero_v1", side_effect=flaky_refresh)],
        )
        lines = [ln for ln in output.splitlines() if ln.startswith(ALERT)]

        self.assertEqual(calls["n"], 3, "loop kept going after both failures")
        self.assertEqual(blaze.issued, 1, "cycle 3 refreshed")
        self.assertEqual(len(lines), 2, output)
        self.assertIn("kind=failed creator=tenant_zero", lines[0])
        self.assertIn("upstream unavailable", lines[0])
        self.assertIn("kind=crashed creator=tenant_zero error_type=OSError", lines[1])
        self.assertIn('error="disk full"', lines[1])
        self.assertIn("scheduled refresh crashed: disk full", lines[1], "legacy text still searchable")
        self.assertTrue(app.blaze_oauth_refresh_status["last_ok"], "cycle 3 recovered")
        self.assertIsNone(app.blaze_oauth_refresh_status["last_error"])
        self._assert_no_token_values(output, blaze)

    def test_save_lost_after_rotation_alerts_every_cycle(self):
        """If Blaze rotates refresh tokens, a save that fails AFTER a
        successful exchange loses the new token, and every later refresh
        sends a dead one. Code can't recover from that (it needs a
        re-login); what it must do is alert on every cycle, not go quiet."""
        real_save = app._foxbot_blaze_oauth_save_tokens_v1
        calls = {"n": 0}

        def save_fails_once(tokens):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("disk full")
            return real_save(tokens)

        _clock, blaze, output = self._run_worker(
            cycles=3,
            extra_patches=[mock.patch.object(app, "_foxbot_blaze_oauth_save_tokens_v1", side_effect=save_fails_once)],
        )
        lines = [ln for ln in output.splitlines() if ln.startswith(ALERT)]
        self.assertEqual(len(lines), 3, output)
        self.assertIn("kind=crashed", lines[0])
        self.assertIn("stale refresh token", lines[1])
        self.assertIn("stale refresh token", lines[2])
        self.assertFalse(app.blaze_oauth_refresh_status["last_ok"])
        self._assert_no_token_values(output, blaze)

    def test_unexpected_error_outside_the_refresh_is_caught_per_cycle(self):
        calls = {"n": 0}

        def flaky_tz_id():
            calls["n"] += 1
            if calls["n"] == 1:
                raise KeyError("tenant id lookup blew up")
            return TZ_ID

        _clock, blaze, output = self._run_worker(
            cycles=2, extra_patches=[mock.patch.object(app, "_tenant_zero_id", side_effect=flaky_tz_id)],
        )
        self.assertIn(f"{ALERT} kind=crashed creator=cycle error_type=KeyError", output)
        self.assertEqual(blaze.issued, 1, "next cycle still refreshed")

    def test_alert_line_shape_is_single_line_and_parseable(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            app._foxbot_oauth_refresh_alert_v1("crashed", ValueError('bad\nmulti "line"\terror'))
        out = buffer.getvalue()
        self.assertEqual(out.count("\n"), 1)
        self.assertTrue(out.startswith(f"{ALERT} kind=crashed creator=tenant_zero error_type=ValueError "))
        self.assertIn("error=\"bad multi 'line' error\"", out)

    def test_success_log_no_longer_dumps_token_file(self):
        self.assertFalse(hasattr(app, "_foxbot_blaze_oauth_log_raw_fields_v1"))
        _clock, blaze, output = self._run_worker(cycles=1)
        self.assertNotIn("saved token fields", output)
        self._assert_no_token_values(output, blaze)


if __name__ == "__main__":
    unittest.main()

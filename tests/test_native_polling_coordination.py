"""Native listener vs. the rest of the app: no token-refresh race, no
double replies.

  * RefreshLockTestCase: the native connector's subscribe-time refresh and
    app.py's tenant-zero refresh route are serialized by
    services.blaze_tokens.TENANT_ZERO_REFRESH_LOCK. A fake Blaze that
    ROTATES refresh tokens (each one valid exactly once) shows every
    concurrent refresh succeeding and the saved token staying valid. A
    control test removes the lock and shows the same fake catching overlap,
    so the passing test means something.
  * PollingReplySuppressionTestCase: with FOXBOT_BLAZE_NATIVE_ENABLED=true,
    any send from the polling thread into the native-owned channel is
    refused at send_blaze_chat_message; other tenants, other callers, and
    native-disabled deployments are unaffected.

No network. No pytest dependency. Run with:
    python -m unittest tests.test_native_polling_coordination -v
"""

import contextlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402
from services import blaze_native_connector as native  # noqa: E402
from services import blaze_tokens  # noqa: E402


class _RotatingBlaze:
    """Refresh tokens are single-use: using one mints a new pair and
    invalidates it. Tracks peak concurrency of in-flight refreshes."""

    def __init__(self, first_refresh_token):
        self.valid = first_refresh_token
        self.issued = 0
        self.in_flight = 0
        self.peak = 0
        self.rejected = []
        self._lock = threading.Lock()

    def refresh(self, refresh_token):
        with self._lock:
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
        try:
            time.sleep(0.05)  # widen the window a race would need
            with self._lock:
                if refresh_token != self.valid:
                    self.rejected.append(refresh_token)
                    return None
                self.issued += 1
                self.valid = f"rt-{self.issued}"
                return {"accessToken": f"at-{self.issued}", "refreshToken": self.valid}
        finally:
            with self._lock:
                self.in_flight -= 1


class RefreshLockTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.token_file = Path(self.tmpdir) / "blaze_oauth_tokens.json"
        self.token_file.write_text(json.dumps({"accessToken": "at-0", "refreshToken": "rt-0"}), encoding="utf-8")
        self.blaze = _RotatingBlaze("rt-0")

        def native_http(method, url, payload=None, headers=None, timeout=15):
            body = self.blaze.refresh(payload.get("refreshToken"))
            if body is None:
                return {"ok": False, "status": 400, "body": {"error": "invalid_grant"}}
            return {"ok": True, "status": 200, "body": body}

        def app_post_json(url, payload, timeout=15):
            body = self.blaze.refresh(payload.get("refreshToken"))
            if body is None:
                raise RuntimeError("Blaze refresh failed: invalid_grant")
            return body

        def app_save(tokens):
            # Stand-in for the identity-verified save: same merge-into-file shape.
            saved = json.loads(self.token_file.read_text(encoding="utf-8") or "{}")
            saved.update(tokens)
            self.token_file.write_text(json.dumps(saved), encoding="utf-8")
            return saved

        path_fn = lambda *a, **k: self.token_file  # noqa: E731
        env = {"BLAZE_CLIENT_ID": "cid", "BLAZE_CLIENT_SECRET": "secret"}
        self._patches = [
            mock.patch.dict(os.environ, env),
            mock.patch.object(blaze_tokens, "storage_path", path_fn),
            mock.patch.object(native, "storage_path", path_fn),
            mock.patch.object(app, "_foxbot_storage_path_v1", path_fn),
            mock.patch.object(native, "_fb_json_http_diag", side_effect=native_http),
            mock.patch.object(app, "_foxbot_blaze_oauth_post_json_v1", side_effect=app_post_json),
            mock.patch.object(app, "_foxbot_blaze_oauth_save_tokens_v1", side_effect=app_save),
            mock.patch.object(app, "_foxbot_require_admin_v1", return_value=None),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _race(self, per_side=4):
        callers = per_side * 2
        barrier = threading.Barrier(callers)
        results = []
        results_lock = threading.Lock()

        def native_side():
            barrier.wait()
            r = native._fb_refresh_token_diag()
            with results_lock:
                results.append(("native", bool(r.get("ok"))))

        def app_side():
            barrier.wait()
            r = app.foxbot_blaze_oauth_refresh_v1(request=None)
            with results_lock:
                results.append(("app", bool(r.get("ok"))))

        threads = [threading.Thread(target=native_side) for _ in range(per_side)]
        threads += [threading.Thread(target=app_side) for _ in range(per_side)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
        return results

    def test_native_and_app_refreshes_never_overlap_and_all_succeed(self):
        results = self._race()

        self.assertEqual(self.blaze.peak, 1, "two refresh exchanges were in flight at once")
        self.assertEqual(self.blaze.rejected, [], "a refresh sent an already-consumed refresh token")
        self.assertTrue(all(ok for _side, ok in results), results)
        self.assertEqual(self.blaze.issued, 8)

        saved = json.loads(self.token_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["refreshToken"], self.blaze.valid, "saved refresh token is not the live one")

    def test_both_paths_share_one_lock_object(self):
        self.assertIs(native.TENANT_ZERO_REFRESH_LOCK, blaze_tokens.TENANT_ZERO_REFRESH_LOCK)
        self.assertIs(app._FOXBOT_TENANT_ZERO_REFRESH_LOCK_V1, blaze_tokens.TENANT_ZERO_REFRESH_LOCK)

    def test_control_without_lock_the_fake_detects_the_race(self):
        no_lock = contextlib.nullcontext()
        with mock.patch.object(native, "TENANT_ZERO_REFRESH_LOCK", no_lock), \
                mock.patch.object(app, "_FOXBOT_TENANT_ZERO_REFRESH_LOCK_V1", no_lock):
            self._race()
        self.assertGreater(self.blaze.peak, 1)
        self.assertTrue(self.blaze.rejected, "expected stale refresh tokens without the lock")


class _FakeResponse:
    status_code = 200
    ok = True
    text = "{}"

    def json(self):
        return {}


class PollingReplySuppressionTestCase(unittest.TestCase):
    TZ_CHANNEL = "chan-tenant-zero"
    OTHER_CHANNEL = "chan-other-creator"

    def setUp(self):
        self._orig_subs = native.STATE.get("subscriptions")
        self._orig_suppressed = app.polling_status.get("last_suppressed_send")
        native.STATE["subscriptions"] = []
        self._patches = [
            mock.patch.object(app, "resolve_blaze_access_token", return_value=("tok", "test")),
            mock.patch.object(app, "_foxbot_bot_connect_creator_active_v1", return_value=False),
            mock.patch("requests.post", return_value=_FakeResponse()),
        ]
        started = [p.start() for p in self._patches]
        self.post = started[2]

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        native.STATE["subscriptions"] = self._orig_subs
        app.polling_status["last_suppressed_send"] = self._orig_suppressed
        app._FOXBOT_POLLING_THREAD_CTX_V1.__dict__.pop("active", None)

    def _env(self, native_enabled=True, channel=TZ_CHANNEL):
        env = {"BLAZE_CLIENT_ID": "cid", "FOXBOT_BLAZE_NATIVE_ENABLED": "true" if native_enabled else "false"}
        if channel:
            env["BLAZE_CHANNEL_ID"] = channel
        return env

    def _send_from_polling_thread(self, env, channel_id):
        """Run send_blaze_chat_message on a fresh thread marked exactly the
        way blaze_polling_worker marks its own thread."""
        out = {}

        def body():
            app._FOXBOT_POLLING_THREAD_CTX_V1.active = True
            out["result"] = app.send_blaze_chat_message("hi", channel_id=channel_id)

        with mock.patch.dict(os.environ, env):
            if not channel_id and "BLAZE_CHANNEL_ID" not in env:
                os.environ.pop("BLAZE_CHANNEL_ID", None)
            t = threading.Thread(target=body)
            t.start()
            t.join(timeout=10)
        return out["result"]

    def test_polling_cannot_send_to_native_channel(self):
        result = self._send_from_polling_thread(self._env(), self.TZ_CHANNEL)
        self.assertTrue(result.get("suppressed"))
        self.assertFalse(result.get("success"))
        self.post.assert_not_called()
        self.assertEqual(app.polling_status["last_suppressed_send"]["channel_id"], self.TZ_CHANNEL)

    def test_default_channel_send_is_also_suppressed(self):
        # channel_id=None falls back to BLAZE_CHANNEL_ID -- same channel.
        result = self._send_from_polling_thread(self._env(), None)
        self.assertTrue(result.get("suppressed"))
        self.post.assert_not_called()

    def test_channel_native_resolved_from_profile_is_suppressed(self):
        native.STATE["subscriptions"] = [{"type": "channel.chat.message", "ok": True, "channelId": "chan-resolved"}]
        result = self._send_from_polling_thread(self._env(channel=None), "chan-resolved")
        self.assertTrue(result.get("suppressed"))
        self.post.assert_not_called()

    def test_other_tenant_channels_still_get_polling_replies(self):
        result = self._send_from_polling_thread(self._env(), self.OTHER_CHANNEL)
        self.assertFalse(result.get("suppressed", False))
        self.post.assert_called_once()

    def test_native_disabled_means_polling_replies_normally(self):
        result = self._send_from_polling_thread(self._env(native_enabled=False), self.TZ_CHANNEL)
        self.assertFalse(result.get("suppressed", False))
        self.post.assert_called_once()

    def test_non_polling_callers_are_not_suppressed(self):
        # e.g. an admin/dashboard route posting into the owner channel.
        with mock.patch.dict(os.environ, self._env()):
            result = app.send_blaze_chat_message("hi", channel_id=self.TZ_CHANNEL)
        self.assertFalse(result.get("suppressed", False))
        self.post.assert_called_once()

    def test_real_polling_worker_thread_is_marked_and_suppressed(self):
        """End to end through blaze_polling_worker itself: one cycle against
        the owner target, with the row processor issuing a reply."""
        seen = []

        def fake_process(target, rows, resolved_creator_id=None, target_status=None):
            seen.append(app.send_blaze_chat_message("!points reply", channel_id=target["channel_id"]))
            app.polling_status["running"] = False  # stop after this cycle
            return 1

        owner_target = {"channel_id": self.TZ_CHANNEL, "handle": "owner", "is_owner_channel": True}
        orig_running = app.polling_status.get("running")
        with mock.patch.dict(os.environ, {**self._env(), "FOXBOT_MULTI_CHANNEL_POLL_SECONDS": "2"}), \
                mock.patch.object(app, "_foxbot_multichannel_targets_v1", return_value={"targets": [owner_target], "unresolved": []}), \
                mock.patch.object(app, "_foxbot_bot_connect_targets_v1", return_value={"targets": []}), \
                mock.patch.object(app, "_foxbot_resolve_creator_id_v1", return_value="tz"), \
                mock.patch.object(app, "get_recent_blaze_messages", return_value={"data": []}), \
                mock.patch.object(app, "extract_rows_from_blaze_response", return_value=[{}]), \
                mock.patch.object(app, "_foxbot_process_channel_rows_v1", side_effect=fake_process):
            t = threading.Thread(target=app.blaze_polling_worker, daemon=True)
            t.start()
            t.join(timeout=15)
        app.polling_status["running"] = orig_running

        self.assertFalse(t.is_alive())
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0].get("suppressed"))
        self.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()

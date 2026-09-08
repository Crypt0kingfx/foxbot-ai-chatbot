"""Tests for the /foxbot-control access-control fix, and the same fix
applied to every other route found with the identical weak-auth pattern
(no Request param -> structurally could not call _foxbot_require_admin_v1,
gated only by the generic "any approved session" middleware, not admin
specifically):

  - /foxbot-control, /dashboard, /admin, /legacy-admin -- admin HTML pages
    whose own real actions were already gated; visibility-only exposure.
    (/dashboard and /admin were later retired to redirects onto
    /studio-v2 -- stale-route cleanup, not a further security change --
    see the dashboard/admin redirect tests below. /legacy-admin is
    untouched.)
  - GET /api/blaze/native/status -- read-only, leaked platform-wide
    native-connector state to any approved session.
  - GET /blaze/start-polling-listener, /blaze/stop-polling-listener --
    the SERIOUS one: these directly start/stop the live polling_thread
    that runs the bot for every creator today. Unlike the others, this
    was a real, currently-exploitable platform-affecting action, not just
    visibility.
  - POST /api/blaze/listener/connect, /disconnect -- same shape, flips
    real shared state (BLAZE_LISTENER_STATE["connected"]).

Same discipline as tests/test_casino_scoped_creator_access.py and
tests/test_my_bot_status_endpoint.py: real signed session cookies through
the real, unmocked auth-gate middleware.

Run with:
    python -m unittest tests.test_foxbot_control_admin_gate -v
"""

import os
import sys
import unittest
import uuid
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402


class FoxbotControlAdminGateTestCase(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        self.client = TestClient(app.app)

        self.admin_blaze_id = f"test-admin-{uuid.uuid4().hex[:10]}"
        self.admin_display_name = "test-admin-owner"

        self.creator_id = f"test-creator-{uuid.uuid4().hex[:10]}"
        self.creator_display_name = "test-scoped-creator"

        self._env_patches = {}
        for key, value in {
            "STUDIO_SESSION_SECRET": "test-secret-do-not-use-in-prod",
            "STUDIO_APPROVED_BLAZE_USER_IDS": ",".join([self.admin_blaze_id, self.creator_id]),
            "STUDIO_AUTH_MODE": "both",
        }.items():
            self._env_patches[key] = os.environ.get(key)
            os.environ[key] = value

        self._tz_patch = mock.patch.object(app, "_tenant_zero_id", return_value=self.admin_blaze_id)
        self._tz_patch.start()

        # Snapshot/restore global process-wide state that
        # start/stop-polling-listener and listener/connect|disconnect
        # mutate -- this module runs in the same process as every other
        # test file (unittest discover), so leaking these forward would
        # break unrelated tests (e.g. test_auto_start_listener_logging's
        # own assumptions about polling_status["running"] at setup).
        self._polling_status_snapshot = dict(app.polling_status)
        self._polling_thread_snapshot = app.polling_thread
        self._blaze_listener_connected_snapshot = app.BLAZE_LISTENER_STATE.get("connected")

    def tearDown(self):
        self._tz_patch.stop()
        for key, value in self._env_patches.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

        app.polling_status.clear()
        app.polling_status.update(self._polling_status_snapshot)
        app.polling_thread = self._polling_thread_snapshot
        app.BLAZE_LISTENER_STATE["connected"] = self._blaze_listener_connected_snapshot

    def _cookies_for(self, blaze_id, display_name):
        token = app._foxbot_dashboard_session_sign_v1(blaze_id, display_name)
        return {"foxbot_dashboard_session": token}

    def _as_admin(self):
        return self._cookies_for(self.admin_blaze_id, self.admin_display_name)

    def _as_scoped_creator(self):
        return self._cookies_for(self.creator_id, self.creator_display_name)

    # ==================================================================
    def test_scoped_creator_denied_foxbot_control(self):
        res = self.client.get("/foxbot-control", cookies=self._as_scoped_creator())
        self.assertEqual(res.status_code, 403)
        body = res.json()
        self.assertFalse(body["ok"])
        self.assertNotIn("FoxBot Control Dashboard", res.text)

    def test_scoped_creator_gets_same_denial_shape_as_other_admin_routes(self):
        """Same JSON error shape _foxbot_require_admin_v1 already returns
        everywhere else -- no bespoke response invented for this route."""
        res = self.client.get("/foxbot-control", cookies=self._as_scoped_creator())
        body = res.json()
        self.assertEqual(
            body["error"],
            "This action requires full admin access, not a scoped creator session.",
        )

    def test_admin_still_gets_full_page_unchanged(self):
        res = self.client.get("/foxbot-control", cookies=self._as_admin())
        self.assertEqual(res.status_code, 200)
        self.assertIn("FoxBot Control Dashboard", res.text)
        self.assertIn("Start Listener", res.text)
        self.assertIn("Emergency OFF", res.text)

    def test_basic_auth_admin_still_gets_full_page(self):
        for key, value in {"STUDIO_ADMIN_USER": "admin", "STUDIO_ADMIN_PASSWORD": "test-password"}.items():
            self._env_patches.setdefault(key, os.environ.get(key))
            os.environ[key] = value
        res = self.client.get("/foxbot-control", auth=("admin", "test-password"))
        self.assertEqual(res.status_code, 200)
        self.assertIn("FoxBot Control Dashboard", res.text)

    def test_no_session_at_all_still_401s_same_as_before(self):
        """Unauthenticated requests never reached route bodies at all --
        that's the OUTER auth-gate middleware, untouched by this fix."""
        res = self.client.get("/foxbot-control")
        self.assertEqual(res.status_code, 401)

    def test_scoped_creator_still_cannot_start_or_stop_the_native_listener(self):
        """Regression check on the actions themselves -- these were
        already correctly admin-gated before this fix and must stay so."""
        cookies = self._as_scoped_creator()
        for path in (
            "/api/blaze/native/start",
            "/api/blaze/native/stop",
            "/api/blaze/native/live-control/on",
            "/api/blaze/native/live-control/off",
        ):
            res = self.client.post(path, cookies=cookies)
            self.assertEqual(res.status_code, 403, f"{path} should still be admin-only")

    # ==================================================================
    # /legacy-admin keeps the original visibility-only fix: admin-only,
    # still renders the real page.
    # ==================================================================
    def test_scoped_creator_denied_legacy_admin(self):
        res = self.client.get("/legacy-admin", cookies=self._as_scoped_creator())
        self.assertEqual(res.status_code, 403, "/legacy-admin should still be admin-only")

    def test_admin_still_gets_legacy_admin_unchanged(self):
        res = self.client.get("/legacy-admin", cookies=self._as_admin())
        self.assertEqual(res.status_code, 200, "/legacy-admin should be unchanged for admin")

    # ==================================================================
    # /dashboard and /admin were retired to thin redirects onto
    # /studio-v2 (the real current dashboard) -- no per-route admin check
    # left to enforce, since /studio-v2 already applies the same outer
    # approved-session gate (below) to whoever lands there. An
    # unauthenticated caller never reaches the redirect at all: the outer
    # gate still 401s on /dashboard and /admin themselves, same as before.
    # ==================================================================
    def test_no_session_denied_dashboard_and_admin_before_any_redirect(self):
        for path in ("/dashboard", "/admin"):
            res = self.client.get(path, follow_redirects=False)
            self.assertEqual(res.status_code, 401, f"{path} should still 401 with no session")

    def test_scoped_creator_redirected_from_dashboard_and_admin_to_studio_v2(self):
        cookies = self._as_scoped_creator()
        for path in ("/dashboard", "/admin"):
            res = self.client.get(path, cookies=cookies, follow_redirects=False)
            self.assertEqual(res.status_code, 307, f"{path} should redirect")
            self.assertEqual(res.headers["location"], "/studio-v2")

    def test_admin_redirected_from_dashboard_and_admin_to_studio_v2(self):
        cookies = self._as_admin()
        for path in ("/dashboard", "/admin"):
            res = self.client.get(path, cookies=cookies, follow_redirects=False)
            self.assertEqual(res.status_code, 307, f"{path} should redirect")
            self.assertEqual(res.headers["location"], "/studio-v2")

    # ==================================================================
    # Read-only platform-wide state leak.
    # ==================================================================
    def test_scoped_creator_denied_native_status(self):
        res = self.client.get("/api/blaze/native/status", cookies=self._as_scoped_creator())
        self.assertEqual(res.status_code, 403)

    def test_admin_still_gets_native_status_unchanged(self):
        res = self.client.get("/api/blaze/native/status", cookies=self._as_admin())
        self.assertEqual(res.status_code, 200)

    # ==================================================================
    # THE serious one: real platform-affecting actions, not just
    # visibility. A scoped creator must not be able to start/stop the
    # live listener for every creator.
    # ==================================================================
    def test_scoped_creator_cannot_stop_the_live_listener(self):
        res = self.client.get("/blaze/stop-polling-listener", cookies=self._as_scoped_creator())
        self.assertEqual(res.status_code, 403)

    def test_scoped_creator_cannot_start_the_live_listener(self):
        with mock.patch.object(app, "blaze_polling_worker"):
            res = self.client.get("/blaze/start-polling-listener", cookies=self._as_scoped_creator())
        self.assertEqual(res.status_code, 403)

    def test_scoped_creator_cannot_toggle_listener_connect_state(self):
        cookies = self._as_scoped_creator()
        for path in ("/api/blaze/listener/connect", "/api/blaze/listener/disconnect"):
            res = self.client.post(path, cookies=cookies)
            self.assertEqual(res.status_code, 403, f"{path} should be admin-only")

    def test_admin_can_still_stop_and_start_the_live_listener(self):
        """This is the operational-recovery path (e.g. restarting the
        listener after an outage) -- must be completely unaffected for a
        genuine admin session. The real background worker is mocked out
        so this test doesn't spawn a thread that makes live Blaze network
        calls; the guard/dispatch behavior is what's under test here."""
        cookies = self._as_admin()

        stop_res = self.client.get("/blaze/stop-polling-listener", cookies=cookies)
        self.assertEqual(stop_res.status_code, 200)
        self.assertTrue(stop_res.json()["success"])

        with mock.patch.object(app, "blaze_polling_worker") as mocked_worker:
            start_res = self.client.get("/blaze/start-polling-listener", cookies=cookies)
            if app.polling_thread is not None:
                app.polling_thread.join(timeout=2)
        self.assertEqual(start_res.status_code, 200)
        self.assertTrue(start_res.json()["success"])
        # Confirms the real action still runs for admin, not just a 200 --
        # a thread was actually spawned targeting the worker function (it
        # returns instantly since the worker itself is mocked out here,
        # so is_alive() can't be asserted after the fact -- called can).
        self.assertTrue(mocked_worker.called)

    def test_admin_can_still_toggle_listener_connect_state(self):
        cookies = self._as_admin()
        res = self.client.post("/api/blaze/listener/connect", cookies=cookies)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["ok"])
        res = self.client.post("/api/blaze/listener/disconnect", cookies=cookies)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["ok"])

    # ==================================================================
    # Confirm isolation from today's other work.
    # ==================================================================
    def test_my_bot_status_endpoint_unaffected(self):
        res = self.client.get("/api/studio/my-bot-status", cookies=self._as_scoped_creator())
        self.assertEqual(res.status_code, 200)
        self.assertIn(res.json()["state"], ("bot_connected", "not_connected"))


if __name__ == "__main__":
    unittest.main()

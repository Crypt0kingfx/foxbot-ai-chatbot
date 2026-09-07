"""Tests for GET /api/studio/my-bot-status -- the per-creator counterpart
to the admin-only /api/blaze/native/diagnostics, backing the Overview
vitals widget for a scoped (non-admin) creator session.

Deliberately does NOT mock the auth boundary away: two distinct dashboard
sessions are built with REAL signed cookies via
app._foxbot_dashboard_session_sign_v1, verified by the REAL, unmocked
app._foxbot_dashboard_session_verify_v1 / auth-gate middleware -- same
discipline as tests/test_casino_scoped_creator_access.py. No DATABASE_URL
needed: this endpoint reads only the in-memory
_FOXBOT_MULTICHANNEL_STATE_V1, which the test seeds directly.

Run with:
    python -m unittest tests.test_my_bot_status_endpoint -v
"""

import os
import sys
import unittest
import uuid
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402


class MyBotStatusEndpointTestCase(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        self.client = TestClient(app.app)

        self.admin_blaze_id = f"test-admin-{uuid.uuid4().hex[:10]}"
        self.admin_display_name = "test-admin-owner"

        self.creator_a_id = f"test-creatorA-{uuid.uuid4().hex[:10]}"
        self.creator_a_display_name = "test-creator-a"
        self.creator_a_handle = "test-creator-a-handle"

        self.creator_b_id = f"test-creatorB-{uuid.uuid4().hex[:10]}"
        self.creator_b_display_name = "test-creator-b"
        self.creator_b_handle = "test-creator-b-handle"

        # Creator C is approved (can log in) but is neither a bot-connect
        # target nor mapped to any connected_creators.json handle --
        # the "neither" fallback path.
        self.creator_c_id = f"test-creatorC-{uuid.uuid4().hex[:10]}"
        self.creator_c_display_name = "test-creator-c"

        self._env_patches = {}
        for key, value in {
            "STUDIO_SESSION_SECRET": "test-secret-do-not-use-in-prod",
            "STUDIO_APPROVED_BLAZE_USER_IDS": ",".join(
                [self.admin_blaze_id, self.creator_a_id, self.creator_b_id, self.creator_c_id]
            ),
            "STUDIO_AUTH_MODE": "both",
        }.items():
            self._env_patches[key] = os.environ.get(key)
            os.environ[key] = value

        self._tz_patch = mock.patch.object(app, "_tenant_zero_id", return_value=self.admin_blaze_id)
        self._tz_patch.start()

        # connected_creators.json join: creator B resolves to a handle
        # (the !joinfox-only stand-in) -- creator A is never looked up
        # this way since it's found via the bot-connect target list
        # first; creator C resolves to nothing (truly unmapped).
        handle_map = {self.creator_b_id: self.creator_b_handle}

        self._handle_patch = mock.patch.object(
            app, "_foxbot_resolve_handle_for_blaze_id_v1",
            side_effect=lambda blaze_id: handle_map.get(blaze_id),
        )
        self._handle_patch.start()

        # Seed the SAME in-memory state /api/foxbot/multichannel/status
        # reads -- only creator A is a live bot-connect target. Two
        # distinct, clearly-different per_target entries so a mix-up
        # between them is easy to catch.
        self._orig_targets = app._FOXBOT_MULTICHANNEL_STATE_V1.get("targets")
        self._orig_per_target = app._FOXBOT_MULTICHANNEL_STATE_V1.get("per_target")

        app._FOXBOT_MULTICHANNEL_STATE_V1["targets"] = [
            {
                "channel_id": "chan-a",
                "channel_slug": "creator-a-channel",
                "handle": self.creator_a_handle,
                "creator_id": self.creator_a_id,
                "is_bot_connect_target": True,
            },
        ]
        app._FOXBOT_MULTICHANNEL_STATE_V1["per_target"] = {
            "chan-a": {
                "handle": self.creator_a_handle,
                "channel_slug": "creator-a-channel",
                "cycles": 5,
                "last_attempt_at": 1234567890.0,
                "last_ok": True,
                "last_error": None,
                "messages_seen": 12,
                "commands_processed": 3,
                "token_source": "own_token",
                "last_reply_at": 1234567891.0,
                "last_command": "!arcade",
                "last_username": "viewerA",
            },
        }

    def tearDown(self):
        self._tz_patch.stop()
        self._handle_patch.stop()

        if self._orig_targets is None:
            app._FOXBOT_MULTICHANNEL_STATE_V1.pop("targets", None)
        else:
            app._FOXBOT_MULTICHANNEL_STATE_V1["targets"] = self._orig_targets

        if self._orig_per_target is None:
            app._FOXBOT_MULTICHANNEL_STATE_V1.pop("per_target", None)
        else:
            app._FOXBOT_MULTICHANNEL_STATE_V1["per_target"] = self._orig_per_target

        for key, value in self._env_patches.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    # ------------------------------------------------------------------
    def _cookies_for(self, blaze_id, display_name):
        token = app._foxbot_dashboard_session_sign_v1(blaze_id, display_name)
        return {"foxbot_dashboard_session": token}

    def _as_admin(self):
        return self._cookies_for(self.admin_blaze_id, self.admin_display_name)

    def _as_creator_a(self):
        return self._cookies_for(self.creator_a_id, self.creator_a_display_name)

    def _as_creator_b(self):
        return self._cookies_for(self.creator_b_id, self.creator_b_display_name)

    def _as_creator_c(self):
        return self._cookies_for(self.creator_c_id, self.creator_c_display_name)

    # ==================================================================
    def test_bot_connect_creator_sees_own_real_status(self):
        res = self.client.get("/api/studio/my-bot-status", cookies=self._as_creator_a())
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["state"], "bot_connected")
        self.assertTrue(body["found"])
        self.assertEqual(body["handle"], self.creator_a_handle)
        status = body["status"]
        self.assertTrue(status["last_ok"])
        self.assertEqual(status["messages_seen"], 12)
        self.assertEqual(status["cycles"], 5)
        self.assertEqual(status["last_command"], "!arcade")
        self.assertEqual(status["last_username"], "viewerA")

    def test_joinfox_only_creator_sees_informational_state_not_error(self):
        res = self.client.get("/api/studio/my-bot-status", cookies=self._as_creator_b())
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["state"], "not_connected")
        self.assertFalse(body["found"])
        self.assertEqual(body["handle"], self.creator_b_handle)

    def test_creator_with_no_access_at_all_does_not_crash(self):
        res = self.client.get("/api/studio/my-bot-status", cookies=self._as_creator_c())
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["state"], "not_connected")
        self.assertFalse(body["found"])
        self.assertIsNone(body["handle"])

    def test_cross_creator_isolation_b_never_sees_a_data(self):
        res = self.client.get("/api/studio/my-bot-status", cookies=self._as_creator_b())
        body = res.json()
        self.assertFalse(body["found"])
        self.assertNotIn("status", body)

    def test_admin_session_does_not_crash_and_is_distinct_state(self):
        res = self.client.get("/api/studio/my-bot-status", cookies=self._as_admin())
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["is_admin"])
        self.assertEqual(body["state"], "admin")

    def test_scoped_creator_never_gets_403_this_was_this_mornings_bug(self):
        for cookies in (self._as_creator_a(), self._as_creator_b(), self._as_creator_c()):
            res = self.client.get("/api/studio/my-bot-status", cookies=cookies)
            self.assertNotEqual(res.status_code, 403)
            self.assertEqual(res.status_code, 200)

    def test_admin_only_diagnostics_route_still_blocks_scoped_creator(self):
        """Regression check: the OLD admin-only diagnostics route must be
        completely untouched by this change -- still 403s a scoped
        creator exactly as before. Proves this work was additive, not a
        replacement of the existing admin-gated behavior."""
        res = self.client.get("/api/blaze/native/diagnostics", cookies=self._as_creator_a())
        self.assertEqual(res.status_code, 403)


if __name__ == "__main__":
    unittest.main()

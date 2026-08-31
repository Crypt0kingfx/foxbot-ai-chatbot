"""Proves app.foxbot_auto_start_listener_v1()'s new logging (added as a
launch-week diagnostic for the silent-auto-start-skip incident) is additive
only: for every precondition branch, the function's side effects (return
value, polling_thread identity/aliveness, polling_status/proof_stats
mutation) are exactly what the pre-existing condition/return logic dictates,
with the only observable addition being one stdout line per branch. A print
statement cannot alter a boolean condition or a return statement, so
matching each branch's existing state-transition contract here is the
proof the diff changed no logic -- not just that it visually looks
additive.

No pytest dependency -- this repo has no test framework installed (see
tests/test_discovery_seeding.py). Run with:
    python -m unittest tests.test_auto_start_listener_logging -v
"""

import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402


class _DeadThread:
    def is_alive(self):
        return False


class _AliveThread:
    def is_alive(self):
        return True


class AutoStartListenerLoggingTestCase(unittest.TestCase):
    def setUp(self):
        # Snapshot every piece of state the function can touch, so each
        # test starts from the same clean slate and tearDown can restore it
        # regardless of what the function under test did.
        self._orig_polling_thread = app.polling_thread
        self._orig_polling_status = dict(app.polling_status)
        self._orig_proof_stats = dict(app.proof_stats)

    def tearDown(self):
        app.polling_thread = self._orig_polling_thread
        app.polling_status.clear()
        app.polling_status.update(self._orig_polling_status)
        app.proof_stats.clear()
        app.proof_stats.update(self._orig_proof_stats)

    def _run(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            result = app.foxbot_auto_start_listener_v1()
        return result, buffer.getvalue()

    def test_skips_when_flag_disabled(self):
        app.polling_thread = _DeadThread()
        with mock.patch.dict(os.environ, {"FOXBOT_AUTO_START_LISTENER": "false"}):
            result, output = self._run()

        # Existing behavior: bails before touching anything else.
        self.assertIsNone(result)
        self.assertIsInstance(app.polling_thread, _DeadThread)
        self.assertFalse(app.polling_status["running"])
        self.assertFalse(app.proof_stats["listener_running"])
        # New behavior: the skip is now logged, naming the actual cause.
        self.assertIn("FOXBOT_AUTO_START_LISTENER is explicitly disabled", output)

    def test_skips_when_client_id_missing(self):
        app.polling_thread = _DeadThread()
        env = {"FOXBOT_AUTO_START_LISTENER": "true", "BLAZE_CHANNEL_ID": "chan-123"}
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("BLAZE_CLIENT_ID", None)
            result, output = self._run()

        self.assertIsNone(result)
        self.assertIsInstance(app.polling_thread, _DeadThread)
        self.assertFalse(app.polling_status["running"])
        self.assertIn("missing required env var(s)", output)
        self.assertIn("BLAZE_CLIENT_ID set=False", output)
        self.assertIn("BLAZE_CHANNEL_ID set=True", output)

    def test_skips_when_channel_id_missing(self):
        app.polling_thread = _DeadThread()
        env = {"FOXBOT_AUTO_START_LISTENER": "true", "BLAZE_CLIENT_ID": "client-123"}
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("BLAZE_CHANNEL_ID", None)
            result, output = self._run()

        self.assertIsNone(result)
        self.assertIsInstance(app.polling_thread, _DeadThread)
        self.assertFalse(app.polling_status["running"])
        self.assertIn("missing required env var(s)", output)
        self.assertIn("BLAZE_CLIENT_ID set=True", output)
        self.assertIn("BLAZE_CHANNEL_ID set=False", output)

    def test_skips_when_token_resolution_fails(self):
        app.polling_thread = _DeadThread()
        env = {
            "FOXBOT_AUTO_START_LISTENER": "true",
            "BLAZE_CLIENT_ID": "client-123",
            "BLAZE_CHANNEL_ID": "chan-123",
        }
        with mock.patch.dict(os.environ, env, clear=False), mock.patch.object(
            app, "resolve_blaze_access_token", return_value=("", "missing")
        ):
            result, output = self._run()

        self.assertIsNone(result)
        self.assertIsInstance(app.polling_thread, _DeadThread)
        self.assertFalse(app.polling_status["running"])
        self.assertIn("resolve_blaze_access_token() returned no token", output)

    def test_skips_when_thread_already_alive(self):
        alive_thread = _AliveThread()
        app.polling_thread = alive_thread
        env = {
            "FOXBOT_AUTO_START_LISTENER": "true",
            "BLAZE_CLIENT_ID": "client-123",
            "BLAZE_CHANNEL_ID": "chan-123",
        }
        with mock.patch.dict(os.environ, env, clear=False), mock.patch.object(
            app, "resolve_blaze_access_token", return_value=("tok", "render_environment")
        ):
            result, output = self._run()

        self.assertIsNone(result)
        # Existing behavior: does NOT replace an already-alive thread.
        self.assertIs(app.polling_thread, alive_thread)
        self.assertFalse(app.polling_status["running"])
        self.assertIn("polling_thread is already alive", output)

    def test_starts_thread_on_success(self):
        app.polling_thread = _DeadThread()
        env = {
            "FOXBOT_AUTO_START_LISTENER": "true",
            "BLAZE_CLIENT_ID": "client-123",
            "BLAZE_CHANNEL_ID": "chan-123",
        }
        with mock.patch.dict(os.environ, env, clear=False), mock.patch.object(
            app, "resolve_blaze_access_token", return_value=("tok", "saved_oauth_file")
        ), mock.patch.object(app, "blaze_polling_worker", side_effect=lambda: None):
            result, output = self._run()
            # Give the daemon thread a moment to finish its no-op body
            # before asserting/tearing down.
            if app.polling_thread is not None:
                app.polling_thread.join(timeout=2)

        self.assertIsNone(result)
        # Existing behavior: a real thread was created and started.
        self.assertIsNotNone(app.polling_thread)
        self.assertTrue(app.polling_status["running"])
        self.assertTrue(app.proof_stats["listener_running"])
        # New behavior: success is now logged too, naming the token source.
        self.assertIn("started polling_thread", output)
        self.assertIn("saved_oauth_file", output)


if __name__ == "__main__":
    unittest.main()

"""Boot auto-start for the native Blaze socket listener.

Two layers:
  * services.blaze_native_connector.start_listener() is idempotent under
    concurrency -- N simultaneous callers open exactly one socket.
  * app.foxbot_auto_start_native_listener_v1() (the startup hook) is opt-in,
    logs every outcome, and never raises into boot.

No network, no real socket: python-socketio is replaced with a fake whose
connect() blocks until the test releases it. No pytest dependency. Run with:
    python -m unittest tests.test_native_listener_auto_start -v
"""

import io
import os
import sys
import threading
import types
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import blaze_native_connector as native  # noqa: E402


class _FakeSioClient:
    instances = []
    release = threading.Event()

    def __init__(self, *args, **kwargs):
        type(self).instances.append(self)
        self.disconnected = False

    def event(self, fn):
        return fn

    def on(self, _name):
        return lambda fn: fn

    def connect(self, *args, **kwargs):
        # Hold the listener "connected" until the test lets it finish.
        type(self).release.wait(timeout=10)

    def wait(self):
        return None

    def disconnect(self):
        self.disconnected = True
        type(self).release.set()

    def get_sid(self, _namespace="/"):
        return "fake-sid"


def _fake_socketio_module():
    module = types.ModuleType("socketio")
    module.Client = _FakeSioClient
    return module


def _ready_config():
    return {"missing_required_for_live": [], "state": native.STATE}


class _NativeStateMixin:
    def setUp(self):
        self._orig_state = dict(native.STATE)
        self._orig_thread = native._thread
        self._orig_socket = native._socket
        _FakeSioClient.instances = []
        _FakeSioClient.release = threading.Event()
        native.STATE["running"] = False
        native.STATE["connected"] = False
        native._thread = None
        native._socket = None
        self._patches = [
            mock.patch.dict(sys.modules, {"socketio": _fake_socketio_module()}),
            mock.patch.object(native, "config_status", side_effect=_ready_config),
            # emit_event writes to Postgres; keep tests offline and fast.
            mock.patch.object(native._foxbot_events_v1, "emit_event", return_value=None),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        _FakeSioClient.release.set()
        if native._thread is not None:
            native._thread.join(timeout=5)
        for p in reversed(self._patches):
            p.stop()
        native.STATE.clear()
        native.STATE.update(self._orig_state)
        native._thread = self._orig_thread
        native._socket = self._orig_socket


class StartListenerIdempotencyTestCase(_NativeStateMixin, unittest.TestCase):
    def test_concurrent_starts_open_exactly_one_socket(self):
        callers = 12
        barrier = threading.Barrier(callers)
        results = []
        results_lock = threading.Lock()

        def call():
            barrier.wait()
            r = native.start_listener()
            with results_lock:
                results.append(r)

        threads = [threading.Thread(target=call) for _ in range(callers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(len(_FakeSioClient.instances), 1)
        self.assertEqual(sum(1 for r in results if r.get("started")), 1)
        self.assertEqual(sum(1 for r in results if r.get("already_running")), callers - 1)
        self.assertTrue(native.STATE["running"])

    def test_second_call_while_running_is_noop(self):
        first = native.start_listener()
        second = native.start_listener()
        self.assertTrue(first.get("started"))
        self.assertTrue(second.get("already_running"))
        self.assertEqual(len(_FakeSioClient.instances), 1)

    def test_alive_thread_counts_as_running_even_if_state_flag_cleared(self):
        native.start_listener()
        native.STATE["running"] = False  # e.g. stop_listener() mid-wind-down
        again = native.start_listener()
        self.assertTrue(again.get("already_running"))
        self.assertEqual(len(_FakeSioClient.instances), 1)

    def test_can_restart_after_listener_thread_ends(self):
        native.start_listener()
        _FakeSioClient.release.set()
        native._thread.join(timeout=5)
        self.assertFalse(native.STATE["running"])

        _FakeSioClient.release = threading.Event()
        again = native.start_listener()
        self.assertTrue(again.get("started"))
        self.assertEqual(len(_FakeSioClient.instances), 2)

    def test_missing_config_does_not_mark_running(self):
        with mock.patch.object(
            native, "config_status",
            return_value={"missing_required_for_live": ["BLAZE_CHANNEL_ID"], "state": native.STATE},
        ):
            result = native.start_listener()
        self.assertFalse(result["ok"])
        self.assertIn("BLAZE_CHANNEL_ID", result["error"])
        self.assertFalse(native.STATE["running"])
        self.assertEqual(_FakeSioClient.instances, [])


import app  # noqa: E402  (heavy import; only needed by the hook tests below)


class NativeAutoStartHookTestCase(unittest.TestCase):
    def _run(self, env):
        buffer = io.StringIO()
        with mock.patch.dict(os.environ, env), redirect_stdout(buffer):
            result = app.foxbot_auto_start_native_listener_v1()
        return result, buffer.getvalue()

    def test_registered_as_startup_handler(self):
        self.assertIn(app.foxbot_auto_start_native_listener_v1, app.app.router.on_startup)

    def test_skipped_unless_native_enabled(self):
        with mock.patch.object(native, "start_listener") as start:
            for value in ["", "false", "0"]:
                result, output = self._run({"FOXBOT_BLAZE_NATIVE_ENABLED": value})
                self.assertEqual(result["reason"], "native_disabled")
                self.assertIn("opt-in", output)
            start.assert_not_called()

    def test_skipped_when_auto_start_opted_out(self):
        with mock.patch.object(native, "start_listener") as start:
            result, output = self._run({
                "FOXBOT_BLAZE_NATIVE_ENABLED": "true",
                "FOXBOT_AUTO_START_NATIVE_LISTENER": "off",
            })
        start.assert_not_called()
        self.assertEqual(result["reason"], "auto_start_disabled")
        self.assertIn("explicitly disabled", output)

    def test_starts_with_shared_event_handler_and_logs_success(self):
        with mock.patch.object(native, "start_listener", return_value={"ok": True, "started": True}) as start:
            result, output = self._run({
                "FOXBOT_BLAZE_NATIVE_ENABLED": "true",
                "FOXBOT_AUTO_START_NATIVE_LISTENER": "true",
            })
        start.assert_called_once_with(app._foxbot_blaze_native_event_handler_v1)
        self.assertTrue(result["started"])
        self.assertIn("[FoxBot Native Auto-Start] started", output)

    def test_already_running_is_logged_not_restarted(self):
        with mock.patch.object(native, "start_listener", return_value={"ok": True, "already_running": True}):
            _result, output = self._run({"FOXBOT_BLAZE_NATIVE_ENABLED": "true"})
        self.assertIn("already running", output)

    def test_error_result_is_logged_as_failure(self):
        with mock.patch.object(native, "start_listener", return_value={"ok": False, "error": "Missing required env vars: BLAZE_CHANNEL_ID"}):
            result, output = self._run({"FOXBOT_BLAZE_NATIVE_ENABLED": "true"})
        self.assertFalse(result["ok"])
        self.assertIn("FAILED: Missing required env vars: BLAZE_CHANNEL_ID", output)

    def test_exception_never_escapes_into_boot(self):
        with mock.patch.object(native, "start_listener", side_effect=RuntimeError("boom")):
            result, output = self._run({"FOXBOT_BLAZE_NATIVE_ENABLED": "true"})
        self.assertFalse(result["ok"])
        self.assertIn("FAILED: start_listener raised RuntimeError: boom", output)


if __name__ == "__main__":
    unittest.main()

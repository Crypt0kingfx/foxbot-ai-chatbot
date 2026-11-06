"""Plinko sessions and drops against a real Postgres (local Docker only --
see tests/db_guard.py). Each test uses its own random creator handle, so
nothing needs truncating and runs can't collide.

Covers: off by default, kill switch, one open session per viewer (including
under concurrency), cooldown, request replay, exactly drops_per_session
drops (including under concurrency), idempotent drop ids, the payout cap,
all-or-nothing drop transactions, idle-session auto-play, and the audit log.

Run with:
    python -m unittest tests.test_plinko_sessions -v
"""

import os
import sys
import threading
import unittest
import uuid
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.db_guard import DATABASE_CONFIGURED, SKIP_REASON  # noqa: E402
from services import casino_rng  # noqa: E402
from services import plinko  # noqa: E402


class _FixedBits(casino_rng.RNGProvider):
    """Every draw returns the same bits (0 -> slot 0 -> 10x)."""

    def __init__(self, bits):
        self.value = bits

    def roll(self, minimum, maximum):
        return minimum

    def choice(self, seq):
        return seq[0]

    def bits(self, k):
        return self.value


@unittest.skipUnless(DATABASE_CONFIGURED, SKIP_REASON)
class PlinkoSessionTestCase(unittest.TestCase):
    def setUp(self):
        self.handle = "plinkotest_" + uuid.uuid4().hex[:10]
        plinko.set_config(self.handle, enabled=True, cooldown_seconds=0)

    def tearDown(self):
        casino_rng.set_provider(casino_rng.SecureRandomProvider())

    # ---- helpers
    def _start(self, user="alice", key=None):
        session = plinko.claim_session(
            self.handle, user_key=user, display_name=user.title(), request_key=key or uuid.uuid4().hex,
            creator_id="cid-test",
        )
        return plinko.activate_session(self.handle, session["session_id"])

    def _sql(self, statement, params=()):
        with plinko._connect() as connection:
            return connection.execute(statement, params).fetchall() if statement.lstrip().upper().startswith("SELECT") \
                else connection.execute(statement, params)

    # ---- switches
    def test_plinko_is_off_by_default(self):
        fresh = "plinkotest_" + uuid.uuid4().hex[:10]
        self.assertFalse(plinko.get_config(fresh).enabled)
        with self.assertRaises(plinko.PlinkoDisabled):
            plinko.claim_session(fresh, user_key="a", display_name="A", request_key="k")

    def test_kill_switch_stops_sessions_drops_and_autoplay(self):
        self._start()
        plinko.set_config(self.handle, kill_switch=True)
        with self.assertRaises(plinko.PlinkoKilled):
            plinko.claim_session(self.handle, user_key="bob", display_name="Bob", request_key="k2")
        with self.assertRaises(plinko.PlinkoKilled):
            plinko.drop(self.handle, user_key="alice", drop_key="d1")
        self._sql(f"UPDATE {plinko.TABLE_SESSIONS} SET last_activity_at = NOW() - INTERVAL '2 hours' "
                  "WHERE creator_handle = %s", (self.handle,))
        self.assertEqual(plinko.finish_expired(self.handle), [])
        # Releasing it resumes exactly where things were.
        plinko.set_config(self.handle, kill_switch=False)
        self.assertEqual(plinko.drop(self.handle, user_key="alice", drop_key="d1")["drop"]["drop_index"], 1)

    def test_turning_off_blocks_new_sessions_but_lets_open_ones_finish(self):
        self._start()
        plinko.set_config(self.handle, enabled=False)
        with self.assertRaises(plinko.PlinkoDisabled):
            plinko.claim_session(self.handle, user_key="bob", display_name="Bob", request_key="k2")
        self.assertFalse(plinko.drop(self.handle, user_key="alice", drop_key="d1")["replayed"])

    # ---- sessions
    def test_one_open_session_per_viewer(self):
        first = self._start()
        with self.assertRaises(plinko.SessionActive) as ctx:
            plinko.claim_session(self.handle, user_key="alice", display_name="Alice", request_key="other")
        self.assertEqual(ctx.exception.session["session_id"], first["session_id"])
        # Someone else can play at the same time.
        self.assertEqual(self._start(user="bob")["status"], plinko.STATUS_ACTIVE)

    def test_concurrent_claims_create_exactly_one_session(self):
        results, errors = [], []
        barrier = threading.Barrier(10)

        def claim(i):
            barrier.wait()
            try:
                results.append(plinko.claim_session(self.handle, user_key="racer", display_name="Racer",
                                                    request_key=f"race-{i}"))
            except plinko.SessionActive:
                errors.append("active")

        threads = [threading.Thread(target=claim, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 9)
        rows = self._sql(f"SELECT COUNT(*) FROM {plinko.TABLE_SESSIONS} WHERE creator_handle = %s AND user_key = 'racer'",
                         (self.handle,))
        self.assertEqual(rows[0][0], 1)

    def test_database_enforces_one_open_session(self):
        import psycopg

        self._start()
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self._sql(
                f"""INSERT INTO {plinko.TABLE_SESSIONS}
                    (session_id, creator_handle, user_key, display_name, request_key, cost, stake,
                     drops_total, max_payout, multipliers, status)
                    VALUES (%s, %s, 'alice', 'Alice', 'sneaky', 100, 10, 10, 500, '[]'::jsonb, 'active')""",
                (uuid.uuid4().hex, self.handle),
            )

    def test_cooldown_between_sessions(self):
        plinko.set_config(self.handle, cooldown_seconds=3600)
        self._start()
        for i in range(10):
            plinko.drop(self.handle, user_key="alice", drop_key=f"d{i}")
        with self.assertRaises(plinko.CooldownActive) as ctx:
            plinko.claim_session(self.handle, user_key="alice", display_name="Alice", request_key="again")
        self.assertGreater(ctx.exception.seconds_left, 3500)
        # A cancelled (never paid) session doesn't start a cooldown.
        plinko.set_config(self.handle, cooldown_seconds=3600)
        s = plinko.claim_session(self.handle, user_key="carol", display_name="Carol", request_key="c1")
        plinko.cancel_session(self.handle, s["session_id"], "insufficient_foxcoins")
        self.assertEqual(plinko.claim_session(self.handle, user_key="carol", display_name="Carol",
                                              request_key="c2")["status"], plinko.STATUS_CLAIMED)

    def test_replayed_play_request_returns_same_session(self):
        a = plinko.claim_session(self.handle, user_key="alice", display_name="Alice", request_key="msg-1")
        b = plinko.claim_session(self.handle, user_key="alice", display_name="Alice", request_key="msg-1")
        self.assertFalse(a["replayed"])
        self.assertTrue(b["replayed"])
        self.assertEqual(a["session_id"], b["session_id"])

    def test_session_snapshots_config(self):
        plinko.set_config(self.handle, session_cost=200, max_session_payout=900)
        session = self._start()
        plinko.set_config(self.handle, session_cost=50, max_session_payout=100)
        self.assertEqual((session["cost"], session["stake"], session["max_payout"]), (200, 20, 900))
        result = plinko.drop(self.handle, user_key="alice", drop_key="d1")
        self.assertEqual(result["session"]["stake"], 20)

    # ---- drops
    def test_exactly_ten_drops_then_session_closes(self):
        self._start()
        indexes = [plinko.drop(self.handle, user_key="alice", drop_key=f"d{i}")["drop"]["drop_index"] for i in range(10)]
        self.assertEqual(indexes, list(range(1, 11)))
        with self.assertRaises(plinko.NoActiveSession):
            plinko.drop(self.handle, user_key="alice", drop_key="d10")
        session = plinko.list_sessions(self.handle)[0]
        self.assertEqual((session["status"], session["drops_used"], session["close_reason"]),
                         (plinko.STATUS_CLOSED, 10, "all_drops_played"))

    def test_claimed_but_unpaid_session_cannot_drop(self):
        plinko.claim_session(self.handle, user_key="alice", display_name="Alice", request_key="k")
        with self.assertRaises(plinko.NoActiveSession):
            plinko.drop(self.handle, user_key="alice", drop_key="d1")

    def test_same_drop_id_never_pays_twice(self):
        self._start()
        first = plinko.drop(self.handle, user_key="alice", drop_key="msg-42")
        again = plinko.drop(self.handle, user_key="alice", drop_key="msg-42")
        self.assertTrue(again["replayed"])
        self.assertEqual(again["drop"]["id"], first["drop"]["id"])
        self.assertEqual(again["session"]["payout_total"], first["session"]["payout_total"])
        self.assertEqual(again["session"]["drops_used"], 1)

    def test_concurrent_copies_of_one_drop_record_one_drop(self):
        self._start()
        results = []
        barrier = threading.Barrier(8)

        def go():
            barrier.wait()
            results.append(plinko.drop(self.handle, user_key="alice", drop_key="dup"))

        threads = [threading.Thread(target=go) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
        self.assertEqual(len(results), 8)
        self.assertEqual(sum(1 for r in results if not r["replayed"]), 1)
        self.assertEqual(len({r["drop"]["id"] for r in results}), 1)
        self.assertEqual(len(plinko.recent_drops(self.handle)), 1)

    def test_concurrent_drops_never_exceed_the_session(self):
        self._start()
        ok, refused = [], []
        barrier = threading.Barrier(15)

        def go(i):
            barrier.wait()
            try:
                ok.append(plinko.drop(self.handle, user_key="alice", drop_key=f"c{i}"))
            except plinko.NoActiveSession:
                refused.append(i)

        threads = [threading.Thread(target=go, args=(i,)) for i in range(15)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(len(ok), 10)
        self.assertEqual(len(refused), 5)
        self.assertEqual(sorted(r["drop"]["drop_index"] for r in ok), list(range(1, 11)))

    def test_session_payout_cap(self):
        casino_rng.set_provider(_FixedBits(0))  # every drop lands in slot 0 (10x)
        plinko.set_config(self.handle, max_session_payout=150)
        self._start()  # stake 10 -> raw 100 per drop
        d1 = plinko.drop(self.handle, user_key="alice", drop_key="a")
        d2 = plinko.drop(self.handle, user_key="alice", drop_key="b")
        d3 = plinko.drop(self.handle, user_key="alice", drop_key="c")
        self.assertEqual((d1["drop"]["payout"], d1["drop"]["capped"]), (100, False))
        self.assertEqual((d2["drop"]["raw_payout"], d2["drop"]["payout"], d2["drop"]["capped"]), (100, 50, True))
        self.assertEqual((d3["drop"]["payout"], d3["drop"]["capped"]), (0, True))
        self.assertEqual(d3["session"]["payout_total"], 150)
        self.assertIsNotNone(d3["drop"]["credited_at"], "a 0 payout has nothing to credit")

    def test_drop_is_all_or_nothing(self):
        """If anything fails mid-transaction (here: the audit insert), the
        drop row, the payout accrual and the drop count all roll back."""
        self._start()
        real_audit = plinko._audit

        def failing_audit(connection, handle, session_id, event, detail, Jsonb):
            if event == "drop":
                raise RuntimeError("simulated failure inside the drop transaction")
            return real_audit(connection, handle, session_id, event, detail, Jsonb)

        with mock.patch.object(plinko, "_audit", side_effect=failing_audit):
            with self.assertRaises(RuntimeError):
                plinko.drop(self.handle, user_key="alice", drop_key="boom")

        self.assertEqual(plinko.recent_drops(self.handle), [])
        session = plinko.get_open_session(self.handle, "alice")
        self.assertEqual((session["drops_used"], session["payout_total"]), (0, 0))
        # And the same drop id works normally afterwards.
        self.assertFalse(plinko.drop(self.handle, user_key="alice", drop_key="boom")["replayed"])

    def test_idle_session_autoplays_remaining_drops_once(self):
        self._start()
        plinko.drop(self.handle, user_key="alice", drop_key="d1")
        plinko.drop(self.handle, user_key="alice", drop_key="d2")
        self._sql(f"UPDATE {plinko.TABLE_SESSIONS} SET last_activity_at = NOW() - INTERVAL '2 hours' "
                  "WHERE creator_handle = %s", (self.handle,))
        played = plinko.finish_expired(self.handle)
        self.assertEqual(len(played), 8)
        self.assertTrue(all(d["auto"] for d in played))
        self.assertEqual([d["drop_index"] for d in played], list(range(3, 11)))
        session = plinko.list_sessions(self.handle)[0]
        self.assertEqual((session["status"], session["close_reason"]), (plinko.STATUS_CLOSED, "expired_autoplay"))
        self.assertEqual(plinko.finish_expired(self.handle), [])

    def test_fresh_session_is_not_autoplayed(self):
        self._start()
        self.assertEqual(plinko.finish_expired(self.handle), [])

    # ---- audit / reads
    def test_audit_log_records_every_step_in_order(self):
        session = self._start()
        for i in range(10):
            drop = plinko.drop(self.handle, user_key="alice", drop_key=f"d{i}")["drop"]
            plinko.mark_drop_credited(self.handle, drop["id"])
        audit = plinko.session_audit(self.handle, session["session_id"])
        events = [e["event"] for e in audit["events"]]
        self.assertEqual(events[:2], ["claimed", "debited"])
        self.assertEqual(events.count("drop"), 10)
        self.assertEqual(events.count("credited"), 10)
        last_drop = len(events) - 1 - events[::-1].index("drop")
        self.assertEqual(events.count("closed"), 1)
        self.assertEqual(events.index("closed"), last_drop + 1, "session closes in the same transaction as its last drop")
        drop_events = [e["detail"] for e in audit["events"] if e["event"] == "drop"]
        self.assertEqual([d["drop_index"] for d in drop_events], list(range(1, 11)))
        self.assertTrue(all({"slot", "bits", "multiplier_pct", "payout", "raw_payout"} <= set(d) for d in drop_events))

    def test_audit_is_scoped_to_the_creator(self):
        session = self._start()
        self.assertIsNone(plinko.session_audit("plinkotest_someoneelse", session["session_id"]))

    def test_config_validation_and_audit(self):
        with self.assertRaises(ValueError):
            plinko.set_config(self.handle, session_cost=105)  # not divisible by 10 drops
        with self.assertRaises(ValueError):
            plinko.set_config(self.handle, cooldown_seconds=-1)
        with self.assertRaises(ValueError):
            plinko.set_config(self.handle, bogus=1)
        plinko.set_config(self.handle, kill_switch=True, actor="studio:test")
        rows = self._sql(f"SELECT detail FROM {plinko.TABLE_AUDIT} WHERE creator_handle = %s AND event = 'config_changed' "
                         "ORDER BY id DESC LIMIT 1", (self.handle,))
        self.assertEqual(rows[0][0]["changes"]["kill_switch"], {"from": False, "to": True})
        self.assertEqual(rows[0][0]["actor"], "studio:test")

    def test_overlay_feed_reads(self):
        self.assertEqual(plinko.latest_drop_id(self.handle), 0)
        self._start()
        first = plinko.drop(self.handle, user_key="alice", drop_key="d1")["drop"]
        second = plinko.drop(self.handle, user_key="alice", drop_key="d2")["drop"]
        self.assertEqual(plinko.latest_drop_id(self.handle), second["id"])
        self.assertEqual([d["id"] for d in plinko.drops_since(self.handle, 0)], [first["id"], second["id"]])
        self.assertEqual([d["id"] for d in plinko.drops_since(self.handle, first["id"])], [second["id"]])
        self.assertEqual(sum(second["path"]), second["slot"])


if __name__ == "__main__":
    unittest.main()

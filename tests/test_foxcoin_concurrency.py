"""FoxCoin economy write concurrency (FOXCOIN_LOCK).

The economy is an in-memory dict changed from several threads at once
(polling loop, native listener, FastAPI threadpool routes, the event loop).
Every balance change is a read-modify-write, so without a lock two threads
can read the same balance and one update is lost.

These tests force the bad interleaving every time instead of hoping for it:
the economy's `balances` dict is swapped for a subclass whose .get() sleeps,
so every thread reads its balance before any thread writes back. With
FOXCOIN_LOCK the sleep happens inside the lock and the threads queue up;
without it they all read the same stale value.

Casino tests use real Postgres (local Docker only; tests/db_guard.py) and
clean up their rows. The FoxCoin side is in-memory and never persisted:
save_persistent_data writes only to a temp/fake path in these tests.

Run with:
    python -m unittest tests.test_foxcoin_concurrency -v
"""

import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402
import providers.promo as promo  # noqa: E402
import services.casino_config as casino_config  # noqa: E402
import services.casino_ledger as cl  # noqa: E402
from services import blaze_tokens  # noqa: E402
from tests.db_guard import DATABASE_CONFIGURED, SKIP_REASON  # noqa: E402

THREADS = 6


class _SlowBalances(dict):
    """balances dict whose reads take `delay` seconds: every concurrent
    writer reads before any writes back, unless something serializes them."""

    delay = 0.05

    def get(self, key, default=None):
        value = super().get(key, default)
        time.sleep(self.delay)
        return value


def _run_together(fn, n=THREADS):
    """Run fn(i) on n threads released at the same instant; return
    (results, errors) indexed by thread."""
    barrier = threading.Barrier(n)
    results, errors = [None] * n, [None] * n

    def worker(i):
        barrier.wait()
        try:
            results[i] = fn(i)
        except Exception as exc:  # noqa: BLE001 - recorded for assertions
            errors[i] = exc

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
        assert not t.is_alive(), "worker thread hung"
    return results, errors


class _EconomyFixture(unittest.TestCase):
    """Fresh creator bucket per test, with slow reads switched on."""

    def setUp(self):
        self.creator_id = f"test-fxlock-{uuid.uuid4().hex[:12]}"
        self.economy = app._creator_economy_v1(self.creator_id)
        self.economy["balances"] = _SlowBalances()

    def tearDown(self):
        app.foxcoin_economy["by_creator"].pop(self.creator_id, None)

    def balance(self, name):
        return int(dict.get(self.economy["balances"], app.viewer_key(name), 0))

    def set_balance(self, name, amount):
        dict.__setitem__(self.economy["balances"], app.viewer_key(name), amount)


class FoxCoinWriteConcurrencyTestCase(_EconomyFixture):
    def test_concurrent_add_points_lose_no_update(self):
        _results, errors = _run_together(lambda i: app.add_points("racer", 5, "test", creator_id=self.creator_id))
        self.assertEqual(errors, [None] * THREADS)
        self.assertEqual(self.balance("racer"), 5 * THREADS)
        self.assertEqual(len(self.economy["transactions"]), THREADS, "every change is in the history")

    def test_concurrent_debits_cannot_overspend(self):
        self.set_balance("spender", 100)

        def debit(i):
            return app.debit_foxcoins_idempotent("spender", 100, f"test:debit:{i}", creator_id=self.creator_id)

        results, errors = _run_together(debit)
        succeeded = [r for r in results if r is not None]
        rejected = [e for e in errors if isinstance(e, app.InsufficientFoxCoins)]
        self.assertEqual(len(succeeded), 1, "100 coins pay for exactly one 100-coin debit")
        self.assertEqual(len(rejected), THREADS - 1)
        self.assertEqual(self.balance("spender"), 0)
        self.assertEqual(len(self.economy["processed_deductions"]), 1)

    def test_same_debit_key_from_many_threads_debits_once(self):
        self.set_balance("spender", 1000)
        _run_together(lambda i: app.debit_foxcoins_idempotent("spender", 100, "test:debit:same", creator_id=self.creator_id))
        self.assertEqual(self.balance("spender"), 900)
        # Balance alone can hide a double debit (the second write can land
        # on the same stale read); the history shows every debit applied.
        self.assertEqual(len(self.economy["transactions"]), 1, "debited once")

    def test_same_credit_key_from_many_threads_credits_once(self):
        results, errors = _run_together(
            lambda i: app.credit_foxcoins_idempotent("payee", 10, "test:credit:same", creator_id=self.creator_id)
        )
        self.assertEqual(errors, [None] * THREADS)
        self.assertEqual(self.balance("payee"), 10, "one idempotency key pays once")
        self.assertEqual(len(self.economy["transactions"]), 1, "credited once, not masked by a lost update")
        self.assertEqual({r["balance"] for r in results}, {10}, "replays return the original result")

    def test_credit_racing_add_points_loses_nothing(self):
        def work(i):
            if i % 2:
                return app.credit_foxcoins_idempotent("mixed", 7, f"test:credit:{i}", creator_id=self.creator_id)
            return app.add_points("mixed", 3, "test", creator_id=self.creator_id)

        _results, errors = _run_together(work)
        self.assertEqual(errors, [None] * THREADS)
        odd = THREADS // 2
        self.assertEqual(self.balance("mixed"), 7 * odd + 3 * (THREADS - odd))


class SpendPointsTestCase(_EconomyFixture):
    def test_spend_points_rejects_without_touching_balance(self):
        self.set_balance("buyer", 9)
        self.assertIsNone(app.spend_points("buyer", 10, "test", creator_id=self.creator_id))
        self.assertEqual(self.balance("buyer"), 9)
        self.assertEqual(self.economy["transactions"], [])

    def test_spend_points_debits_and_returns_new_balance(self):
        self.set_balance("buyer", 30)
        self.assertEqual(app.spend_points("buyer", 10, "test", creator_id=self.creator_id), 20)
        self.assertEqual(self.balance("buyer"), 20)

    def test_spend_points_rejects_negative_cost(self):
        with self.assertRaises(ValueError):
            app.spend_points("buyer", -5, "test", creator_id=self.creator_id)

    def test_concurrent_spend_points_cannot_overspend(self):
        self.set_balance("buyer", 25)
        results, errors = _run_together(lambda i: app.spend_points("buyer", 25, "test", creator_id=self.creator_id))
        self.assertEqual(errors, [None] * THREADS)
        self.assertEqual(results.count(0), 1, "exactly one purchase succeeds")
        self.assertEqual(results.count(None), THREADS - 1)
        self.assertEqual(self.balance("buyer"), 0)


class ChatSpendCommandsTestCase(unittest.TestCase):
    """The real chat commands that used to check the balance and then
    spend: !redeem (shop) and !powerattack (boss). Tenant zero's bucket is
    used because that's what chat() resolves to without a creator handle;
    each test uses its own throwaway viewer and removes it afterwards.

    Per-viewer command cooldowns are set to 0 here (creators can configure
    that). With a cooldown on, the same command from the same viewer is
    mostly stopped by the cooldown -- but that check is itself unlocked, and
    it never stops two DIFFERENT spend commands overlapping, which the
    mixed test covers with cooldowns on."""

    def setUp(self):
        self.viewer = f"fxlock{uuid.uuid4().hex[:8]}"
        self.key = app.viewer_key(self.viewer)
        self.economy = app._creator_economy_v1(app._tenant_zero_id())
        self._orig_balances = self.economy["balances"]
        self.economy["balances"] = _SlowBalances(self._orig_balances)
        self._orig_boss = json.loads(json.dumps(app.boss_battle))
        self._orig_cooldowns = json.loads(json.dumps(app.cooldown_settings))
        app.cooldown_settings["!redeem"] = 0
        app.cooldown_settings["!powerattack"] = 0
        app.boss_battle.update({"active": True, "name": "Test Boss", "hp": 10_000_000, "max_hp": 10_000_000})

    def tearDown(self):
        restored = dict(self.economy["balances"])
        restored.pop(self.key, None)
        self._orig_balances.clear()
        self._orig_balances.update(restored)
        self.economy["balances"] = self._orig_balances
        app.boss_battle.clear()
        app.boss_battle.update(self._orig_boss)
        app.cooldown_settings.clear()
        app.cooldown_settings.update(self._orig_cooldowns)

    def _set(self, amount):
        dict.__setitem__(self.economy["balances"], self.key, amount)

    def _get(self):
        return int(dict.get(self.economy["balances"], self.key, 0))

    def _chat_one(self, message):
        # Pin randomness: power attack reward = 0, so balances are exact.
        with mock.patch.object(app, "current_event_multiplier", return_value=1), \
                mock.patch.object(app.random, "randint", return_value=0):
            return app.chat(message=message, username=self.viewer, dedupe_key=f"test-{uuid.uuid4().hex}")

    def _chat_together(self, messages):
        return _run_together(lambda i: self._chat_one(messages[i]), n=len(messages))

    def test_concurrent_redeem_cannot_overspend(self):
        cost = int(app.reward_shop["hug"]["cost"])
        self._set(cost)
        results, errors = self._chat_together(["!redeem hug"] * THREADS)
        self.assertEqual(errors, [None] * THREADS)
        refused = [r for r in results if "you need" in r["response"]]
        self.assertEqual(len(refused), THREADS - 1, "only one hug is paid for")
        self.assertEqual(self._get(), 0)

    def test_concurrent_powerattack_cannot_overspend(self):
        self._set(25)
        results, errors = self._chat_together(["!powerattack"] * THREADS)
        self.assertEqual(errors, [None] * THREADS)
        refused = [r for r in results if "power attack costs" in r["response"]]
        self.assertEqual(len(refused), THREADS - 1, "one 25-coin attack from 25 coins")
        self.assertEqual(self._get(), 0, "reward randint is pinned to 0, so the balance is exactly cost-spent")

    def test_different_spend_commands_overlap_even_with_cooldowns_on(self):
        # Cooldowns are per command, so these two never block each other.
        # 25 coins buys one power attack OR one 25-coin shop item, not both.
        app.cooldown_settings["!redeem"] = 15
        app.cooldown_settings["!powerattack"] = 15
        item = next(name for name, r in app.reward_shop.items()
                    if int(r.get("cost", 0)) == 25 and name != "mysterybox")
        self._set(25)
        results, errors = self._chat_together(["!powerattack", f"!redeem {item}"])
        self.assertEqual(errors, [None, None])
        refused = [r for r in results if "you need" in r["response"] or "power attack costs" in r["response"]]
        self.assertEqual(len(refused), 1, f"only one of the two purchases goes through: {results}")
        self.assertEqual(self._get(), 0)


@unittest.skipUnless(DATABASE_CONFIGURED, SKIP_REASON)
class CasinoConversionConcurrencyTestCase(_EconomyFixture):
    """providers/promo.py: FoxCoin -> PROMO (deposit) and PROMO -> FoxCoin
    (withdraw). Both FoxCoin legs go through the idempotent debit/credit,
    which now hold FOXCOIN_LOCK; promo.py itself does no FoxCoin writes."""

    RATE = 10

    def setUp(self):
        super().setUp()
        casino_config.set_config(self.creator_id, foxcoins_per_promo=self.RATE, daily_promo_limit=1_000_000)
        self.provider = promo.PromoProvider()
        self.user = "casinoracer"

    def tearDown(self):
        with cl._connect() as connection:
            cl._ensure_schema(connection)
            for table in (cl.TABLE_LEDGER, cl.TABLE_BALANCES, promo.TABLE_ATTEMPTS, casino_config.TABLE_CONFIG):
                connection.execute(f"DELETE FROM {table} WHERE creator_id = %s", (self.creator_id,))
        super().tearDown()

    def promo_balance(self):
        return cl.get_balance(self.creator_id, self.user, promo.CURRENCY_PROMO)

    def test_concurrent_conversions_cannot_spend_the_same_coins_twice(self):
        # Coins for exactly one 10-PROMO conversion; every thread tries one.
        # Reads are slowed enough that all threads' debits overlap even
        # though each first does its own Postgres claim.
        self.economy["balances"].delay = 0.4
        self.set_balance(self.user, 10 * self.RATE)

        def convert(i):
            return self.provider.deposit(self.creator_id, self.user, 10, idempotency_key=f"{self.creator_id}:conv:{i}")

        results, errors = _run_together(convert)
        succeeded = [r for r in results if r is not None]
        self.assertEqual(len(succeeded), 1, f"one conversion from coins for one; errors={errors}")
        self.assertTrue(all(isinstance(e, app.InsufficientFoxCoins) for e in errors if e is not None))
        self.assertEqual(self.balance(self.user), 0)
        self.assertEqual(self.promo_balance(), 10, "PROMO minted exactly once")

    def test_concurrent_cashout_same_key_credits_foxcoins_once(self):
        self.set_balance(self.user, 10 * self.RATE)
        self.provider.deposit(self.creator_id, self.user, 10, idempotency_key=f"{self.creator_id}:seed")
        self.assertEqual(self.balance(self.user), 0)
        self.economy["balances"].delay = 0.4

        def cashout(i):
            return self.provider.withdraw(self.creator_id, self.user, 10, idempotency_key=f"{self.creator_id}:out")

        results, errors = _run_together(cashout)
        # A same-key replay that reaches casino_ledger.debit while the first
        # call's debit is still committing can get InsufficientFunds instead
        # of the replayed entry (the ledger checks the key before taking the
        # row lock). No value moves for that thread; it's a ledger quirk,
        # not a FoxCoin one. What this test is about is the FoxCoin side.
        self.assertTrue(all(isinstance(e, cl.InsufficientFunds) for e in errors if e is not None), errors)
        self.assertTrue(any(r is not None for r in results))
        self.assertEqual(self.promo_balance(), 0, "PROMO debited once")
        self.assertEqual(self.balance(self.user), 10 * self.RATE, "FoxCoins credited once, not once per thread")
        cashout_rows = [t for t in self.economy["transactions"] if t["reason"] == "casino_promo_cashout"]
        self.assertEqual(len(cashout_rows), 1, "one FoxCoin credit for the one cashout")
        self.assertEqual({r["foxcoin_balance"] for r in results if r is not None}, {10 * self.RATE})

    def test_cashouts_racing_chat_rewards_lose_nothing(self):
        self.set_balance(self.user, 30 * self.RATE)
        self.provider.deposit(self.creator_id, self.user, 30, idempotency_key=f"{self.creator_id}:seed")
        self.economy["balances"].delay = 0.2

        def work(i):
            if i % 2:
                return self.provider.withdraw(self.creator_id, self.user, 10, idempotency_key=f"{self.creator_id}:out:{i}")
            return app.add_points(self.user, 1, "chat activity", creator_id=self.creator_id)

        _results, errors = _run_together(work)
        self.assertEqual([e for e in errors if e is not None], [])
        cashouts = THREADS // 2
        self.assertEqual(self.balance(self.user), cashouts * 10 * self.RATE + (THREADS - cashouts))


class _FakeDataPath:
    """Stands in for data/foxbot_data.json: records writes in memory (no
    file, no Postgres). `hold_first_write` makes the first writer wait for
    a later writer to finish (up to 1s) before it writes."""

    name = "foxbot_data.json"

    def __init__(self):
        self.writes = []
        self.text = None
        self.hold_first_write = False
        self.second_write_done = threading.Event()

    def exists(self):
        return self.text is not None

    def read_text(self, encoding="utf-8"):
        return self.text

    def write_text(self, data, encoding="utf-8"):
        first = not self.writes and self.hold_first_write and not getattr(self, "_holding", False)
        if first:
            self._holding = True
            self.second_write_done.wait(timeout=1.0)
        self.writes.append(data)
        self.text = data
        if not first and self.hold_first_write:
            self.second_write_done.set()


class SaveOrderingTestCase(_EconomyFixture):
    def setUp(self):
        super().setUp()
        self.economy["balances"] = {}
        self.path = _FakeDataPath()
        self._patches = [
            mock.patch.object(app, "_foxbot_storage_path_v1", lambda *a, **k: self.path),
            mock.patch.object(app, "_foxbot_hydration_failed_v1", return_value=False),
            mock.patch.object(app, "_foxbot_last_saved_snapshot_json_v1", None),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        super().tearDown()

    def _saved_balance(self, name):
        data = json.loads(self.path.text)
        return data["foxcoin_economy"]["by_creator"][self.creator_id]["balances"].get(app.viewer_key(name))

    def test_older_snapshot_never_overwrites_newer(self):
        """Save A copies the state, then stalls before writing; save B
        copies a newer state and writes it. A must not then land its older
        copy on top."""
        app.add_points("saver", 1, "test", creator_id=self.creator_id)
        self.path.hold_first_write = True
        older = threading.Thread(target=app.save_persistent_data, daemon=True)
        older.start()
        time.sleep(0.2)  # A has copied balance=1 and is stalled in its write
        app.add_points("saver", 1, "test", creator_id=self.creator_id)
        newer = threading.Thread(target=app.save_persistent_data, daemon=True)
        newer.start()
        older.join(timeout=10)
        newer.join(timeout=10)
        self.assertFalse(older.is_alive() or newer.is_alive())
        self.assertEqual(self._saved_balance("saver"), 2, "the last write on disk is the newest state")

    def test_save_while_balances_change_never_fails(self):
        # Production runs Python 3.12 (.python-version), where json.dumps
        # with indent=2 uses the pure-Python encoder, which other threads
        # can interrupt -- so a writer can change the dict mid-copy. Python
        # 3.13+ does indent in C (no interruption), which would hide the
        # race, so force the 3.12 path. A large economy makes it slow
        # enough for writers to land mid-copy.
        for n in range(5000):
            dict.__setitem__(self.economy["balances"], f"seed{n}", n)
        stop = threading.Event()

        def writer():
            n = 0
            while not stop.is_set():
                app.add_points(f"newviewer{n}", 1, "test", creator_id=self.creator_id)
                n += 1

        t = threading.Thread(target=writer, daemon=True)
        encoder_patch = mock.patch.object(json.encoder, "c_make_encoder", None)
        encoder_patch.start()
        self.addCleanup(encoder_patch.stop)
        old_interval = sys.getswitchinterval()
        sys.setswitchinterval(1e-5)
        out = io.StringIO()
        try:
            t.start()
            with redirect_stdout(out):
                results = [app.save_persistent_data() for _ in range(8)]
        finally:
            stop.set()
            t.join(timeout=10)
            sys.setswitchinterval(old_interval)
        self.assertNotIn("save failed", out.getvalue())
        self.assertEqual(results, [True] * 8)

    def test_load_marks_loaded_state_as_saved(self):
        app.add_points("loader", 4, "test", creator_id=self.creator_id)
        self.assertTrue(app.save_persistent_data())
        writes = len(self.path.writes)
        with redirect_stdout(io.StringIO()):
            self.assertTrue(app.load_persistent_data())
        self.assertTrue(app.save_persistent_data())
        self.assertEqual(len(self.path.writes), writes, "nothing changed since the load, so nothing is rewritten")
        self.assertEqual(app.get_balance("loader", creator_id=self.creator_id), 4)


class _TrackedLock:
    """Wraps a Lock/RLock and records, per acquisition, which tracked locks
    the acquiring thread already held."""

    _local = threading.local()

    def __init__(self, inner, name, log):
        self.inner, self.name, self.log = inner, name, log

    @classmethod
    def held(cls):
        if not hasattr(cls._local, "stack"):
            cls._local.stack = []
        return cls._local.stack

    def acquire(self, *args, **kwargs):
        self.log.append((self.name, tuple(self.held())))
        ok = self.inner.acquire(*args, **kwargs)
        if ok:
            self.held().append(self.name)
        return ok

    def release(self):
        stack = self.held()
        stack.reverse()
        stack.remove(self.name)
        stack.reverse()
        self.inner.release()

    def locked(self):
        return self.inner.locked() if hasattr(self.inner, "locked") else bool(self.held())

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False


def _nesting_violations(log):
    return [(name, held) for name, held in log
            if (name == "foxcoin" and "refresh" in held) or (name == "refresh" and "foxcoin" in held)]


class LockOrderingTestCase(unittest.TestCase):
    """FOXCOIN_LOCK and TENANT_ZERO_REFRESH_LOCK must never nest, in either
    order. Both are swapped for recording wrappers, then the real refresh
    loop (fake Blaze, real token save) and the real economy + save/load
    paths run; the recording must show both locks taken and never one
    inside the other."""

    def setUp(self):
        self.log = []
        refresh = _TrackedLock(blaze_tokens.TENANT_ZERO_REFRESH_LOCK, "refresh", self.log)
        foxcoin = _TrackedLock(app.FOXCOIN_LOCK, "foxcoin", self.log)
        self.tmp = tempfile.mkdtemp()
        data_path = Path(self.tmp) / "foxbot_data.json"
        self._patches = [
            mock.patch.object(app, "_FOXBOT_TENANT_ZERO_REFRESH_LOCK_V1", refresh),
            mock.patch.object(blaze_tokens, "TENANT_ZERO_REFRESH_LOCK", refresh),
            mock.patch.object(app, "FOXCOIN_LOCK", foxcoin),
        ]
        self._data_path = data_path
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_detector_flags_nesting(self):
        with app._FOXBOT_TENANT_ZERO_REFRESH_LOCK_V1:
            with app.FOXCOIN_LOCK:
                pass
        self.assertEqual(_nesting_violations(self.log), [("foxcoin", ("refresh",))])

    def test_refresh_and_economy_never_nest_locks(self):
        from tests.test_tenant_zero_refresh_loop import TenantZeroRefreshLoopTestCase

        refresh_case = TenantZeroRefreshLoopTestCase("test_loop_refreshes_on_schedule_without_crashing")
        refresh_case.setUp()
        try:
            _clock, blaze, _out = refresh_case._run_worker(cycles=2)
        finally:
            refresh_case.tearDown()
        self.assertEqual(blaze.issued, 2, "the real refresh path ran")

        creator_id = f"test-fxlock-{uuid.uuid4().hex[:12]}"
        try:
            with mock.patch.object(app, "_foxbot_storage_path_v1", lambda *a, **k: self._data_path), \
                    mock.patch.object(app, "_foxbot_hydration_failed_v1", return_value=False), \
                    mock.patch.object(app, "_foxbot_last_saved_snapshot_json_v1", None), \
                    redirect_stdout(io.StringIO()):
                app.add_points("orderviewer", 50, "test", creator_id=creator_id)
                app.spend_points("orderviewer", 10, "test", creator_id=creator_id)
                app.debit_foxcoins_idempotent("orderviewer", 5, "test:order:d", creator_id=creator_id)
                app.credit_foxcoins_idempotent("orderviewer", 5, "test:order:c", creator_id=creator_id)
                self.assertTrue(app.save_persistent_data())
                self.assertTrue(app.load_persistent_data())
        finally:
            app.foxcoin_economy["by_creator"].pop(creator_id, None)

        names = {name for name, _held in self.log}
        self.assertEqual(names, {"refresh", "foxcoin"}, "both locks were exercised")
        self.assertEqual(_nesting_violations(self.log), [])

    def test_refresh_code_does_not_reference_economy(self):
        """Static backstop: the functions that run under the refresh lock
        don't mention FOXCOIN_LOCK or any FoxCoin writer/saver."""
        import inspect

        forbidden = ("FOXCOIN_LOCK", "add_points", "spend_points", "foxcoins_idempotent", "save_persistent_data")
        for fn in (app._foxbot_blaze_oauth_refresh_tenant_zero_v1,
                   app._foxbot_blaze_oauth_refresh_tenant_zero_locked_v1,
                   app._foxbot_blaze_oauth_save_tokens_v1):
            source = inspect.getsource(fn)
            for name in forbidden:
                with self.subTest(fn=fn.__name__, name=name):
                    self.assertNotIn(name, source)


if __name__ == "__main__":
    unittest.main()

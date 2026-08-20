"""Command-layer tests for Casino Phase 5/6: !convert, !casinoflip (wager),
!casino, !roulette wired into app.chat().

Two test cases:
  - CasinoCommandsDormancyTestCase: no DATABASE_URL required. Proves the
    FOXBOT_CASINO_ENABLED platform flag alone makes every casino command
    silent and the rest of chat() (including the pre-existing free
    !coinflip arcade command and !foxhelp's text) byte-identical to
    before Phase 5. This is the deploy-dormant guarantee -- it must hold
    with zero DB configured at all.
  - CasinoCommandsFunctionalTestCase: real Postgres via DATABASE_URL, same
    convention as test_casino_ledger.py / test_promo_provider.py /
    test_casino_rounds.py. Proves the commands actually work end-to-end
    through app.chat() once both gates (platform flag + per-creator
    casino_enabled) are on, that command-spam idempotency holds (the
    dedupe_key -> idempotency_key/round_id derivation), and that hostile/
    malformed input is rejected with a friendly reply, never a crash.

Run with:
    python -m unittest tests.test_casino_commands -v
"""

import os
import sys
import unittest
import uuid
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402
import games.coinflip as coinflip  # noqa: E402
import games.roulette as roulette  # noqa: E402
import services.casino_config as casino_config  # noqa: E402
import services.casino_ledger as cl  # noqa: E402
import services.casino_rng as casino_rng  # noqa: E402
import services.casino_rounds as cr  # noqa: E402


DATABASE_CONFIGURED = bool(os.getenv("DATABASE_URL"))
SKIP_REASON = (
    "DATABASE_URL not set -- the functional/idempotency proofs need a "
    "real Postgres database (a throwaway/dev one, not production)."
)


class _FixedChoiceProvider(casino_rng.RNGProvider):
    def __init__(self, value):
        self.value = value

    def roll(self, minimum, maximum):
        return minimum

    def choice(self, seq):
        return self.value


class _FixedRollProvider(casino_rng.RNGProvider):
    def __init__(self, value):
        self.value = value

    def roll(self, minimum, maximum):
        return self.value

    def choice(self, seq):
        return seq[0]


class CasinoCommandsDormancyTestCase(unittest.TestCase):
    """No DATABASE_URL required -- the whole point is that dormancy holds
    without one. If any of these needed a DB, dormancy wouldn't be real."""

    def setUp(self):
        self._original_flag = os.environ.pop("FOXBOT_CASINO_ENABLED", None)

    def tearDown(self):
        if self._original_flag is None:
            os.environ.pop("FOXBOT_CASINO_ENABLED", None)
        else:
            os.environ["FOXBOT_CASINO_ENABLED"] = self._original_flag

    def test_flag_off_all_casino_commands_silent(self):
        self.assertFalse(app._foxbot_casino_enabled_v1())
        for message in ("!convert 5", "!convert", "!casinoflip heads 10", "!casino", "!roulette red 10"):
            result = app.chat(message=message, username="tester")
            self.assertEqual(result.get("response"), "", f"{message!r} must be silent with the flag off")

    def test_flag_off_foxhelp_unchanged(self):
        reply = app.chat(message="!foxhelp", username="tester").get("response", "")
        self.assertNotIn("!convert", reply)
        self.assertNotIn("!casino", reply)
        self.assertNotIn("!roulette", reply)

    def test_flag_off_existing_free_coinflip_unaffected(self):
        reply = app.chat(message="!coinflip", username="tester").get("response", "")
        self.assertTrue(
            "FoxBot flips a coin" in reply or "cooldown" in reply,
            f"bare !coinflip's original arcade branch must still run, got {reply!r}",
        )

    def test_flag_on_but_no_database_fails_closed_with_friendly_reply(self):
        os.environ["FOXBOT_CASINO_ENABLED"] = "true"
        # DATABASE_URL is genuinely unset in THIS test case by construction
        # (skip-gated functional tests own the DB-configured path) -- a
        # command must reply gracefully, not crash chat() entirely.
        if DATABASE_CONFIGURED:
            self.skipTest("DATABASE_URL is set in this environment; this proof needs it unset.")
        reply = app.chat(message="!casino", username="tester").get("response", "")
        self.assertTrue(reply)
        self.assertNotIn("Traceback", reply)


@unittest.skipUnless(DATABASE_CONFIGURED, SKIP_REASON)
class CasinoCommandsFunctionalTestCase(unittest.TestCase):
    def setUp(self):
        self.creator_id = f"test-commands-{uuid.uuid4().hex[:12]}"
        self.username = "TestViewer"
        self.user_id = app.viewer_key(self.username)

        self._original_flag = os.environ.get("FOXBOT_CASINO_ENABLED")
        os.environ["FOXBOT_CASINO_ENABLED"] = "true"

        casino_config.set_config(
            self.creator_id, foxcoins_per_promo=10, daily_promo_limit=5000, casino_enabled=True,
        )

        self._resolve_patch = mock.patch.object(
            app, "_foxbot_resolve_creator_id_v1", return_value=self.creator_id,
        )
        self._resolve_patch.start()

        self._original_rng_provider = casino_rng.get_provider()

    def tearDown(self):
        self._resolve_patch.stop()
        casino_rng.set_provider(self._original_rng_provider)

        if self._original_flag is None:
            os.environ.pop("FOXBOT_CASINO_ENABLED", None)
        else:
            os.environ["FOXBOT_CASINO_ENABLED"] = self._original_flag

        with cl._connect() as connection:
            cl._ensure_schema(connection)
            cr._ensure_schema(connection)
            casino_config._ensure_schema(connection)
            connection.execute(f"DELETE FROM {cl.TABLE_LEDGER} WHERE creator_id = %s", (self.creator_id,))
            connection.execute(f"DELETE FROM {cl.TABLE_BALANCES} WHERE creator_id = %s", (self.creator_id,))
            connection.execute(f"DELETE FROM {cr.TABLE_ROUNDS} WHERE creator_id = %s", (self.creator_id,))
            connection.execute(f"DELETE FROM {casino_config.TABLE_GAME_CONFIG} WHERE creator_id = %s", (self.creator_id,))
            connection.execute(f"DELETE FROM {casino_config.TABLE_CONFIG} WHERE creator_id = %s", (self.creator_id,))

    def _seed_foxcoins(self, amount):
        app.add_points(self.username, amount, "test_seed", creator_id=self.creator_id)

    def _promo_balance(self):
        return cl.get_balance(self.creator_id, self.user_id, cr.CURRENCY_PROMO)

    # ------------------------------------------------------------------
    def test_convert_command_works(self):
        self._seed_foxcoins(1000)
        reply = app.chat(message="!convert 5", username=self.username).get("response", "")

        self.assertIn("converted", reply.lower())
        self.assertIn("50", reply)  # foxcoin cost = 5 promo * rate 10
        self.assertEqual(self._promo_balance(), 5)
        self.assertEqual(app.get_balance(self.username, creator_id=self.creator_id), 1000 - 50)

    def test_convert_idempotent_on_repeated_dedupe_key(self):
        self._seed_foxcoins(1000)
        key = f"dedupe-{uuid.uuid4().hex[:8]}"

        replies = [
            app.chat(message="!convert 5", username=self.username, dedupe_key=key).get("response", "")
            for _ in range(3)
        ]

        self.assertTrue(all(replies))
        self.assertEqual(self._promo_balance(), 5, "same dedupe_key must not double-convert")
        self.assertEqual(app.get_balance(self.username, creator_id=self.creator_id), 1000 - 50)

    # ------------------------------------------------------------------
    def test_casinoflip_command_win(self):
        self._seed_foxcoins(1000)
        app.chat(message="!convert 20", username=self.username)
        casino_rng.set_provider(_FixedChoiceProvider("heads"))

        reply = app.chat(message="!casinoflip heads 10", username=self.username).get("response", "")

        self.assertIn("won", reply.lower())
        self.assertEqual(self._promo_balance(), 20 - 10 + 20)

    def test_casinoflip_command_loss(self):
        self._seed_foxcoins(1000)
        app.chat(message="!convert 20", username=self.username)
        casino_rng.set_provider(_FixedChoiceProvider("tails"))

        reply = app.chat(message="!casinoflip heads 10", username=self.username).get("response", "")

        self.assertIn("lost", reply.lower())
        self.assertEqual(self._promo_balance(), 20 - 10)

    def test_casinoflip_idempotent_on_repeated_dedupe_key(self):
        self._seed_foxcoins(1000)
        app.chat(message="!convert 20", username=self.username)
        casino_rng.set_provider(_FixedChoiceProvider("heads"))
        key = f"dedupe-{uuid.uuid4().hex[:8]}"

        replies = [
            app.chat(message="!casinoflip heads 10", username=self.username, dedupe_key=key).get("response", "")
            for _ in range(3)
        ]

        self.assertEqual(len(set(replies)), 1, "spamming the same message must return the identical reply")
        self.assertEqual(self._promo_balance(), 20 - 10 + 20, "same dedupe_key must not double-wager or double-pay")

    # ------------------------------------------------------------------
    def test_roulette_command_straight_number_win(self):
        self._seed_foxcoins(1000)
        app.chat(message="!convert 20", username=self.username)
        casino_rng.set_provider(_FixedRollProvider(17))

        reply = app.chat(message="!roulette number 17 10", username=self.username).get("response", "")

        self.assertIn("won", reply.lower())
        self.assertIn("17", reply)
        self.assertEqual(self._promo_balance(), 20 - 10 + 360)

    def test_roulette_command_color_bet_loss(self):
        self._seed_foxcoins(1000)
        app.chat(message="!convert 20", username=self.username)
        casino_rng.set_provider(_FixedRollProvider(2))  # black

        reply = app.chat(message="!roulette red 10", username=self.username).get("response", "")

        self.assertIn("lost", reply.lower())
        self.assertEqual(self._promo_balance(), 20 - 10)

    def test_roulette_command_dozen_win(self):
        self._seed_foxcoins(1000)
        app.chat(message="!convert 20", username=self.username)
        casino_rng.set_provider(_FixedRollProvider(15))

        reply = app.chat(message="!roulette dozen 2 10", username=self.username).get("response", "")

        self.assertIn("won", reply.lower())
        self.assertEqual(self._promo_balance(), 20 - 10 + 30)

    def test_roulette_idempotent_on_repeated_dedupe_key(self):
        self._seed_foxcoins(1000)
        app.chat(message="!convert 20", username=self.username)
        casino_rng.set_provider(_FixedRollProvider(1))  # red
        key = f"dedupe-{uuid.uuid4().hex[:8]}"

        replies = [
            app.chat(message="!roulette red 10", username=self.username, dedupe_key=key).get("response", "")
            for _ in range(3)
        ]

        self.assertEqual(len(set(replies)), 1, "spamming the same message must return the identical reply")
        self.assertEqual(self._promo_balance(), 20 - 10 + 20, "same dedupe_key must not double-wager or double-pay")

    def test_roulette_resume_after_loss_never_rerolls(self):
        """Command-layer version of the anti-exploit proof: retrying the
        SAME chat message (same dedupe_key) after a loss, with the RNG
        rearmed to a winning number, must still reply with the original
        loss -- proving app.py's round_id derivation and games/roulette.py
        both honor play_round()'s persisted-outcome guarantee end-to-end."""
        self._seed_foxcoins(1000)
        app.chat(message="!convert 20", username=self.username)
        key = f"dedupe-{uuid.uuid4().hex[:8]}"

        casino_rng.set_provider(_FixedRollProvider(2))  # bet red, pocket 2 is black -> loss
        first = app.chat(message="!roulette red 10", username=self.username, dedupe_key=key).get("response", "")
        self.assertIn("lost", first.lower())

        casino_rng.set_provider(_FixedRollProvider(1))  # rearmed to a red-winning pocket
        second = app.chat(message="!roulette red 10", username=self.username, dedupe_key=key).get("response", "")

        self.assertEqual(first, second, "retry must return the identical, already-settled reply")
        self.assertIn("lost", second.lower())
        self.assertEqual(self._promo_balance(), 20 - 10, "no phantom payout from a re-roll on retry")

    # ------------------------------------------------------------------
    def test_casino_command_shows_balance(self):
        self._seed_foxcoins(1000)
        app.chat(message="!convert 7", username=self.username)

        reply = app.chat(message="!casino", username=self.username).get("response", "")
        self.assertIn("7", reply)

    # ------------------------------------------------------------------
    def test_invalid_convert_input_rejected_gracefully(self):
        for bad in ("!convert", "!convert abc", "!convert -5", "!convert 0"):
            reply = app.chat(message=bad, username=self.username).get("response", "")
            self.assertTrue(reply, f"{bad!r} should get a helpful reply, not silence")
            self.assertNotIn("Traceback", reply)
        self.assertEqual(self._promo_balance(), 0, "no rejected input should move any money")

    def test_invalid_casinoflip_input_rejected_gracefully(self):
        self._seed_foxcoins(1000)
        app.chat(message="!convert 50", username=self.username)
        balance_before = self._promo_balance()

        for bad in (
            "!casinoflip heads abc", "!casinoflip sideways 10", "!casinoflip heads -5",
            "!casinoflip heads 0", "!casinoflip heads",
        ):
            reply = app.chat(message=bad, username=self.username).get("response", "")
            self.assertTrue(reply, f"{bad!r} should get a helpful reply, not silence")
            self.assertNotIn("Traceback", reply)

        self.assertEqual(self._promo_balance(), balance_before, "no rejected wager should move any promo")

    def test_invalid_roulette_input_rejected_gracefully(self):
        self._seed_foxcoins(1000)
        app.chat(message="!convert 50", username=self.username)
        balance_before = self._promo_balance()

        for bad in (
            "!roulette sideways 10", "!roulette number 37 10", "!roulette number -1 10",
            "!roulette number 17 abc", "!roulette number 17 -5", "!roulette number 17 0",
            "!roulette dozen 4 10", "!roulette column 0 10", "!roulette red abc",
            "!roulette number 17",
        ):
            reply = app.chat(message=bad, username=self.username).get("response", "")
            self.assertTrue(reply, f"{bad!r} should get a helpful reply, not silence")
            self.assertNotIn("Traceback", reply)

        self.assertEqual(self._promo_balance(), balance_before, "no rejected bet should move any promo")

    def test_insufficient_foxcoins_for_convert_friendly_message(self):
        reply = app.chat(message="!convert 5", username=self.username).get("response", "")
        self.assertIn("need", reply.lower())
        self.assertEqual(self._promo_balance(), 0)

    def test_insufficient_promo_for_wager_friendly_message(self):
        reply = app.chat(message="!casinoflip heads 10", username=self.username).get("response", "")
        self.assertIn("enough", reply.lower())

    # ------------------------------------------------------------------
    def test_creator_not_opted_in_blocks_commands(self):
        casino_config.set_config(self.creator_id, casino_enabled=False)
        self._seed_foxcoins(1000)

        for message in ("!convert 5", "!casinoflip heads 10", "!casino", "!roulette red 10"):
            reply = app.chat(message=message, username=self.username).get("response", "")
            self.assertIn("isn't enabled", reply)

        self.assertEqual(app.get_balance(self.username, creator_id=self.creator_id), 1000, "no debit while opted out")

    def test_game_disabled_blocks_wager_but_not_convert(self):
        casino_config.set_game_config(self.creator_id, coinflip.GAME_ID, enabled=False)
        self._seed_foxcoins(1000)

        convert_reply = app.chat(message="!convert 5", username=self.username).get("response", "")
        self.assertIn("converted", convert_reply.lower())

        flip_reply = app.chat(message="!casinoflip heads 1", username=self.username).get("response", "")
        self.assertIn("disabled", flip_reply.lower())

    def test_roulette_disabled_blocks_wager_but_not_coinflip(self):
        casino_config.set_game_config(self.creator_id, roulette.GAME_ID, enabled=False)
        self._seed_foxcoins(1000)
        app.chat(message="!convert 50", username=self.username)
        casino_rng.set_provider(_FixedChoiceProvider("heads"))

        roulette_reply = app.chat(message="!roulette red 10", username=self.username).get("response", "")
        self.assertIn("disabled", roulette_reply.lower())

        flip_reply = app.chat(message="!casinoflip heads 1", username=self.username).get("response", "")
        self.assertNotIn("disabled", flip_reply.lower(), "disabling roulette must not disable coinflip")
        self.assertIn("won", flip_reply.lower())


if __name__ == "__main__":
    unittest.main()

"""Proof for FoxBot Free For Everyone v1 (System 1 -- creator_access.py):

- A creator with no prior grant/trial record gets has_access immediately.
- A previously-set expiry, even a long-expired one, no longer blocks access.
- The one remaining kill switch (block_creator/unblock_creator, backed by
  access_revoked) still works, and survives a later !joinfox/!access/!verify
  re-check (start_trial/mark_subscriber/verify_current_subscription no
  longer clear access_revoked) -- same deny-list-survives-re-check property
  proven for bot-connect's F-access design.

Uses an isolated temp file (FOXBOT_CONNECTED_CREATORS_FILE) so this never
touches the real data/connected_creators.json, and a real local Postgres
(DATABASE_URL) so storage_paths.py's Neon-hydration path runs against an
actual (throwaway/dev) database instead of erroring -- skipped, not faked,
without one, matching this repo's existing test convention.

Run with:
    python -m unittest tests.test_creator_access_free_for_everyone -v
"""

import os
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATABASE_CONFIGURED = bool(os.getenv("DATABASE_URL"))
SKIP_REASON = (
    "DATABASE_URL not set -- these tests need a real Postgres database "
    "(a throwaway/dev one, not production) so storage_paths.py's Neon "
    "hydration path runs honestly instead of being mocked away."
)

# DATA_PATH is resolved once, at import time, from this env var -- must be
# set before services.creator_access is ever imported in this process.
_TEMP_DIR = tempfile.mkdtemp(prefix="foxbot_creator_access_test_")
os.environ["FOXBOT_CONNECTED_CREATORS_FILE"] = os.path.join(_TEMP_DIR, "connected_creators.json")

import services.creator_access as creator_access  # noqa: E402


@unittest.skipUnless(DATABASE_CONFIGURED, SKIP_REASON)
class FreeForEveryoneTestCase(unittest.TestCase):
    def _fresh_handle(self, prefix="fftest"):
        return f"{prefix}_{uuid.uuid4().hex[:12]}"

    # --- Open by default -----------------------------------------------

    def test_new_creator_with_no_record_gets_access_immediately(self):
        handle = self._fresh_handle("brandnew")
        access = creator_access.get_access(handle)
        self.assertTrue(access["has_access"])
        self.assertEqual(access["status"], "active")

    def test_access_snapshot_none_creator_has_access(self):
        # The exact shape returned for "no record exists at all".
        snapshot = creator_access.access_snapshot(None)
        self.assertTrue(snapshot["has_access"])
        self.assertEqual(snapshot["status"], "active")

    def test_expired_trial_no_longer_blocks(self):
        # A legacy record shaped exactly like the pre-change data (trial
        # ended weeks ago, never subscribed, never revoked) must now read
        # as full access -- access_snapshot() no longer looks at these
        # dates to decide has_access at all.
        legacy_creator = {
            "handle": "legacyhandle",
            "trial_started_at": "2026-07-01T00:00:00+00:00",
            "trial_ends_at": "2026-07-08T00:00:00+00:00",
            "access_revoked": False,
        }
        snapshot = creator_access.access_snapshot(legacy_creator)
        self.assertTrue(snapshot["has_access"])
        self.assertEqual(snapshot["status"], "active")
        # Historical dates are preserved for display, just not enforced.
        self.assertEqual(snapshot["trial_ends_at"], "2026-07-08T00:00:00+00:00")

    def test_never_started_trial_also_has_access(self):
        never_joined = {"handle": "neverjoined"}
        snapshot = creator_access.access_snapshot(never_joined)
        self.assertTrue(snapshot["has_access"])

    # --- Deny-list kill switch -------------------------------------------

    def test_block_creator_removes_access(self):
        handle = self._fresh_handle("blockme")
        self.assertTrue(creator_access.get_access(handle)["has_access"])

        result = creator_access.block_creator(handle, reason="abuse test")
        self.assertTrue(result["ok"])
        self.assertFalse(result["has_access"])
        self.assertEqual(result["status"], "blocked")

        self.assertFalse(creator_access.get_access(handle)["has_access"])

    def test_unblock_restores_access(self):
        handle = self._fresh_handle("unblockme")
        creator_access.block_creator(handle)
        self.assertFalse(creator_access.get_access(handle)["has_access"])

        result = creator_access.unblock_creator(handle)
        self.assertTrue(result["ok"])
        self.assertTrue(result["has_access"])

        self.assertTrue(creator_access.get_access(handle)["has_access"])

    def test_block_survives_joinfox_recheck(self):
        # The exact regression this change could have introduced: start_trial
        # used to reset access_revoked=False on every call. A blocked
        # creator re-typing !joinfox must NOT silently unblock themselves.
        handle = self._fresh_handle("blockthenjoin")
        creator_access.block_creator(handle, reason="abuse test")
        self.assertFalse(creator_access.get_access(handle)["has_access"])

        creator_access.start_trial(handle, display_name="Blocked Creator")
        self.assertFalse(
            creator_access.get_access(handle)["has_access"],
            "start_trial() re-enabled a blocked creator's access",
        )

    def test_block_survives_mark_subscriber_recheck(self):
        handle = self._fresh_handle("blockthensub")
        creator_access.block_creator(handle, reason="abuse test")

        creator_access.mark_subscriber(handle)
        self.assertFalse(
            creator_access.get_access(handle)["has_access"],
            "mark_subscriber() re-enabled a blocked creator's access",
        )

    def test_block_survives_verify_current_subscription_recheck(self):
        handle = self._fresh_handle("blockthenverify")
        creator_access.block_creator(handle, reason="abuse test")

        creator_access.verify_current_subscription(handle)
        self.assertFalse(
            creator_access.get_access(handle)["has_access"],
            "verify_current_subscription() re-enabled a blocked creator's access",
        )

    def test_unblock_unknown_handle_reports_not_found(self):
        handle = self._fresh_handle("neverexisted")
        result = creator_access.unblock_creator(handle)
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()

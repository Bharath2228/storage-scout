import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import auth


class AuthStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        base = Path(self.temp_dir.name)
        self.store_path = base / "auth_store.json"
        self.lockout_path = base / "auth_lockout.json"
        self.iterations_patch = mock.patch.object(auth, "PBKDF2_ITERATIONS", 100)
        self.iterations_patch.start()
        self.addCleanup(self.iterations_patch.stop)

    def make_store(self):
        return auth.AuthStore(self.store_path, self.lockout_path)

    def test_add_verify_remove_behavior_remains_compatible(self):
        store = self.make_store()
        store.add_user("Alice", "pass")
        store.add_user("Bob", "word")

        self.assertTrue(store.verify(" alice ", "pass"))
        self.assertFalse(store.verify("Alice", "wrong"))
        self.assertEqual(store.canonical_username("ALICE"), "Alice")
        self.assertEqual(store.list_usernames(), ["Alice", "Bob"])

        store.remove_user("bob")
        self.assertEqual(store.list_usernames(), ["Alice"])
        with self.assertRaises(ValueError):
            store.remove_user("Alice")

    def test_lockout_persists_and_is_scoped_to_attempted_username(self):
        store = self.make_store()
        store.add_user("Alice", "pass")
        store.add_user("Bob", "word")

        for _ in range(auth.MAX_FAILED_ATTEMPTS):
            self.assertFalse(store.verify("Alice", "wrong"))

        restarted_store = self.make_store()
        self.assertGreater(restarted_store.lockout_remaining("Alice"), 0)
        self.assertFalse(restarted_store.verify("Alice", "pass"))
        self.assertEqual(restarted_store.lockout_remaining("Bob"), 0)
        self.assertTrue(restarted_store.verify("Bob", "word"))

    def test_success_clears_persisted_failures(self):
        store = self.make_store()
        store.add_user("Alice", "pass")
        self.assertFalse(store.verify("Alice", "wrong"))
        self.assertTrue(self.lockout_path.exists())

        self.assertTrue(store.verify("Alice", "pass"))
        self.assertFalse(self.lockout_path.exists())

    def test_unknown_user_still_runs_password_hash(self):
        store = self.make_store()
        with mock.patch.object(
            auth,
            "_password_hash",
            wraps=auth._password_hash,
        ) as password_hash:
            self.assertFalse(store.verify("Missing", "wrong"))

        password_hash.assert_called_once_with("wrong", auth._DUMMY_SALT)

    def test_lockout_state_uses_raw_attempted_username_as_key(self):
        store = self.make_store()
        store.add_user("Alice", "pass")
        self.assertFalse(store.verify(" Alice ", "wrong"))

        with self.lockout_path.open("r", encoding="utf-8") as handle:
            attempts = json.load(handle)["attempts"]

        self.assertIn(" Alice ", attempts)
        self.assertNotIn("Alice", attempts)

    def test_default_paths_are_absolute_and_share_app_data_directory(self):
        self.assertTrue(os.path.isabs(auth.AUTH_STORE_PATH))
        self.assertEqual(Path(auth.AUTH_STORE_PATH).name, "auth_store.json")
        self.assertEqual(Path(auth.AUTH_STORE_PATH).parent.name, "data")
        self.assertEqual(
            Path(auth.AUTH_STORE_PATH).parent,
            Path(auth.DELETE_AUDIT_LOG_PATH).parent,
        )

    def test_legacy_file_copy_preserves_existing_data(self):
        source = Path(self.temp_dir.name) / "legacy.json"
        target = Path(self.temp_dir.name) / "app-data" / "auth_store.json"
        source.write_text('{"version": 1}', encoding="utf-8")

        auth._copy_legacy_file(str(source), str(target))

        self.assertEqual(target.read_text(encoding="utf-8"), '{"version": 1}')
        self.assertTrue(source.exists())

    def test_audit_log_format_is_unchanged(self):
        audit_path = Path(self.temp_dir.name) / "delete_audit.log"
        auth.append_delete_audit(
            "delete_denied",
            username="UNKNOWN",
            items=2,
            size=10,
            reason="invalid_credentials",
            attempted_username="Alice",
            path=audit_path,
        )

        line = audit_path.read_text(encoding="utf-8").strip()
        self.assertRegex(
            line,
            (
                r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}  user=UNKNOWN  "
                r"attempt=Alice  action=delete_denied  items=2  size=10  "
                r"reason=invalid_credentials$"
            ),
        )

    def test_windows_permissions_use_current_user_acl(self):
        with (
            mock.patch.object(auth.os, "name", "nt"),
            mock.patch.object(
                auth,
                "_windows_current_identity",
                return_value="DESKTOP\\Bharath",
            ),
            mock.patch.object(auth.subprocess, "run") as run,
        ):
            auth._restrict_file_permissions(r"C:\data\auth_store.json")

        args = run.call_args.args[0]
        self.assertEqual(args[0], "icacls")
        self.assertIn("/inheritance:r", args)
        self.assertIn("DESKTOP\\Bharath:(F)", args)


if __name__ == "__main__":
    unittest.main()

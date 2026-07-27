import base64
import getpass
import hashlib
import hmac
import json
import math
import os
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


def _app_data_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return base / "IBMS" / "Watchdog"


APP_DATA_DIR = _app_data_dir()
AUTH_STORE_PATH = str(APP_DATA_DIR / "auth_store.json")
AUTH_LOCKOUT_PATH = str(APP_DATA_DIR / "auth_lockout.json")
DELETE_AUDIT_LOG_PATH = str(APP_DATA_DIR / "delete_audit.log")
LEGACY_APP_DIR = Path(__file__).resolve().parent.parent
LEGACY_AUTH_STORE_PATH = str(LEGACY_APP_DIR / "auth_store.json")
LEGACY_DELETE_AUDIT_LOG_PATH = str(LEGACY_APP_DIR / "delete_audit.log")
PBKDF2_ITERATIONS = 200_000
MIN_PASSWORD_LENGTH = 4
MAX_FAILED_ATTEMPTS = 3
LOCKOUT_SECONDS = 30

_DUMMY_SALT = b"\0" * 16
_FILE_LOCK = threading.RLock()


@dataclass
class AuthorizedUser:
    username: str
    salt: bytes
    password_hash: bytes


def _password_hash(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256",
        str(password or "").encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
    )


def _windows_current_identity() -> str:
    try:
        result = subprocess.run(
            ["whoami"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        identity = result.stdout.strip()
        if result.returncode == 0 and identity:
            return identity
    except (OSError, subprocess.SubprocessError):
        pass

    username = getpass.getuser()
    domain = os.environ.get("USERDOMAIN", "")
    return f"{domain}\\{username}" if domain and "\\" not in username else username


def _restrict_file_permissions(path: str) -> None:
    try:
        if os.name == "nt":
            identity = _windows_current_identity()
            subprocess.run(
                [
                    "icacls",
                    os.fspath(path),
                    "/inheritance:r",
                    "/grant:r",
                    f"{identity}:(F)",
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            os.chmod(path, 0o600)
    except (OSError, subprocess.SubprocessError):
        pass


def _write_json_atomic(path: str, data: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(data, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
        temp_path = None
        _restrict_file_permissions(str(target))
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def _copy_legacy_file(source: str, target: str) -> None:
    if os.path.exists(target) or not os.path.isfile(source):
        return
    destination = Path(target)
    temp_path = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = handle.name
            with open(source, "rb") as source_handle:
                shutil.copyfileobj(source_handle, handle)
            handle.flush()
            os.fsync(handle.fileno())
        if not os.path.exists(destination):
            os.replace(temp_path, destination)
            temp_path = None
            _restrict_file_permissions(str(destination))
    except OSError:
        pass
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


class AuthStore:
    """Deterrence-grade local auth; pair with proper OS/NTFS permissions for real enforcement."""

    def __init__(self, path: str = AUTH_STORE_PATH, lockout_path: str | None = None):
        self.path = os.fspath(path)
        if os.path.abspath(self.path) == os.path.abspath(AUTH_STORE_PATH):
            with _FILE_LOCK:
                _copy_legacy_file(LEGACY_AUTH_STORE_PATH, self.path)
        if lockout_path is None:
            self.lockout_path = os.fspath(Path(self.path).with_name("auth_lockout.json"))
        else:
            self.lockout_path = os.fspath(lockout_path)
        self._users: dict[str, AuthorizedUser] = {}
        self._load()

    def _key(self, username: str) -> str:
        return str(username or "").strip().casefold()

    def _load(self) -> None:
        self._users = {}
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return

        if not isinstance(data, dict):
            return
        for entry in data.get("users", []):
            username = str(entry.get("username", "")).strip()
            if not username:
                continue
            try:
                salt = base64.b64decode(entry.get("salt", ""))
                password_hash = base64.b64decode(entry.get("password_hash", ""))
            except Exception:
                continue
            if salt and password_hash:
                self._users[self._key(username)] = AuthorizedUser(username, salt, password_hash)

    def _save(self) -> None:
        data = {
            "version": 1,
            "kdf": "pbkdf2_hmac_sha256",
            "iterations": PBKDF2_ITERATIONS,
            "users": [
                {
                    "username": user.username,
                    "salt": base64.b64encode(user.salt).decode("ascii"),
                    "password_hash": base64.b64encode(user.password_hash).decode("ascii"),
                }
                for user in sorted(self._users.values(), key=lambda item: item.username.casefold())
            ],
        }
        with _FILE_LOCK:
            _write_json_atomic(self.path, data)

    def _restrict_file_permissions(self, path: str) -> None:
        _restrict_file_permissions(path)

    def _load_attempt_state(self) -> dict[str, dict[str, float | int]]:
        if not os.path.exists(self.lockout_path):
            return {}
        try:
            with open(self.lockout_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {}

        if not isinstance(data, dict):
            return {}
        attempts = data.get("attempts", {})
        if not isinstance(attempts, dict):
            return {}

        result = {}
        for username, entry in attempts.items():
            if not isinstance(entry, dict):
                continue
            try:
                failures = max(0, int(entry.get("failures", 0)))
                lockout_until = max(0.0, float(entry.get("lockout_until", 0.0)))
            except (TypeError, ValueError):
                continue
            if failures or lockout_until:
                result[str(username)] = {
                    "failures": failures,
                    "lockout_until": lockout_until,
                }
        return result

    def _save_attempt_state(self, attempts: dict[str, dict[str, float | int]]) -> None:
        if attempts:
            _write_json_atomic(
                self.lockout_path,
                {"version": 1, "attempts": attempts},
            )
            return
        try:
            os.remove(self.lockout_path)
        except FileNotFoundError:
            pass
        except OSError:
            pass

    @staticmethod
    def _active_attempt_entry(
        attempts: dict[str, dict[str, float | int]],
        username: str,
        now: float,
    ) -> dict[str, float | int]:
        entry = attempts.get(username, {"failures": 0, "lockout_until": 0.0})
        lockout_until = float(entry.get("lockout_until", 0.0))
        if lockout_until and lockout_until <= now:
            entry = {"failures": 0, "lockout_until": 0.0}
        return entry

    def lockout_remaining(self, username: str) -> int:
        attempted_username = str(username or "")
        now = time.time()
        with _FILE_LOCK:
            attempts = self._load_attempt_state()
            entry = self._active_attempt_entry(attempts, attempted_username, now)
            lockout_until = float(entry.get("lockout_until", 0.0))
            return math.ceil(max(0.0, lockout_until - now))

    def add_user(self, username: str, password: str) -> None:
        username = str(username or "").strip()
        if not username:
            raise ValueError("Username is required.")
        if len(str(password or "")) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
        key = self._key(username)
        if key in self._users:
            raise ValueError("Username already exists.")
        salt = os.urandom(16)
        self._users[key] = AuthorizedUser(username, salt, _password_hash(password, salt))
        self._save()

    def remove_user(self, username: str) -> None:
        key = self._key(username)
        if key not in self._users:
            return
        if len(self._users) <= 1:
            raise ValueError("At least one authorized user is required.")
        del self._users[key]
        self._save()

    def verify(self, username: str, password: str) -> bool:
        attempted_username = str(username or "")
        now = time.time()
        with _FILE_LOCK:
            attempts = self._load_attempt_state()
            entry = self._active_attempt_entry(attempts, attempted_username, now)
            if float(entry.get("lockout_until", 0.0)) > now:
                return False

            user = self._users.get(self._key(username))
            if not user:
                # Keep unknown-user checks expensive so username existence is not exposed by timing.
                _password_hash(password, _DUMMY_SALT)
                verified = False
            else:
                computed = _password_hash(password, user.salt)
                verified = hmac.compare_digest(computed, user.password_hash)

            if verified:
                if attempted_username in attempts:
                    del attempts[attempted_username]
                    self._save_attempt_state(attempts)
                return True

            failures = int(entry.get("failures", 0)) + 1
            if failures >= MAX_FAILED_ATTEMPTS:
                entry = {"failures": 0, "lockout_until": now + LOCKOUT_SECONDS}
            else:
                entry = {"failures": failures, "lockout_until": 0.0}
            attempts[attempted_username] = entry
            self._save_attempt_state(attempts)
            return False

    def canonical_username(self, username: str) -> str:
        user = self._users.get(self._key(username))
        return user.username if user else str(username or "").strip()

    def list_usernames(self) -> list[str]:
        return sorted((user.username for user in self._users.values()), key=str.casefold)

    def is_empty(self) -> bool:
        return not self._users


def append_delete_audit(
    action: str,
    username: str = "UNKNOWN",
    items: int | None = None,
    size: int | None = None,
    errors: int | None = None,
    reason: str | None = None,
    attempted_username: str | None = None,
    path: str = DELETE_AUDIT_LOG_PATH,
) -> None:
    parts = [
        datetime.now().isoformat(timespec="seconds"),
        f"user={username or 'UNKNOWN'}",
    ]
    if attempted_username is not None:
        parts.append(f"attempt={attempted_username or 'UNKNOWN'}")
    parts.append(f"action={action}")
    if items is not None:
        parts.append(f"items={int(items or 0)}")
    if size is not None:
        parts.append(f"size={int(size or 0)}")
    if errors is not None:
        parts.append(f"errors={int(errors or 0)}")
    if reason:
        parts.append(f"reason={reason}")

    if os.path.abspath(path) == os.path.abspath(DELETE_AUDIT_LOG_PATH):
        with _FILE_LOCK:
            _copy_legacy_file(LEGACY_DELETE_AUDIT_LOG_PATH, path)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("  ".join(parts) + "\n")
    _restrict_file_permissions(path)

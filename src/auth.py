import base64
import hmac
import json
import os
from dataclasses import dataclass
from datetime import datetime


AUTH_STORE_PATH = "auth_store.json"
DELETE_AUDIT_LOG_PATH = "delete_audit.log"
PBKDF2_ITERATIONS = 200_000
MIN_PASSWORD_LENGTH = 4


@dataclass
class AuthorizedUser:
    username: str
    salt: bytes
    password_hash: bytes


def _password_hash(password: str, salt: bytes) -> bytes:
    return hashlib_pbkdf2(password, salt)


def hashlib_pbkdf2(password: str, salt: bytes) -> bytes:
    import hashlib

    return hashlib.pbkdf2_hmac(
        "sha256",
        str(password or "").encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
    )


class AuthStore:
    """Deterrence-grade local auth; pair with proper OS/NTFS permissions for real enforcement."""

    def __init__(self, path: str = AUTH_STORE_PATH):
        self.path = path
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
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        self._restrict_file_permissions(self.path)

    def _restrict_file_permissions(self, path: str) -> None:
        try:
            if os.name != "nt":
                os.chmod(path, 0o600)
        except OSError:
            pass

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
        user = self._users.get(self._key(username))
        if not user:
            fake_salt = b"\0" * 16
            fake_hash = _password_hash(password, fake_salt)
            return hmac.compare_digest(fake_hash, b"\0" * len(fake_hash)) and False
        computed = _password_hash(password, user.salt)
        return hmac.compare_digest(computed, user.password_hash)

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

    with open(path, "a", encoding="utf-8") as handle:
        handle.write("  ".join(parts) + "\n")
    try:
        if os.name != "nt":
            os.chmod(path, 0o600)
    except OSError:
        pass

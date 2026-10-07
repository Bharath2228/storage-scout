import argparse
import getpass
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.auth import AuthStore  # noqa: E402


def add_user(store: AuthStore, username: str) -> int:
    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords do not match.", file=sys.stderr)
        return 1
    try:
        store.add_user(username, password)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Added authorized user: {username}")
    return 0


def remove_user(store: AuthStore, username: str) -> int:
    try:
        store.remove_user(username)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Removed authorized user: {username}")
    return 0


def list_users(store: AuthStore) -> int:
    usernames = store.list_usernames()
    if not usernames:
        print("No authorized users configured.")
        return 0
    for username in usernames:
        print(username)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Manage Storage Scout delete authorization users.")
    parser.add_argument(
        "--store",
        default=None,
        help="Path to auth_store.json (defaults to the application data directory)",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--add-user", metavar="USERNAME")
    group.add_argument("--remove-user", metavar="USERNAME")
    group.add_argument("--list-users", action="store_true")
    args = parser.parse_args(argv)

    store = AuthStore(args.store) if args.store else AuthStore()
    if args.add_user:
        return add_user(store, args.add_user)
    if args.remove_user:
        return remove_user(store, args.remove_user)
    return list_users(store)


if __name__ == "__main__":
    raise SystemExit(main())

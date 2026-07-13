"""Seed / create an app user for Chainlit login.

Usage:
    python scripts/create_user.py <username> <password> [role]

Run inside the app container (or with the same env), e.g.:
    docker compose exec app python scripts/create_user.py admin secret admin
"""

from __future__ import annotations

import sys

from csdap_agent.db import postgres


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(1)
    username, password = sys.argv[1], sys.argv[2]
    role = sys.argv[3] if len(sys.argv) > 3 else "user"
    postgres.create_user(username, password, role)
    print(f"Created/updated user '{username}' (role={role}).")


if __name__ == "__main__":
    main()

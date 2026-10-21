"""Password storage via the OS keyring (Windows Credential Manager, macOS
Keychain, Secret Service on Linux).

The password for a connection is stored under service ``oracle-mcp`` with the
connection id as the account name, so DBeaver and SQL Developer entries that
point at the same database share one credential.
"""

from __future__ import annotations

import keyring

from . import KEYRING_SERVICE


def get_password(conn_id: str) -> str | None:
    try:
        return keyring.get_password(KEYRING_SERVICE, conn_id)
    except keyring.errors.KeyringError:
        return None


def set_password(conn_id: str, password: str) -> None:
    keyring.set_password(KEYRING_SERVICE, conn_id, password)


def delete_password(conn_id: str) -> None:
    try:
        keyring.delete_password(KEYRING_SERVICE, conn_id)
    except keyring.errors.PasswordDeleteError:
        pass


def has_password(conn_id: str) -> bool:
    return get_password(conn_id) is not None

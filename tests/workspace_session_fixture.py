"""Authenticate API regression fixtures against their temporary SQLite database."""
from contextlib import closing
from secrets import token_urlsafe
from time import time

from backend.api.workspace_auth import COOKIE, token_digest
from backend.db.connection import get_connection


def authenticate_fixture(client, *, role='admin'):
    token = token_urlsafe(32)
    with closing(get_connection()) as connection, connection:
        user_id = connection.execute(
            'INSERT INTO workspace_users (username, display_name, password_hash, role) VALUES (?, ?, ?, ?)',
            (token[:16], '测试账号', 'fixture-only', role),
        ).lastrowid
        connection.execute('INSERT INTO workspace_sessions VALUES (?, ?, ?)',
                           (token_digest(token), user_id, int(time()) + 3600))
    client.cookies.set(COOKIE, token)
    return user_id

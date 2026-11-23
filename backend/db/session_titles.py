"""Conditional title writes keep slow model responses from overwriting manual names."""
from contextlib import closing
from backend.db.connection import get_connection
from backend.db.repository import _current_timestamp


def load_title_input(session_id: str) -> dict | None:
    with closing(get_connection()) as connection:
        row = connection.execute('''SELECT s.title,
            (SELECT m.content FROM chat_messages m WHERE m.session_id=s.id AND m.role='user'
             ORDER BY m.message_order,m.id LIMIT 1) AS first_question
            FROM chat_sessions s WHERE s.id=?''', (session_id,)).fetchone()
        return dict(row) if row else None


def save_generated_title(session_id: str, expected_title: str | None, title: str) -> bool:
    with closing(get_connection()) as connection, connection:
        result = connection.execute('''UPDATE chat_sessions SET title=?,updated_at=?
            WHERE id=? AND title IS ? AND (title IS NULL OR TRIM(title) IN ('','新会话','未命名会话'))''',
            (title, _current_timestamp(), session_id, expected_title))
        return result.rowcount == 1

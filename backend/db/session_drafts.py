"""Reuse an account's empty draft atomically, without touching history."""
from contextlib import closing

from backend.db.connection import get_connection
from backend.db.repository import _current_timestamp


def get_or_create_draft(session_id: str, mode: str, *, owner_user_id: int | None = None) -> str:
    if not session_id.strip() or session_id.startswith('knowledge_base:'):
        raise ValueError('会话编号无效。')
    with closing(get_connection()) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        existing = connection.execute('''SELECT s.id FROM chat_sessions s
            WHERE s.owner_user_id IS ? AND s.mode=? AND s.title='新会话' AND substr(s.id,1,15)<>'knowledge_base:'
                AND NOT EXISTS(SELECT 1 FROM chat_messages m WHERE m.session_id=s.id)
                AND NOT EXISTS(SELECT 1 FROM documents d WHERE d.session_id=s.id)
            ORDER BY s.updated_at DESC,s.created_at DESC,s.id DESC LIMIT 1''', (owner_user_id, mode)).fetchone()
        if existing:
            return existing['id']
        if connection.execute('SELECT 1 FROM deleted_chat_sessions WHERE session_id=?', (session_id,)).fetchone():
            raise ValueError('会话已删除，请重新新建会话。')
        if connection.execute('SELECT 1 FROM chat_sessions WHERE id=?', (session_id,)).fetchone():
            raise ValueError('会话编号已使用，请重新新建会话。')
        now = _current_timestamp()
        connection.execute('INSERT INTO chat_sessions(id,mode,title,created_at,updated_at,owner_user_id) VALUES(?,?,?,?,?,?)',
                           (session_id, mode, '新会话', now, now, owner_user_id))
        return session_id

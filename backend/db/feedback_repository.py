"""回答反馈按消息去重；目前为共享演示环境，每条回答保存最新一条反馈。"""
from contextlib import closing
from backend.db.connection import get_connection
from backend.db.repository import _current_timestamp


def save_feedback(session_id: str, message_id: int, *, rating: str, reason: str, comment: str) -> dict | None:
    with closing(get_connection()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        message = conn.execute("SELECT id FROM chat_messages WHERE id=? AND session_id=? AND role='assistant'",
                               (message_id, session_id)).fetchone()
        if message is None:
            return None
        now = _current_timestamp()
        conn.execute('''INSERT INTO answer_feedback (message_id, rating, reason, comment, updated_at)
                        VALUES (?, ?, ?, ?, ?) ON CONFLICT(message_id) DO UPDATE SET
                        rating=excluded.rating, reason=excluded.reason, comment=excluded.comment, updated_at=excluded.updated_at''',
                     (message_id, rating, reason, comment, now))
        return dict(message_id=message_id, rating=rating, reason=reason, comment=comment, updated_at=now)


def list_feedback(*, rating: str | None, page: int, page_size: int) -> dict:
    with closing(get_connection()) as conn, conn:
        # 一次读事务保证计数、筛选与分页来自同一个快照。
        conn.execute('BEGIN')
        counts = {key: 0 for key in ('helpful', 'unhelpful')}
        counts.update({row['rating']: row['n'] for row in conn.execute('SELECT rating, COUNT(*) n FROM answer_feedback GROUP BY rating')})
        total = counts[rating] if rating else sum(counts.values())
        rows = conn.execute('''SELECT f.*, m.session_id, m.content AS answer,
            (SELECT q.content FROM chat_messages q WHERE q.session_id=m.session_id AND q.role='user'
             AND (q.message_order < m.message_order OR (q.message_order=m.message_order AND q.id < m.id))
             ORDER BY q.message_order DESC, q.id DESC LIMIT 1) AS question
            FROM answer_feedback f JOIN chat_messages m ON m.id=f.message_id
            WHERE (? IS NULL OR f.rating=?) ORDER BY f.updated_at DESC, f.message_id DESC LIMIT ? OFFSET ?''',
                            (rating, rating, page_size, (page-1)*page_size)).fetchall()
        return dict(items=[dict(row) for row in rows], total=total, counts=counts, page=page, page_size=page_size)

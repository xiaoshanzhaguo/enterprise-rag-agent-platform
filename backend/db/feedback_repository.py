"""回答反馈按消息去重；目前为共享演示环境，每条回答保存最新一条反馈。"""
from contextlib import closing
from backend.db.connection import get_connection
from backend.db.repository import _current_timestamp


class FeedbackConflict(ValueError):
    """反馈已变更或当前记录不能作为问题处理。"""


def save_feedback(session_id: str, message_id: int, *, rating: str, reason: str, comment: str) -> dict | None:
    with closing(get_connection()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        message = conn.execute("SELECT id FROM chat_messages WHERE id=? AND session_id=? AND role='assistant'",
                               (message_id, session_id)).fetchone()
        if message is None:
            return None
        now = _current_timestamp()
        # 内容相同的重试不重开已解决问题；内容改变时重新进入待处理。
        conn.execute('''INSERT INTO answer_feedback (message_id, rating, reason, comment, updated_at)
                        VALUES (?, ?, ?, ?, ?) ON CONFLICT(message_id) DO UPDATE SET
                        rating=excluded.rating, reason=excluded.reason, comment=excluded.comment, updated_at=excluded.updated_at,
                        review_status='pending', review_note='', revision=answer_feedback.revision+1
                        WHERE answer_feedback.rating<>excluded.rating OR answer_feedback.reason<>excluded.reason
                              OR answer_feedback.comment<>excluded.comment''',
                     (message_id, rating, reason, comment, now))
        return dict(conn.execute('SELECT * FROM answer_feedback WHERE message_id=?', (message_id,)).fetchone())


def review_feedback(message_id: int, *, status: str, note: str, expected_revision: int) -> dict | None:
    with closing(get_connection()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        current = conn.execute('SELECT * FROM answer_feedback WHERE message_id=?', (message_id,)).fetchone()
        if current is None:
            return None
        if current['rating'] != 'unhelpful':
            raise FeedbackConflict('仅无帮助反馈需要处理，请刷新列表核对。')
        if current['revision'] != expected_revision:
            raise FeedbackConflict('反馈或处理结果已变更，请刷新列表后重新核对。')
        if current['review_status'] != status or current['review_note'] != note:
            conn.execute('''UPDATE answer_feedback SET review_status=?, review_note=?, revision=revision+1,
                            updated_at=? WHERE message_id=?''', (status, note, _current_timestamp(), message_id))
        return dict(conn.execute('SELECT * FROM answer_feedback WHERE message_id=?', (message_id,)).fetchone())


def list_feedback(*, rating: str | None, page: int, page_size: int, review_status: str | None = None) -> dict:
    with closing(get_connection()) as conn, conn:
        # 一次读事务保证计数、筛选与分页来自同一个快照。
        conn.execute('BEGIN')
        counts = {key: 0 for key in ('helpful', 'unhelpful')}
        counts.update({row['rating']: row['n'] for row in conn.execute('SELECT rating, COUNT(*) n FROM answer_feedback GROUP BY rating')})
        review_counts = {key: 0 for key in ('pending', 'in_progress', 'resolved')}
        review_counts.update({row['review_status']: row['n'] for row in conn.execute(
            "SELECT review_status, COUNT(*) n FROM answer_feedback WHERE rating='unhelpful' GROUP BY review_status")})
        filters = "(? IS NULL OR f.rating=?) AND (? IS NULL OR (f.rating='unhelpful' AND f.review_status=?))"
        params = (rating, rating, review_status, review_status)
        total = conn.execute('SELECT COUNT(*) FROM answer_feedback f WHERE '+filters, params).fetchone()[0]
        rows = conn.execute('''SELECT f.*, m.session_id, m.content AS answer,
            (SELECT q.content FROM chat_messages q WHERE q.session_id=m.session_id AND q.role='user'
             AND (q.message_order < m.message_order OR (q.message_order=m.message_order AND q.id < m.id))
             ORDER BY q.message_order DESC, q.id DESC LIMIT 1) AS question
            FROM answer_feedback f JOIN chat_messages m ON m.id=f.message_id
            WHERE '''+filters+''' ORDER BY f.updated_at DESC, f.message_id DESC LIMIT ? OFFSET ?''',
                            (*params, page_size, (page-1)*page_size)).fetchall()
        return dict(items=[dict(row) for row in rows], total=total, counts=counts, review_counts=review_counts, page=page, page_size=page_size)

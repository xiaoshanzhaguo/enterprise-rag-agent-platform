"""Keyset-paged session summaries; preserves the legacy recent-list endpoint."""
import base64
import json
from contextlib import closing

from backend.db.connection import get_connection


def list_session_page(*, limit=50, cursor=None, mode=None, include_empty=False, owner_user_id=None):
    limit=max(1,min(int(limit),50))
    scope = [owner_user_id, mode, include_empty]
    position=None
    if cursor:
        try:
            data=json.loads(base64.b64decode(cursor+'='*(-len(cursor)%4),altchars=b'-_',validate=True))
            position=data['after']
            if (data.get('v')!=2 or data.get('scope')!=scope or not isinstance(position,list)
                    or len(position)!=3 or any(not isinstance(value,str) or not value or len(value)>256 for value in position)):
                raise ValueError('Invalid session cursor')
        except (ValueError,KeyError,TypeError) as error:
            raise ValueError('Invalid session cursor') from error
    with closing(get_connection()) as connection:
        rows=connection.execute('''
            SELECT s.id AS session_id,s.mode,
                COALESCE(NULLIF(TRIM(s.title),''),
                    (SELECT m.content FROM chat_messages m WHERE m.session_id=s.id AND m.role='user'
                     ORDER BY m.message_order,m.id LIMIT 1),'未命名会话') AS title,
                s.created_at,s.updated_at,COUNT(messages.id) AS message_count
            FROM chat_sessions s LEFT JOIN chat_messages messages ON messages.session_id=s.id
            WHERE substr(s.id,1,15)<>'knowledge_base:' AND (? IS NULL OR s.mode=?)
                AND s.owner_user_id IS ?
                AND (? IS NULL OR (s.updated_at,s.created_at,s.id)<(?,?,?))
            GROUP BY s.id HAVING ? OR COUNT(messages.id)>0
            ORDER BY s.updated_at DESC,s.created_at DESC,s.id DESC LIMIT ?
        ''',(mode,mode,owner_user_id,None if position is None else 1,*(position or [None]*3),include_empty,limit+1)).fetchall()
    page=[dict(row) for row in rows[:limit]]
    next_cursor=None
    if len(rows)>limit:
        last=page[-1]
        data=dict(v=2,scope=scope,after=[last['updated_at'],last['created_at'],last['session_id']])
        next_cursor=base64.urlsafe_b64encode(json.dumps(data,ensure_ascii=False,separators=(',',':')).encode()).decode().rstrip('=')
    return dict(sessions=page,next_cursor=next_cursor)

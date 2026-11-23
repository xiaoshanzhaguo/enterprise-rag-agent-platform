"""Authorization for account-owned conversations and their temporary documents."""
from contextlib import closing
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from backend.api.workspace_auth import check_module_access, current_user, require_module
from backend.db.connection import get_connection
from backend.rag.knowledge_bases import SESSION_SCOPED_KNOWLEDGE_BASE_ID

WorkspaceUser = Annotated[dict, Depends(require_module('chat'))]


def require_session_owner(session_id: str, user: dict) -> None:
    if session_id.startswith('knowledge_base:'):
        raise HTTPException(403, '公共知识库不是聊天会话。')
    with closing(get_connection()) as connection:
        owned = connection.execute(
            'SELECT 1 FROM chat_sessions WHERE id = ? AND owner_user_id = ?', (session_id, user['id'])
        ).fetchone()
    if not owned:
        raise HTTPException(404, '会话不存在或已被删除。')


def validate_shared_knowledge_base(knowledge_base_id: str | None) -> None:
    if knowledge_base_id is not None and (
        not isinstance(knowledge_base_id, str) or knowledge_base_id.strip() == SESSION_SCOPED_KNOWLEDGE_BASE_ID
    ):
        raise HTTPException(400, '知识库编号无效。')


def authorize_document_scope(request: Request, session_id: str, knowledge_base_id: str | None, *, preview=False) -> None:
    validate_shared_knowledge_base(knowledge_base_id)
    user = current_user(request)
    check_module_access(user, 'chat' if preview or not knowledge_base_id else 'knowledge')
    if not knowledge_base_id:
        require_session_owner(session_id, user)

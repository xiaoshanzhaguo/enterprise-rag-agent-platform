"""隔离的会话浏览器验收：真实 SQLite/SSE，固定模型输出，不调用外部 API。"""
import os
import argparse
from contextlib import closing
from pathlib import Path
import sys
import tempfile
import time
import json
import threading
from unittest.mock import patch

runtime = tempfile.TemporaryDirectory(prefix='chat-ui-', ignore_cleanup_errors=True)
os.environ['DATABASE_URL'] = 'sqlite:///' + (Path(runtime.name) / 'chat.db').as_posix()
os.environ['RAG_RETRIEVAL_MODE'] = 'keyword'
os.environ['VECTOR_STORE_DIR'] = str(Path(runtime.name) / 'chroma')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn
from backend.api.workspace_auth import NewUser, insert_user
from backend.main import app
from backend.db.connection import get_connection
from backend.db.init_db import init_database
from backend.db.repository import ensure_chat_session, ensure_knowledge_base, save_chat_message
from backend.services.agent_router import AgentRouteDecision, AgentRouteType
from backend.services.agent_service import run_agent_stream
from starlette.responses import StreamingResponse


def recovery_stream(request, client):
    """UI-only fault fixture: a disconnected client and an independently persisted reply."""
    save_chat_message(request.session_id, 'user', request.input_text, mode=request.mode)
    completed = threading.Event()
    saved = []
    answer = '隔离恢复验收：请准备发票和行程单。'

    def finish():
        time.sleep(8)
        saved.append(save_chat_message(request.session_id, 'assistant', answer, mode=request.mode))
        completed.set()

    threading.Thread(target=finish, daemon=True).start()

    def events():
        for _ in range(20):
            time.sleep(0.5)
            yield 'data: ' + json.dumps({'event_type': 'delta', 'session_id': request.session_id,
                                         'content': '已找到相关材料。'}, ensure_ascii=False) + '\n\n'
        completed.wait(timeout=5)
        yield 'data: ' + json.dumps({'event_type': 'final', 'session_id': request.session_id,
                                     'content': answer, 'message_id': saved[0]}, ensure_ascii=False) + '\n\n'

    return StreamingResponse(events(), media_type='text/event-stream')


def model_answer(client, messages):
    # 普通对话路由仍经过生产 Agent、消息入库与 SSE 编码。
    text = str(messages[-1])
    if '模拟异常' in text:
        raise RuntimeError('浏览器验收模拟异常，请按追踪编号核对。')
    if '慢速' in text:
        for part in ['旧会话专属回答'] * 15:
            time.sleep(0.5)
            yield part
    else:
        yield '浏览器验收回答：会话内容已经分别保存。'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=18002, help='隔离验收服务端口')
    parser.add_argument('--recovery', action='store_true', help='模拟客户端停止后后台独立完成入库，仅用于UI恢复验收')
    parser.add_argument('--drafts', action='store_true', help='准备第二个会话和知识库，验收草稿与选择恢复')
    parser.add_argument('--feedback', action='store_true', help='准备一条待处理的模拟负反馈')
    parser.add_argument('--trace', action='store_true', help='用样例文档与确定路由验收执行摘要')
    args = parser.parse_args()
    init_database()
    with closing(get_connection()) as connection, connection:
        user = insert_user(connection, NewUser(username='chat-ui', display_name='问答验收账号',
                                               password='local-chat-ui-fixture', role='admin'))
    owner_user_id = user['id']
    print(f'Isolated UI data: {runtime.name}', flush=True)
    print('Local test login: chat-ui / local-chat-ui-fixture', flush=True)
    if args.drafts:
        ensure_knowledge_base('draft_test', name='草稿验收知识库', description='仅用于隔离浏览器验收')
        ensure_chat_session('history-b', mode='企业知识库问答', title='独立草稿会话', owner_user_id=owner_user_id)
    ensure_chat_session('history-a', mode='企业知识库问答', title='年假制度咨询', owner_user_id=owner_user_id)
    save_chat_message('history-a', 'user', '年假应该提前多久申请？')
    message_id = save_chat_message('history-a', 'assistant', '请提前 3 个工作日申请。[来源:leave_policy.md#chunk-1]', metadata={
        'rag_preview_chunks': [{'rank': 1, 'file_name': 'leave_policy.md', 'chunk_id': 1,
                                'source': 'leave_policy.md#chunk-1', 'text_preview': '年假应提前 3 个工作日申请。'}]})
    if args.feedback:
        from backend.db.feedback_repository import save_feedback
        save_feedback('history-a', message_id, rating='unhelpful', reason='incomplete', comment='浏览器验收：需要核对审批流程。')
    if args.trace:
        from backend.rag.chunker import split_text_into_chunks
        from backend.rag.store import save_document_chunks
        document = Path(__file__).resolve().parents[1]/'docs/sample_knowledge_base/hr/leave_policy.md'
        save_document_chunks('ui-corpus', 'leave_policy.md', split_text_into_chunks(document.read_text(encoding='utf-8')),
                             knowledge_base_id='enterprise_default', knowledge_base_type='hr')

    def route_for_ui(question, **kwargs):
        needs_knowledge = args.trace and any(term in question for term in ('年假', '未收录'))
        return AgentRouteDecision(AgentRouteType.KNOWLEDGE_QA if needs_knowledge else AgentRouteType.IRRELEVANT,
                                  '隔离浏览器验收固定路由', needs_knowledge, question)

    with runtime, patch('backend.api.chat.get_client', return_value=object()), \
         patch('backend.api.chat.run_agent_stream', side_effect=recovery_stream if args.recovery else run_agent_stream), \
         patch('backend.services.agent_service.route_question', side_effect=route_for_ui), \
         patch('backend.services.agent_service._stream_model_answer', side_effect=model_answer):
        uvicorn.run(app, host='127.0.0.1', port=args.port)

"""隔离的会话浏览器验收：真实 SQLite/SSE，固定模型输出，不调用外部 API。"""
import os
import argparse
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

runtime = tempfile.TemporaryDirectory(prefix='chat-ui-', ignore_cleanup_errors=True)
os.environ['DATABASE_URL'] = 'sqlite:///' + (Path(runtime.name) / 'chat.db').as_posix()
os.environ['RAG_RETRIEVAL_MODE'] = 'keyword'
os.environ['VECTOR_STORE_DIR'] = str(Path(runtime.name) / 'chroma')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn
from backend.main import app
from backend.db.init_db import init_database
from backend.db.repository import ensure_chat_session, save_chat_message
from backend.services.agent_router import AgentRouteDecision, AgentRouteType


def model_answer(client, messages):
    # 普通对话路由仍经过生产 Agent、消息入库与 SSE 编码。
    text = str(messages[-1])
    if '慢速' in text:
        for part in ['旧会话专属回答'] * 15:
            time.sleep(0.5)
            yield part
    else:
        yield '浏览器验收回答：会话内容已经分别保存。'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--feedback', action='store_true', help='准备一条待处理的模拟负反馈')
    args = parser.parse_args()
    init_database()
    ensure_chat_session('history-a', mode='企业知识库问答', title='年假制度咨询')
    save_chat_message('history-a', 'user', '年假应该提前多久申请？')
    message_id = save_chat_message('history-a', 'assistant', '请提前 3 个工作日申请。[来源:leave_policy.md#chunk-1]', metadata={
        'rag_preview_chunks': [{'rank': 1, 'file_name': 'leave_policy.md', 'chunk_id': 1,
                                'source': 'leave_policy.md#chunk-1', 'text_preview': '年假应提前 3 个工作日申请。'}]})
    if args.feedback:
        from backend.db.feedback_repository import save_feedback
        save_feedback('history-a', message_id, rating='unhelpful', reason='incomplete', comment='浏览器验收：需要核对审批流程。')
    with runtime, patch('backend.api.chat.get_client', return_value=object()), \
         patch('backend.services.agent_service.route_question', return_value=AgentRouteDecision(AgentRouteType.IRRELEVANT, '隔离验收普通对话', False)), \
         patch('backend.services.agent_service._stream_model_answer', side_effect=model_answer):
        uvicorn.run(app, host='127.0.0.1', port=18002)

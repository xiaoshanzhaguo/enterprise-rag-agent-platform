"""第 26 天浏览器验收：隔离 SQLite + 真实接口，模型与 Webhook 为模拟。

仅监听回环地址；/__test__/availability 仅存在于此测试进程。
"""

import argparse
import asyncio
from contextlib import ExitStack
import gc
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
from uuid import UUID

import uvicorn
from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if __name__ == '__main__':
    runtime = tempfile.TemporaryDirectory(prefix='workspace-ui-', ignore_cleanup_errors=True)
    os.environ['DATABASE_URL'] = 'sqlite:///' + (Path(runtime.name) / 'ui.db').as_posix()
    os.environ['VECTOR_STORE_DIR'] = str(Path(runtime.name) / 'chroma')
    os.environ['RAG_RETRIEVAL_MODE'] = 'keyword'
from backend.main import app
from backend.db.init_db import init_database
from backend.db.process_repository import start_process_execution, finish_process_execution
from backend.rag.chunker import split_text_into_chunks
from backend.rag.store import save_document_chunks
from tests.process_ui_fixture import model_reply, webhook_reply

availability = {'mode': 'normal'}


@app.post('/__test__/availability')
def set_availability(payload: dict):
    if payload.get('mode') not in {'normal', 'error', 'slow'}:
        return JSONResponse({'detail': 'unknown test mode'}, status_code=422)
    availability.update(payload)
    return availability


@app.middleware('http')
async def simulate_unavailability(request, call_next):
    if request.method == 'GET' and request.url.path == '/process_applications':
        if availability['mode'] == 'error':
            return JSONResponse({'detail': '执行记录暂不可用，请稍后刷新。'}, status_code=503)
        if availability['mode'] == 'slow':
            await asyncio.sleep(17)
    return await call_next(request)


def seed_records():
    for index in range(12):
        request_id = str(UUID(int=index + 1))
        status = ('simulated', 'simulated', 'running', 'timeout', 'failed', 'simulated')[index % 6]
        record_id = start_process_execution({'request_id': request_id, 'fields': {
            'application_type': ('VPN 权限', 'GitLab 权限', '报销申请')[index % 3],
            'applicant': ('张三', '王芳', '陈晨')[index % 3],
            'department': ('研发部', '测试部', '产品部')[index % 3],
            'permission_level': '只读', 'approver': '李经理', 'additional_info': '浏览器验收虚拟样例',
        }})
        if status != 'running':
            output = {'request_id': request_id, 'status': 'simulated', 'ticket_id': f'SIM-20261026-{index+1:03}', 'actions': ['ticket_simulated']}
            finish_process_execution(record_id, status=status, response_status_code=200 if status == 'simulated' else 504 if status == 'timeout' else 502,
                                     output=output if status == 'simulated' else None,
                                     error_message=None if status == 'simulated' else '请按申请编号核对执行情况，勿重复提交。')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=18002)
    parser.add_argument('--empty', action='store_true')
    args = parser.parse_args()
    with runtime, ExitStack() as stack:
        stack.enter_context(patch('backend.api.processes.get_client', return_value=object()))
        stack.enter_context(patch('backend.services.process_extraction.invoke_chat_model', side_effect=model_reply))
        stack.enter_context(patch('backend.services.process_submission._call_n8n_webhook', side_effect=webhook_reply))
        init_database()
        if not args.empty:
            seed_records()
        for category, filename in [('hr', 'leave_policy.md'), ('finance', 'reimbursement_policy.md'), ('it', 'gitlab_access_request.md')]:
            save_document_chunks('ui-corpus', filename, split_text_into_chunks((ROOT/'docs/sample_knowledge_base'/category/filename).read_text(encoding='utf-8')),
                                 knowledge_base_id='enterprise_default', knowledge_base_type=category)
        uvicorn.run(app, host='127.0.0.1', port=args.port)
        gc.collect()

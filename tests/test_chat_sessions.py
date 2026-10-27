"""真实 SQLite/API：多会话、引用恢复、删除后迟到写入与公共知识库边界。"""
from contextlib import closing
from pathlib import Path
import gc
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.api.chat import router
from backend.db.connection import get_connection
from backend.db.init_db import init_database
from backend.db.repository import ensure_chat_session, save_chat_message, save_document_with_chunks

MODE = '企业知识库问答'


class ChatSessionTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.config = SimpleNamespace(database_url='sqlite:///' + str(Path(folder.name) / 'chat.db'))
        config_patch = patch('backend.db.connection.settings', self.config)
        config_patch.start()
        self.addCleanup(config_patch.stop)
        init_database(self.config.database_url)
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)
        vector_cleanup = patch('backend.api.chat.clear_document_chunks')
        self.cleanup_vectors = vector_cleanup.start()
        self.addCleanup(vector_cleanup.stop)
        self.addCleanup(gc.collect)

    def create(self, session='a', mode=MODE):
        response = self.client.post('/chat_session', json={'session_id': session, 'mode': mode, 'title': '新会话'})
        self.assertEqual(response.status_code, 200, response.text)

    def test_empty_sessions_mode_filter_and_backward_compatibility(self):
        self.create(); self.create('b'); self.create('other', '普通聊天')
        ensure_chat_session('knowledge_base:enterprise_default')
        self.assertEqual(self.client.get('/chat_sessions').json()['sessions'], [])
        result = self.client.get('/chat_sessions', params={'mode': MODE, 'include_empty': True}).json()['sessions']
        self.assertEqual({item['session_id'] for item in result}, {'a', 'b'})
        self.assertTrue(all(item['message_count'] == 0 for item in result))
        all_sessions = self.client.get('/chat_sessions?include_empty=true').json()['sessions']
        self.assertEqual(len(all_sessions), 3)

    def test_restore_messages_and_citations_isolated_by_session(self):
        self.create(); self.create('b')
        save_chat_message('a', 'user', '年假怎么申请？')
        metadata = {'rag_preview_chunks': [{'source': 'leave.md#chunk-1', 'text_preview': '提前申请'}],
                    'agent_route': {'route_result': 'use_knowledge_base'}}
        save_chat_message('a', 'assistant', '提前申请 [来源:leave.md#chunk-1]', metadata=metadata)
        self.assertEqual(self.client.get('/chat_session/b').json()['messages'], [])
        message = self.client.get('/chat_session/a').json()['messages'][1]
        self.assertEqual(message['rag_preview_chunks'], metadata['rag_preview_chunks'])
        self.assertEqual(message['agent_route'], metadata['agent_route'])

    def test_rename_validation_and_missing(self):
        self.create()
        self.assertEqual(self.client.patch('/chat_session/a', json={'title': ' 年假咨询 '}).json()['title'], '年假咨询')
        self.assertEqual(self.client.get('/chat_session/a').json()['title'], '年假咨询')
        for title in ('  ', '字' * 81):
            self.assertEqual(self.client.patch('/chat_session/a', json={'title': title}).status_code, 422)
        self.assertEqual(self.client.patch('/chat_session/missing', json={'title': '测试'}).status_code, 404)

    def test_delete_cascades_but_preserves_shared_docs_and_audit(self):
        self.create()
        save_chat_message('a', 'user', '测试')
        save_document_with_chunks('a', 'private.md', ['临时资料'])
        save_document_with_chunks('knowledge_base:enterprise_default', 'shared.md', ['公共资料'], knowledge_base_id='enterprise_default')
        with closing(get_connection()) as conn, conn:
            conn.execute("INSERT INTO n8n_execution_records (session_id, workflow_name) VALUES ('a', 'audit')")
        self.assertEqual(self.client.delete('/chat_session/a').status_code, 200)
        self.assertEqual(self.client.get('/chat_session/a').status_code, 404)
        with closing(get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM chat_messages').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT file_name FROM documents').fetchone()[0], 'shared.md')
            self.assertIsNone(conn.execute('SELECT session_id FROM n8n_execution_records').fetchone()[0])
        self.cleanup_vectors.assert_called_once_with('a')

    def test_deleted_session_cannot_be_resurrected_by_late_stream(self):
        self.create()
        self.client.delete('/chat_session/a')
        init_database(self.config.database_url)  # 重启迁移幂等且不会清除删除标记。
        with self.assertRaisesRegex(ValueError, '已删除'):
            save_chat_message('a', 'assistant', '迟到的回答')
        self.assertEqual(self.client.post('/chat_session', json={'session_id': 'a', 'mode': MODE}).status_code, 409)
        self.assertEqual(self.client.delete('/chat_session/a').status_code, 200)
        self.assertEqual(self.client.get('/chat_sessions?include_empty=true').json()['sessions'], [])

    def test_public_knowledge_base_cannot_be_managed_as_chat(self):
        ensure_chat_session('knowledge_base:enterprise_default')
        for method, payload in [('get', None), ('delete', None), ('patch', {'title': '恶意改名'})]:
            kwargs = {'json': payload} if payload else {}
            response = getattr(self.client, method)('/chat_session/knowledge_base:enterprise_default', **kwargs)
            self.assertEqual(response.status_code, 403)
        self.cleanup_vectors.assert_not_called()


if __name__ == '__main__':
    unittest.main()

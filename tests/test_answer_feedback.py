"""回答反馈：真实 SQLite 幂等、消息归属、校验、历史恢复和删除级联。"""
from contextlib import closing
import gc
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.api.chat import router as chat_router
from backend.api.feedback import router
from backend.db.connection import get_connection
from backend.db.init_db import init_database
from backend.db.repository import delete_chat_session, ensure_chat_session, save_chat_message


class AnswerFeedbackTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        config = SimpleNamespace(database_url='sqlite:///' + str(Path(folder.name)/'feedback.db'))
        config_patch = patch('backend.db.connection.settings', config)
        config_patch.start(); self.addCleanup(config_patch.stop)
        self.addCleanup(gc.collect)
        init_database(config.database_url)
        self.database_url = config.database_url
        app = FastAPI(); app.include_router(router); app.include_router(chat_router)
        self.client = TestClient(app)
        ensure_chat_session('a', mode='企业知识库问答', title='反馈测试')
        self.question = save_chat_message('a', 'user', '年假怎么申请？')
        self.answer = save_chat_message('a', 'assistant', '提前申请。', metadata={'rag_preview_chunks': [{'source': 'leave.md#chunk-1'}]})

    def post(self, payload, session='a', message=None):
        return self.client.post(f'/chat_session/{session}/messages/{message or self.answer}/feedback', json=payload)

    def test_upsert_and_history_restore_use_persisted_message_id(self):
        self.assertEqual(self.post({'rating': 'helpful'}).status_code, 200)
        payload = {'rating': 'unhelpful', 'reason': 'incomplete', 'comment': '  缺少时限  '}
        for _ in range(2):
            self.assertEqual(self.post(payload).status_code, 200)
        history = self.client.get('/chat_session/a').json()['messages']
        self.assertEqual(history[1]['message_id'], self.answer)
        self.assertEqual(history[1]['feedback']['comment'], '缺少时限')
        self.assertEqual(history[1]['feedback']['rating'], 'unhelpful')
        self.assertNotIn('feedback', history[0])
        self.assertEqual(history[1]['rag_preview_chunks'][0]['source'], 'leave.md#chunk-1')
        result = self.client.get('/answer_feedback').json()
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['counts'], {'helpful': 0, 'unhelpful': 1})
        self.assertEqual(result['items'][0]['question'], '年假怎么申请？')
        self.assertEqual(result['items'][0]['answer'], '提前申请。')

    def test_reject_wrong_session_user_message_deleted_and_reserved(self):
        ensure_chat_session('b')
        for session, message in [('b', self.answer), ('a', self.question), ('a', 999999)]:
            self.assertEqual(self.post({'rating': 'helpful'}, session, message).status_code, 404)
        self.assertEqual(self.post({'rating': 'helpful'}, 'knowledge_base:enterprise_default').status_code, 403)
        delete_chat_session('a')
        self.assertEqual(self.post({'rating': 'helpful'}).status_code, 404)

    def test_validation_does_not_write_partial_feedback(self):
        for payload in ({'rating': 'great'}, {'rating': 'unhelpful'}, {'rating': 'unhelpful', 'reason': 'bad'},
                        {'rating': 'helpful', 'reason': 'other'}, {'rating': 'helpful', 'comment': '字'*501},
                        {'rating': 'helpful', 'answer': '伪造答案'}):
            with self.subTest(payload=payload):
                self.assertEqual(self.post(payload).status_code, 422)
        self.assertEqual(self.client.get('/answer_feedback').json()['total'], 0)
        for message_id in (0, -1, 9223372036854775808):
            self.assertEqual(self.client.post(f'/chat_session/a/messages/{message_id}/feedback', json={'rating': 'helpful'}).status_code, 422)

    def test_pagination_filter_counts_and_corresponding_question(self):
        self.post({'rating': 'helpful'})
        for index in range(11):
            save_chat_message('a', 'user', f'后续问题{index}')
            message = save_chat_message('a', 'assistant', f'后续回答{index}')
            self.post({'rating': 'unhelpful', 'reason': 'other'}, message=message)
        first = self.client.get('/answer_feedback?rating=unhelpful&page_size=10').json()
        second = self.client.get('/answer_feedback?rating=unhelpful&page_size=10&page=2').json()
        self.assertEqual((first['total'], len(first['items']), len(second['items'])), (11, 10, 1))
        self.assertEqual(first['counts'], {'helpful': 1, 'unhelpful': 11})
        self.assertEqual(first['items'][0]['question'], '后续问题10')
        self.assertEqual(len({x['message_id'] for x in first['items']+second['items']}), 11)
        self.assertEqual(self.client.get('/answer_feedback?rating=helpful').json()['total'], 1)
        for query in ('rating=unknown', 'page=0', 'page_size=51'):
            self.assertEqual(self.client.get('/answer_feedback?'+query).status_code, 422)

    def test_restart_migration_preserves_feedback_and_delete_cascades(self):
        self.post({'rating': 'helpful'})
        init_database(self.database_url)
        self.assertEqual(self.client.get('/answer_feedback').json()['total'], 1)
        delete_chat_session('a')
        self.assertEqual(self.client.get('/answer_feedback').json()['total'], 0)
        with closing(get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM answer_feedback').fetchone()[0], 0)

    def test_storage_error_is_clear_and_does_not_leak_details(self):
        for target, url, method in [('save_feedback', f'/chat_session/a/messages/{self.answer}/feedback', 'post'),
                                    ('list_feedback', '/answer_feedback', 'get')]:
            with patch('backend.api.feedback.'+target, side_effect=sqlite3.OperationalError('private database path')):
                kwargs = {'json': {'rating': 'helpful'}} if method == 'post' else {}
                result = getattr(self.client, method)(url, **kwargs)
            self.assertEqual(result.status_code, 503)
            self.assertNotIn('private', result.text)


if __name__ == '__main__':
    unittest.main()

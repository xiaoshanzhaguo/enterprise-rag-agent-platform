"""Title restoration uses temporary SQLite and fake model responses only."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import threading
import unittest
from unittest.mock import patch

from backend.db.connection import get_connection
from backend.db.repository import ensure_chat_session, get_chat_session_title, rename_chat_session, save_chat_message
from backend.schema.chat_schema import ChatRequest
from backend.services.agent_service import run_agent_stream
from backend.services.session_title import ensure_generated_session_title, is_default_title
from tests import test_answer_feedback as fixture

MODE = '企业知识库问答'


class SessionTitleTests(unittest.TestCase):
    setUp = fixture.AnswerFeedbackTests.setUp

    def draft(self, question=True):
        ensure_chat_session('draft', mode=MODE, title='新会话', owner_user_id=self.owner_id)
        if question:
            save_chat_message('draft', 'user', '打车报销需要什么材料？', mode=MODE)

    def generate(self):
        return ensure_generated_session_title('draft', MODE, '下一轮问题', object())

    def test_names_existing_draft_and_does_not_generate_again(self):
        self.draft()
        with patch('backend.services.session_title.invoke_chat_model', return_value='打车报销材料') as model:
            self.assertEqual(self.generate(), '打车报销材料')
            self.assertEqual(self.generate(), '打车报销材料')
        self.assertEqual(model.call_count, 1)
        self.assertEqual(get_chat_session_title('draft'), '打车报销材料')

    def test_uses_first_question_for_old_conversation(self):
        self.draft()
        save_chat_message('draft', 'assistant', '原回答', mode=MODE)
        save_chat_message('draft', 'user', '年假如何申请？', mode=MODE)
        with patch('backend.services.session_title.invoke_chat_model', return_value='打车报销材料') as model:
            self.generate()
        prompt = model.call_args.kwargs['messages'][1]['content']
        self.assertIn('打车报销需要什么材料？', prompt)
        self.assertNotIn('年假如何申请', prompt)
        self.assertNotIn('下一轮问题', prompt)

    def test_manual_title_skips_model(self):
        self.draft(); rename_chat_session('draft', '我的报销咨询')
        with patch('backend.services.session_title.invoke_chat_model') as model:
            self.assertEqual(self.generate(), '我的报销咨询')
        model.assert_not_called()

    def test_manual_rename_during_model_response_wins(self):
        self.draft()
        def slow_response(**_):
            rename_chat_session('draft', '人工命名')
            return '迟到的模型标题'
        with patch('backend.services.session_title.invoke_chat_model', side_effect=slow_response):
            self.assertEqual(self.generate(), '人工命名')
        self.assertEqual(get_chat_session_title('draft'), '人工命名')

    def test_concurrent_title_results_do_not_overwrite_each_other(self):
        self.draft(); barrier = threading.Barrier(2)
        def response(**_):
            barrier.wait(timeout=5)
            return threading.current_thread().name
        with patch('backend.services.session_title.invoke_chat_model', side_effect=response):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: self.generate(), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(get_chat_session_title('draft'), results[0])

    def test_model_failure_uses_local_fallback(self):
        self.draft()
        with patch('backend.services.session_title.invoke_chat_model', side_effect=RuntimeError('offline')):
            title = self.generate()
        self.assertFalse(is_default_title(title))
        self.assertEqual(get_chat_session_title('draft'), title)

    def test_api_repairs_old_placeholder_without_changing_messages(self):
        self.draft()
        with patch('backend.api.chat.get_client', return_value=object()), \
             patch('backend.services.session_title.invoke_chat_model', return_value='打车报销材料'):
            response = self.client.post('/chat_session/draft/auto_title')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['title'], '打车报销材料')
        self.assertEqual(len(self.client.get('/chat_session/draft').json()['messages']), 1)

    def test_api_empty_missing_and_internal_sessions_do_not_call_model(self):
        self.draft(question=False)
        with patch('backend.api.chat.get_client') as model:
            self.assertEqual(self.client.post('/chat_session/draft/auto_title').json()['title'], '新会话')
            self.assertEqual(self.client.post('/chat_session/missing/auto_title').status_code, 404)
            self.assertEqual(self.client.post('/chat_session/knowledge_base:test/auto_title').status_code, 403)
        model.assert_not_called()

    def test_real_agent_entry_names_draft_before_first_question_is_saved(self):
        self.draft(question=False)
        async def first_event():
            response = run_agent_stream(ChatRequest(session_id='draft', input_text='打车报销需要什么材料？',
                mode=MODE, task_type='agent', use_rag=False), object())
            return await anext(response.body_iterator)
        with patch('backend.services.session_title.invoke_chat_model', return_value='打车报销材料') as model:
            event = asyncio.run(first_event())
        self.assertIn('workflow_start', event)
        self.assertEqual(get_chat_session_title('draft'), '打车报销材料')
        model.assert_called_once()
        with closing(get_connection()) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM chat_messages WHERE session_id='draft'").fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()

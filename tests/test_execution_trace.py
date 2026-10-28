"""执行摘要来自真实 Agent 分支；SSE、持久化和旧历史入口一致。"""
import asyncio
import json
import unittest
from unittest.mock import patch
from uuid import UUID
from tests import test_answer_feedback as fixture
from backend.db.repository import get_session_messages, save_document_with_chunks
from backend.schema.chat_schema import ChatRequest
from backend.services.agent_router import AgentRouteDecision, AgentRouteType
from backend.services.agent_service import run_agent_stream
from backend.services.execution_trace import ExecutionTrace


class ExecutionTraceTests(unittest.TestCase):
    setUp = fixture.AnswerFeedbackTests.setUp

    def collect(self):
        async def read():
            response = run_agent_stream(ChatRequest(session_id='a', task_type='agent', mode='企业知识库问答', input_text='年假怎么申请？', use_rag=True), object())
            return [json.loads(item.removeprefix('data: ')) async for item in response.body_iterator]
        return asyncio.run(read())

    def test_clock_skip_and_unique_request_ids(self):
        with patch('backend.services.execution_trace.perf_counter', side_effect=[1.0, 1.1, 1.3, 1.5]):
            trace = ExecutionTrace(); trace.begin('routing'); trace.end(); trace.skip('retrieval')
            result = trace.snapshot('completed')
        self.assertEqual(result['stages'][0]['duration_ms'], 200)
        self.assertEqual(result['stages'][1], dict(name='retrieval', status='skipped', duration_ms=0))
        self.assertEqual(result['total_ms'], 500)
        self.assertNotEqual(trace.trace_id, ExecutionTrace().trace_id)
        UUID(trace.trace_id)

    def test_normal_chat_skips_retrieval_and_restores_same_summary(self):
        decision = AgentRouteDecision(AgentRouteType.IRRELEVANT, '普通对话', False)
        with patch('backend.services.agent_service.route_question', return_value=decision), \
             patch('backend.services.agent_service._stream_model_answer', return_value=iter(['你好'])):
            events = self.collect()
        final = events[-1]
        trace = final['metadata']['execution_trace']
        self.assertEqual(final['event_type'], 'final')
        self.assertEqual(trace['trace_id'], events[0]['metadata']['execution_trace']['trace_id'])
        self.assertEqual((trace['route_type'], trace['retrieval_mode'], trace['hit_count']), ('irrelevant', 'skipped', 0))
        self.assertEqual(trace['answer_strategy'], 'model')
        self.assertTrue(all(stage['duration_ms'] >= 0 for stage in trace['stages']))
        self.assertEqual(get_session_messages('a')[-1]['execution_trace'], trace)
        restored = self.client.post('/chat_history', json={'mode_names': ['企业知识库问答']})
        self.assertEqual(restored.status_code, 200)
        messages = restored.json()['mode_sessions']['企业知识库问答']['messages']
        self.assertEqual(messages[-1]['execution_trace'], trace)
        self.assertEqual(messages[-1]['message_id'], final['message_id'])

    def test_knowledge_hit_and_refusal_are_distinguished(self):
        decision = AgentRouteDecision(AgentRouteType.KNOWLEDGE_QA, '制度咨询', True, '年假')
        chunks = save_document_with_chunks('a', 'leave.md', ['提前申请'])
        chunks[0].update(file_name='leave.md', chunk_id=1, text='提前申请', score=1.0, retrieval_mode='keyword')
        for matched, mode, strategy in [(chunks, 'keyword', 'model'), ([], 'no_hit', 'evidence_refusal')]:
            with self.subTest(strategy=strategy), \
                 patch('backend.services.agent_service.route_question', return_value=decision), \
                 patch('backend.services.agent_service._retrieve_agent_rag_chunks', return_value=(matched, mode, '年假', '')), \
                 patch('backend.services.agent_service._stream_model_answer', return_value=iter(['提前申请。[来源:leave.md#chunk-1]'])) as model:
                final = self.collect()[-1]
                self.assertEqual(final['event_type'], 'final', final)
                trace = final['metadata']['execution_trace']
                self.assertEqual((trace['retrieval_mode'], trace['answer_strategy'], trace['hit_count']), (mode, strategy, len(matched)))
                self.assertEqual(trace['stages'][1]['status'], 'completed')
                self.assertEqual(model.call_count, 1 if matched else 0)

    def test_failed_route_has_same_trace_id_and_no_fake_success(self):
        with patch('backend.services.agent_service.route_question', side_effect=RuntimeError('模拟模型异常')):
            events = self.collect()
        self.assertEqual(events[-1]['event_type'], 'error')
        trace = events[-1]['metadata']['execution_trace']
        self.assertEqual(trace['trace_id'], events[0]['metadata']['execution_trace']['trace_id'])
        self.assertEqual((trace['status'], trace['stages'][-1]['status']), ('failed', 'failed'))
        self.assertNotIn('模拟模型异常', json.dumps(trace))

    def test_legacy_history_restores_feedback_and_old_messages_without_trace(self):
        self.client.post(f'/chat_session/a/messages/{self.answer}/feedback', json={'rating': 'helpful'})
        result = self.client.post('/chat_history', json={'mode_names': ['企业知识库问答', '不存在模式']})
        self.assertEqual(result.status_code, 200)
        messages = result.json()['mode_sessions']['企业知识库问答']['messages']
        self.assertEqual(messages[-1]['feedback']['rating'], 'helpful')
        self.assertNotIn('execution_trace', messages[-1])
        self.assertNotIn('不存在模式', result.json()['mode_sessions'])

    def test_generation_error_marks_answer_stage_failed_without_saving_partial_answer(self):
        def broken_stream(**kwargs):
            yield '部分内容'
            raise RuntimeError('模拟断流')
        decision = AgentRouteDecision(AgentRouteType.IRRELEVANT, '普通对话', False)
        with patch('backend.services.agent_service.route_question', return_value=decision), \
             patch('backend.services.agent_service._stream_model_answer', side_effect=broken_stream):
            events = self.collect()
        final = events[-1]
        self.assertEqual(final['event_type'], 'error')
        self.assertEqual(final['metadata']['execution_trace']['stages'][-1]['name'], 'answer')
        self.assertEqual(final['metadata']['execution_trace']['stages'][-1]['status'], 'failed')
        self.assertEqual(get_session_messages('a')[-1]['role'], 'user')


if __name__ == '__main__':
    unittest.main()

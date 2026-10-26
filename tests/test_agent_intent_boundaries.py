"""回归：财务问答不能提取申请字段，查询规则不能被当成实际办理。"""

import asyncio
from contextlib import ExitStack
import json
import unittest
from unittest.mock import patch

from backend.schema.chat_schema import ChatRequest
from backend.services.agent_router import AgentRouteDecision, AgentRouteType, route_question_by_rules
from backend.services.agent_service import run_agent_stream


class AgentIntentBoundaryTests(unittest.TestCase):
    def test_rules_distinguish_queries_from_applications(self):
        cases = [
            ('报销需要在多久内提交？', AgentRouteType.FINANCE_PROCESS),
            ('我是财务部王芳，请帮我提交报销申请。', AgentRouteType.PROCESS_APPLY),
            ('我想申请年假。', AgentRouteType.PROCESS_APPLY),
            ('我是销售部赵明，申请出差报销。', AgentRouteType.PROCESS_APPLY),
            ('远程办公每月默认最多几天，超过后由谁审批？', AgentRouteType.KNOWLEDGE_QA),
            ('怎么申请请假？', AgentRouteType.KNOWLEDGE_QA),
            ('申请VPN需要谁审批？', AgentRouteType.IT_SUPPORT),
            ('GitLab权限我只是咨询，不发起申请。', AgentRouteType.IT_SUPPORT),
            ('申请VPN只读权限', AgentRouteType.PROCESS_APPLY),
            ('能帮我申请VPN权限吗？', AgentRouteType.PROCESS_APPLY),
        ]
        for question, route in cases:
            with self.subTest(question=question):
                self.assertEqual(route_question_by_rules(question).route_type, route)

    def run_finance_query(self, chunks):
        async def collect(response):
            return [json.loads(item.removeprefix('data: ')) async for item in response.body_iterator]

        module = 'backend.services.agent_service.'
        with ExitStack() as stack:
            mocks = {name: stack.enter_context(patch(module + name)) for name in (
                'get_chat_session_title', 'ensure_chat_session', 'save_chat_message',
                'route_question', '_retrieve_agent_rag_chunks', 'save_rag_query_with_hits',
                'update_rag_query_answer', '_build_agent_rag_metadata', 'stream_chat_model',
                'extract_process_fields',
            )}
            mocks['get_chat_session_title'].return_value = '财务咨询'
            mocks['_build_agent_rag_metadata'].return_value = {}
            mocks['route_question'].return_value = AgentRouteDecision(
                AgentRouteType.FINANCE_PROCESS, '财务制度咨询', True, '报销提交时限')
            mocks['_retrieve_agent_rag_chunks'].return_value = (chunks, 'keyword', '报销提交时限', '')
            mocks['stream_chat_model'].return_value = iter(['费用发生后15个工作日内提交。[来源: reimbursement_policy.md#chunk-2]'])
            request = ChatRequest(session_id='eval-finance', task_type='agent', use_rag=True,
                                  input_text='日常费用发生后，报销申请需要在多久内提交？')
            events = asyncio.run(collect(run_agent_stream(request, object())))
            mocks['extract_process_fields'].assert_not_called()
            final = next(e for e in events if e['event_type'] == 'final')
            answer = json.loads(final['content'])['generate_answer']
            self.assertNotIn('process_extraction', final['metadata'])
            self.assertEqual(mocks['update_rag_query_answer'].call_args.kwargs['answer_text'], answer)
            return answer, mocks['stream_chat_model'].call_count

    def test_finance_policy_query_generates_cited_answer(self):
        answer, calls = self.run_finance_query([{'file_name': 'reimbursement_policy.md', 'chunk_id': 2,
                                                'text': '费用发生后15个工作日内提交。', 'score': 1.0}])
        self.assertIn('15个工作日', answer)
        self.assertIn('[来源:', answer)
        self.assertEqual(calls, 1)

    def test_finance_query_without_evidence_refuses_instead_of_extracting(self):
        answer, calls = self.run_finance_query([])
        self.assertIn('知识库中没有找到依据', answer)
        self.assertEqual(calls, 0)


if __name__ == '__main__':
    unittest.main()

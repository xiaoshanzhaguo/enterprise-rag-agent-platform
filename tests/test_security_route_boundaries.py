"""公司数据与设备安全制度归 IT；不抢占产品发布治理或泛化代码问题。"""
import unittest
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
from backend.services.agent_router import AgentRouteType, route_question, route_question_by_rules
from tests import test_answer_feedback as fixture
from backend.config import settings
from backend.db.repository import save_document_with_chunks
from backend.rag.chunker import split_text_into_chunks
from backend.services.agent_service import _resolve_agent_knowledge_base_type_filter, _retrieve_agent_rag_chunks


class SecurityRouteBoundaryTests(unittest.TestCase):
    def test_security_rules_and_adjacent_domains(self):
        cases = [
            ('办公设备安全规范允许把公司源代码同步到个人GitHub吗？', AgentRouteType.IT_SUPPORT),
            ('公司的办公电脑允许安装未经批准的软件吗？', AgentRouteType.IT_SUPPORT),
            ('客户业务数据能拷贝到个人网盘吗？', AgentRouteType.IT_SUPPORT),
            ('公司的业务代码可否复制到私人笔记本？', AgentRouteType.IT_SUPPORT),
            ('公司电脑离职交接前怎么清理业务资料？', AgentRouteType.IT_SUPPORT),
            ('版本发布前安全检查不通过可以直接上线吗？', AgentRouteType.PRODUCT_DOC),
            ('涉及登录的灰度发布比例按什么规则扩大？', AgentRouteType.PRODUCT_DOC),
            ('差旅住宿费用有哪些标准？', AgentRouteType.FINANCE_PROCESS),
            ('员工年假需要提前几天申请？', AgentRouteType.KNOWLEDGE_QA),
            ('我是研发部王明，请帮我开通GitLab权限。', AgentRouteType.PROCESS_APPLY),
        ]
        with patch('backend.services.agent_router._decide_need_knowledge_base_by_llm', return_value=None):
            for question, expected in cases:
                with self.subTest(question=question):
                    self.assertEqual(route_question_by_rules(question).route_type, expected)
                    disabled = route_question(question, use_rag=False, client=object())
                    self.assertEqual(disabled.route_type, expected)
                    self.assertFalse(disabled.should_use_rag)
                    self.assertEqual(disabled.rewritten_query, '')

    def test_generic_code_examples_do_not_become_security_rules(self):
        for question in ['解释 Python 源代码中的生成器。', '写一段示例代码演示上传文件。', 'GitHub个人仓库怎么上传代码？']:
            with self.subTest(question=question):
                self.assertNotEqual(route_question_by_rules(question).route_type, AgentRouteType.IT_SUPPORT)


class SecurityRetrievalTests(unittest.TestCase):
    setUp = fixture.AnswerFeedbackTests.setUp

    def test_security_route_retrieves_real_sample_in_it_scope(self):
        root = Path(__file__).resolve().parents[1]
        document = (root/'docs/sample_knowledge_base/it/device_security_policy.md').read_text(encoding='utf-8')
        save_document_with_chunks('corpus', 'device_security_policy.md', split_text_into_chunks(document),
                                  knowledge_base_id='enterprise_default', knowledge_base_type='it')
        save_document_with_chunks('corpus', 'leave_policy.md', ['年假、个人电脑与公司代码'],
                                  knowledge_base_id='enterprise_default', knowledge_base_type='hr')
        question = '办公设备安全规范允许把公司源代码同步到个人GitHub吗？'
        decision = route_question_by_rules(question)
        category, _ = _resolve_agent_knowledge_base_type_filter(decision.route_type, question, None)
        self.assertEqual(category, 'it')
        with patch('backend.rag.service.settings', replace(settings, rag_retrieval_mode='keyword')):
            chunks, mode, _, _ = _retrieve_agent_rag_chunks('a', question, question, 'enterprise_default', category, 3)
        self.assertEqual(mode, 'keyword')
        self.assertTrue(chunks)
        self.assertTrue(all(chunk['knowledge_base_type'] == 'it' for chunk in chunks))
        self.assertIn('device_security_policy.md', {chunk['file_name'] for chunk in chunks})


if __name__ == '__main__':
    unittest.main()

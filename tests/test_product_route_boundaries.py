"""产品发布治理与 IT 故障需区分；用相似表达覆盖规则降级路径。"""
import unittest
from unittest.mock import patch
from backend.services.agent_router import route_question, route_question_by_rules, AgentRouteType


CASES = [
    ('版本发布前至少提前多久冻结需求？冻结后允许哪些变更？', AgentRouteType.PRODUCT_DOC),
    ('涉及登录的版本发布，灰度比例从多少开始，需要观察多久才能扩大到50%？', AgentRouteType.PRODUCT_DOC),
    ('客户反馈被分为P1时，多久给出处理计划，多久同步一次进展？', AgentRouteType.PRODUCT_DOC),
    ('正式上线前，需求锁定后还能改哪些内容？', AgentRouteType.PRODUCT_DOC),
    ('灰度发布发现异常时，回滚的准入条件是什么？', AgentRouteType.PRODUCT_DOC),
    ('产品 FAQ 有哪些功能说明？', AgentRouteType.PRODUCT_DOC),
    ('VPN版本升级后一直登录失败，怎么排查？', AgentRouteType.IT_SUPPORT),
    ('VPN客户端升级失败，怎么回滚旧客户端？', AgentRouteType.IT_SUPPORT),
    ('我的账号无法登录，提示网络错误。', AgentRouteType.IT_SUPPORT),
    ('申请GitLab权限需要谁审批？', AgentRouteType.IT_SUPPORT),
    ('我是研发部王明，请为我申请GitLab权限。', AgentRouteType.PROCESS_APPLY),
    ('报销需要哪些发票？', AgentRouteType.FINANCE_PROCESS),
]


class ProductRouteBoundaryTests(unittest.TestCase):
    def test_rule_fallback_product_and_neighboring_intents(self):
        for question, expected in CASES:
            with self.subTest(question=question):
                self.assertEqual(route_question_by_rules(question).route_type, expected)

    def test_actual_router_uses_rules_when_model_unavailable(self):
        with patch('backend.services.agent_router._decide_need_knowledge_base_by_llm', return_value=None):
            for question, expected in CASES:
                with self.subTest(question=question):
                    decision = route_question(question, use_rag=False, client=object())
                    self.assertEqual(decision.route_type, expected)
                    self.assertFalse(decision.should_use_rag)
                    self.assertEqual(decision.rewritten_query, '')


if __name__ == '__main__':
    unittest.main()

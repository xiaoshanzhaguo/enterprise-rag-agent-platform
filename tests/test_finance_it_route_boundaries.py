"""财务应用的访问问题与承载它的 IT 资源分开分类，不调用模型。"""

import unittest

from backend.services.agent_router import AgentRouteType, route_question, route_question_by_rules


CASES = [
    ('finance_credentials', '财务报销系统的管理员账号和初始密码分别是什么？', AgentRouteType.FINANCE_PROCESS),
    ('finance_login', '财务系统登录失败，应该联系谁？', AgentRouteType.FINANCE_PROCESS),
    ('expense_permission', '费用报销平台的审批权限由谁配置？', AgentRouteType.FINANCE_PROCESS),
    ('payment_account', '付款管理系统的管理员账户在哪里查看？', AgentRouteType.FINANCE_PROCESS),
    ('invoice_password', '发票平台的初始密码如何重置？', AgentRouteType.FINANCE_PROCESS),
    ('finance_via_vpn', '通过VPN访问财务报销系统时，系统管理员账号是什么？', AgentRouteType.FINANCE_PROCESS),
    ('finance_after_server', '服务器已经恢复，财务系统的登录密码如何修改？', AgentRouteType.FINANCE_PROCESS),
    ('finance_policy', '财务报销需要哪些发票，金额超过5000元由谁审批？', AgentRouteType.FINANCE_PROCESS),
    ('linux_admin', 'Linux服务器的管理员账号和初始密码是什么？', AgentRouteType.IT_SUPPORT),
    ('windows_admin', 'Windows操作系统的管理员密码怎么重置？', AgentRouteType.IT_SUPPORT),
    ('computer_admin', '计算机系统管理员凭据由谁保管？', AgentRouteType.IT_SUPPORT),
    ('finance_server', '财务系统所在服务器的管理员账号由谁管理？', AgentRouteType.IT_SUPPORT),
    ('finance_database', '财务报销系统的数据库管理员密码如何轮换？', AgentRouteType.IT_SUPPORT),
    ('finance_os', '财务平台所在Linux操作系统的root口令如何重置？', AgentRouteType.IT_SUPPORT),
    ('finance_staff_vpn', '财务部员工的VPN账号登录失败如何处理？', AgentRouteType.IT_SUPPORT),
    ('finance_staff_gitlab', '负责报销开发的GitLab管理员权限由谁审批？', AgentRouteType.IT_SUPPORT),
    ('finance_staff_pc', '处理报销的电脑管理员账户被锁定了，怎么办？', AgentRouteType.IT_SUPPORT),
    ('finance_context_network', '财务系统无法访问，VPN账号认证失败如何排查？', AgentRouteType.IT_SUPPORT),
    ('vpn_before_finance', 'VPN账号已解锁，财务系统的管理员密码如何重置？', AgentRouteType.FINANCE_PROCESS),
    ('generic_account', '我的账号一直登录失败，怎么排查？', AgentRouteType.IT_SUPPORT),
    ('application', '我是财务部王芳，请帮我提交报销申请。', AgentRouteType.PROCESS_APPLY),
    ('product', '涉及财务系统权限的版本发布检查需要哪些步骤？', AgentRouteType.PRODUCT_DOC),
    ('security', '公司财务电脑允许安装未经批准的软件吗？', AgentRouteType.IT_SUPPORT),
    ('unrelated', '员工年假需要提前几天申请？', AgentRouteType.KNOWLEDGE_QA),
]


ROLE_CASES = [
    ('linux_admin_expense', 'Linux服务器管理员出差的费用如何报销？', AgentRouteType.FINANCE_PROCESS),
    ('database_admin_travel', '数据库管理员的差旅报销标准是什么？', AgentRouteType.FINANCE_PROCESS),
    ('server_admin_invoice', '服务器管理员报销时需要提交哪些发票？', AgentRouteType.FINANCE_PROCESS),
    ('os_admin_travel', '操作系统管理员出差的住宿费用标准是多少？', AgentRouteType.FINANCE_PROCESS),
    ('linux_admin_account', 'Linux服务器管理员账号如何申请？', AgentRouteType.IT_SUPPORT),
    ('database_admin_password', '数据库管理员忘记登录密码怎么办？', AgentRouteType.IT_SUPPORT),
    ('server_admin_permission', '如何申请服务器管理员权限？', AgentRouteType.IT_SUPPORT),
    ('server_admin_auth', '服务器管理员的登录认证失败如何排查？', AgentRouteType.IT_SUPPORT),
]


class AdminRoleRouteBoundaryTests(unittest.TestCase):
    def test_roles_do_not_replace_business_intent(self):
        for case_id, question, expected in ROLE_CASES:
            with self.subTest(case_id=case_id, question=question):
                decision = route_question_by_rules(question)
                self.assertEqual(decision.route_type, expected)
                self.assertTrue(decision.should_use_rag)

    def test_role_fallback_respects_rag_switch(self):
        for case_id, question, expected in ROLE_CASES:
            for use_rag in (True, False):
                with self.subTest(case_id=case_id, use_rag=use_rag):
                    decision = route_question(question, use_rag=use_rag, client=None)
                    self.assertEqual(decision.route_type, expected)
                    self.assertEqual(decision.should_use_rag, use_rag)
                    self.assertEqual(decision.rewritten_query, question.lower() if use_rag else '')


class FinanceITRouteBoundaryTests(unittest.TestCase):
    def test_system_access_context_and_adjacent_domains(self):
        for case_id, question, expected in CASES:
            with self.subTest(case_id=case_id, question=question):
                decision = route_question_by_rules(question)
                self.assertEqual(decision.route_type, expected)
                self.assertTrue(decision.should_use_rag)

    def test_actual_router_fallback_respects_rag_switch(self):
        # client=None 直接使用真实规则降级路径，不创建模型或伪造分类结果。
        for case_id, question, expected in CASES:
            for use_rag in (True, False):
                with self.subTest(case_id=case_id, use_rag=use_rag):
                    decision = route_question(question, use_rag=use_rag, client=None)
                    self.assertEqual(decision.route_type, expected)
                    self.assertEqual(decision.should_use_rag, use_rag)
                    self.assertEqual(decision.rewritten_query, question.lower() if use_rag else '')


if __name__ == '__main__':
    unittest.main()

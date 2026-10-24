"""离线验证模板渲染和 Router 接入，不请求模型服务。"""

import json
import unittest
from unittest.mock import patch

from backend.prompt.agent_prompts import build_agent_decision_messages
from backend.prompt.process_prompts import build_process_field_extraction_messages
from backend.services.agent_router import (
    AgentRouteType,
    _decide_need_knowledge_base_by_llm,
)


class PromptTemplateTests(unittest.TestCase):
    def test_agent_json_example_and_user_braces(self):
        question = '根据 {附件} 查询制度，原文含 {"days": 3}'
        messages = build_agent_decision_messages(question)
        self.assertEqual(messages[1]["content"], "用户问题：" + question)
        example = messages[0]["content"].split("JSON 格式必须是：")[1]
        self.assertEqual(json.loads(example)["need_knowledge_base"], True)

    def test_process_fields_and_input_are_preserved(self):
        definitions = {"applicant": "申请人", "reason": "原因 {原文}"}
        text = '张三申请请假，备注 {"type": "leave"}'
        messages = build_process_field_extraction_messages("请假", definitions, text)
        self.assertIn(json.dumps(definitions, ensure_ascii=False), messages[0]["content"])
        self.assertEqual(messages[1]["content"], "用户提供的申请内容：\n" + text)
        self.assertIn('"missing_fields"', messages[0]["content"])

    def test_process_requires_field_definitions(self):
        for definitions in ({}, {"": "申请人"}, {"name": ""}):
            with self.subTest(definitions=definitions), self.assertRaises(ValueError):
                build_process_field_extraction_messages("请假", definitions, "张三")

    @patch("backend.services.agent_router.invoke_chat_model")
    def test_router_uses_templates_and_preserves_decision(self, invoke):
        invoke.return_value = json.dumps({
            "route_type": "PROCESS_APPLY", "need_knowledge_base": True,
            "reason": "查询申请制度", "rewritten_query": "  请假申请规则  ",
        })
        client = object()
        decision = _decide_need_knowledge_base_by_llm("怎么申请请假？", client)
        self.assertEqual(decision.route_type, AgentRouteType.PROCESS_APPLY)
        self.assertEqual(decision.rewritten_query, "请假申请规则")
        invoke.assert_called_once_with(
            client=client, messages=build_agent_decision_messages("怎么申请请假？"),
            temperature=0,
        )


if __name__ == "__main__":
    unittest.main()

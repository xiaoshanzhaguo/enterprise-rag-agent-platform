"""流程字段协议、路由和 SSE 持久化的离线测试。"""

import asyncio
import json
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from backend.schema.chat_schema import ChatRequest
from backend.services.agent_router import AgentRouteDecision, AgentRouteType, route_question
from backend.services.agent_service import run_agent_stream
from backend.services.process_extraction import extract_process_fields


class ProcessExtractionTests(unittest.TestCase):
    @patch("backend.services.process_extraction.invoke_chat_model")
    def test_all_fields_and_missing_list_recomputed(self, invoke):
        invoke.return_value = json.dumps({"fields": {
            "application_type": "VPN权限", "applicant": " 张三 ", "department": "研发部",
            "permission_level": "只读", "approver": "李经理", "additional_info": "远程办公",
        }, "missing_fields": ["applicant"]})
        result = extract_process_fields("张三，研发部，申请VPN只读权限，审批人李经理，用于远程办公", object())
        self.assertEqual(result.fields.applicant, "张三")
        self.assertEqual(result.missing_fields, [])
        self.assertEqual(result.status, "extracted")

    @patch("backend.services.process_extraction.invoke_chat_model")
    def test_fenced_json_and_missing_values(self, invoke):
        invoke.return_value = '```json\n{"fields":{"application_type":"请假","applicant":" "}}\n```'
        result = extract_process_fields("我想请假", object())
        self.assertIsNone(result.fields.applicant)
        self.assertIn("applicant", result.missing_fields)
        self.assertEqual(len(result.missing_fields), 5)

    @patch("backend.services.process_extraction.invoke_chat_model")
    def test_invalid_output_and_call_failure(self, invoke):
        for raw in ('不是JSON', '[]', '{}', '{"fields":{"applicant":123}}',
                    '{"fields":{"unknown":"张三"}}', '{"fields":null}'):
            with self.subTest(raw=raw):
                invoke.return_value = raw
                result = extract_process_fields("申请", object())
                self.assertEqual(result.status, "failed")
                self.assertEqual(len(result.missing_fields), 6)
        invoke.side_effect = RuntimeError("private connection information")
        self.assertNotIn("private", extract_process_fields("申请", object()).model_dump_json())

    @patch("backend.services.agent_router._decide_need_knowledge_base_by_llm", return_value=None)
    def test_rag_off_still_routes_permission_application(self, decide):
        result = route_question("申请VPN只读权限", use_rag=False, client=object())
        self.assertEqual(result.route_type, AgentRouteType.PROCESS_APPLY)
        self.assertFalse(result.should_use_rag)
        self.assertEqual(result.rewritten_query, "")

    def test_process_sse_and_saved_metadata_even_without_rag_hit(self):
        async def collect(response):
            return [json.loads(item.removeprefix("data: ")) async for item in response.body_iterator]

        module = "backend.services.agent_service."
        with ExitStack() as stack:
            mocks = {name: stack.enter_context(patch(module + name)) for name in (
                "get_chat_session_title", "ensure_chat_session", "save_chat_message",
                "route_question", "_retrieve_agent_rag_chunks", "save_rag_query_with_hits",
                "update_rag_query_answer", "_build_agent_rag_metadata", "stream_chat_model",
            )}
            mocks["get_chat_session_title"].return_value = "申请"
            mocks["save_chat_message"].return_value = 42
            mocks["_build_agent_rag_metadata"].return_value = {}
            mocks["route_question"].return_value = AgentRouteDecision(
                AgentRouteType.PROCESS_APPLY, "流程申请", True, "权限申请",
            )
            mocks["_retrieve_agent_rag_chunks"].return_value = ([], "no_hit", "权限申请", "")
            invoke = stack.enter_context(patch("backend.services.process_extraction.invoke_chat_model"))
            invoke.return_value = '{"fields":{"application_type":"VPN权限","applicant":"张三"}}'
            response = run_agent_stream(ChatRequest(
                session_id="s", task_type="agent", use_rag=True,
                input_text="张三申请VPN权限",
            ), object())
            events = asyncio.run(collect(response))
            self.assertNotIn("error", [e["event_type"] for e in events])
            final = next(e for e in events if e["event_type"] == "final")
            self.assertEqual(final['message_id'], 42)
            payload = json.loads(json.loads(final["content"])["generate_answer"])
            self.assertEqual(payload["fields"]["applicant"], "张三")
            self.assertEqual(final["metadata"]["process_extraction"], payload)
            saved = mocks["save_chat_message"].call_args.kwargs
            self.assertEqual(saved["metadata"]["process_extraction"], payload)
            mocks["stream_chat_model"].assert_not_called()


if __name__ == "__main__":
    unittest.main()

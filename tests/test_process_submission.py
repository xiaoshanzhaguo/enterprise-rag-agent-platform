"""Webhook HTTP 契约、API 和 n8n Code 节点的离线验证。"""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx

from backend.api.processes import router
from backend.db.init_db import init_database
from backend.schema.process_submission_schema import ProcessSubmissionRequest, ProcessSubmissionResponse
from backend.services.process_submission import ProcessSubmissionError, submit_process_application


class ProcessSubmissionTests(unittest.TestCase):
    def setUp(self):
        self.database_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.database_directory.cleanup)
        self.database_settings = SimpleNamespace(database_url="sqlite:///" + str(Path(self.database_directory.name) / "test.db"))
        self.database_patch = patch("backend.db.connection.settings", self.database_settings)
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)
        init_database()
        self.transport_calls = 0
        self.request = ProcessSubmissionRequest(fields={
            "application_type": "VPN权限", "applicant": "张三", "department": "研发部",
        })
        self.ack = {
            "request_id": str(self.request.request_id), "status": "simulated",
            "ticket_id": "SIM-123", "actions": ["ticket_simulated"],
        }
        self.settings_patch = patch("backend.services.process_submission.settings")
        self.settings = self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)
        self.settings.n8n_webhook_url = "http://n8n:5678/webhook/process-application"
        self.settings.n8n_webhook_token = "test-token"
        self.settings.n8n_webhook_timeout_seconds = 10

    def run_with_transport(self, handler):
        # 每个传输故障场景使用独立数据库，避免相同编号触发重复提交保护。
        self.transport_calls += 1
        database_url = "sqlite:///" + str(Path(self.database_directory.name) / f"transport-{self.transport_calls}.db")
        init_database(database_url)
        client = httpx.Client(transport=httpx.MockTransport(handler))
        with patch.object(self.database_settings, "database_url", database_url), patch("backend.services.process_submission.httpx.Client", return_value=client):
            return submit_process_application(self.request)

    def test_webhook_body_header_and_ack(self):
        calls = []

        def handle(request):
            calls.append(request)
            self.assertEqual(request.headers["X-Webhook-Token"], "test-token")
            body = json.loads(request.content)
            self.assertEqual(body["event"], "process_application.submitted")
            self.assertEqual(body["fields"]["applicant"], "张三")
            self.assertEqual(body["request_id"], str(self.request.request_id))
            return httpx.Response(200, json=self.ack)

        result = self.run_with_transport(handle)
        self.assertEqual(result.status, "simulated")
        self.assertEqual(len(calls), 1)

    def test_config_missing_or_invalid_does_not_send(self):
        cases = [("n8n_webhook_url", None), ("n8n_webhook_url", "file:///secret"),
                 ("n8n_webhook_token", ""), ("n8n_webhook_timeout_seconds", -1),
                 ("n8n_webhook_timeout_seconds", float("nan"))]
        for name, value in cases:
            self.request.request_id = uuid4()
            original = getattr(self.settings, name)
            with self.subTest(name=name), patch("backend.services.process_submission.httpx.Client") as client:
                setattr(self.settings, name, value)
                with self.assertRaises(ProcessSubmissionError) as error:
                    submit_process_application(self.request)
                self.assertEqual(error.exception.status_code, 503)
                client.assert_not_called()
            setattr(self.settings, name, original)

    def test_bad_ack_and_http_errors_are_not_success(self):
        responses = [httpx.Response(401), httpx.Response(500),
                     httpx.Response(302, headers={"Location": "http://other"}),
                     httpx.Response(200, text="Workflow was started"),
                     httpx.Response(200, json={}),
                     httpx.Response(200, json={**self.ack, "request_id": str(uuid4())}),
                     httpx.Response(200, json={**self.ack, "status": "approved"})]
        for response in responses:
            with self.subTest(response=response), self.assertRaises(ProcessSubmissionError) as error:
                self.run_with_transport(lambda request: response)
            self.assertEqual(error.exception.status_code, 502)

    def test_timeout_and_network_errors_are_not_retried(self):
        for exception, code in ((httpx.ReadTimeout("secret"), 504), (httpx.ConnectError("secret"), 502)):
            calls = []

            def fail(request):
                calls.append(request)
                raise exception

            with self.subTest(code=code), self.assertRaises(ProcessSubmissionError) as error:
                self.run_with_transport(fail)
            self.assertEqual(error.exception.status_code, code)
            self.assertNotIn("secret", str(error.exception))
            self.assertEqual(len(calls), 1)

    def test_api_validation_and_error_codes(self):
        app = FastAPI()
        app.include_router(router)
        with TestClient(app) as client, patch("backend.api.processes.submit_process_application") as submit:
            for fields in ({}, {"application_type": "申请", "applicant": "  ", "department": "研发"},
                           {"application_type": "申请", "applicant": "张三", "department": "研发", "unknown": "x"}):
                self.assertEqual(client.post("/process_applications", json={"fields": fields}).status_code, 422)
            submit.assert_not_called()
            submit.return_value = ProcessSubmissionResponse.model_validate(self.ack)
            response = client.post("/process_applications", json=self.request.model_dump(mode="json"))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), self.ack)
            for code in (502, 503, 504):
                submit.side_effect = ProcessSubmissionError(code, "流程异常")
                self.assertEqual(client.post("/process_applications", json=self.request.model_dump(mode="json")).status_code, code)

    @unittest.skipUnless(shutil.which("node"), "需要 Node.js 来验证 n8n Code 节点")
    def test_n8n_workflow_code_nodes_and_connections(self):
        workflow = json.loads((Path(__file__).resolve().parents[1] / "integrations/n8n/process_application.json").read_text(encoding="utf-8"))
        nodes = workflow["nodes"]
        self.assertFalse(workflow["active"])
        self.assertEqual(nodes[0]["parameters"]["authentication"], "headerAuth")
        self.assertEqual(nodes[0]["parameters"]["responseMode"], "responseNode")
        for current, following in zip(nodes, nodes[1:]):
            self.assertEqual(workflow["connections"][current["name"]]["main"][0][0]["node"], following["name"])
        payload = {"event": "process_application.submitted", **self.request.model_dump(mode="json")}
        # 在 Node 中逐个执行工作流内的原始 JS，模拟 n8n 的 $input.first()。
        script = "const workflow = " + json.dumps(workflow) + ";\n"
        script += "let items = [{json:{body:" + json.dumps(payload) + "}}];\n"
        script += "for (const node of workflow.nodes.filter(n => n.type === 'n8n-nodes-base.code')) { items = new Function('$input', node.parameters.jsCode)({first: () => items[0]}); }\n"
        script += "process.stdout.write(JSON.stringify(items[0].json));"
        result = subprocess.run([shutil.which("node"), "-"], input=script, capture_output=True, text=True, encoding="utf-8", check=True)
        ack = ProcessSubmissionResponse.model_validate_json(result.stdout)
        self.assertEqual(ack.request_id, self.request.request_id)
        self.assertEqual(ack.actions, ["table_row_simulated", "notification_simulated", "ticket_simulated"])


if __name__ == "__main__":
    unittest.main()

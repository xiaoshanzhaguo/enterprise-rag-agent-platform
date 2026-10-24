"""字段提取接口不触发提交；验证输入、模型失败及协议。"""

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.processes import router
from backend.schema.process_schema import ProcessExtractionResult, ProcessFields


class ProcessExtractApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    @patch("backend.api.processes.submit_process_application")
    @patch("backend.api.processes.extract_process_fields")
    @patch("backend.api.processes.get_client")
    def test_extract_success_does_not_submit(self, get_client, extract, submit):
        extract.return_value = ProcessExtractionResult(fields=ProcessFields(applicant="张三"), missing_fields=["department"])
        response = self.client.post("/process_fields/extract", json={"input_text": " 张三申请VPN "})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["fields"]["applicant"], "张三")
        extract.assert_called_once_with("张三申请VPN", get_client.return_value)
        submit.assert_not_called()

    @patch("backend.api.processes.get_client")
    def test_empty_or_invalid_input_rejected(self, get_client):
        for payload in ({}, {"input_text": " "}, {"input_text": "x" * 20001}, {"input_text": "申请", "extra": "x"}):
            with self.subTest(payload=str(payload)[:40]):
                self.assertEqual(self.client.post("/process_fields/extract", json=payload).status_code, 422)
        get_client.assert_not_called()

    @patch("backend.api.processes.extract_process_fields")
    @patch("backend.api.processes.get_client")
    def test_model_errors(self, get_client, extract):
        get_client.side_effect = ValueError("private")
        response = self.client.post("/process_fields/extract", json={"input_text": "申请"})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private", response.text)
        get_client.side_effect = None
        extract.return_value = ProcessExtractionResult(fields=ProcessFields(), missing_fields=[], status="failed", error="提取失败")
        self.assertEqual(self.client.post("/process_fields/extract", json={"input_text": "申请"}).status_code, 502)

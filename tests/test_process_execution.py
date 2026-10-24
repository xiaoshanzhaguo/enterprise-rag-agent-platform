"""临时 SQLite 上验证完整提交持久化、迁移及重复提交保护。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
from threading import Barrier
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.processes import router
from backend.db.connection import get_connection
from backend.db.init_db import init_database
from backend.db.process_repository import get_process_execution
from backend.db.schema import CREATE_TABLE_SQL
from backend.schema.process_submission_schema import ProcessSubmissionRequest, ProcessSubmissionResponse
from backend.services.process_submission import ProcessSubmissionError, submit_process_application


class ProcessExecutionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database_url = "sqlite:///" + str(Path(directory.name) / "execution.db")
        database_patch = patch("backend.db.connection.settings", SimpleNamespace(database_url=self.database_url))
        database_patch.start()
        self.addCleanup(database_patch.stop)
        init_database()
        self.request = ProcessSubmissionRequest(session_id="s", fields={
            "application_type": "请假", "applicant": "张三", "department": "研发部",
        })
        self.ack = ProcessSubmissionResponse(
            request_id=self.request.request_id, status="simulated",
            ticket_id="SIM-test", actions=["ticket_simulated"],
        )

    def record(self):
        return get_process_execution(str(self.request.request_id))

    @patch("backend.services.process_submission._call_n8n_webhook")
    def test_success_saved_before_return_and_queryable(self, call):
        def execute(request):
            record = self.record()
            self.assertEqual(record["status"], "running")
            self.assertIsNone(record["finished_at"])
            return self.ack

        call.side_effect = execute
        app = FastAPI()
        app.include_router(router)
        with TestClient(app) as client:
            response = client.post("/process_applications", json=self.request.model_dump(mode="json"))
            self.assertEqual(response.status_code, 200)
            result = client.get(f"/process_applications/{self.request.request_id}")
            self.assertEqual(result.status_code, 200)
            record = result.json()
            self.assertEqual(record["status"], "simulated")
            self.assertEqual(record["response_status_code"], 200)
            self.assertEqual(record["output"], response.json())
            self.assertEqual(record["input"]["fields"]["applicant"], "张三")
            self.assertTrue(record["finished_at"])
            self.assertEqual(client.get(f"/process_applications/{uuid4()}").status_code, 404)
            self.assertEqual(client.get("/process_applications/invalid").status_code, 422)
        # 删除聊天会话只断开外键关联，不删除执行审计记录。
        with closing(get_connection()) as connection, connection:
            connection.execute("DELETE FROM chat_sessions WHERE id = 's'")
        self.assertIsNone(self.record()["session_id"])
        self.assertEqual(self.record()["input"]["session_id"], "s")

    @patch("backend.services.process_submission._call_n8n_webhook")
    def test_failure_and_timeout_saved(self, call):
        for code, state in ((502, "failed"), (503, "failed"), (504, "timeout")):
            with self.subTest(code=code):
                self.request.request_id = uuid4()
                call.side_effect = ProcessSubmissionError(code, "脱敏错误")
                with self.assertRaises(ProcessSubmissionError):
                    submit_process_application(self.request)
                record = self.record()
                self.assertEqual(record["status"], state)
                self.assertEqual(record["response_status_code"], code)
                self.assertEqual(record["error_message"], "脱敏错误")
                self.assertIsNone(record["output"])
                self.assertTrue(record["finished_at"])

    @patch("backend.services.process_submission._call_n8n_webhook")
    def test_concurrent_duplicate_does_not_send_twice(self, call):
        call.return_value = self.ack
        barrier = Barrier(2)

        def submit():
            barrier.wait(timeout=5)
            try:
                submit_process_application(self.request)
                return 200
            except ProcessSubmissionError as exc:
                return exc.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: submit(), range(2)))
        self.assertEqual(sorted(results), [200, 409])
        call.assert_called_once()
        self.assertEqual(self.record()["status"], "simulated")

    @patch("backend.services.process_submission._call_n8n_webhook")
    def test_start_storage_failure_never_calls_webhook(self, call):
        with patch("backend.services.process_submission.start_process_execution", side_effect=sqlite3.OperationalError("private")):
            with self.assertRaises(ProcessSubmissionError) as error:
                submit_process_application(self.request)
        self.assertEqual(error.exception.status_code, 503)
        call.assert_not_called()

    @patch("backend.services.process_submission._call_n8n_webhook")
    def test_finish_storage_failure_keeps_running_record(self, call):
        call.return_value = self.ack
        with patch("backend.services.process_submission.finish_process_execution", side_effect=sqlite3.OperationalError("private")):
            with self.assertRaises(ProcessSubmissionError) as error:
                submit_process_application(self.request)
        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(self.record()["status"], "running")
        with self.assertRaises(ProcessSubmissionError) as duplicate:
            submit_process_application(self.request)
        self.assertEqual(duplicate.exception.status_code, 409)
        call.assert_called_once()

    def test_legacy_table_migrates_without_losing_records(self):
        # 用同一临时目录中的另一文件重建第 19 天的旧表。
        legacy_url = self.database_url.replace("execution.db", "legacy.db")
        legacy_sql = next(sql for sql in CREATE_TABLE_SQL if "CREATE TABLE IF NOT EXISTS n8n_execution_records" in sql)
        legacy_sql = legacy_sql.replace("        request_id TEXT,\n", "").replace("        response_status_code INTEGER,\n", "")
        with closing(get_connection(legacy_url)) as connection, connection:
            for sql in CREATE_TABLE_SQL:
                if "CREATE TABLE IF NOT EXISTS n8n_execution_records" not in sql:
                    connection.execute(sql)
            connection.execute(legacy_sql)
            connection.execute("INSERT INTO n8n_execution_records (workflow_name) VALUES ('legacy')")
        init_database(legacy_url)
        init_database(legacy_url)
        with closing(get_connection(legacy_url)) as connection:
            row = connection.execute("SELECT * FROM n8n_execution_records").fetchone()
            self.assertEqual(row["workflow_name"], "legacy")
            self.assertIsNone(row["request_id"])
            self.assertIsNone(row["response_status_code"])


if __name__ == "__main__":
    unittest.main()

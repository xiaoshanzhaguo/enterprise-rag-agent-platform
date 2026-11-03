"""C1：FastAPI + 临时 SQLite + Fake Adapter；不启动真实模型或 Chroma。"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import sqlite3
from threading import BoundedSemaphore, Event
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.api.chat import router as chat_router
from backend.api.rag_index import router, get_index_management_service
from backend.db import index_repository as repository
from backend.db.repository import save_document_with_chunks
from backend.services.rag_documents import IndexManagementService
from backend.services.rag_indexing import RagIndexingService
from tests import test_index_repository as fixture
from tests import test_rag_indexing_service as executors


class RagIndexApiTests(unittest.TestCase):
    prepare = fixture.IndexRepositoryTests.prepare
    counts = fixture.IndexRepositoryTests.counts

    def setUp(self):
        fixture.IndexRepositoryTests.setUp(self)
        self.embedding = executors.FakeEmbedding()
        self.store = executors.FakeVectorStore()
        self.gate = BoundedSemaphore(1)
        self.engine = RagIndexingService(self.embedding, self.store, owner_instance_id="api-test", execution_gate=self.gate)
        self.profile = dict(fixture.PROFILE, embedding_dimension=None, distance_metric=None)
        self.profile_factory = Mock(side_effect=lambda: deepcopy(self.profile))
        self.manager = IndexManagementService(indexing=self.engine, mode="vector", profile_factory=self.profile_factory)
        app = FastAPI()
        app.include_router(chat_router)
        app.include_router(router)
        app.dependency_overrides[get_index_management_service] = lambda: self.manager
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def upload(self, **changes):
        request = dict(session_id="upload", file_name="finance.md", document_text="报销需要提供发票。")
        request.update(changes)
        return self.client.post("/index_document", json=request)

    def pending(self, **changes):
        return self.prepare(vector=True, target_profile=self.profile, **changes).operation

    def action(self, task, action="resume", **changes):
        return self.client.post(f"/rag_index_operations/{task['operation_id']}/{action}",
            params=dict(session_id="upload", **changes), json={"expected_state_version": task["state_version"]})

    def test_keyword_upload_retains_old_fields_and_never_constructs_vector_engine(self):
        self.manager.mode = "keyword"
        self.manager.indexing = None
        with patch("backend.services.rag_documents.create_rag_indexing_service", side_effect=AssertionError("forbidden")):
            response = self.upload()
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["session_id"], "upload")
        self.assertEqual(data["knowledge_base_id"], "session_scoped")
        self.assertEqual(data["file_name"], "finance.md")
        self.assertEqual(data["knowledge_base_type"], "finance")
        self.assertEqual(data["process_type"], "reimbursement")
        self.assertEqual(data["text_status"], "persisted")
        self.assertEqual(data["vector_index"]["status"], "not_requested")
        self.assertEqual(data["vector_index"]["attempt_count"], 0)
        self.assertEqual(self.counts(), (1, data["chunk_count"], 1))
        self.profile_factory.assert_not_called()

    def test_vector_upload_binds_and_ready_duplicate_does_not_reexecute(self):
        first = self.upload()
        self.assertEqual(first.status_code, 200, first.text)
        data = first.json()
        self.assertEqual(data["vector_index"]["status"], "ready")
        operation = repository.get_index_operation(data["operation_id"])
        self.assertEqual(operation["target_profile"], fixture.PROFILE)
        second = self.upload()
        self.assertEqual(second.status_code, 200, second.text)
        self.assertTrue(second.json()["document_reused"])
        self.assertEqual(second.json()["operation_id"], data["operation_id"])
        self.assertEqual(len(self.embedding.calls), 1)
        self.assertEqual(len(self.store.writes), 1)
        self.assertEqual(self.counts(), (1, data["chunk_count"], 1))

    def test_status_query_is_read_only_redacted_and_location_is_usable(self):
        task = self.pending()
        active = repository.claim_index_operation(task["operation_id"], task["state_version"], "other")
        response = self.client.get(f"/rag_index_operations/{task['operation_id']}", params={"session_id": "upload"})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertTrue(data["vector_index"]["execution_claimed"])
        self.assertFalse(data["vector_index"]["resumable"])
        self.assertNotIn("attempt_token", response.text)
        self.assertNotIn("fingerprint", response.text)
        self.assertNotIn(active["attempt_token"], response.text)
        self.assertEqual(repository.get_index_operation(task["operation_id"]), active)
        self.assertEqual(self.embedding.calls, [])
        self.assertEqual(self.store.probes, [])
        self.assertEqual(self.client.get(data["status_url"]).status_code, 200)

    def test_resume_pending_preserves_document_and_chunk_ids(self):
        task = self.pending()
        before = repository.load_index_input(task["operation_id"])
        response = self.action(task)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["resume_applied"])
        self.assertFalse(response.json()["retry_applied"])
        self.assertEqual(response.json()["vector_index"]["status"], "ready")
        self.assertEqual(repository.load_index_input(task["operation_id"])["chunks"], before["chunks"])
        self.assertEqual(self.counts(), (1, 2, 1))

    def test_claimed_resume_returns_202_without_reexecution(self):
        task = self.pending()
        active = repository.claim_index_operation(task["operation_id"], task["state_version"], "other")
        response = self.action(active)
        self.assertEqual(response.status_code, 202, response.text)
        self.assertFalse(response.json()["resume_applied"])
        self.assertTrue(response.json()["vector_index"]["execution_claimed"])
        self.assertEqual(repository.get_index_operation(task["operation_id"]), active)
        self.assertEqual(self.embedding.calls, [])

    def test_retry_failed_and_resume_wrong_action_has_receipt(self):
        task = self.pending()
        self.embedding.error = RuntimeError("secret-token")
        failure = self.action(task)
        self.assertEqual(failure.status_code, 502, failure.text)
        receipt = failure.json()["receipt"]
        self.assertTrue(receipt["resume_applied"])
        self.assertEqual(receipt["text_status"], "persisted")
        self.assertNotIn("secret-token", failure.text)
        failed = repository.get_index_operation(task["operation_id"])
        self.assertEqual(self.action(failed).status_code, 409)
        self.embedding.error = None
        retry = self.action(failed, "retry")
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertTrue(retry.json()["retry_applied"])
        self.assertEqual(retry.json()["vector_index"]["status"], "ready")

    def test_interrupted_task_requires_explicit_retry(self):
        task = self.pending()
        active = repository.claim_index_operation(task["operation_id"], task["state_version"], "stopped-worker")
        interrupted = repository.recover_index_operation(active["operation_id"], active["state_version"],
                                                         execution_stopped_confirmed=True)
        self.assertEqual(self.action(interrupted).status_code, 409)
        self.assertEqual(self.embedding.calls, [])
        retry = self.action(interrupted, "retry")
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertTrue(retry.json()["retry_applied"])

    def test_ready_repeated_resume_retry_are_read_only(self):
        task = self.pending()
        self.assertEqual(self.action(task).status_code, 200)
        ready = repository.get_index_operation(task["operation_id"])
        for action in ("retry", "resume"):
            response = self.action(task, action)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse(response.json()[f"{action}_applied"])
        self.assertEqual(repository.get_index_operation(task["operation_id"]), ready)
        self.assertEqual(len(self.store.writes), 1)

    def test_capacity_exhaustion_returns_pending_and_location_then_resume(self):
        self.gate.acquire()
        try:
            response = self.upload()
        finally:
            self.gate.release()
        self.assertEqual(response.status_code, 202, response.text)
        data = response.json()
        self.assertTrue(data["vector_index"]["resumable"])
        self.assertFalse(data["vector_index"]["execution_claimed"])
        self.assertEqual(data["vector_index"]["attempt_count"], 0)
        self.assertEqual(self.client.get(response.headers["Location"]).status_code, 200)
        task = repository.get_index_operation(data["operation_id"])
        self.assertEqual(self.action(task).status_code, 200)

    def test_retry_without_capacity_remains_unclaimed_and_resumable(self):
        task = self.pending()
        self.embedding.error = RuntimeError("injected")
        self.action(task)
        failed = repository.get_index_operation(task["operation_id"])
        self.gate.acquire()
        try:
            response = self.action(failed, "retry")
        finally:
            self.gate.release()
        self.assertEqual(response.status_code, 202, response.text)
        self.assertFalse(response.json()["retry_applied"])
        self.assertTrue(response.json()["vector_index"]["resumable"])

    def test_finish_failure_returns_unconfirmed_503_with_persisted_text_ids(self):
        with patch.object(repository, "finish_index_operation", side_effect=sqlite3.OperationalError("secret/path")):
            response = self.upload()
        self.assertEqual(response.status_code, 503, response.text)
        receipt = response.json()["receipt"]
        self.assertEqual(receipt["text_status"], "persisted")
        self.assertFalse(receipt["state_confirmed"])
        self.assertIsNone(receipt["vector_index"])
        self.assertTrue(receipt["document_id"])
        self.assertNotIn("secret/path", response.text)
        query = self.client.get(receipt["status_url"])
        self.assertTrue(query.json()["vector_index"]["execution_claimed"])

    def test_prepare_and_query_database_errors_are_503_without_fake_receipts(self):
        with patch.object(repository, "prepare_document_index", side_effect=sqlite3.OperationalError("private")):
            response = self.upload()
        self.assertEqual(response.status_code, 503)
        self.assertIsNone(response.json()["receipt"])
        self.assertEqual(self.counts(), (0, 0, 0))
        with patch.object(repository, "get_index_operation", side_effect=sqlite3.OperationalError("private")):
            query = self.client.get(f"/rag_index_operations/{uuid4()}", params={"session_id": "upload"})
        self.assertEqual(query.status_code, 503)
        self.assertNotIn("private", query.text)

    def test_static_profile_conflict_and_keyword_disable_do_not_claim(self):
        task = self.pending()
        self.profile["model"] = "changed-model"
        response = self.action(task)
        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.json()["receipt"]["vector_index"]["current_config_compatible"])
        self.manager.mode = "keyword"
        self.assertEqual(self.action(task).status_code, 409)
        self.assertEqual(repository.get_index_operation(task["operation_id"]), task)
        self.assertEqual(self.embedding.calls, [])

    def test_invalid_uploads_and_action_requests_are_rejected(self):
        self.assertEqual(self.upload(document_text=" ").status_code, 400)
        self.assertEqual(self.upload(knowledge_base_type="invalid").status_code, 400)
        self.assertEqual(self.upload(session_id=" ").status_code, 422)
        task = self.pending()
        for body in ({"expected_state_version": True}, {"expected_state_version": 0},
                     {"expected_state_version": 1, "attempt_token": "override"}):
            response = self.client.post(f"/rag_index_operations/{task['operation_id']}/resume",
                                       params={"session_id": "upload"}, json=body)
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.get("/rag_index_operations/invalid", params={"session_id": "upload"}).status_code, 422)
        self.assertEqual(self.client.get(f"/rag_index_operations/{uuid4()}", params={"session_id": "upload"}).status_code, 404)
        self.assertEqual(self.action(dict(task, state_version=99)).status_code, 409)
        self.assertEqual(self.client.post(f"/rag_index_operations/{task['operation_id']}/recover",
            json={"expected_state_version": task["state_version"], "confirm_execution_stopped": True}).status_code, 404)

    def test_resource_scope_and_shared_management_role_are_checked(self):
        task = self.pending()
        self.assertEqual(self.client.get(f"/rag_index_operations/{task['operation_id']}",
            params={"session_id": "another-session"}).status_code, 404)
        self.assertEqual(self.upload(knowledge_base_id="enterprise_default", user_role="employee").status_code, 403)
        shared = self.pending(knowledge_base_id="enterprise_default", file_name="shared.md")
        response = self.action(shared, knowledge_base_id="enterprise_default", user_role="employee")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.action(shared, knowledge_base_id="enterprise_default", user_role="kb_admin").status_code, 200)
        self.assertEqual(self.client.get(f"/rag_index_operations/{shared['operation_id']}",
            params={"session_id": shared["storage_session_id"]}).status_code, 404)

    def test_status_list_preserves_legacy_unknown_and_chunk_counts(self):
        self.manager.mode = "keyword"
        uploaded = self.upload().json()
        save_document_with_chunks("upload", "legacy.md", ["legacy-one", "legacy-two"])
        response = self.client.get("/rag_status/upload")
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertTrue(data["has_document"])
        self.assertEqual(data["document_count"], 2)
        self.assertEqual(data["chunk_count"], uploaded["chunk_count"] + 2)
        self.assertEqual(data["vector_index_summary"], dict(ready=0, pending=0, failed=0, not_requested=1, unknown=1))
        self.assertEqual(next(doc for doc in data["documents"] if doc["file_name"] == "legacy.md")["tracking_source"], "legacy")
        self.assertNotIn("attempt_token", response.text)

    def test_duplicate_failed_upload_does_not_implicitly_retry(self):
        self.embedding.error = RuntimeError("injected")
        first = self.upload()
        task = repository.get_index_operation(first.json()["receipt"]["operation_id"])
        self.embedding.error = None
        second = self.upload()
        self.assertEqual(second.status_code, 409, second.text)
        self.assertEqual(repository.get_index_operation(task["operation_id"]), task)
        self.assertEqual(len(self.embedding.calls), 1)

    def test_vector_write_failure_has_502_receipt_and_explicit_retry_succeeds(self):
        task = self.pending()
        self.store.write_error = RuntimeError("private Chroma details")
        failed = self.action(task)
        self.assertEqual(failed.status_code, 502, failed.text)
        receipt = failed.json()["receipt"]
        self.assertEqual(receipt["vector_index"]["error_code"], "upsert_outcome_unknown")
        self.assertEqual(receipt["document_id"], task["document_id"])
        self.assertNotIn("private Chroma details", failed.text)
        current = repository.get_index_operation(task["operation_id"])
        self.store.write_error = None
        retry = self.action(current, "retry")
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(self.counts(), (1, 2, 1))

    def test_keyword_to_vector_upload_reuses_the_same_operation_and_chunks(self):
        self.manager.mode = "keyword"
        first = self.upload().json()
        before = repository.load_index_input(first["operation_id"])
        self.manager.mode = "vector"
        response = self.upload()
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["operation_id"], first["operation_id"])
        self.assertEqual(data["document_id"], first["document_id"])
        self.assertEqual(repository.load_index_input(first["operation_id"])["chunks"], before["chunks"])
        self.assertEqual(data["vector_index"]["status"], "ready")

    def test_factory_failure_after_text_commit_keeps_known_identifiers(self):
        self.manager.indexing = None
        with patch("backend.services.rag_documents.create_rag_indexing_service", side_effect=RuntimeError("private")):
            response = self.upload()
        self.assertEqual(response.status_code, 503, response.text)
        receipt = response.json()["receipt"]
        self.assertEqual(receipt["text_status"], "persisted")
        self.assertFalse(receipt["state_confirmed"])
        self.assertIsNone(receipt["vector_index"])
        persisted = repository.get_index_operation(receipt["operation_id"])
        self.assertEqual(persisted["status"], "pending")
        self.assertFalse(persisted["execution_claimed"])
        self.assertNotIn("private", response.text)

    def test_real_factory_wiring_is_lazy_and_injectable(self):
        self.manager.indexing = None
        with patch("backend.services.rag_documents.create_rag_indexing_service", return_value=self.engine) as factory:
            response = self.upload()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(factory.call_args.kwargs["retrieval_mode"], "vector")

    def test_status_query_remains_responsive_while_sync_execution_is_blocked(self):
        task = self.pending()
        entered, release = Event(), Event()
        def block(context):
            entered.set()
            if not release.wait(timeout=5):
                raise TimeoutError("test did not release worker")
        self.embedding.on_call = block
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.action, task)
            try:
                self.assertTrue(entered.wait(timeout=3))
                query = self.client.get(f"/rag_index_operations/{task['operation_id']}", params={"session_id": "upload"})
                self.assertEqual(query.status_code, 200)
                self.assertTrue(query.json()["vector_index"]["execution_claimed"])
                self.assertEqual(self.action(task).status_code, 202)
            finally:
                release.set()
            self.assertEqual(future.result(timeout=5).status_code, 200)
        self.assertEqual(len(self.embedding.calls), 1)


if __name__ == "__main__":
    unittest.main()

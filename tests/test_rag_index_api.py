"""C1：FastAPI + 临时 SQLite + Fake Adapter；不启动真实模型或 Chroma。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from dataclasses import replace
import sqlite3
from threading import BoundedSemaphore, Event
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.api.chat import router as chat_router
from backend.api.rag_index import router, get_index_management_service
from backend.config import settings
from backend.db import index_repository as repository
from backend.db.connection import get_connection
from backend.db.init_db import init_database
from backend.db.repository import ensure_chat_session, save_document_with_chunks
from backend.services.rag_documents import IndexManagementService
from backend.services.agent_router import AgentRouteType
from backend.services.agent_service import _resolve_agent_knowledge_base_type_filter
from backend.services.rag_indexing import RagIndexingService
from tests import test_index_repository as fixture
from tests.test_document_text import image_file, pdf_file, word_file
from tests import test_rag_indexing_service as executors
from tests.workspace_session_fixture import authenticate_fixture


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
        ensure_chat_session('upload', owner_user_id=authenticate_fixture(self.client))

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

    def test_new_knowledge_base_keeps_its_name_and_description_after_upload(self):
        request = {"name": "项目资料", "description": "交付文档", "user_role": "kb_admin"}
        self.assertEqual(self.client.post("/knowledge_bases", json={**request, "user_role": "employee"}).status_code, 403)
        created = self.client.post("/knowledge_bases", json=request)
        self.assertEqual(created.status_code, 201, created.text)
        identity = created.json()["knowledge_base_id"]
        upload = self.upload(knowledge_base_id=identity, user_role="kb_admin")
        self.assertEqual(upload.status_code, 200, upload.text)
        bases = self.client.get("/knowledge_bases").json()["knowledge_bases"]
        self.assertTrue(any(base["knowledge_base_id"] == identity and base["name"] == "项目资料"
                            and base["description"] == "交付文档" for base in bases))

    def test_binary_upload_extraction_and_retrieval_in_selected_knowledge_base(self):
        self.manager.mode = "keyword"
        created = self.client.post("/knowledge_bases", json={"name": "测试资料", "user_role": "kb_admin"}).json()
        base_id = created["knowledge_base_id"]
        for name, data, query in [
            ("制度.docx", word_file(), "差旅报销提交行程单和发票"),
            ("policy.pdf", pdf_file(), "Travel approval requires a receipt."),
            ("invoice.png", image_file(), "Invoice 2026"),
        ]:
            with self.subTest(name=name):
                parsed = self.client.post("/extract_document_text", params={"file_name": name}, content=data)
                self.assertEqual(parsed.status_code, 200, parsed.text)
                document = self.upload(file_name=name, document_text=parsed.json()["document_text"],
                                       knowledge_base_id=base_id, user_role="kb_admin")
                self.assertEqual(document.status_code, 200, document.text)
                self.assertEqual(document.json()["knowledge_base_id"], base_id)
                with patch("backend.rag.service.settings", replace(settings, rag_retrieval_mode="keyword")):
                    preview = self.client.post("/rag_preview", json={
                        "session_id": "upload", "knowledge_base_id": base_id, "query": query,
                    })
                self.assertEqual(preview.status_code, 200, preview.text)
                self.assertTrue(any(chunk["file_name"] == name for chunk in preview.json()["chunks"]), preview.text)
        self.assertEqual(self.counts()[0], 3)
        self.assertFalse(self.embedding.calls)

    def test_knowledge_base_edit_delete_and_stale_upload_protection(self):
        self.manager.mode = "keyword"
        request = {"name": "知识库", "description": "简介", "user_role": "kb_admin"}
        base = self.client.post("/knowledge_bases", json=request).json()
        identity = base["knowledge_base_id"]
        path = f"/knowledge_bases/{identity}"

        changed = self.client.patch(path, json={"name": "新知识库", "description": ""})
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(changed.json()["knowledge_base_id"], identity)
        self.assertEqual(changed.json()["name"], "新知识库")
        self.assertIsNone(changed.json()["description"])
        self.assertEqual(self.client.delete("/knowledge_bases/enterprise_default").status_code, 409)
        self.assertEqual(self.client.delete(path).status_code, 200)
        self.assertEqual(self.client.patch(path, json={"name": "过期编辑"}).status_code, 404)
        self.assertEqual(self.upload(knowledge_base_id=identity, user_role="kb_admin").status_code, 404)
        self.assertNotIn(identity, [base["knowledge_base_id"] for base in self.client.get("/knowledge_bases").json()["knowledge_bases"]])
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_custom_category_can_be_renamed_without_breaking_retrieval(self):
        self.manager.mode = "keyword"
        path = "/knowledge_base_categories"
        created = self.client.post(path, json={"label": "合同归档"})
        self.assertEqual(created.status_code, 201, created.text)
        category = created.json()
        identity = category["category_id"]
        self.assertFalse(category["builtin"])
        self.assertEqual(self.client.post(path, json={"label": "  合同归档  "}).status_code, 409)
        self.assertEqual(self.client.post(path, json={"label": "hr"}).status_code, 409)
        payload = {"knowledge_base_id": "enterprise_default", "user_role": "kb_admin"}
        for name, kind in [("archive.md", identity), ("other.md", "general")]:
            result = self.upload(file_name=name, document_text="合同归档需要审批", knowledge_base_type=kind, **payload)
            self.assertEqual(result.status_code, 200, result.text)
        edited = self.client.patch(f"{path}/{identity}", json={"label": "合同资料", "description": "仅测试"})
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertEqual(edited.json()["knowledge_base_type"], identity)
        with patch("backend.rag.service.settings", replace(settings, rag_retrieval_mode="keyword")):
            result = self.client.post("/rag_preview", json={
                "session_id": "upload", "query": "合同归档需要审批", "knowledge_base_id": "enterprise_default",
                "knowledge_base_type_filter": identity,
            })
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual([row["file_name"] for row in result.json()["chunks"]], ["archive.md"])
        self.assertEqual(self.client.delete(f"{path}/{identity}").status_code, 409)

    def test_category_permissions_protected_default_and_stale_category(self):
        self.manager.mode = "keyword"
        path = "/knowledge_base_categories"
        self.assertEqual(self.client.delete(f"{path}/general").status_code, 409)
        created = self.client.post(path, json={"label": "临时分类"})
        self.assertEqual(created.status_code, 201, created.text)
        identity = created.json()["category_id"]
        with TestClient(self.client.app) as other:
            self.assertEqual(other.post(path, json={"label": "无权限"}).status_code, 401)
            member_id = authenticate_fixture(other, role="member")
            with closing(get_connection()) as connection, connection:
                connection.execute("UPDATE workspace_users SET module_permissions_json = ? WHERE id = ?", ('["chat"]', member_id))
            self.assertEqual(other.get(path).status_code, 200)
            self.assertEqual(other.delete(f"{path}/{identity}").status_code, 403)
        self.assertEqual(self.client.delete(f"{path}/{identity}").status_code, 200)
        self.assertEqual(self.upload(knowledge_base_type=identity).status_code, 400)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_builtin_rename_and_removal_survive_database_initialization(self):
        self.manager.mode = "keyword"
        path = "/knowledge_base_categories"
        renamed = self.client.patch(f"{path}/finance", json={"label": "费用制度", "description": "报销与付款"})
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertEqual(renamed.json()["category_id"], "finance")
        self.assertEqual(renamed.json()["department"], "财务")
        self.assertEqual(self.client.post(path, json={"label": "财务"}).status_code, 201)
        self.assertEqual(self.client.delete(f"{path}/finance").status_code, 200)
        init_database(self.url)
        items = self.client.get(path).json()["categories"]
        self.assertFalse(any(item["category_id"] == "finance" for item in items))
        self.assertEqual(self.upload(knowledge_base_type="finance").status_code, 400)
        applied_filter, note = _resolve_agent_knowledge_base_type_filter(AgentRouteType.FINANCE_PROCESS, "如何报销？", None)
        self.assertIsNone(applied_filter)
        self.assertIn("检索全部分类", note)
        inferred = self.upload(file_name="报销规则.md", document_text="报销需要发票。")
        self.assertEqual(inferred.status_code, 200, inferred.text)
        self.assertEqual(inferred.json()["knowledge_base_type"], "general")

    def test_referenced_builtin_category_can_be_renamed_but_not_deleted(self):
        self.manager.mode = "keyword"
        uploaded = self.upload(file_name="it.md", document_text="VPN 使用规则", knowledge_base_type="it")
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        path = "/knowledge_base_categories/it"
        self.assertEqual(self.client.patch(path, json={"label": "信息技术"}).status_code, 200)
        self.assertEqual(self.client.delete(path).status_code, 409)

    def test_nonempty_knowledge_base_cannot_be_deleted_and_mutations_require_permission(self):
        self.manager.mode = "keyword"
        base = self.client.post("/knowledge_bases", json={"name": "有文档", "user_role": "kb_admin"}).json()
        identity = base["knowledge_base_id"]
        path = f"/knowledge_bases/{identity}"
        response = self.upload(knowledge_base_id=identity, user_role="kb_admin")
        self.assertEqual(response.status_code, 200, response.text)
        document_id = response.json()["document_id"]
        with closing(get_connection()) as connection, connection:
            connection.execute("UPDATE logical_documents SET enabled = 0 WHERE current_document_id = ?", (document_id,))
        self.assertEqual(self.client.delete(path).status_code, 409)
        self.assertEqual(self.counts()[0], 1)
        with TestClient(self.client.app) as other:
            self.assertEqual(other.delete(path).status_code, 401)
            member_id = authenticate_fixture(other, role="member")
            with closing(get_connection()) as connection, connection:
                connection.execute("UPDATE workspace_users SET module_permissions_json = ? WHERE id = ?", ('["chat"]', member_id))
            self.assertEqual(other.patch(path, json={"name": "禁止修改"}).status_code, 403)
            self.assertEqual(other.delete(path).status_code, 403)

    def test_binary_extraction_requires_knowledge_permission_and_limits_request_body(self):
        with TestClient(self.client.app) as other:
            self.assertEqual(other.post("/extract_document_text?file_name=policy.pdf", content=b"x").status_code, 401)
            member_id = authenticate_fixture(other, role="member")
            with closing(get_connection()) as connection, connection:
                connection.execute("UPDATE workspace_users SET module_permissions_json = ? WHERE id = ?", ('["chat"]', member_id))
            self.assertEqual(other.post("/extract_document_text?file_name=policy.pdf", content=b"x").status_code, 403)
        result = self.client.post("/extract_document_text?file_name=policy.pdf", content=b"x" * (2 * 1024 * 1024 + 1))
        self.assertEqual(result.status_code, 413)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_existing_legacy_document_indexes_once_without_changing_chunks_or_scope(self):
        from backend.db.version_repository import document_version

        chunks = save_document_with_chunks("legacy-session", "legacy.md", ["第一段", "第二段"])
        document_id = chunks[0]["document_id"]
        fixture.IndexRepositoryTests.execute(self, "UPDATE documents SET knowledge_base_id = 'enterprise_default' WHERE id = ?",
                                             (document_id,))
        original = document_version(document_id)
        params = {"session_id": "upload", "knowledge_base_id": "enterprise_default", "user_role": "kb_admin"}
        path = f"/documents/{document_id}/index"
        self.assertEqual(self.client.post(path, params={**params, "knowledge_base_id": "another"}).status_code, 404)
        response = self.client.post(path, params=params)
        self.assertEqual(response.status_code, 200, response.text)
        receipt = response.json()
        self.assertEqual(receipt["vector_index"]["status"], "ready")
        self.assertEqual(repository.get_index_operation(receipt["operation_id"])["storage_session_id"], "legacy-session")
        self.assertEqual(document_version(document_id), original)
        self.assertEqual(self.client.post(path, params=params).json()["operation_id"], receipt["operation_id"])
        self.assertEqual(len(self.store.writes), 1)
        self.assertEqual(self.counts(), (1, 2, 1))

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
            params={"session_id": shared["storage_session_id"]}).status_code, 403)

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

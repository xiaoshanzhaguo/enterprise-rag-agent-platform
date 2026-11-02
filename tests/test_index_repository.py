"""阶段 A：仅临时 SQLite，验证事务、状态和兼容性；不导入任何模型/向量客户端。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
import gc
import hashlib
import sqlite3
import tempfile
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from backend.db.connection import get_connection
from backend.db.init_db import init_database
from backend.db.index_repository import (
    IndexInputChanged, IndexOperationConflict, claim_index_operation, finish_index_operation,
    get_index_operation, load_index_input, prepare_document_index, prepare_index_retry,
)
from backend.db.repository import (
    _prepare_document_storage_scope, _save_document_with_chunks_in_connection,
    save_document_with_chunks,
)


PROFILE = {
    "profile_schema_version": 2, "provider": "local", "model": "test-embedding",
    "collection_name": "rag_chunks", "endpoint_fingerprint": None,
    "vector_store_fingerprint": "sha256:" + hashlib.sha256(b"temporary-test-location").hexdigest(),
    "embedding_dimension": 3, "normalize_embeddings": True, "distance_metric": "cosine",
}


class IndexRepositoryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="rag-index-phase-a-")
        self.addCleanup(directory.cleanup)
        self.url = "sqlite:///" + (Path(directory.name) / "test.db").as_posix()
        config = patch("backend.db.connection.settings", SimpleNamespace(database_url=self.url))
        config.start()
        self.addCleanup(config.stop)
        # 复用的旧父记录 helper 依赖 GC 释放连接；Windows 删除临时库前先回收。
        self.addCleanup(gc.collect)
        init_database(self.url)

    def prepare(self, vector=False, **changes):
        arguments = dict(session_id="upload", file_name="policy.md", chunks=["第一条制度", "第二条制度"],
                         retrieval_mode="vector" if vector else "keyword",
                         target_profile=PROFILE if vector else None)
        arguments.update(changes)
        return prepare_document_index(**arguments)

    def counts(self):
        with closing(get_connection()) as connection:
            return tuple(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                         for table in ("documents", "document_chunks", "rag_index_tasks"))

    def execute(self, sql, parameters=()):
        with closing(get_connection()) as connection, connection:
            connection.execute(sql, parameters)

    def claimed(self):
        prepared = self.prepare(vector=True)
        return claim_index_operation(prepared.operation["operation_id"], 1, "worker-a")

    def failed(self):
        operation = self.claimed()
        return finish_index_operation(operation["operation_id"], operation["attempt_token"],
                                      "failed", "embedding_failed")

    def test_keyword_text_chunks_and_task_are_committed_together(self):
        prepared = self.prepare()
        operation = get_index_operation(prepared.operation["operation_id"])
        self.assertEqual(self.counts(), (1, 2, 1))
        self.assertEqual(operation["status"], "not_requested")
        self.assertEqual((operation["attempt_count"], operation["retry_count"], operation["state_version"]), (0, 0, 1))
        self.assertIsNone(operation["target_profile"])
        self.assertIsNone(operation["attempt_token"])
        self.assertEqual(datetime.fromisoformat(operation["created_at"]).tzinfo, timezone.utc)
        self.assertEqual([c["text"] for c in load_index_input(operation["operation_id"])["chunks"]],
                         ["第一条制度", "第二条制度"])

    def test_vector_pending_and_profile_are_persisted_without_execution(self):
        prepared = self.prepare(vector=True)
        operation = get_index_operation(prepared.operation["operation_id"])
        self.assertEqual(operation["status"], "pending")
        self.assertEqual(operation["attempt_count"], 0)
        self.assertFalse(operation["execution_claimed"])
        self.assertEqual(operation["target_profile"], PROFILE)
        self.assertEqual(len(operation["input_sha256"]), 64)
        self.assertEqual(len(operation["target_sha256"]), 64)
        self.assertEqual(prepared.chunks, load_index_input(operation["operation_id"])["chunks"])

    def test_every_connection_enforces_foreign_keys(self):
        for _ in range(2):
            with closing(get_connection()) as connection:
                self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.execute("INSERT INTO document_chunks(document_id, chunk_index, chunk_text, text_length) "
                         "VALUES (999, 1, 'orphan', 6)")
        operation = self.prepare().operation
        with closing(get_connection()) as connection:
            columns = [r[1] for r in connection.execute("PRAGMA table_info(rag_index_tasks)")]
            values = list(connection.execute("SELECT * FROM rag_index_tasks").fetchone())
        values[columns.index("operation_id")] = str(uuid4())
        values[columns.index("document_id")] = 999
        with self.assertRaises(sqlite3.IntegrityError):
            self.execute("INSERT INTO rag_index_tasks VALUES (" + ",".join("?" for _ in values) + ")", values)
        self.execute("DELETE FROM documents WHERE id = ?", (operation["document_id"],))
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_schema_rejects_invalid_states_and_duplicate_document_task(self):
        operation = self.prepare().operation
        for assignment in ("status='running'", "expected_chunk_count=0", "attempt_count=-1",
                           "state_version=0", "attempt_token='x'", "target_profile_json='{}'"):
            with self.subTest(assignment=assignment), self.assertRaises(sqlite3.IntegrityError):
                self.execute("UPDATE rag_index_tasks SET " + assignment)
        with self.assertRaises(sqlite3.IntegrityError):
            self.execute("INSERT INTO rag_index_tasks SELECT ?, document_id, status, input_manifest_json, "
                         "input_sha256, target_profile_json, target_sha256, expected_chunk_count, attempt_count, "
                         "state_version, attempt_token, owner_instance_id, claimed_at, error_code, error_message, "
                         "created_at, updated_at FROM rag_index_tasks", (str(uuid4()),))
        self.assertEqual(get_index_operation(operation["operation_id"]), operation)

    def test_failure_at_each_write_rolls_back_the_entire_main_transaction(self):
        triggers = [
            ("documents", "", "document_write"),
            ("document_chunks", "WHEN NEW.chunk_index = 2", "second_chunk_write"),
            ("rag_index_tasks", "", "task_write"),
        ]
        for table, condition, name in triggers:
            with self.subTest(stage=name):
                self.execute(f"CREATE TRIGGER fail_write BEFORE INSERT ON {table} {condition} "
                             "BEGIN SELECT RAISE(ABORT, 'injected'); END")
                with self.assertRaisesRegex(sqlite3.IntegrityError, "injected"):
                    self.prepare(vector=True)
                self.assertEqual(self.counts(), (0, 0, 0))
                self.execute("DROP TRIGGER fail_write")
        # 父级会话允许预先提交，不在文档/块/任务的回滚承诺中。
        with closing(get_connection()) as connection:
            self.assertIsNotNone(connection.execute("SELECT id FROM chat_sessions WHERE id='upload'").fetchone())

    def test_failed_legacy_adoption_rolls_back_metadata_changes(self):
        saved = save_document_with_chunks("upload", "policy.md", ["第一条制度", "第二条制度"], department="old")
        self.execute("CREATE TRIGGER fail_task BEFORE INSERT ON rag_index_tasks "
                     "BEGIN SELECT RAISE(ABORT, 'injected'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.prepare(vector=True, department="new")
        with closing(get_connection()) as connection:
            self.assertEqual(connection.execute("SELECT department FROM documents WHERE id=?",
                                                (saved[0]["document_id"],)).fetchone()[0], "old")
        self.assertEqual(self.counts(), (1, 2, 0))

    def test_connection_helper_does_not_open_or_commit_its_own_connection(self):
        normalized, storage, kb = _prepare_document_storage_scope("upload")
        with closing(get_connection()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            with patch("backend.db.repository.get_connection", side_effect=AssertionError("unexpected connection")):
                saved, reused = _save_document_with_chunks_in_connection(
                    connection, session_id="upload", file_name="policy.md", chunks=["text"],
                    storage_session_id=storage, normalized_knowledge_base_id=normalized,
                    document_knowledge_base_id=kb,
                )
            self.assertFalse(reused)
            self.assertTrue(connection.in_transaction)
            self.assertEqual(len(saved), 1)
            connection.rollback()
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_public_save_signature_and_list_return_remain_compatible(self):
        self.assertEqual(save_document_with_chunks("", "x", ["text"]), [])
        self.assertEqual(save_document_with_chunks("upload", "x", []), [])
        first = save_document_with_chunks("upload", "x", ["a", "b"], "legacy", "upload", None,
                                          "hr", "HR", "leave", "active")
        second = save_document_with_chunks("upload", "x", ["a", "b"], department="new")
        self.assertIsInstance(first, list)
        self.assertEqual([c["db_chunk_id"] for c in first], [c["db_chunk_id"] for c in second])
        self.assertEqual(second[0]["department"], "new")
        self.assertEqual(set(second[0]), {"chunk_id", "db_chunk_id", "document_id", "file_name", "text",
                                         "text_length", "created_at", "knowledge_base_id", "knowledge_base_type",
                                         "department", "process_type", "process_status"})
        self.assertEqual(self.counts(), (1, 2, 0))

    def test_repeated_upload_reuses_ids_operation_and_state(self):
        for vector in (False, True):
            with self.subTest(vector=vector):
                name = "vector" if vector else "keyword"
                first = self.prepare(vector=vector, file_name=name)
                second = self.prepare(vector=vector, file_name=name)
                self.assertTrue(second.document_reused)
                self.assertEqual(second.chunks, first.chunks)
                self.assertEqual(second.operation, first.operation)
        self.assertEqual(self.counts(), (2, 4, 2))

    def test_keyword_to_vector_promotes_same_operation_without_attempt(self):
        first = self.prepare()
        promoted = self.prepare(vector=True)
        self.assertEqual(promoted.document_id, first.document_id)
        self.assertEqual(promoted.chunks, first.chunks)
        self.assertEqual(promoted.operation["operation_id"], first.operation["operation_id"])
        self.assertEqual((promoted.operation["status"], promoted.operation["state_version"],
                          promoted.operation["attempt_count"]), ("pending", 2, 0))

    def test_legacy_keyword_stays_untracked_and_explicit_vector_can_adopt(self):
        saved = save_document_with_chunks("upload", "policy.md", ["第一条制度", "第二条制度"])
        legacy = self.prepare()
        self.assertIsNone(legacy.operation)
        self.assertTrue(legacy.document_reused)
        adopted = self.prepare(vector=True)
        self.assertEqual([c["db_chunk_id"] for c in saved], [c["db_chunk_id"] for c in adopted.chunks])
        self.assertEqual(adopted.operation["status"], "pending")
        self.assertEqual(self.counts(), (1, 2, 1))

    def test_ignored_promotion_is_a_conflict_and_does_not_change_task(self):
        first = self.prepare()
        self.execute("CREATE TRIGGER ignore_promotion BEFORE UPDATE ON rag_index_tasks "
                     "WHEN NEW.status='pending' BEGIN SELECT RAISE(IGNORE); END")
        with self.assertRaises(IndexOperationConflict):
            self.prepare(vector=True)
        self.assertEqual(get_index_operation(first.operation["operation_id"]), first.operation)
        self.assertEqual(self.counts(), (1, 2, 1))

    def test_metadata_conflicts_do_not_update_tracked_document(self):
        original = self.prepare()
        for change in ({"department": "new"}, {"knowledge_base_type": "hr"},
                       {"process_type": "leave"}, {"process_status": "draft"}):
            with self.subTest(change=change), self.assertRaises(IndexOperationConflict):
                self.prepare(**change)
        self.assertEqual(load_index_input(original.operation["operation_id"])["chunks"], original.chunks)
        self.assertEqual(get_index_operation(original.operation["operation_id"]), original.operation)

    def test_chunk_boundary_hash_collision_does_not_change_input(self):
        self.prepare(chunks=["a", "b"])
        with self.assertRaises(IndexOperationConflict):
            self.prepare(chunks=["a\n\nb"])
        self.assertEqual(self.counts(), (1, 2, 1))

    def test_profile_conflicts_and_unspecified_runtime_constraints(self):
        first = self.prepare(vector=True)
        for key, value in (("model", "other"), ("normalize_embeddings", False),
                           ("embedding_dimension", 4), ("distance_metric", "l2"),
                           ("vector_store_fingerprint", "sha256:" + "a" * 64)):
            with self.subTest(key=key), self.assertRaises(IndexOperationConflict):
                self.prepare(vector=True, target_profile=dict(PROFILE, **{key: value}))
        repeated = self.prepare(vector=True, target_profile=dict(PROFILE, embedding_dimension=None, distance_metric=None))
        self.assertEqual(repeated.operation, first.operation)

    def test_profile_validation_rejects_secrets_and_invalid_fields_before_writes(self):
        invalid = [dict(PROFILE, api_key="secret"), dict(PROFILE, endpoint_fingerprint="https://private/token"),
                   dict(PROFILE, embedding_dimension=True), dict(PROFILE, normalize_embeddings="true"),
                   dict(PROFILE, profile_schema_version=1), dict(PROFILE, distance_metric="unknown")]
        for profile in invalid:
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                self.prepare(vector=True, target_profile=profile)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_input_validation_and_missing_operation(self):
        for change in ({"session_id": " "}, {"chunks": []}, {"chunks": [""]},
                       {"retrieval_mode": "invalid"}, {"target_profile": PROFILE}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.prepare(**change)
        self.assertIsNone(get_index_operation("missing"))
        with self.assertRaises(LookupError):
            load_index_input("missing")
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_scope_and_null_filename_deduplication(self):
        first = self.prepare(file_name=None, knowledge_base_id="enterprise_default")
        second = self.prepare(session_id="another-uploader", file_name=None, knowledge_base_id="enterprise_default")
        private = self.prepare(file_name=None)
        self.assertEqual(first.operation, second.operation)
        self.assertEqual(first.operation["storage_session_id"], "knowledge_base:enterprise_default")
        self.assertNotEqual(first.document_id, private.document_id)
        self.assertEqual(self.counts(), (2, 4, 2))

    def test_two_concurrent_preparations_create_only_one_task(self):
        barrier = Barrier(2)
        def prepare():
            barrier.wait(timeout=5)
            return self.prepare(vector=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: prepare(), range(2)))
        self.assertEqual(results[0].operation["operation_id"], results[1].operation["operation_id"])
        self.assertEqual(self.counts(), (1, 2, 1))

    def test_two_concurrent_claims_have_only_one_winner(self):
        operation = self.prepare(vector=True).operation
        barrier = Barrier(2)
        def claim(owner):
            barrier.wait(timeout=5)
            try:
                return claim_index_operation(operation["operation_id"], 1, owner)
            except IndexOperationConflict:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, ("a", "b")))
        self.assertEqual(sum(result is not None for result in results), 1)
        current = get_index_operation(operation["operation_id"])
        self.assertEqual((current["attempt_count"], current["state_version"]), (1, 2))
        self.assertTrue(current["execution_claimed"])

    def test_only_current_token_can_finish_and_old_token_cannot_overwrite_new_attempt(self):
        first = self.claimed()
        with self.assertRaises(IndexOperationConflict):
            finish_index_operation(first["operation_id"], "wrong-token", "ready")
        failed = finish_index_operation(first["operation_id"], first["attempt_token"], "failed", "embedding_failed")
        pending = prepare_index_retry(first["operation_id"], failed["state_version"])
        second = claim_index_operation(first["operation_id"], pending["state_version"], "b")
        with self.assertRaises(IndexOperationConflict):
            finish_index_operation(first["operation_id"], first["attempt_token"], "ready")
        ready = finish_index_operation(second["operation_id"], second["attempt_token"], "ready")
        self.assertEqual(ready["status"], "ready")
        self.assertFalse(ready["execution_claimed"])
        self.assertEqual((ready["attempt_count"], ready["retry_count"]), (2, 1))
        with self.assertRaises(IndexOperationConflict):
            finish_index_operation(second["operation_id"], second["attempt_token"], "failed", "embedding_failed")

    def test_stale_state_version_cannot_claim_or_retry(self):
        failed = self.failed()
        with self.assertRaises(IndexOperationConflict):
            prepare_index_retry(failed["operation_id"], 1)
        pending = prepare_index_retry(failed["operation_id"], failed["state_version"])
        with self.assertRaises(IndexOperationConflict):
            claim_index_operation(failed["operation_id"], failed["state_version"], "late")
        claimed = claim_index_operation(failed["operation_id"], pending["state_version"], "next")
        finish_index_operation(claimed["operation_id"], claimed["attempt_token"], "failed", "upsert_outcome_unknown")
        before = get_index_operation(failed["operation_id"])
        with self.assertRaises(IndexOperationConflict):
            prepare_index_retry(failed["operation_id"], failed["state_version"])
        self.assertEqual(get_index_operation(failed["operation_id"]), before)

    def test_retry_does_not_execute_and_duplicate_terminal_or_active_requests_are_read_only(self):
        failed = self.failed()
        pending = prepare_index_retry(failed["operation_id"], failed["state_version"])
        self.assertEqual(pending["attempt_count"], 1)
        self.assertIsNone(pending["error_message"])
        self.assertIsNone(pending["error_code"])
        with self.assertRaises(IndexOperationConflict):
            prepare_index_retry(pending["operation_id"], pending["state_version"])
        active = claim_index_operation(pending["operation_id"], pending["state_version"], "next")
        self.assertEqual(prepare_index_retry(active["operation_id"], 1), active)
        ready = finish_index_operation(active["operation_id"], active["attempt_token"], "ready")
        self.assertEqual(prepare_index_retry(ready["operation_id"], 1), ready)
        self.assertEqual(self.prepare().operation, ready)  # keyword 不重写历史向量状态。

    def test_failure_message_is_controlled_and_not_raw_sdk_text(self):
        active = self.claimed()
        failed = finish_index_operation(active["operation_id"], active["attempt_token"], "failed",
                                        "upsert_outcome_unknown", "https://private/?api_key=secret")
        self.assertEqual(failed["error_message"], "向量写入结果未确认。")
        self.assertNotIn("secret", str(failed))

    def test_unbound_profile_cannot_be_declared_ready(self):
        task = self.prepare(vector=True, target_profile=dict(PROFILE, embedding_dimension=None, distance_metric=None)).operation
        active = claim_index_operation(task["operation_id"], 1, "a")
        with self.assertRaises(IndexOperationConflict):
            finish_index_operation(task["operation_id"], active["attempt_token"], "ready")
        self.assertEqual(get_index_operation(task["operation_id"]), active)

    def test_load_detects_text_metadata_and_missing_chunk_without_writing(self):
        changes = [
            "UPDATE document_chunks SET chunk_text='changed' WHERE chunk_index=2",
            "UPDATE document_chunks SET file_name='changed' WHERE chunk_index=2",
            "UPDATE documents SET department='changed'",
            "UPDATE documents SET file_name='changed'",
            "DELETE FROM document_chunks WHERE chunk_index=2",
        ]
        for index, sql in enumerate(changes):
            prepared = self.prepare(file_name=f"case-{index}")
            # 在专用临时连接的事务中更改并检查，随后回滚，以便子用例互不干扰。
            with self.subTest(sql=sql), closing(get_connection()) as connection:
                connection.execute("BEGIN")
                connection.execute(sql)
                # 使用同一快照的校验函数；公开只读方法另有独立检查。
                from backend.db.index_repository import _require_operation, _verify_input
                operation = _require_operation(connection, prepared.operation["operation_id"])
                total_changes = connection.total_changes
                with self.assertRaises(IndexInputChanged):
                    _verify_input(connection, operation)
                self.assertEqual(connection.total_changes, total_changes)
                connection.rollback()
            self.assertEqual(get_index_operation(prepared.operation["operation_id"]), prepared.operation)
        prepared = self.prepare(file_name="committed-tamper")
        self.execute("UPDATE document_chunks SET chunk_text='changed' WHERE document_id=?",
                     (prepared.document_id,))
        with self.assertRaises(IndexInputChanged):
            load_index_input(prepared.operation["operation_id"])
        self.assertEqual(get_index_operation(prepared.operation["operation_id"]), prepared.operation)

    def test_initialization_of_old_database_does_not_backfill_or_modify_old_documents(self):
        saved = save_document_with_chunks("upload", "old.md", ["old text"])
        self.execute("DROP TABLE rag_index_tasks")  # 仅本测试的临时库，模拟旧结构。
        with closing(get_connection()) as connection:
            before = dict(connection.execute("SELECT * FROM documents").fetchone())
        init_database(self.url)
        init_database(self.url)
        with closing(get_connection()) as connection:
            self.assertEqual(dict(connection.execute("SELECT * FROM documents").fetchone()), before)
            self.assertIsNotNone(connection.execute("SELECT name FROM sqlite_master WHERE name="
                                                    "'idx_rag_index_tasks_status_updated'").fetchone())
        self.assertEqual(self.counts(), (1, 1, 0))
        self.assertEqual(saved[0]["document_id"], before["id"])


if __name__ == "__main__":
    unittest.main()

"""B3：真实 Repository/Chroma 适配器 + Fake Embedding/Collection，验证失败恢复。"""

from copy import deepcopy
from contextlib import closing
from pathlib import Path
import sqlite3
from threading import BoundedSemaphore
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from backend.db import index_repository as repository
from backend.db.connection import get_connection
from backend.rag import embedding_client, vector_store
from backend.rag.index_adapters import ChromaVectorStoreAdapter, build_index_target_profile
from backend.rag.index_contracts import EmbeddingBatch
from backend.services.rag_indexing import RagIndexingService
from tests import test_index_repository as db_fixture
from tests import test_rag_index_adapters as adapter_fixture


class RecoveryEmbedding:
    def __init__(self):
        self.error = None
        self.dimension = 3
        self.generation = 0
        self.calls = []

    def vector(self, position):
        # 不同轮次给出不同结果，检测重试是否真正覆盖旧内容，而不只是 ID 去重。
        return [float(self.generation * 100 + position + 1)] + [0.0] * (self.dimension - 1)

    def embed(self, texts, *, target_profile, context):
        self.calls.append((tuple(texts), context))
        if self.error:
            raise self.error
        profile = {key: target_profile[key] for key in (
            "provider", "model", "endpoint_fingerprint", "normalize_embeddings",
        )}
        profile["embedding_dimension"] = self.dimension
        return EmbeddingBatch([self.vector(i) for i in range(len(texts))], profile)


class FaultCollection(adapter_fixture.FakeCollection):
    """模拟一次 upsert 内部分批落盘；并不声称生产适配器已实现客户端分批。"""

    def __init__(self):
        super().__init__()
        self.requests = []
        self.batch_size = 2
        self.fail_before_write = False
        self.fail_after_batches = None
        self.lose_ack = False
        self.after_write = None

    def upsert(self, **payload):
        self.requests.append(deepcopy(payload))
        if self.fail_before_write:
            raise RuntimeError("injected failure before persistence")
        for number, start in enumerate(range(0, len(payload["ids"]), self.batch_size), 1):
            batch = {key: values[start:start + self.batch_size] for key, values in payload.items()}
            super().upsert(**batch)
            if number == self.fail_after_batches:
                raise TimeoutError("injected partial persistence")
        if self.after_write:
            self.after_write()
        if self.lose_ack:
            raise TimeoutError("injected acknowledgement loss")


class RagIndexRecoveryTests(unittest.TestCase):
    prepare = db_fixture.IndexRepositoryTests.prepare
    counts = db_fixture.IndexRepositoryTests.counts

    def setUp(self):
        db_fixture.IndexRepositoryTests.setUp(self)
        config = SimpleNamespace(
            embedding_provider="local", embedding_model="recovery-fake", embedding_base_url=None,
            embedding_api_key=None, base_url="https://example.invalid/v1",
            vector_store_dir=str(Path(self.url.removeprefix("sqlite:///")).parent / "vectors"),
        )
        for module in (embedding_client, vector_store):
            replacement = patch.object(module, "settings", config)
            replacement.start()
            self.addCleanup(replacement.stop)
        self.embedding = RecoveryEmbedding()
        self.collection = FaultCollection()
        self.service = RagIndexingService(
            self.embedding, ChromaVectorStoreAdapter(collection_factory=lambda: self.collection),
            owner_instance_id="recovery-test", execution_gate=BoundedSemaphore(1),
        )
        # 若测试误走真实连接/模型入口，立即失败。
        for module, name in ((vector_store, "get_chroma_collection"),
                             (embedding_client, "generate_embeddings")):
            replacement = patch.object(module, name, side_effect=AssertionError("real dependency forbidden"))
            replacement.start()
            self.addCleanup(replacement.stop)

    def task(self, **changes):
        arguments = dict(
            vector=True, chunks=[f"policy-{i}" for i in range(5)],
            target_profile=build_index_target_profile(), knowledge_base_id="enterprise_default",
            knowledge_base_type="finance", department="finance", process_type="reimbursement",
        )
        arguments.update(changes)
        task = self.prepare(**arguments).operation
        self.original = repository.load_index_input(task["operation_id"])
        return task

    def resume(self, task):
        return self.service.resume(task["operation_id"], task["state_version"])

    def retry(self, task):
        return self.service.retry(task["operation_id"], task["state_version"])

    def expected_records(self):
        operation = self.original["operation"]
        expected = {}
        for i, chunk in enumerate(self.original["chunks"]):
            vector_id = f"{operation['storage_session_id']}:{chunk['db_chunk_id']}"
            expected[vector_id] = dict(embedding=self.embedding.vector(i), text=chunk["text"], metadata={
                "session_id": operation["storage_session_id"], "knowledge_base_id": "enterprise_default",
                "document_id": operation["document_id"], "db_chunk_id": chunk["db_chunk_id"],
                "file_name": "policy.md", "chunk_id": chunk["chunk_id"], "knowledge_base_type": "finance",
                "department": "finance", "process_type": "reimbursement", "process_status": "active",
            })
        return expected

    def assert_snapshot_unchanged(self, task):
        loaded = repository.load_index_input(task["operation_id"])
        self.assertEqual(loaded["chunks"], self.original["chunks"])
        for key in ("operation_id", "document_id", "storage_session_id", "input_sha256", "expected_chunk_count"):
            self.assertEqual(loaded["operation"][key], self.original["operation"][key])
        self.assertEqual(self.counts(), (1, 5, 1))

    def assert_ready(self, result, *, attempts=2):
        self.assertTrue(result.state_confirmed)
        self.assertEqual(result.reason, "ready")
        persisted = repository.get_index_operation(result.operation_id)
        self.assertEqual(result.operation, persisted)
        self.assertEqual(persisted["status"], "ready")
        self.assertEqual(persisted["attempt_count"], attempts)
        self.assertFalse(persisted["execution_claimed"])
        self.assertIsNone(persisted["attempt_token"])
        self.assertIsNone(persisted["error_code"])
        self.assertIsNone(persisted["error_message"])
        self.assertEqual(self.collection.records, self.expected_records())
        self.assertEqual(self.collection.count(), 5)
        self.assert_snapshot_unchanged(persisted)

    def assert_failed(self, result, code, vector_count):
        self.assertTrue(result.state_confirmed)
        self.assertEqual(result.reason, "failed")
        persisted = repository.get_index_operation(result.operation_id)
        self.assertEqual(result.operation, persisted)
        self.assertEqual(persisted["status"], "failed")
        self.assertEqual(persisted["error_code"], code)
        self.assertFalse(persisted["execution_claimed"])
        self.assertEqual(self.collection.count(), vector_count)
        self.assert_snapshot_unchanged(persisted)

    def test_embedding_failure_then_retry_preserves_sqlite_input(self):
        task = self.task()
        self.embedding.error = RuntimeError("injected embedding failure")
        failed = self.resume(task)
        self.assert_failed(failed, "embedding_failed", 0)
        self.assertEqual(self.collection.requests, [])
        self.assertIsNone(failed.operation["target_profile"]["embedding_dimension"])
        self.embedding.error = None
        self.assert_ready(self.retry(failed.operation))

    def test_embedding_dimension_mismatch_never_writes_then_retry_recovers(self):
        profile = dict(build_index_target_profile(), embedding_dimension=3)
        task = self.task(target_profile=profile)
        self.embedding.dimension = 4
        failed = self.resume(task)
        self.assert_failed(failed, "index_configuration_changed", 0)
        self.assertEqual(self.collection.requests, [])
        self.assertEqual(failed.operation["target_profile"]["embedding_dimension"], 3)
        self.embedding.dimension = 3
        self.assert_ready(self.retry(failed.operation))

    def test_upsert_failure_before_any_write_can_be_retried(self):
        task = self.task()
        self.collection.fail_before_write = True
        failed = self.resume(task)
        self.assert_failed(failed, "upsert_outcome_unknown", 0)
        self.collection.fail_before_write = False
        self.assert_ready(self.retry(failed.operation))
        self.assertEqual(self.collection.requests[0], self.collection.requests[1])

    def test_full_write_lost_ack_retry_overwrites_without_duplicate_vectors(self):
        task = self.task()
        self.collection.lose_ack = True
        failed = self.resume(task)
        self.assert_failed(failed, "upsert_outcome_unknown", 5)
        original_ids = set(self.collection.records)
        old_records = deepcopy(self.collection.records)
        self.embedding.generation = 1
        self.collection.lose_ack = False
        self.assert_ready(self.retry(failed.operation))
        self.assertEqual(set(self.collection.records), original_ids)
        self.assertNotEqual(self.collection.records, old_records)
        self.assertEqual(self.collection.requests[0]["ids"], self.collection.requests[1]["ids"])

    def test_partial_internal_batches_are_overwritten_and_missing_vectors_filled(self):
        # 分别模拟首批和第二批落盘后失败；生产适配器仍只调用一次 upsert。
        for committed_batches in (1, 2):
            with self.subTest(committed_batches=committed_batches):
                self.collection.fail_after_batches = committed_batches
                if committed_batches == 1:
                    task = self.task()
                    failed = self.resume(task)
                else:
                    self.collection.records.clear()
                    self.embedding.generation += 1
                    failed = self.retry(failed.operation)
                self.assert_failed(failed, "upsert_outcome_unknown", committed_batches * 2)
                partial = deepcopy(self.collection.records)
                self.assertEqual(set(partial), set(list(self.expected_records())[:committed_batches * 2]))
        self.embedding.generation += 1
        self.collection.fail_after_batches = None
        self.assert_ready(self.retry(failed.operation), attempts=3)
        self.assertTrue(all(self.collection.records[key] != value for key, value in partial.items()))
        self.assertEqual([request["ids"] for request in self.collection.requests],
                         [self.collection.requests[0]["ids"]] * 3)

    def test_repeated_failures_keep_one_operation_and_fresh_attempt_tokens(self):
        task = self.task()
        self.collection.fail_before_write = True
        first = self.resume(task)
        second = self.retry(first.operation)
        self.assert_failed(second, "upsert_outcome_unknown", 0)
        self.assertEqual(second.operation["attempt_count"], 2)
        self.assertGreater(second.operation["state_version"], first.operation["state_version"])
        tokens = [context.attempt_token for _, context in self.embedding.calls]
        self.assertEqual(len(set(tokens)), 2)
        self.collection.fail_before_write = False
        ready = self.retry(second.operation)
        self.assert_ready(ready, attempts=3)
        self.assertEqual(len({context.attempt_token for _, context in self.embedding.calls}), 3)

    def test_finish_sql_failure_after_write_returns_unconfirmed_and_blocks_reentry(self):
        task = self.task()
        # 在真实 SQLite UPDATE 中触发失败，验证事务回滚，而不只 mock Repository 返回。
        with closing(get_connection()) as connection, connection:
            connection.execute("""CREATE TRIGGER reject_index_ready BEFORE UPDATE OF status ON rag_index_tasks
                WHEN NEW.status = 'ready' BEGIN SELECT RAISE(ABORT, 'injected finish failure'); END""")
        result = self.resume(task)
        self.assertFalse(result.state_confirmed)
        self.assertIsNone(result.operation)
        self.assertEqual(result.reason, "state_unconfirmed")
        self.assertEqual(self.collection.records, self.expected_records())
        pending = repository.get_index_operation(task["operation_id"])
        self.assertEqual(pending["status"], "pending")
        self.assertTrue(pending["execution_claimed"])
        for action in (self.service.resume, self.service.retry):
            receipt = action(task["operation_id"], pending["state_version"])
            self.assertEqual(receipt.reason, "already_claimed")
            self.assertFalse(receipt.execution_started)
        self.assertEqual(len(self.collection.requests), 1)
        self.assertEqual(repository.get_index_operation(task["operation_id"]), pending)
        self.assert_snapshot_unchanged(pending)

    def test_stopped_execution_can_be_explicitly_recovered_then_retried_with_same_ids(self):
        task = self.task()
        with patch.object(repository, "finish_index_operation", side_effect=sqlite3.OperationalError("injected")):
            receipt = self.resume(task)
        self.assertFalse(receipt.state_confirmed)
        first_ids = set(self.collection.records)
        pending = repository.get_index_operation(task["operation_id"])
        self.assertEqual(pending["status"], "pending")
        # 同步调用已退出，Fake 不含后台执行者；模拟确认停写后通过现有 Repository 维护恢复。
        # 不新增自动接管、维护 API 或数据库修复 SQL。
        interrupted = repository.finish_index_operation(
            task["operation_id"], pending["attempt_token"], "failed", "interrupted",
        )
        self.assertEqual(repository.get_index_operation(task["operation_id"]), interrupted)
        self.assertEqual(interrupted["status"], "failed")
        self.assertEqual(interrupted["error_code"], "interrupted")
        self.assertFalse(interrupted["execution_claimed"])
        self.assertEqual(self.collection.count(), 5)
        self.embedding.generation += 1
        self.assert_ready(self.retry(interrupted))
        self.assertEqual(set(self.collection.records), first_ids)
        self.assertEqual(self.collection.requests[0]["ids"], self.collection.requests[1]["ids"])

    def test_finish_commit_ack_loss_reconciles_by_query_without_reexecution(self):
        task = self.task()
        finish = repository.finish_index_operation
        def commit_then_lose_ack(*args, **kwargs):
            finish(*args, **kwargs)
            raise sqlite3.OperationalError("injected finish acknowledgement loss")
        with patch.object(repository, "finish_index_operation", side_effect=commit_then_lose_ack):
            result = self.resume(task)
        self.assertFalse(result.state_confirmed)
        self.assertIsNone(result.operation)
        persisted = repository.get_index_operation(task["operation_id"])
        self.assertEqual(persisted["status"], "ready")
        self.assert_ready(self.resume(task), attempts=1)
        self.assertEqual(len(self.collection.requests), 1)
        self.assertEqual(len(self.embedding.calls), 1)

    def test_failure_state_sql_failure_keeps_claim_and_requires_explicit_recovery(self):
        task = self.task()
        self.embedding.error = RuntimeError("injected embedding failure")
        with patch.object(repository, "finish_index_operation", side_effect=sqlite3.OperationalError("injected")):
            result = self.resume(task)
        self.assertFalse(result.state_confirmed)
        self.assertIsNone(result.operation)
        pending = repository.get_index_operation(task["operation_id"])
        self.assertEqual(pending["status"], "pending")
        self.assertTrue(pending["execution_claimed"])
        self.embedding.error = None
        self.assertEqual(self.retry(pending).reason, "already_claimed")
        self.assertEqual(self.collection.count(), 0)
        self.assert_snapshot_unchanged(pending)

    def test_old_token_cannot_finish_new_attempt_and_current_attempt_can_complete(self):
        task = self.task()
        self.collection.fail_before_write = True
        failed = self.resume(task)
        old_token = self.embedding.calls[0][1].attempt_token
        original_upsert = self.collection.upsert
        def check_old_token(**payload):
            active = repository.get_index_operation(task["operation_id"])
            self.assertNotEqual(active["attempt_token"], old_token)
            for status in ("ready", "failed"):
                with self.assertRaises(repository.IndexOperationConflict):
                    repository.finish_index_operation(task["operation_id"], old_token, status,
                                                      "interrupted" if status == "failed" else None)
                self.assertEqual(repository.get_index_operation(task["operation_id"]), active)
            return original_upsert(**payload)
        self.collection.fail_before_write = False
        with patch.object(self.collection, "upsert", side_effect=check_old_token):
            self.assert_ready(self.retry(failed.operation))

    def test_ready_duplicate_actions_and_stale_retry_do_not_write(self):
        task = self.task()
        self.collection.lose_ack = True
        failed = self.resume(task)
        before = deepcopy(self.collection.records)
        rejected = self.service.retry(task["operation_id"], task["state_version"])
        self.assertEqual(rejected.reason, "version_conflict")
        self.assertEqual(self.collection.records, before)
        self.collection.lose_ack = False
        ready = self.retry(failed.operation)
        self.assert_ready(ready)
        for action in (self.service.resume, self.service.retry):
            result = action(task["operation_id"], 1)
            self.assertEqual(result.operation, ready.operation)
            self.assertFalse(result.execution_started)
        self.assertEqual(len(self.collection.requests), 2)

    def test_retry_preserves_unrelated_vectors(self):
        task = self.task()
        sentinel = dict(embedding=[0.0, 1.0, 0.0], text="other-document", metadata={"document_id": 999})
        self.collection.records["other-scope:999"] = deepcopy(sentinel)
        self.collection.dimension = 3
        self.collection.fail_after_batches = 1
        failed = self.resume(task)
        self.assert_failed(failed, "upsert_outcome_unknown", 3)
        self.collection.fail_after_batches = None
        ready = self.retry(failed.operation)
        self.assertEqual(ready.reason, "ready")
        expected = dict(self.expected_records(), **{"other-scope:999": sentinel})
        self.assertEqual(self.collection.records, expected)
        self.assertEqual(self.collection.count(), 6)
        self.assert_snapshot_unchanged(ready.operation)
        self.collection.delete.assert_not_called()


if __name__ == "__main__":
    unittest.main()

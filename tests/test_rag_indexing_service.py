"""B1：真实临时 Repository + 独立 Fake Embedding / Fake Vector Store。"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import math
import sqlite3
from threading import BoundedSemaphore, Event
import unittest
from unittest.mock import patch

from backend.db import index_repository as repository
from backend.services.rag_indexing import EmbeddingBatch, IndexExecutionError, RagIndexingService
from tests import test_index_repository as fixture


class FakeEmbedding:
    def __init__(self):
        self.calls = []
        self.profile = {key: fixture.PROFILE[key] for key in (
            "provider", "model", "endpoint_fingerprint", "normalize_embeddings", "embedding_dimension",
        )}
        self.error = None
        self.on_call = None
        self.vectors = None

    def embed(self, texts, *, target_profile, context):
        self.calls.append((texts, deepcopy(target_profile), context))
        if self.on_call:
            self.on_call(context)
        if self.error:
            raise self.error
        vectors = ([[1.0, 0.0, 0.0] for _ in texts] if self.vectors is None else self.vectors)
        return EmbeddingBatch(deepcopy(vectors), deepcopy(self.profile))


class FakeVectorStore:
    def __init__(self):
        self.probes = []
        self.writes = []
        self.records = {}
        self.profile = {key: fixture.PROFILE[key] for key in (
            "collection_name", "vector_store_fingerprint", "distance_metric",
        )}
        self.probe_error = None
        self.write_error = None
        self.on_write = None
        self.after_write = None

    def inspect_profile(self, *, target_profile, context):
        self.probes.append((deepcopy(target_profile), context))
        if self.probe_error:
            raise self.probe_error
        return deepcopy(self.profile)

    def upsert(self, *, chunks, embeddings, target_profile, context):
        self.writes.append((deepcopy(chunks), deepcopy(embeddings), deepcopy(target_profile), context))
        if self.on_write:
            self.on_write(context)
        if self.write_error:
            raise self.write_error
        for chunk, vector in zip(chunks, embeddings):
            # ID 映射属于 Fake adapter；沿用生产 Vector Store 的稳定 scope:db_chunk_id 契约。
            vector_id = f"{context.storage_session_id}:{chunk['db_chunk_id']}"
            self.records[vector_id] = (deepcopy(chunk), vector)
        if self.after_write:
            self.after_write(context)


class RagIndexingServiceTests(unittest.TestCase):
    prepare = fixture.IndexRepositoryTests.prepare
    execute = fixture.IndexRepositoryTests.execute
    counts = fixture.IndexRepositoryTests.counts

    def setUp(self):
        fixture.IndexRepositoryTests.setUp(self)
        self.embedding = FakeEmbedding()
        self.store = FakeVectorStore()
        self.gate = BoundedSemaphore(1)
        self.service = RagIndexingService(self.embedding, self.store, owner_instance_id="service-a",
                                          execution_gate=self.gate)

    def task(self, known=False, **changes):
        profile = dict(fixture.PROFILE) if known else dict(fixture.PROFILE, embedding_dimension=None, distance_metric=None)
        return self.prepare(vector=True, target_profile=profile, **changes).operation

    def resume(self, operation):
        return self.service.resume(operation["operation_id"], operation["state_version"])

    def test_normal_execution_binds_before_write_and_uses_latest_version(self):
        task = self.task()
        bind = repository.bind_index_profile
        seen = []
        def observe_binding(operation_id, token, version, bindings):
            result = bind(operation_id, token, version, bindings)
            seen.append(result)
            return result
        def observe_write(context):
            persisted = repository.get_index_operation(context.operation_id)
            self.assertEqual(persisted["target_profile"], fixture.PROFILE)
            self.assertEqual(persisted["state_version"], context.state_version)
            self.assertEqual(persisted["attempt_token"], context.attempt_token)
            self.assertEqual(context.state_version, seen[0]["state_version"])
        self.store.on_write = observe_write
        with patch.object(repository, "bind_index_profile", side_effect=observe_binding):
            result = self.resume(task)
        self.assertEqual(result.reason, "ready")
        self.assertTrue(result.execution_started)
        self.assertTrue(result.state_confirmed)
        self.assertEqual(result.operation["state_version"], 4)
        self.assertEqual(result.operation["attempt_count"], 1)
        self.assertIsNone(result.operation["attempt_token"])
        self.assertEqual(self.embedding.calls[0][2].attempt_token, self.store.writes[0][3].attempt_token)
        self.assertEqual(self.counts(), (1, 2, 1))
        self.assertEqual(len(self.store.records), 2)

    def test_known_profile_binding_is_idempotent(self):
        task = self.task(known=True)
        result = self.resume(task)
        self.assertEqual(result.operation["state_version"], 3)
        self.assertEqual(self.store.writes[0][3].state_version, 2)
        self.assertEqual(self.store.writes[0][2], fixture.PROFILE)

    def test_input_snapshot_failure_records_failed_without_executors(self):
        task = self.task()
        self.execute("UPDATE document_chunks SET chunk_text='tampered' WHERE document_id=?", (task["document_id"],))
        result = self.resume(task)
        self.assertEqual((result.reason, result.error_code), ("failed", "index_input_changed"))
        self.assertEqual(result.operation["status"], "failed")
        self.assertEqual(self.embedding.calls, [])
        self.assertEqual(self.store.probes, [])
        self.assertEqual(self.store.writes, [])

    def test_binding_conflict_prevents_upsert_and_records_failed(self):
        task = self.task()
        with patch.object(repository, "bind_index_profile", side_effect=repository.IndexOperationConflict("injected")):
            result = self.resume(task)
        self.assertEqual((result.operation["status"], result.error_code), ("failed", "index_configuration_changed"))
        self.assertEqual(self.store.writes, [])
        self.assertIsNone(result.operation["target_profile"]["embedding_dimension"])

    def test_binding_storage_failure_is_not_treated_as_vector_success(self):
        task = self.task()
        with patch.object(repository, "bind_index_profile", side_effect=sqlite3.OperationalError("private db path")):
            result = self.resume(task)
        self.assertEqual((result.operation["status"], result.error_code), ("failed", "interrupted"))
        self.assertEqual(self.store.writes, [])
        self.assertNotIn("private db path", str(result))

    def test_embedding_failure_records_safe_error_and_no_vectors(self):
        task = self.task()
        self.embedding.error = RuntimeError("https://private/?api_key=secret")
        result = self.resume(task)
        self.assertEqual((result.reason, result.error_code), ("failed", "embedding_failed"))
        self.assertFalse(result.operation["execution_claimed"])
        self.assertEqual(self.store.writes, [])
        self.assertNotIn("secret", str(result))

    def test_embedding_timeout_and_controlled_error_codes(self):
        for index, error in enumerate((TimeoutError("private"), IndexExecutionError("embedding_timeout"))):
            task = self.task(file_name=f"timeout-{index}")
            self.embedding.error = error
            result = self.resume(task)
            self.assertEqual(result.error_code, "embedding_timeout")
            self.assertEqual(result.operation["status"], "failed")
        self.assertEqual(self.store.writes, [])

    def test_invalid_embedding_count_shape_and_values_fail_before_upsert(self):
        for index, vectors in enumerate(([], [[1.0]], [[1.0], [1.0]], [[math.nan, 0, 0]] * 2,
                                         [[math.inf, 0, 0]] * 2, [[True, 0, 0]] * 2)):
            task = self.task(file_name=f"invalid-{index}")
            self.embedding.vectors = vectors
            result = self.resume(task)
            self.assertEqual(result.operation["status"], "failed")
            self.assertIn(result.error_code, ("embedding_count_mismatch", "embedding_failed"))
        self.assertEqual(self.store.writes, [])

    def test_known_embedding_configuration_cannot_be_overwritten(self):
        task = self.task(known=True)
        self.embedding.profile["model"] = "another-model"
        result = self.resume(task)
        self.assertEqual(result.error_code, "index_configuration_changed")
        self.assertEqual(result.operation["target_profile"], fixture.PROFILE)
        self.assertEqual(self.store.writes, [])

    def test_vector_target_change_is_rejected_before_embedding(self):
        task = self.task(known=True)
        self.store.profile["distance_metric"] = "l2"
        result = self.resume(task)
        self.assertEqual(result.error_code, "index_configuration_changed")
        self.assertEqual(self.embedding.calls, [])
        self.assertEqual(self.store.writes, [])

    def test_vector_probe_error_records_failed_without_embedding(self):
        task = self.task()
        self.store.probe_error = RuntimeError("private collection path")
        result = self.resume(task)
        self.assertEqual(result.error_code, "collection_profile_unverified")
        self.assertEqual(result.operation["status"], "failed")
        self.assertEqual(self.embedding.calls, [])

    def test_upsert_error_records_unknown_outcome_failure(self):
        task = self.task()
        self.store.write_error = RuntimeError("private write failure")
        result = self.resume(task)
        self.assertEqual((result.operation["status"], result.error_code), ("failed", "upsert_outcome_unknown"))
        self.assertEqual(result.operation["target_profile"], fixture.PROFILE)
        self.assertFalse(result.operation["execution_claimed"])

    def test_failed_retry_reuses_operation_document_chunks_and_vector_ids(self):
        task = self.task()
        def lost_ack(context):
            raise TimeoutError("write committed but acknowledgement lost")
        self.store.after_write = lost_ack
        failed = self.resume(task)
        first_ids = set(self.store.records)
        first_chunks = self.store.writes[0][0]
        self.assertEqual(failed.operation["status"], "failed")
        self.store.after_write = None
        result = self.service.retry(task["operation_id"], failed.operation["state_version"])
        self.assertEqual(result.operation["status"], "ready")
        self.assertEqual(result.operation["operation_id"], task["operation_id"])
        self.assertEqual(result.operation["document_id"], task["document_id"])
        self.assertEqual(self.store.writes[1][0], first_chunks)
        self.assertEqual(set(self.store.records), first_ids)
        self.assertEqual(result.operation["attempt_count"], 2)
        self.assertEqual(self.counts(), (1, 2, 1))

    def test_claimed_task_does_not_execute_or_release_other_owner(self):
        task = self.task()
        active = repository.claim_index_operation(task["operation_id"], 1, "another-worker")
        result = self.resume(task)
        self.assertEqual(result.reason, "already_claimed")
        self.assertFalse(result.execution_started)
        self.assertEqual(repository.get_index_operation(task["operation_id"]), active)
        self.assertEqual(self.store.probes, [])
        self.assertEqual(self.embedding.calls, [])

    def test_completed_task_is_not_executed_again(self):
        task = self.task()
        ready = self.resume(task)
        for method in (self.service.resume, self.service.retry):
            result = method(task["operation_id"], 1)
            self.assertFalse(result.execution_started)
            self.assertEqual(result.operation, ready.operation)
        self.assertEqual(len(self.embedding.calls), 1)
        self.assertEqual(len(self.store.writes), 1)

    def test_stale_version_and_wrong_resume_retry_actions_do_not_execute(self):
        task = self.task()
        self.assertEqual(self.service.resume(task["operation_id"], 2).reason, "version_conflict")
        self.assertEqual(self.service.retry(task["operation_id"], 1).reason, "resume_required")
        self.assertEqual(self.embedding.calls, [])
        self.embedding.error = RuntimeError("injected")
        failed = self.resume(task)
        self.embedding.error = None
        self.assertEqual(self.service.resume(task["operation_id"], failed.operation["state_version"]).reason, "retry_required")
        self.assertEqual(self.service.retry(task["operation_id"], 1).reason, "version_conflict")
        self.assertEqual(len(self.embedding.calls), 1)

    def test_claim_race_returns_current_owner_without_marking_it_failed(self):
        task = self.task()
        claim = repository.claim_index_operation
        def competing_claim(operation_id, version, owner):
            claim(operation_id, version, "winner")
            return claim(operation_id, version, owner)
        with patch.object(repository, "claim_index_operation", side_effect=competing_claim):
            result = self.resume(task)
        self.assertEqual(result.reason, "already_claimed")
        self.assertEqual(result.operation["owner_instance_id"], "winner")
        self.assertEqual(self.embedding.calls, [])
        self.assertTrue(self.gate.acquire(blocking=False))
        self.gate.release()

    def test_keyword_and_not_requested_tasks_never_call_executors(self):
        task = self.task()
        service = RagIndexingService(self.embedding, self.store, owner_instance_id="kw", retrieval_mode="keyword")
        self.assertEqual(service.resume(task["operation_id"], 1).reason, "vector_index_disabled")
        keyword = self.prepare(file_name="keyword").operation
        self.assertEqual(self.resume(keyword).reason, "index_not_requested")
        self.assertEqual(self.store.probes, [])
        self.assertEqual(self.embedding.calls, [])

    def test_ready_confirmation_failure_is_unconfirmed_and_not_retried(self):
        task = self.task()
        with patch.object(repository, "finish_index_operation", side_effect=sqlite3.OperationalError("private")) as finish:
            result = self.resume(task)
        self.assertFalse(result.state_confirmed)
        self.assertIsNone(result.operation)
        self.assertTrue(result.execution_started)
        self.assertEqual(finish.call_count, 1)
        current = repository.get_index_operation(task["operation_id"])
        self.assertEqual(current["status"], "pending")
        self.assertTrue(current["execution_claimed"])
        self.assertEqual(len(self.store.records), 2)
        self.assertEqual(self.service.resume(task["operation_id"], current["state_version"]).reason, "already_claimed")
        self.assertEqual(len(self.store.writes), 1)

    def test_confirmation_committed_but_ack_lost_is_reconciled_only_by_later_query(self):
        task = self.task()
        finish = repository.finish_index_operation
        def lost_ack(*args, **kwargs):
            finish(*args, **kwargs)
            raise sqlite3.OperationalError("ack lost")
        with patch.object(repository, "finish_index_operation", side_effect=lost_ack):
            result = self.resume(task)
        self.assertFalse(result.state_confirmed)
        self.assertIsNone(result.operation)
        self.assertEqual(repository.get_index_operation(task["operation_id"])["status"], "ready")
        self.assertEqual(self.resume(task).reason, "ready")
        self.assertEqual(len(self.embedding.calls), 1)

    def test_failure_confirmation_failure_keeps_pending_and_no_false_failed_receipt(self):
        task = self.task()
        self.embedding.error = RuntimeError("injected")
        with patch.object(repository, "finish_index_operation", side_effect=sqlite3.OperationalError("private")):
            result = self.resume(task)
        self.assertFalse(result.state_confirmed)
        self.assertIsNone(result.operation)
        self.assertTrue(repository.get_index_operation(task["operation_id"])["execution_claimed"])
        self.assertEqual(self.store.writes, [])

    def test_stale_token_cannot_bind_or_fail_another_attempt(self):
        task = self.task()
        newer = []
        def replace_attempt(context):
            failed = repository.finish_index_operation(context.operation_id, context.attempt_token, "failed", "interrupted")
            pending = repository.prepare_index_retry(context.operation_id, failed["state_version"])
            newer.append(repository.claim_index_operation(context.operation_id, pending["state_version"], "new-worker"))
        self.embedding.on_call = replace_attempt
        result = self.resume(task)
        self.assertFalse(result.state_confirmed)
        self.assertEqual(repository.get_index_operation(task["operation_id"]), newer[0])
        self.assertEqual(self.store.writes, [])

    def test_stale_token_cannot_finish_a_newer_attempt_after_upsert(self):
        task = self.task()
        newer = []
        def replace_attempt(context):
            failed = repository.finish_index_operation(context.operation_id, context.attempt_token, "failed", "interrupted")
            pending = repository.prepare_index_retry(context.operation_id, failed["state_version"])
            newer.append(repository.claim_index_operation(context.operation_id, pending["state_version"], "new-worker"))
        self.store.after_write = replace_attempt
        result = self.resume(task)
        self.assertFalse(result.state_confirmed)
        self.assertEqual(repository.get_index_operation(task["operation_id"]), newer[0])
        self.assertEqual(len(self.store.writes), 1)

    def test_capacity_is_nonblocking_and_budget_does_not_release_running_attempt(self):
        task = self.task()
        another = self.task(file_name="another")
        entered, release = Event(), Event()
        def block(context):
            entered.set()
            if not release.wait(timeout=5):
                raise AssertionError("test did not release fake")
        self.embedding.on_call = block
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(self.resume, task)
            try:
                self.assertTrue(entered.wait(timeout=5))
                self.assertEqual(self.resume(task).reason, "already_claimed")
                result = self.resume(another)
                self.assertEqual(result.reason, "capacity_unavailable")
                self.assertFalse(result.operation["execution_claimed"])
                self.assertEqual(result.operation["attempt_count"], 0)
            finally:
                release.set()
            self.assertEqual(running.result(timeout=5).reason, "ready")
        self.embedding.on_call = None
        self.assertEqual(self.resume(another).reason, "ready")

    def test_retry_without_capacity_leaves_resumable_pending(self):
        task = self.task()
        self.embedding.error = RuntimeError("injected")
        failed = self.resume(task)
        self.embedding.error = None
        self.gate.acquire()
        try:
            result = self.service.retry(task["operation_id"], failed.operation["state_version"])
            self.assertEqual(result.reason, "capacity_unavailable")
            self.assertEqual(result.operation["status"], "pending")
            self.assertFalse(result.execution_started)
            self.assertFalse(result.operation["execution_claimed"])
            self.assertEqual(result.operation["attempt_count"], 1)
        finally:
            self.gate.release()
        self.assertEqual(self.resume(result.operation).reason, "ready")

    def test_budget_exhausted_before_upsert_is_failed_but_late_upsert_success_is_ready(self):
        now = [0.0]
        service = RagIndexingService(self.embedding, self.store, owner_instance_id="clock", budget_seconds=1,
                                     clock=lambda: now[0], execution_gate=self.gate)
        task = self.task()
        self.embedding.on_call = lambda context: now.__setitem__(0, 2.0)
        result = service.resume(task["operation_id"], 1)
        self.assertEqual(result.error_code, "attempt_budget_exceeded")
        self.assertEqual(self.store.writes, [])
        self.embedding.on_call = None
        later = self.task(file_name="late-success")
        self.store.after_write = lambda context: now.__setitem__(0, now[0] + 2)
        result = service.resume(later["operation_id"], 1)
        self.assertEqual(result.reason, "ready")
        self.assertTrue(result.state_confirmed)

    def test_missing_task_bad_version_and_storage_read_failure(self):
        with self.assertRaises(LookupError):
            self.service.resume("missing", 1)
        for version in (0, True, "1"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                self.service.resume("missing", version)
        with patch.object(repository, "get_index_operation", side_effect=sqlite3.OperationalError("private")):
            result = self.service.resume("missing", 1)
        self.assertFalse(result.state_confirmed)
        self.assertFalse(result.execution_started)
        self.assertEqual(self.embedding.calls, [])


if __name__ == "__main__":
    unittest.main()

"""R2：临时 SQLite、真实 Service/Adapter 与故障注入 Fake Collection，无模型调用。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
import sqlite3
from threading import Barrier, Event
from urllib.parse import urlsplit, parse_qs
from unittest.mock import patch
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.chat import router as chat_router
from backend.api.document_deletion import router, get_deletion_service
from backend.db import deletion_repository as deletion, index_repository as index
from backend.db.connection import get_connection
from backend.db.repository import get_document_chunks, save_rag_query_with_hits, ensure_chat_session
from backend.rag.deletion_adapter import ChromaDeletionAdapter
from backend.rag import vector_store
from backend.rag.service import build_rag_context_from_chunks
from backend.services.document_deletion import DocumentDeletionService
from backend.services.rag_indexing import RagIndexingService
from tests import test_index_repository as fixture
from tests.test_rag_indexing_service import FakeEmbedding, FakeVectorStore


TARGET = {key: fixture.PROFILE[key] for key in ('collection_name', 'vector_store_fingerprint')}


class FakeDeletionCollection:
    name = 'rag_chunks'

    def __init__(self):
        self.records = {}
        self.calls = []
        self.failure = None
        self.query_calls = []

    def delete(self, *, ids):
        self.calls.append(list(ids))
        if self.failure == 'no_op':
            return
        for key in ids:
            self.records.pop(key, None)
            if self.failure == 'partial':
                raise RuntimeError('private SDK error must not leak')
        if self.failure == 'lost_ack':
            raise TimeoutError('private acknowledgement lost')

    def get(self, *, ids, include):
        return {'ids': [key for key in ids if key in self.records]}

    def query(self, **kwargs):
        self.query_calls.append(kwargs)
        def matches(meta, where):
            if '$and' in where:
                return all(matches(meta, item) for item in where['$and'])
            return all(meta.get(k) in v['$in'] if isinstance(v, dict) else meta.get(k) == v for k, v in where.items())
        rows = [row for row in self.records.values() if matches(row, kwargs['where'])][:kwargs['n_results']]
        return {'documents': [[r['text'] for r in rows]], 'metadatas': [rows], 'distances': [[0.1] * len(rows)]}


class DocumentDeletionTests(unittest.TestCase):
    prepare = fixture.IndexRepositoryTests.prepare
    execute = fixture.IndexRepositoryTests.execute

    def setUp(self):
        fixture.IndexRepositoryTests.setUp(self)
        self.collection = FakeDeletionCollection()
        target_patch = patch('backend.rag.deletion_adapter.build_deletion_target', return_value=TARGET)
        target_patch.start()
        self.addCleanup(target_patch.stop)
        self.adapter = ChromaDeletionAdapter(lambda: self.collection)
        self.service = DocumentDeletionService(self.adapter, lambda: TARGET)
        app = FastAPI()
        app.include_router(chat_router)
        app.include_router(router)
        app.dependency_overrides[get_deletion_service] = lambda: self.service
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def document(self, **kwargs):
        args = dict(knowledge_base_id='enterprise_default', knowledge_base_type='finance')
        args.update(kwargs)
        prepared = self.prepare(**args)
        for chunk in prepared.chunks:
            self.collection.records[f"{prepared.operation['storage_session_id']}:{chunk['db_chunk_id']}"] = deepcopy(chunk)
            self.collection.records[f"{prepared.operation['storage_session_id']}:{chunk['db_chunk_id']}"]['session_id'] = prepared.operation['storage_session_id']
        return prepared

    def remove(self, prepared):
        return self.service.create(prepared.document_id, 'enterprise_default', 'kb_admin', 1)

    def task(self, prepared):
        return deletion.prepare_deletion(prepared.document_id, 'enterprise_default', 1, TARGET)

    def lifecycle(self, prepared):
        with closing(get_connection()) as connection:
            row = connection.execute('SELECT lifecycle_state FROM documents WHERE id = ?', (prepared.document_id,)).fetchone()
            return row[0] if row else None

    def retry(self, receipt):
        return self.service.retry(receipt['operation_id'], 'enterprise_default', 'kb_admin', receipt['state_version'])

    def test_normal_delete_cascades_but_independent_receipt_survives(self):
        doc = self.document()
        other = self.document(file_name='other.md')
        receipt = self.remove(doc)
        self.assertEqual(receipt['status'], 'completed')
        self.assertIsNone(self.lifecycle(doc))
        self.assertEqual(self.lifecycle(other), 'active')
        self.assertEqual(len(self.collection.records), len(other.chunks))
        task = deletion.get_deletion(receipt['operation_id'])
        self.assertEqual(task['vector_ids'], [f"{doc.operation['storage_session_id']}:{c['db_chunk_id']}" for c in doc.chunks])
        self.assertEqual(task['attempt_count'], 1)
        self.assertIsNone(index.get_index_operation(doc.operation['operation_id']))
        self.assertNotIn('attempt_token', receipt)

    def test_keyword_document_still_cleans_historical_vectors(self):
        doc = self.document()
        self.assertEqual(doc.operation['status'], 'not_requested')
        self.assertEqual(self.remove(doc)['status'], 'completed')
        self.assertEqual(self.collection.records, {})

    def test_no_vector_document_is_safe(self):
        doc = self.document()
        self.collection.records.clear()
        self.assertEqual(self.remove(doc)['status'], 'completed')

    def test_partial_failure_retains_snapshot_and_explicit_retry_finishes(self):
        doc = self.document()
        self.collection.failure = 'partial'
        failed = self.remove(doc)
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(self.lifecycle(doc), 'deleting')
        self.assertEqual(len(self.collection.records), 1)
        snapshot = deletion.get_deletion(failed['operation_id'])['vector_ids']
        self.assertEqual(len(snapshot), 2)
        self.assertNotIn('private', failed['error_message'])
        self.collection.failure = None
        done = self.retry(failed)
        self.assertEqual(done['operation_id'], failed['operation_id'])
        self.assertEqual(done['attempt_count'], 2)
        self.assertEqual(done['status'], 'completed')
        self.assertEqual(deletion.get_deletion(done['operation_id'])['vector_ids'], snapshot)

    def test_lost_delete_acknowledgement_retry_is_idempotent(self):
        doc = self.document()
        self.collection.failure = 'lost_ack'
        failed = self.remove(doc)
        self.assertEqual(self.collection.records, {})
        self.collection.failure = None
        self.assertEqual(self.retry(failed)['status'], 'completed')

    def test_delete_verification_rejects_noop(self):
        doc = self.document()
        self.collection.failure = 'no_op'
        self.assertEqual(self.remove(doc)['status'], 'failed')
        self.assertEqual(self.lifecycle(doc), 'deleting')

    def test_partial_batch_delete_retry_preserves_ids(self):
        doc = self.document(chunks=[f'chunk-{i}' for i in range(205)])
        original = self.collection.delete
        def fail_second(*, ids):
            if len(self.collection.calls) == 1:
                raise TimeoutError('batch 2 failed')
            original(ids=ids)
        with patch.object(self.collection, 'delete', side_effect=fail_second):
            failed = self.remove(doc)
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(len(self.collection.records), 105)
        self.assertEqual(self.retry(failed)['status'], 'completed')
        self.assertEqual(self.collection.records, {})

    def test_sqlite_final_delete_failure_rolls_back_and_retry_succeeds(self):
        doc = self.document()
        self.execute("CREATE TRIGGER fail_document_delete BEFORE DELETE ON documents BEGIN SELECT RAISE(ABORT, 'injected'); END")
        failed = self.remove(doc)
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(self.collection.records, {})
        self.assertEqual(self.lifecycle(doc), 'deleting')
        self.execute('DROP TRIGGER fail_document_delete')
        self.assertEqual(self.retry(failed)['status'], 'completed')

    def test_completed_confirmation_lost_is_only_success_after_persisted_query(self):
        doc = self.document()
        original = deletion.finish_deletion
        def lost(*args, **kwargs):
            result = original(*args, **kwargs)
            if kwargs['completed']:
                raise sqlite3.OperationalError('lost acknowledgement')
            return result
        with patch.object(deletion, 'finish_deletion', side_effect=lost):
            receipt = self.remove(doc)
        self.assertEqual(receipt['status'], 'completed')
        self.assertTrue(receipt['state_confirmed'])

    def test_finish_unavailable_does_not_report_completed_and_manual_recovery_is_controlled(self):
        doc = self.document()
        with patch.object(deletion, 'finish_deletion', side_effect=sqlite3.OperationalError):
            uncertain = self.remove(doc)
        self.assertFalse(uncertain['state_confirmed'])
        self.assertEqual(uncertain['status'], 'pending')
        self.assertEqual(self.lifecycle(doc), 'deleting')
        current = deletion.get_deletion(uncertain['operation_id'])
        with self.assertRaises(ValueError):
            deletion.recover_deletion(current['operation_id'], current['state_version'])
        recovered = deletion.recover_deletion(current['operation_id'], current['state_version'], execution_stopped_confirmed=True)
        with self.assertRaises(deletion.DeletionConflict):
            deletion.finish_deletion(current['operation_id'], current['attempt_token'], current['state_version'], completed=True)
        self.assertEqual(self.retry(recovered)['status'], 'completed')

    def test_claim_failure_preserves_unclaimed_task_for_query_and_repeat(self):
        doc = self.document()
        with patch.object(deletion, 'claim_deletion', side_effect=sqlite3.OperationalError):
            unknown = self.remove(doc)
        self.assertFalse(unknown['state_confirmed'])
        self.assertEqual(len(self.collection.calls), 0)
        confirmed = self.service.query(unknown['operation_id'], 'enterprise_default', 'kb_admin')
        self.assertTrue(confirmed['retryable'])
        self.assertEqual(self.retry(confirmed)['status'], 'completed')

    def test_duplicate_delete_never_executes_again_and_failed_requires_retry(self):
        doc = self.document()
        self.collection.failure = 'no_op'
        failed = self.remove(doc)
        calls = len(self.collection.calls)
        self.assertEqual(self.remove(doc)['operation_id'], failed['operation_id'])
        self.assertEqual(len(self.collection.calls), calls)
        self.collection.failure = None
        done = self.retry(failed)
        calls = len(self.collection.calls)
        self.assertEqual(self.remove(doc)['operation_id'], done['operation_id'])
        self.assertEqual(len(self.collection.calls), calls)

    def test_active_index_claim_rejects_deletion_without_side_effects(self):
        doc = self.document(vector=True)
        index.claim_index_operation(doc.operation['operation_id'], doc.operation['state_version'], 'worker')
        with self.assertRaises(deletion.DeletionConflict):
            self.task(doc)
        self.assertEqual(self.lifecycle(doc), 'active')
        self.assertIsNone(deletion.get_document_deletion(doc.document_id, 'enterprise_default'))

    def test_pending_and_failed_indexes_cannot_resume_retry_or_upload_after_deleting(self):
        for failed in (False, True):
            doc = self.document(vector=True, file_name=f'{failed}.md')
            if failed:
                claimed = index.claim_index_operation(doc.operation['operation_id'], 1, 'worker')
                index.finish_index_operation(claimed['operation_id'], claimed['attempt_token'], 'failed', 'embedding_failed')
            self.task(doc)
            current = index.get_index_operation(doc.operation['operation_id'])
            with self.assertRaises(index.IndexOperationConflict):
                index.claim_index_operation(current['operation_id'], current['state_version'], 'late-worker')
            with self.assertRaises(index.IndexOperationConflict):
                index.prepare_index_retry(current['operation_id'], current['state_version'])
            with self.assertRaises(index.IndexOperationConflict):
                self.document(vector=True, file_name=f'{failed}.md')
            embedding, store = FakeEmbedding(), FakeVectorStore()
            engine = RagIndexingService(embedding, store, owner_instance_id='late-worker')
            self.assertEqual(engine.resume(current['operation_id'], current['state_version']).reason, 'document_deleting')
            self.assertEqual(embedding.calls, [])
            self.assertEqual(store.writes, [])

    def test_delete_and_index_claim_are_transactionally_exclusive(self):
        doc = self.document(vector=True)
        barrier = Barrier(2)
        def delete():
            barrier.wait()
            try: self.task(doc); return 'deleted'
            except deletion.DeletionConflict: return 'blocked'
        def claim():
            barrier.wait()
            try: index.claim_index_operation(doc.operation['operation_id'], 1, 'racer'); return 'claimed'
            except index.IndexOperationConflict: return 'blocked'
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(delete), pool.submit(claim)]
            results = [future.result() for future in futures]
        self.assertIn(results, [['deleted', 'blocked'], ['blocked', 'claimed']])

    def test_delete_during_real_service_execution_is_rejected(self):
        doc = self.document(vector=True)
        entered, release = Event(), Event()
        embedding, store = FakeEmbedding(), FakeVectorStore()
        embedding.on_call = lambda context: (entered.set(), release.wait(5))
        service = RagIndexingService(embedding, store, owner_instance_id='in-flight')
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(service.resume, doc.operation['operation_id'], 1)
            try:
                self.assertTrue(entered.wait(5))
                with self.assertRaises(deletion.DeletionConflict): self.task(doc)
            finally: release.set()
            self.assertEqual(future.result().reason, 'ready')

    def test_cas_claim_versions_and_tokens_are_enforced(self):
        doc = self.document()
        task = self.task(doc)
        with self.assertRaises(deletion.DeletionConflict): deletion.claim_deletion(task['operation_id'], 99)
        claimed = deletion.claim_deletion(task['operation_id'], task['state_version'])
        with self.assertRaises(deletion.DeletionConflict): deletion.claim_deletion(task['operation_id'], task['state_version'])
        with self.assertRaises(deletion.DeletionConflict):
            deletion.finish_deletion(task['operation_id'], 'wrong', claimed['state_version'], completed=True)
        with self.assertRaises(deletion.DeletionConflict):
            deletion.finish_deletion(task['operation_id'], claimed['attempt_token'], task['state_version'], completed=True)

    def test_snapshot_tampering_refuses_claim(self):
        task = self.task(self.document())
        self.execute("UPDATE document_deletion_tasks SET vector_ids_json = '[]' WHERE operation_id = ?", (task['operation_id'],))
        with self.assertRaises(deletion.DeletionConflict): deletion.claim_deletion(task['operation_id'], 1)

    def test_prepare_task_insert_failure_rolls_back_deleting_and_index_version(self):
        doc = self.document(vector=True)
        self.execute("CREATE TRIGGER fail_delete_task BEFORE INSERT ON document_deletion_tasks BEGIN SELECT RAISE(ABORT, 'injected'); END")
        with self.assertRaises(sqlite3.Error): self.task(doc)
        self.assertEqual(self.lifecycle(doc), 'active')
        self.assertEqual(index.get_index_operation(doc.operation['operation_id'])['state_version'], 1)

    def test_sqlite_completion_update_failure_also_rolls_back_physical_document_delete(self):
        doc = self.document()
        self.execute("CREATE TRIGGER fail_completion BEFORE UPDATE ON document_deletion_tasks "
                     "WHEN NEW.status = 'completed' BEGIN SELECT RAISE(ABORT, 'injected'); END")
        failed = self.remove(doc)
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(self.lifecycle(doc), 'deleting')
        self.assertIsNotNone(index.get_index_operation(doc.operation['operation_id']))
        self.execute('DROP TRIGGER fail_completion')
        self.assertEqual(self.retry(failed)['status'], 'completed')

    def test_concurrent_deletion_claim_has_one_winner(self):
        task = self.task(self.document())
        barrier = Barrier(2)
        def claim():
            barrier.wait()
            try: deletion.claim_deletion(task['operation_id'], 1); return True
            except deletion.DeletionConflict: return False
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(claim), pool.submit(claim)]
            self.assertEqual(sum(f.result() for f in futures), 1)

    def test_delete_and_failed_index_retry_cannot_produce_a_late_writer(self):
        doc = self.document(vector=True)
        claimed = index.claim_index_operation(doc.operation['operation_id'], 1, 'worker')
        failed = index.finish_index_operation(claimed['operation_id'], claimed['attempt_token'], 'failed', 'embedding_failed')
        barrier = Barrier(2)
        def delete():
            barrier.wait()
            try: self.task(doc); return True
            except deletion.DeletionConflict: return False
        def retry():
            barrier.wait()
            try:
                pending = index.prepare_index_retry(failed['operation_id'], failed['state_version'])
                index.claim_index_operation(pending['operation_id'], pending['state_version'], 'retry-worker')
                return True
            except index.IndexOperationConflict: return False
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(delete), pool.submit(retry)]
            results = [f.result() for f in futures]
        self.assertIn(results, [[True, False], [False, True]])

    def test_completed_deletion_cannot_resume_old_index_or_recreate_old_ids(self):
        doc = self.document(vector=True)
        self.assertEqual(self.remove(doc)['status'], 'completed')
        embedding, store = FakeEmbedding(), FakeVectorStore()
        service = RagIndexingService(embedding, store, owner_instance_id='late')
        with self.assertRaises(LookupError): service.resume(doc.operation['operation_id'], 1)
        with self.assertRaises(index.IndexOperationConflict): index.claim_index_operation(doc.operation['operation_id'], 1, 'late')
        self.assertEqual(store.records, {})
        self.assertEqual(embedding.calls, [])

    def test_legacy_document_without_index_task_can_be_deleted(self):
        doc = self.document()
        self.execute('DELETE FROM rag_index_tasks WHERE document_id = ?', (doc.document_id,))
        self.assertEqual(self.remove(doc)['status'], 'completed')

    def test_retrieval_excludes_deleting_and_orphans_without_losing_other_document(self):
        doc = self.document()
        other = self.document(file_name='other.md')
        cached = deepcopy(doc.chunks)
        self.task(doc)
        with patch.object(vector_store, 'get_chroma_collection', return_value=self.collection), \
             patch.object(vector_store, 'generate_embedding', return_value=[1, 0, 0]):
            found = vector_store.retrieve_similar_chunks(None, '制度', 'finance', 'enterprise_default', top_k=1)
        self.assertEqual([c['document_id'] for c in found], [other.document_id])
        self.assertEqual(len(get_document_chunks('', 'enterprise_default')), len(other.chunks))
        self.assertNotIn(cached[0]['text'], build_rag_context_from_chunks(cached))
        # 故意留下物理向量，直接完成 SQLite 删除，模拟历史残留。
        task = deletion.get_document_deletion(doc.document_id, 'enterprise_default')
        claimed = deletion.claim_deletion(task['operation_id'], task['state_version'])
        deletion.finish_deletion(task['operation_id'], claimed['attempt_token'], claimed['state_version'], completed=True)
        with patch.object(vector_store, 'get_chroma_collection', return_value=self.collection), \
             patch.object(vector_store, 'generate_embedding', return_value=[1, 0, 0]):
            self.assertTrue(all(c['document_id'] == other.document_id for c in
                vector_store.retrieve_similar_chunks(None, '制度', 'finance', 'enterprise_default')))
        ensure_chat_session('query')
        query_id = save_rag_query_with_hits('query', '制度', 3, cached, retrieval_mode='vector')
        with closing(get_connection()) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM rag_hits WHERE rag_query_id = ?', (query_id,)).fetchone()[0], 0)

    def test_adapter_refuses_changed_storage_target(self):
        doc = self.document()
        with patch('backend.rag.deletion_adapter.build_deletion_target', return_value={**TARGET, 'collection_name': 'other'}):
            self.assertEqual(self.remove(doc)['status'], 'failed')
        self.assertEqual(self.collection.calls, [])

    def test_api_permissions_scope_status_and_no_recover_route(self):
        doc = self.document(vector=True)
        path = f'/documents/{doc.document_id}'
        params = dict(knowledge_base_id='enterprise_default', user_role='employee')
        self.assertEqual(self.client.request('DELETE', path, params=params, json={'expected_document_version': 1}).status_code, 403)
        params['user_role'] = 'kb_admin'
        params['knowledge_base_id'] = 'other'
        self.assertEqual(self.client.request('DELETE', path, params=params, json={'expected_document_version': 1}).status_code, 404)
        params['knowledge_base_id'] = 'enterprise_default'
        claimed = index.claim_index_operation(doc.operation['operation_id'], 1, 'busy')
        self.assertEqual(self.client.request('DELETE', path, params=params, json={'expected_document_version': 1}).status_code, 409)
        index.finish_index_operation(claimed['operation_id'], claimed['attempt_token'], 'ready')
        response = self.client.request('DELETE', path, params=params, json={'expected_document_version': 1})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn('attempt_token', response.text)
        task_id = response.json()['operation_id']
        self.assertEqual(self.client.get(f'/document_deletions/{task_id}', params=params).status_code, 200)
        self.assertEqual(self.client.post(f'/document_deletions/{task_id}/recover').status_code, 404)

    def test_api_claimed_returns_202_retry_errors_have_receipt(self):
        doc = self.document()
        task = self.task(doc)
        claimed = deletion.claim_deletion(task['operation_id'], 1)
        params = dict(knowledge_base_id='enterprise_default', user_role='kb_admin')
        url = f'/documents/{doc.document_id}'
        response = self.client.request('DELETE', url, params=params, json={'expected_document_version': 1})
        self.assertEqual(response.status_code, 202, response.text)
        self.assertNotIn('attempt_token', response.text)
        deletion.finish_deletion(task['operation_id'], claimed['attempt_token'], claimed['state_version'], completed=False)
        failed = deletion.get_deletion(task['operation_id'])
        retry = f"/document_deletions/{task['operation_id']}/retry"
        self.assertEqual(self.client.post(retry, params=params, json={'expected_state_version': 1}).status_code, 409)
        self.collection.failure = 'no_op'
        response = self.client.post(retry, params=params, json={'expected_state_version': failed['state_version']})
        self.assertEqual(response.status_code, 502, response.text)
        self.assertTrue(response.json()['retryable'])
        status = self.client.get('/rag_status/upload', params={'knowledge_base_id': 'enterprise_default'}).json()
        self.assertEqual(status['documents'][0]['lifecycle_state'], 'deleting')
        self.assertEqual(status['documents'][0]['deletion']['status'], 'failed')

    def test_status_url_and_location_can_be_followed_without_rebuilding_scope(self):
        kb = 'team/财务 & 预算'
        params = dict(knowledge_base_id=kb, user_role='admin')
        for state in ('completed', 'pending', 'failed', 'unconfirmed'):
            with self.subTest(state=state):
                doc = self.document(knowledge_base_id=kb, file_name=f'{state}.md')
                if state == 'pending':
                    task = deletion.prepare_deletion(doc.document_id, kb, 1, TARGET)
                    deletion.claim_deletion(task['operation_id'], task['state_version'])
                self.collection.failure = 'no_op' if state == 'failed' else None
                def request():
                    return self.client.request('DELETE', f'/documents/{doc.document_id}', params=params,
                                               json={'expected_document_version': 1})
                if state == 'unconfirmed':
                    with patch.object(deletion, 'finish_deletion', side_effect=sqlite3.OperationalError):
                        response = request()
                else:
                    response = request()
                self.assertEqual(response.status_code, {'completed': 200, 'pending': 202, 'failed': 502, 'unconfirmed': 503}[state], response.text)
                location = response.headers['Location']
                self.assertEqual(location, response.json()['status_url'])
                self.assertEqual(parse_qs(urlsplit(location).query), {key: [value] for key, value in params.items()})
                followed = self.client.get(location)
                self.assertEqual(followed.status_code, 200, followed.text)
                self.assertEqual(followed.json()['document_id'], doc.document_id)
                self.assertEqual(self.client.get(followed.headers['Location']).status_code, 200)
                if state == 'failed':
                    self.collection.failure = None
                    retried = self.client.post(f"/document_deletions/{response.json()['operation_id']}/retry", params=params,
                        json={'expected_state_version': response.json()['state_version']})
                    self.assertEqual(retried.status_code, 200, retried.text)
                    self.assertEqual(self.client.get(retried.headers['Location']).status_code, 200)


if __name__ == '__main__':
    unittest.main()

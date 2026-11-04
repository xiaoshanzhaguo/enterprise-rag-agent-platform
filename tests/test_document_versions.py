"""R3: temporary SQLite only; no real model, Chroma or business data."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier, Event
import json
from unittest.mock import patch
import unittest
import test_index_repository as fixtures
from backend.db import version_repository as versions, index_repository as indexes, deletion_repository as deletion
from backend.db.connection import get_connection
from backend.db.init_db import init_database
from backend.db.repository import get_active_document_ids, get_document_chunks, filter_active_chunks
from backend.schema.chat_schema import IndexDocumentRequest
from backend.services.rag_documents import IndexManagementService
from backend.services.document_versions import DocumentVersionService, VersionForbidden


STORE = {'collection_name': 'rag_chunks', 'vector_store_fingerprint': fixtures.PROFILE['vector_store_fingerprint']}


class DocumentVersionTests(unittest.TestCase):
    setUp = fixtures.IndexRepositoryTests.setUp
    prepare = fixtures.IndexRepositoryTests.prepare
    execute = fixtures.IndexRepositoryTests.execute

    def identity(self, doc):
        return versions.document_version(doc.document_id)['logical_document_id']

    def next(self, doc, **changes):
        return self.prepare(logical_document_id=self.identity(doc), chunks=['更新的制度'], **changes)

    def publish(self, doc):
        parent = versions.get_logical_document(self.identity(doc))
        versions.publish_document(doc.document_id, parent['state_version'])

    def test_new_logical_document(self):
        first = self.prepare()
        second = self.prepare(file_name='another.md')
        self.assertNotEqual(self.identity(first), self.identity(second))
        self.assertEqual(versions.document_version(first.document_id)['version_number'], 1)

    def test_same_filename_changed_content_is_independent(self):
        first = self.prepare()
        second = self.prepare(chunks=['不同内容'])
        self.assertNotEqual(self.identity(first), self.identity(second))

    def test_v2_v3(self):
        first = self.prepare()
        second = self.next(first)
        third = self.prepare(logical_document_id=self.identity(first), chunks=['第三版'])
        self.assertEqual([versions.document_version(d.document_id)['version_number'] for d in [first, second, third]], [1, 2, 3])

    def test_rename_does_not_create_version(self):
        first = self.prepare()
        duplicate = self.prepare(logical_document_id=self.identity(first), file_name='renamed.md')
        self.assertEqual(first.document_id, duplicate.document_id)
        self.assertEqual(first.chunks, duplicate.chunks)

    def test_category_and_department_create_immutable_versions(self):
        first = self.prepare()
        second = self.prepare(logical_document_id=self.identity(first), knowledge_base_type='hr', department='HR')
        self.assertNotEqual(first.document_id, second.document_id)
        self.assertEqual(versions.get_logical_document(self.identity(first))['versions'][-1]['knowledge_base_type'], 'general')

    def test_duplicate_metadata_reuses_history(self):
        first = self.prepare()
        self.next(first)
        duplicate = self.prepare(logical_document_id=self.identity(first))
        self.assertTrue(duplicate.document_reused)
        self.assertEqual(first.document_id, duplicate.document_id)

    def test_candidates_not_retrievable(self):
        first = self.prepare()
        self.assertEqual(get_active_document_ids('upload'), [])
        self.assertEqual(get_document_chunks('upload'), [])
        self.assertEqual(filter_active_chunks(first.chunks), [])

    def test_publish_and_rollback_retrieval(self):
        first = self.prepare(); self.publish(first)
        second = self.next(first); self.publish(second)
        self.assertEqual(get_active_document_ids('upload'), [second.document_id])
        self.assertEqual(filter_active_chunks(first.chunks), [])
        self.publish(first)
        self.assertEqual(get_active_document_ids('upload'), [first.document_id])
        self.assertEqual(versions.get_logical_document(self.identity(first))['version_counter'], 2)

    def test_failed_new_version_keeps_old(self):
        first = self.prepare(); self.publish(first)
        second = self.next(first, vector=True)
        claimed = indexes.claim_index_operation(second.operation['operation_id'], 1, 'test')
        indexes.finish_index_operation(claimed['operation_id'], claimed['attempt_token'], 'failed', 'embedding_failed')
        with self.assertRaises(versions.VersionConflict): self.publish(second)
        self.assertEqual(get_active_document_ids('upload'), [first.document_id])

    def test_ready_auto_publication(self):
        first = self.prepare(); self.publish(first)
        second = self.next(first, vector=True)
        claimed = indexes.claim_index_operation(second.operation['operation_id'], 1, 'test')
        indexes.finish_index_operation(claimed['operation_id'], claimed['attempt_token'], 'ready')
        self.assertTrue(versions.auto_publish(second.document_id))
        self.assertEqual(get_active_document_ids('upload'), [second.document_id])

    def test_publication_cas_conflict_keeps_candidate(self):
        first = self.prepare(); self.publish(first)
        second = self.next(first)
        self.publish(first)  # explicit choice supersedes a pending automatic publication
        self.assertFalse(versions.auto_publish(second.document_id))
        self.assertEqual(get_active_document_ids('upload'), [first.document_id])

    def test_published_history_never_implicitly_republished(self):
        first = self.prepare(); self.publish(first)
        second = self.next(first); self.publish(second)
        self.assertFalse(versions.auto_publish(first.document_id))
        self.assertEqual(get_active_document_ids('upload'), [second.document_id])

    def test_current_deletion_protected(self):
        first = self.prepare(); self.publish(first)
        with self.assertRaises(deletion.DeletionConflict):
            deletion.prepare_deletion(first.document_id, 'session_scoped', 1, STORE)
        self.assertEqual(get_active_document_ids('upload'), [first.document_id])

    def test_history_delete_and_monotonic_counter(self):
        first = self.prepare(); self.publish(first)
        second = self.next(first); self.publish(second)
        task = deletion.prepare_deletion(first.document_id, 'session_scoped', 1, STORE)
        self.assertEqual(task['logical_document_id'], self.identity(second))
        self.assertEqual(task['version_number'], 1)
        claimed = deletion.claim_deletion(task['operation_id'], task['state_version'])
        deletion.finish_deletion(claimed['operation_id'], claimed['attempt_token'], claimed['state_version'], completed=True)
        third = self.prepare(logical_document_id=self.identity(second), chunks=['新第三版'])
        self.assertEqual(versions.document_version(third.document_id)['version_number'], 3)

    def test_disable_immediately_hides_and_allows_delete(self):
        first = self.prepare(); self.publish(first)
        parent = versions.get_logical_document(self.identity(first))
        versions.disable_document(self.identity(first), parent['state_version'])
        self.assertEqual(get_active_document_ids('upload'), [])
        task = deletion.prepare_deletion(first.document_id, 'session_scoped', 1, STORE)
        self.assertEqual(task['status'], 'pending')

    def test_disable_blocks_auto_publish(self):
        first = self.prepare(); self.publish(first)
        second = self.next(first)
        versions.disable_document(self.identity(first), 2)
        self.assertFalse(versions.auto_publish(second.document_id))
        self.assertEqual(get_active_document_ids('upload'), [])

    def test_deleting_candidate_cannot_publish_or_claim(self):
        first = self.prepare(vector=True)
        deletion.prepare_deletion(first.document_id, 'session_scoped', 1, STORE)
        with self.assertRaises(versions.VersionConflict): self.publish(first)
        with self.assertRaises(indexes.IndexOperationConflict):
            indexes.claim_index_operation(first.operation['operation_id'], 1, 'test')

    def test_index_claim_blocks_delete(self):
        first = self.prepare(vector=True)
        indexes.claim_index_operation(first.operation['operation_id'], 1, 'test')
        with self.assertRaises(deletion.DeletionConflict):
            deletion.prepare_deletion(first.document_id, 'session_scoped', 1, STORE)

    def test_concurrent_upload_allocates_unique_numbers(self):
        first = self.prepare(); barrier = Barrier(2)
        def upload(text):
            barrier.wait()
            return self.prepare(logical_document_id=self.identity(first), chunks=[text])
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(upload, ['并发甲', '并发乙']))
        self.assertEqual(sorted(versions.document_version(d.document_id)['version_number'] for d in results), [2, 3])

    def test_concurrent_publish_only_one_wins(self):
        first = self.prepare(); second = self.next(first); barrier = Barrier(2)
        def publish(doc):
            barrier.wait()
            try: versions.publish_document(doc.document_id, 1); return True
            except versions.VersionConflict: return False
        with ThreadPoolExecutor(2) as pool: self.assertEqual(sum(pool.map(publish, [first, second])), 1)

    def test_publish_delete_race_has_single_safe_winner(self):
        first = self.prepare(); self.publish(first)
        second = self.next(first); barrier = Barrier(2)
        def publish():
            barrier.wait()
            try: versions.publish_document(second.document_id, 2); return 'publish'
            except versions.VersionConflict: return 'conflict'
        def remove():
            barrier.wait()
            try: deletion.prepare_deletion(second.document_id, 'session_scoped', 1, STORE); return 'delete'
            except deletion.DeletionConflict: return 'conflict'
        with ThreadPoolExecutor(2) as pool:
            a, b = pool.submit(publish), pool.submit(remove)
            self.assertEqual([a.result(), b.result()].count('conflict'), 1)

    def test_wrong_scope_rejected(self):
        first = self.prepare()
        with self.assertRaises(versions.VersionConflict):
            self.prepare(session_id='other', logical_document_id=self.identity(first))

    def test_legacy_helper_never_mutates_published_metadata(self):
        from backend.db.repository import save_document_with_chunks
        first = save_document_with_chunks('upload', 'same.md', ['正文'], department='HR')
        second = save_document_with_chunks('upload', 'same.md', ['正文'], department='财务')
        self.assertNotEqual(first[0]['document_id'], second[0]['document_id'])
        with closing(get_connection()) as connection:
            self.assertEqual(connection.execute('SELECT department FROM documents WHERE id = ?', (first[0]['document_id'],)).fetchone()[0], 'HR')

    def test_keyword_upload_publishes_and_rename_inherits_metadata(self):
        service = IndexManagementService(mode='keyword')
        first = service.upload(IndexDocumentRequest(session_id='admin', file_name='finance.md', document_text='企业规则'))
        self.assertTrue(first.receipt['is_current_version'])
        second = service.upload(IndexDocumentRequest(session_id='admin', file_name='hr.md', document_text='企业规则',
                                logical_document_id=first.receipt['logical_document_id']))
        self.assertEqual(first.receipt['document_id'], second.receipt['document_id'])

    def test_migration_keeps_ids_and_is_repeatable(self):
        first = self.prepare()
        old_chunks = [c['db_chunk_id'] for c in first.chunks]
        identity = self.identity(first)
        with closing(get_connection()) as connection, connection:
            connection.execute('UPDATE documents SET logical_document_id = NULL, version_number = NULL, published_at = NULL')
            connection.execute('DELETE FROM logical_documents WHERE logical_document_id = ?', (identity,))
        init_database(self.url)
        migrated = versions.document_version(first.document_id)
        init_database(self.url)
        self.assertEqual(migrated, versions.document_version(first.document_id))
        self.assertEqual([c['db_chunk_id'] for c in get_document_chunks('upload')], old_chunks)
        self.assertEqual(indexes.get_index_operation(first.operation['operation_id'])['document_id'], first.document_id)
        self.assertEqual(indexes.load_index_input(first.operation['operation_id'])['chunks'], first.chunks)

    def test_api_publication_permissions_and_cas(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.api.document_versions import router
        app = FastAPI(); app.include_router(router)
        with TestClient(app) as client:
            created = client.post('/logical_documents?knowledge_base_id=version-test&user_role=kb_admin', json={'display_name': '制度'})
            self.assertEqual(created.status_code, 200)
            identity = created.json()['logical_document_id']
            self.assertEqual(client.post(f'/logical_documents/{identity}/disable?knowledge_base_id=version-test',
                             json={'expected_state_version': 1}).status_code, 403)
            url = f'/logical_documents/{identity}/disable?knowledge_base_id=version-test&user_role=kb_admin'
            self.assertEqual(client.post(url, json={'expected_state_version': 1}).status_code, 200)
            self.assertEqual(client.post(url, json={'expected_state_version': 1}).status_code, 409)
            self.assertEqual(client.get(f'/logical_documents/{identity}?knowledge_base_id=other').status_code, 404)

    def test_actual_pre_r3_schema_migration(self):
        from backend.db.schema import CREATE_TABLE_SQL
        from backend.db.connection import resolve_sqlite_path
        legacy_url = 'sqlite:///' + resolve_sqlite_path(self.url).with_name('legacy.db').as_posix()
        with closing(get_connection(legacy_url)) as connection, connection:
            for statement in CREATE_TABLE_SQL[:-1]: connection.execute(statement)
            connection.execute("INSERT INTO chat_sessions(id, mode) VALUES ('legacy', 'rag')")
            for doc_id in [41, 42]:
                connection.execute("INSERT INTO documents(id,session_id,file_name,content_hash) VALUES (?, 'legacy', 'same.md', 'legacy-hash')", (doc_id,))
                connection.execute("INSERT INTO document_chunks(id,document_id,chunk_index,chunk_text,text_length) VALUES (?, ?, 1, '历史正文', 4)", (doc_id + 100, doc_id))
        init_database(legacy_url)
        init_database(legacy_url)
        with closing(get_connection(legacy_url)) as connection:
            rows = connection.execute('SELECT * FROM documents ORDER BY id').fetchall()
            self.assertEqual([r['id'] for r in rows], [41, 42])
            self.assertEqual([r['version_number'] for r in rows], [1, 1])
            self.assertNotEqual(rows[0]['logical_document_id'], rows[1]['logical_document_id'])
            self.assertEqual(connection.execute('SELECT count(*) FROM logical_documents WHERE current_document_id IS NOT NULL').fetchone()[0], 2)
            self.assertEqual([r[0] for r in connection.execute('SELECT id FROM document_chunks ORDER BY id')], [141, 142])
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_failed_legacy_migration_remains_unpublished(self):
        first = self.prepare(vector=True)
        with closing(get_connection()) as connection, connection:
            connection.execute('UPDATE documents SET logical_document_id = NULL')
        init_database(self.url)
        self.assertEqual(get_active_document_ids('upload'), [])

    def test_ready_service_recovers_publication_after_finish_ack_loss(self):
        from backend.services.rag_indexing import RagIndexingService
        first = self.prepare(vector=True)
        claimed = indexes.claim_index_operation(first.operation['operation_id'], 1, 'worker')
        ready = indexes.finish_index_operation(claimed['operation_id'], claimed['attempt_token'], 'ready')
        engine = RagIndexingService(None, None, owner_instance_id='test')
        self.assertEqual(engine.resume(ready['operation_id'], ready['state_version']).reason, 'ready')
        self.assertEqual(get_active_document_ids('upload'), [first.document_id])

    def test_vector_service_failure_then_retry_auto_publishes(self):
        from test_rag_indexing_service import FakeEmbedding, FakeVectorStore
        from backend.services.rag_indexing import RagIndexingService
        first = self.prepare(); self.publish(first)
        second = self.next(first, vector=True)
        engine = RagIndexingService(FakeEmbedding(), FakeVectorStore(), owner_instance_id='test')
        with patch.object(engine.embedding, 'embed', side_effect=RuntimeError('fake failure')):
            failed = engine.resume(second.operation['operation_id'], 1)
        self.assertEqual(failed.reason, 'failed')
        self.assertEqual(get_active_document_ids('upload'), [first.document_id])
        ready = engine.retry(failed.operation_id, failed.operation['state_version'])
        self.assertEqual(ready.reason, 'ready')
        self.assertEqual(get_active_document_ids('upload'), [second.document_id])

    def test_deletion_failure_does_not_erase_logical_audit(self):
        first = self.prepare()
        task = deletion.prepare_deletion(first.document_id, 'session_scoped', 1, STORE)
        claimed = deletion.claim_deletion(task['operation_id'], task['state_version'])
        failed = deletion.finish_deletion(claimed['operation_id'], claimed['attempt_token'], claimed['state_version'], completed=False)
        self.assertEqual(failed['logical_document_id'], self.identity(first))
        self.assertEqual(failed['version_number'], 1)
        self.assertEqual(get_active_document_ids('upload'), [])

    def test_keyword_and_context_and_hit_log_exclude_history(self):
        from backend.rag.service import build_rag_context_from_chunks
        from backend.db.repository import ensure_chat_session, save_rag_query_with_hits
        first = self.prepare(); self.publish(first)
        second = self.next(first); self.publish(second)
        self.assertEqual([c['document_id'] for c in get_document_chunks('upload')], [second.document_id])
        self.assertNotIn(first.chunks[0]['text'], build_rag_context_from_chunks(first.chunks))
        ensure_chat_session('question')
        query_id = save_rag_query_with_hits('question', '制度', 3, first.chunks, retrieval_mode='keyword')
        with closing(get_connection()) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM rag_hits WHERE rag_query_id = ?', (query_id,)).fetchone()[0], 0)

    def test_vector_whitelist_rejects_historical_and_candidate_vectors(self):
        from tests.test_document_deletion import FakeDeletionCollection
        from backend.rag import vector_store
        first = self.prepare(); self.publish(first)
        second = self.next(first); self.publish(second)
        third = self.prepare(logical_document_id=self.identity(first), chunks=['未发布第三版'])
        collection = FakeDeletionCollection()
        for document in [first, second, third]:
            for chunk in document.chunks: collection.records[str(chunk['db_chunk_id'])] = dict(chunk, session_id='upload')
        with patch.object(vector_store, 'get_chroma_collection', return_value=collection), \
             patch.object(vector_store, 'generate_embedding', return_value=[1., 0., 0.]):
            found = vector_store.retrieve_similar_chunks('upload', '制度', None, None)
        self.assertEqual([c['document_id'] for c in found], [second.document_id])
        self.assertEqual(collection.query_calls[0]['where']['$and'][-1], {'document_id': {'$in': [second.document_id]}})

    def test_vector_mode_does_not_publish_keyword_only_candidate(self):
        first = self.prepare()
        with self.assertRaises(versions.VersionConflict):
            versions.publish_document(first.document_id, 1, retrieval_mode='vector')

    def test_explicit_null_metadata_creates_version_without_mutating_history(self):
        service = IndexManagementService(mode='keyword')
        first = service.upload(IndexDocumentRequest(session_id='admin', file_name='p.md', document_text='企业制度',
                                                    knowledge_base_type='finance', department='财务BP'))
        second = service.upload(IndexDocumentRequest(session_id='admin', file_name='p.md', document_text='企业制度',
                            logical_document_id=first.receipt['logical_document_id'], department=None))
        self.assertNotEqual(first.receipt['document_id'], second.receipt['document_id'])
        self.assertIsNone(second.receipt['department'])

    def test_concurrent_same_content_reuses_one_version(self):
        first = self.prepare(); identity = self.identity(first); barrier = Barrier(2)
        def upload(_):
            barrier.wait()
            return self.prepare(logical_document_id=identity, chunks=['重复新版本'])
        with ThreadPoolExecutor(2) as pool: result = list(pool.map(upload, [0, 1]))
        self.assertEqual(result[0].document_id, result[1].document_id)
        self.assertEqual(versions.get_logical_document(identity)['version_counter'], 2)

    def test_public_version_service_rejects_session_scope_without_writes(self):
        first = self.prepare(); self.publish(first)
        service = DocumentVersionService()
        parent = versions.get_logical_document(self.identity(first))
        calls = [lambda: service.list('session_scoped'),
                 lambda: service.get(self.identity(first), 'session_scoped'),
                 lambda: service.create('session_scoped', 'kb_admin', 'unsafe'),
                 lambda: service.publish(self.identity(first), 'session_scoped', 'kb_admin', first.document_id, 2),
                 lambda: service.disable(self.identity(first), 'session_scoped', 'kb_admin', 2)]
        for call in calls:
            with self.assertRaises(VersionForbidden): call()
        self.assertEqual(versions.get_logical_document(self.identity(first)), parent)
        self.assertEqual(get_active_document_ids('upload'), [first.document_id])

    def test_public_version_api_rejects_session_scope(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.api.document_versions import router
        first = self.prepare(); identity = self.identity(first)
        app = FastAPI(); app.include_router(router)
        with TestClient(app) as client:
            query = '?knowledge_base_id=session_scoped&user_role=kb_admin'
            self.assertEqual(client.get('/logical_documents' + query).status_code, 403)
            self.assertEqual(client.get(f'/logical_documents/{identity}' + query).status_code, 403)
            self.assertEqual(client.post('/logical_documents' + query, json={'display_name': 'test'}).status_code, 403)
            self.assertEqual(client.post(f'/logical_documents/{identity}/publish' + query,
                             json={'document_id': first.document_id, 'expected_state_version': 1}).status_code, 403)
            self.assertEqual(client.post(f'/logical_documents/{identity}/disable' + query,
                             json={'expected_state_version': 1}).status_code, 403)

    def test_public_scope_checks_storage_session_not_just_kb_field(self):
        first = self.prepare()
        self.execute('UPDATE logical_documents SET knowledge_base_id = ?', ('enterprise_default',))
        service = DocumentVersionService()
        self.assertEqual(service.list('enterprise_default')['logical_documents'], [])
        with self.assertRaises(LookupError): service.get(self.identity(first), 'enterprise_default')
        with self.assertRaises(LookupError): service.disable(self.identity(first), 'enterprise_default', 'kb_admin', 1)

    def test_management_status_uses_one_snapshot_during_publication(self):
        first = self.prepare(); self.publish(first)
        second = self.next(first)
        identity = self.identity(first)
        reading, written = Event(), Event()
        original = versions._document_version_in_connection
        calls = []
        def version_read(connection, document_id):
            value = original(connection, document_id)
            calls.append(document_id)
            if len(calls) == 1:
                reading.set()
                self.assertTrue(written.wait(3))
            return value
        def publish():
            self.assertTrue(reading.wait(3))
            with closing(get_connection()) as connection, connection:
                connection.execute('BEGIN IMMEDIATE')
                connection.execute('UPDATE logical_documents SET current_document_id = ?, state_version = state_version + 1 WHERE logical_document_id = ?',
                                   (second.document_id, identity))
                written.set()  # COMMIT waits for the reader snapshot; other rows must still see the old pointer.
        with patch.object(versions, '_document_version_in_connection', side_effect=version_read):
            with ThreadPoolExecutor(2) as pool:
                writer = pool.submit(publish)
                snapshot = IndexManagementService(mode='keyword').document_status('upload')
                writer.result(timeout=3)
        docs = snapshot['documents']
        self.assertEqual([d['document_id'] for d in docs if d['is_current_version']], [first.document_id])
        self.assertEqual({d['logical_state_version'] for d in docs}, {2})
        self.assertEqual(get_active_document_ids('upload'), [second.document_id])
        self.assertNotIn('attempt_token', json.dumps(snapshot))

    def agent_metadata(self, chunks):
        from types import SimpleNamespace
        from backend.services.agent_service import _build_agent_rag_metadata
        return _build_agent_rag_metadata(request=SimpleNamespace(session_id='upload'), matched_chunks=chunks,
            need_knowledge_base=True, decision_reason='test', rewritten_query='制度', effective_retrieval_query='制度',
            retrieval_mode='keyword', knowledge_base_id=None, knowledge_base_type_filter=None)

    def test_agent_rechecks_evidence_and_metadata_after_publication(self):
        from backend.services.agent_service import _current_evidence
        first = self.prepare(); self.publish(first)
        second = self.next(first); self.publish(second)
        self.assertEqual(_current_evidence(first.chunks), [])
        self.assertNotIn('rag_preview_chunks', self.agent_metadata(first.chunks))

    def test_message_write_rechecks_citations_after_metadata_was_built(self):
        from backend.db.repository import save_chat_message, get_session_messages
        first = self.prepare(); self.publish(first)
        metadata = self.agent_metadata(first.chunks)
        self.assertEqual(metadata['rag_preview_chunks'][0]['document_id'], first.document_id)
        second = self.next(first); self.publish(second)
        save_chat_message('upload', 'assistant', 'answer', metadata=metadata)
        saved = get_session_messages('upload')[-1]
        self.assertNotIn('rag_preview_chunks', metadata)
        self.assertFalse(saved.get('rag_preview_chunks'))
        self.assertTrue(metadata['rag_citations_invalidated'])

    def test_message_write_rejects_disabled_and_deleting_citations(self):
        from backend.db.repository import save_chat_message, get_session_messages
        first = self.prepare(); self.publish(first)
        before_disable = self.agent_metadata(first.chunks)
        versions.disable_document(self.identity(first), 2)
        save_chat_message('upload', 'assistant', 'disabled', metadata=before_disable)
        self.assertNotIn('rag_preview_chunks', before_disable)
        # A copied old receipt is checked again after deletion starts.
        self.publish(first)
        before_delete = self.agent_metadata(first.chunks)
        versions.disable_document(self.identity(first), versions.document_version(first.document_id)['logical_state_version'])
        deletion.prepare_deletion(first.document_id, 'session_scoped', 1, STORE)
        save_chat_message('upload', 'assistant', 'deleting', metadata=before_delete)
        self.assertNotIn('rag_preview_chunks', before_delete)
        self.assertTrue(all(not msg.get('rag_preview_chunks') for msg in get_session_messages('upload')))

    def test_current_citations_and_status_are_saved_and_emitted_consistently(self):
        from backend.db.repository import save_chat_message
        first = self.prepare(); self.publish(first)
        second = self.next(first); self.publish(second)
        metadata = self.agent_metadata(second.chunks)
        save_chat_message('upload', 'assistant', 'current answer', metadata=metadata)
        self.assertEqual(metadata['rag_preview_chunks'][0]['db_chunk_id'], second.chunks[0]['db_chunk_id'])
        self.assertEqual([d['document_id'] for d in metadata['rag_status_info']['documents']], [second.document_id])
        with closing(get_connection()) as connection:
            persisted = json.loads(connection.execute("SELECT metadata_json FROM chat_messages WHERE content = 'current answer'").fetchone()[0])
        self.assertEqual(persisted, metadata)

    def test_message_validation_and_insert_hold_publication_write_lock(self):
        from backend.db import repository
        first = self.prepare(); self.publish(first)
        second = self.next(first)
        metadata = self.agent_metadata(first.chunks)
        validating, begin_attempted, acquired = Event(), Event(), Event()
        original = repository._filter_active_chunks_in_connection
        def validate(connection, chunks):
            valid = original(connection, chunks)
            validating.set()
            self.assertTrue(begin_attempted.wait(3))
            self.assertFalse(acquired.is_set())
            return valid
        def publish():
            self.assertTrue(validating.wait(3))
            with closing(get_connection()) as connection, connection:
                connection.set_trace_callback(lambda sql: begin_attempted.set() if sql == 'BEGIN IMMEDIATE' else None)
                connection.execute('BEGIN IMMEDIATE')
                acquired.set()
                connection.execute('UPDATE logical_documents SET current_document_id = ?, state_version = state_version + 1 WHERE logical_document_id = ?',
                                   (second.document_id, self.identity(first)))
        with patch.object(repository, '_filter_active_chunks_in_connection', side_effect=validate):
            with ThreadPoolExecutor(2) as pool:
                writer = pool.submit(publish)
                repository.save_chat_message('upload', 'assistant', 'atomic answer', metadata=metadata)
                writer.result(timeout=3)
        self.assertTrue(acquired.is_set())
        self.assertEqual(metadata['rag_preview_chunks'][0]['document_id'], first.document_id)
        self.assertEqual(get_active_document_ids('upload'), [second.document_id])


if __name__ == '__main__': unittest.main()

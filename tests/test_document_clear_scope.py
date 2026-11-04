"""R2-01：真实临时 SQLite + Fake Collection 验证会话清空范围。"""

from contextlib import closing
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import quote
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.chat import router
from backend.db.connection import get_connection
from backend.db.repository import delete_session_documents
from backend.rag.store import clear_document_chunks
from tests import test_index_repository as fixture


class DocumentClearScopeTests(unittest.TestCase):
    setUpDatabase = fixture.IndexRepositoryTests.setUp
    prepare = fixture.IndexRepositoryTests.prepare

    def setUp(self):
        self.setUpDatabase()
        self.private = self.prepare(session_id='employee-session')
        self.shared = self.prepare(session_id='uploader', knowledge_base_id='enterprise_default')
        self.vectors = {
            'employee-session:1': {'session_id': 'employee-session'},
            'knowledge_base:enterprise_default:2': {'session_id': 'knowledge_base:enterprise_default'},
        }
        self.collection = Mock()
        self.collection.delete.side_effect = self.delete_vectors
        factory = patch('backend.rag.vector_store.get_chroma_collection', return_value=self.collection)
        self.factory = factory.start()
        self.addCleanup(factory.stop)
        settings = patch('backend.rag.store.settings', SimpleNamespace(rag_retrieval_mode='vector'))
        self.settings = settings.start()
        self.addCleanup(settings.stop)
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def delete_vectors(self, *, where):
        self.vectors = {key: value for key, value in self.vectors.items()
                        if value['session_id'] != where['session_id']}

    def snapshot(self):
        with closing(get_connection()) as connection:
            return list(connection.iterdump())

    def assert_private_cleared(self):
        with closing(get_connection()) as connection:
            for table in ('documents', 'document_chunks', 'rag_index_tasks'):
                key = 'id' if table == 'documents' else 'document_id'
                self.assertEqual(connection.execute(
                    f'SELECT COUNT(*) FROM {table} WHERE {key} = ?', (self.private.document_id,),
                ).fetchone()[0], 0)
                self.assertGreater(connection.execute(
                    f'SELECT COUNT(*) FROM {table} WHERE {key} = ?', (self.shared.document_id,),
                ).fetchone()[0], 0)

    def test_vector_session_clear_preserves_public_documents_and_vectors(self):
        response = self.client.delete('/clear_document/employee-session')
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_private_cleared()
        self.collection.delete.assert_called_once_with(where={'session_id': 'employee-session'})
        self.assertEqual(list(self.vectors), ['knowledge_base:enterprise_default:2'])

    def test_keyword_session_clear_keeps_original_no_vector_call_behavior(self):
        self.settings.rag_retrieval_mode = 'keyword'
        response = self.client.delete('/clear_document/employee-session')
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_private_cleared()
        self.factory.assert_not_called()

    def test_api_rejects_internal_scope_before_calling_service_in_both_modes(self):
        before, vectors = self.snapshot(), dict(self.vectors)
        with patch('backend.api.chat.clear_document_chunks') as service:
            for mode in ('keyword', 'vector'):
                self.settings.rag_retrieval_mode = mode
                for scope in ('knowledge_base:enterprise_default', 'knowledge_base:other', 'knowledge_base:'):
                    with self.subTest(mode=mode, scope=scope):
                        response = self.client.delete('/clear_document/' + quote(scope, safe=''))
                        self.assertEqual(response.status_code, 403, response.text)
                        self.assertIn('公共知识库', response.json()['detail'])
            service.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.vectors, vectors)
        self.factory.assert_not_called()
        self.collection.delete.assert_not_called()

    def test_direct_service_rejects_before_sqlite_or_chroma_mutation(self):
        before, vectors = self.snapshot(), dict(self.vectors)
        with patch('backend.rag.store.delete_session_documents', wraps=delete_session_documents) as delete_sql:
            for mode in ('keyword', 'vector'):
                self.settings.rag_retrieval_mode = mode
                for scope in ('knowledge_base:enterprise_default', 'knowledge_base:other', 'knowledge_base:'):
                    with self.subTest(mode=mode, scope=scope):
                        with self.assertRaisesRegex(ValueError, '公共知识库'):
                            clear_document_chunks(scope)
            delete_sql.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.vectors, vectors)
        self.factory.assert_not_called()
        self.collection.delete.assert_not_called()

    def test_existing_chat_delete_protection_preserves_all_data(self):
        before = self.snapshot()
        response = self.client.delete('/chat_session/knowledge_base:enterprise_default')
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(self.snapshot(), before)
        self.factory.assert_not_called()
        self.collection.delete.assert_not_called()

    def test_normal_chat_delete_still_cascades_and_cleans_its_vectors(self):
        response = self.client.delete('/chat_session/employee-session')
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_private_cleared()
        self.collection.delete.assert_called_once_with(where={'session_id': 'employee-session'})
        self.assertEqual(list(self.vectors), ['knowledge_base:enterprise_default:2'])

    def test_similar_non_internal_session_name_is_allowed(self):
        self.prepare(session_id='knowledge_base_employee')
        response = self.client.delete('/clear_document/knowledge_base_employee')
        self.assertEqual(response.status_code, 200, response.text)
        self.collection.delete.assert_called_once_with(where={'session_id': 'knowledge_base_employee'})


if __name__ == '__main__':
    unittest.main()

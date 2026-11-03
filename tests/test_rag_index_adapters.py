"""B2：真实适配代码 + 临时 Repository + Mock SDK/Collection，无网络或真实模型。"""

from copy import deepcopy
import math
from pathlib import Path
import re
import sys
from threading import BoundedSemaphore
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx
from openai import APITimeoutError

from backend.db import index_repository as repository
from backend.rag import embedding_client, vector_store
from backend.rag.index_adapters import (
    ChromaVectorStoreAdapter, ExistingEmbeddingAdapter, build_index_target_profile,
)
from backend.rag.index_contracts import IndexExecutionContext, IndexExecutionError
from backend.services.rag_indexing import RagIndexingService, create_rag_indexing_service
from tests import test_index_repository as fixture


class ArrayRow:
    def __init__(self, values):
        self.values = values

    def tolist(self):
        return self.values


class FakeCollection:
    def __init__(self):
        self.name = "rag_chunks"
        self.configuration_json = {"hnsw": {"space": "l2"}}
        self.metadata = None
        self.dimension = None
        self.records = {}
        self.upsert_calls = []
        self.query_calls = []
        self.on_upsert = None
        self.error_after_write = False
        self.modify = Mock(side_effect=AssertionError("must not modify collection"))
        self.delete = Mock(side_effect=AssertionError("must not delete collection"))

    def get_model(self):
        return SimpleNamespace(dimension=self.dimension)

    def count(self):
        return len(self.records)

    def upsert(self, **kwargs):
        if self.on_upsert:
            self.on_upsert(kwargs)
        self.upsert_calls.append(deepcopy(kwargs))
        lengths = [len(kwargs[key]) for key in ("ids", "embeddings", "documents", "metadatas")]
        if len(set(lengths)) != 1:
            raise AssertionError("misaligned vectors")
        for vector_id, embedding, text, metadata in zip(
            kwargs["ids"], kwargs["embeddings"], kwargs["documents"], kwargs["metadatas"],
        ):
            self.records[vector_id] = dict(embedding=embedding, text=text, metadata=metadata)
        self.dimension = len(kwargs["embeddings"][0])
        if self.error_after_write:
            raise TimeoutError("simulated acknowledgement loss")

    def query(self, **kwargs):
        self.query_calls.append(kwargs)
        def matches(metadata, where):
            if "$and" in where:
                return all(matches(metadata, item) for item in where["$and"])
            return all(metadata.get(key) == value for key, value in where.items())
        records = [row for row in self.records.values() if matches(row["metadata"], kwargs["where"])][:kwargs["n_results"]]
        return {"documents": [[row["text"] for row in records]],
                "metadatas": [[row["metadata"] for row in records]], "distances": [[0.0] * len(records)]}


class FakeRemoteClient:
    def __init__(self):
        self.options = []
        self.requests = []
        self.mutate_response = None
        self.embeddings = SimpleNamespace(create=self.create)

    def with_options(self, **options):
        self.options.append(options)
        return self

    def create(self, *, model, input):
        self.requests.append((model, list(input)))
        rows = [SimpleNamespace(index=index, embedding=[float(int(re.search(r"\d+$", text).group()) + 1), 0.0, 0.0])
                for index, text in enumerate(input)]
        if self.mutate_response:
            rows = self.mutate_response(rows)
        return SimpleNamespace(data=list(reversed(rows)))


class RagIndexAdapterTests(unittest.TestCase):
    prepare = fixture.IndexRepositoryTests.prepare
    execute = fixture.IndexRepositoryTests.execute
    counts = fixture.IndexRepositoryTests.counts

    def setUp(self):
        fixture.IndexRepositoryTests.setUp(self)
        self.config = SimpleNamespace(
            embedding_provider="local", embedding_model="fake-local-model", embedding_base_url=None,
            embedding_api_key=None, base_url="https://example.invalid/v1", api_key="not-a-real-key",
            vector_store_dir=str(Path(self.url.removeprefix("sqlite:///")).parent / "vectors"),
            rag_preview_text_limit=80, rag_vector_score_threshold=0.0, rag_retrieval_mode="vector",
        )
        for name in ("backend.rag.embedding_client.settings", "backend.rag.vector_store.settings"):
            config_patch = patch(name, self.config)
            config_patch.start()
            self.addCleanup(config_patch.stop)
        self.collection = FakeCollection()
        self.original_get_collection = vector_store.get_chroma_collection
        collection_patch = patch.object(vector_store, "get_chroma_collection", return_value=self.collection)
        self.collection_factory = collection_patch.start()
        self.addCleanup(collection_patch.stop)
        self.model = Mock()
        self.model.encode.side_effect = lambda texts, **kwargs: [ArrayRow([1.0, 0.0, 0.0]) for _ in texts]
        model_patch = patch.object(embedding_client, "get_local_embedding_model", return_value=self.model)
        self.model_factory = model_patch.start()
        self.addCleanup(model_patch.stop)
        self.service = RagIndexingService(ExistingEmbeddingAdapter(), ChromaVectorStoreAdapter(),
                                          owner_instance_id="adapter-test", execution_gate=BoundedSemaphore(1))

    def task(self, **changes):
        arguments = dict(target_profile=build_index_target_profile(), retrieval_mode="vector")
        arguments.update(changes)
        return self.prepare(**arguments).operation

    def resume(self, task):
        return self.service.resume(task["operation_id"], task["state_version"])

    def context(self, task):
        return IndexExecutionContext(task["operation_id"], task["document_id"], task["state_version"],
                                     "test-token", task["storage_session_id"], 100.0, lambda: 0.0)

    def remote(self):
        self.config.embedding_provider = "openai"
        self.config.embedding_model = "fake-remote-model"
        client = FakeRemoteClient()
        client_patch = patch.object(embedding_client, "get_remote_embedding_client", return_value=client)
        client_patch.start()
        self.addCleanup(client_patch.stop)
        return client

    def test_local_integration_binds_before_upsert_and_preserves_metadata(self):
        task = self.task(knowledge_base_id="enterprise_default", knowledge_base_type="finance",
                         department="财务", process_type="reimbursement")
        def inspect_before_write(payload):
            operation = repository.get_index_operation(task["operation_id"])
            self.assertEqual(operation["target_profile"]["embedding_dimension"], 3)
            self.assertEqual(operation["target_profile"]["distance_metric"], "l2")
            self.assertEqual(operation["state_version"], 3)
        self.collection.on_upsert = inspect_before_write
        result = self.resume(task)
        self.assertEqual(result.reason, "ready")
        self.assertTrue(result.state_confirmed)
        self.model_factory.assert_called_once_with("fake-local-model", local_files_only=True)
        self.assertTrue(self.model.encode.call_args.kwargs["normalize_embeddings"])
        payload = self.collection.upsert_calls[0]
        self.assertEqual(payload["documents"], ["第一条制度", "第二条制度"])
        chunks = repository.load_index_input(task["operation_id"])["chunks"]
        self.assertEqual(payload["ids"], [vector_store.build_vector_id(task["storage_session_id"], c["db_chunk_id"]) for c in chunks])
        for metadata, chunk in zip(payload["metadatas"], chunks):
            self.assertEqual(metadata, {
                "session_id": "knowledge_base:enterprise_default", "knowledge_base_id": "enterprise_default",
                "document_id": task["document_id"], "db_chunk_id": chunk["db_chunk_id"], "file_name": "policy.md",
                "chunk_id": chunk["chunk_id"], "knowledge_base_type": "finance", "department": "财务",
                "process_type": "reimbursement", "process_status": "active",
            })
        self.assertTrue(all(call.kwargs == {"create_if_missing": False} for call in self.collection_factory.call_args_list))
        self.collection.modify.assert_not_called()
        self.collection.delete.assert_not_called()

    def test_remote_batches_are_reordered_by_index_without_losing_chunk_positions(self):
        client = self.remote()
        texts = [f"text-{index}" for index in range(23)]
        task = self.task(chunks=texts)
        result = self.resume(task)
        self.assertEqual(result.reason, "ready")
        self.assertEqual([len(texts) for _, texts in client.requests], [10, 10, 3])
        self.assertEqual([text for _, batch in client.requests for text in batch], texts)
        self.assertEqual(len(client.options), 3)
        for options in client.options:
            self.assertEqual(options["max_retries"], 0)
            self.assertLessEqual(options["timeout"].read, 30)
        payload = self.collection.upsert_calls[0]
        self.assertEqual(payload["documents"], texts)
        self.assertEqual([v[0] for v in payload["embeddings"]], list(range(1, 24)))
        self.assertFalse(result.operation["target_profile"]["normalize_embeddings"])
        self.assertEqual(self.model_factory.call_count, 0)

    def test_duplicate_missing_and_out_of_range_response_indexes_prevent_upsert(self):
        client = self.remote()
        mutations = [lambda rows: [rows[0], rows[0]], lambda rows: rows[:-1],
                     lambda rows: [SimpleNamespace(index=9, embedding=rows[0].embedding), rows[1]],
                     lambda rows: [SimpleNamespace(index=True, embedding=rows[0].embedding), rows[1]]]
        for index, mutation in enumerate(mutations):
            client.mutate_response = mutation
            task = self.task(file_name=f"invalid-index-{index}", chunks=["text-0", "text-1"])
            result = self.resume(task)
            self.assertEqual(result.operation["status"], "failed")
        self.assertEqual(self.collection.upsert_calls, [])

    def test_remote_later_batch_failure_does_not_write_partial_vectors(self):
        client = self.remote()
        def truncate_second_batch(rows):
            return rows if len(client.requests) == 1 else rows[:-1]
        client.mutate_response = truncate_second_batch
        task = self.task(chunks=[f"text-{i}" for i in range(12)])
        result = self.resume(task)
        self.assertEqual(result.operation["status"], "failed")
        self.assertEqual(self.collection.upsert_calls, [])
        self.assertEqual(self.counts(), (1, 12, 1))

    def test_remote_sdk_timeout_records_controlled_failure_without_upsert(self):
        client = self.remote()
        with patch.object(client.embeddings, "create", side_effect=APITimeoutError(
            request=httpx.Request("POST", "https://example.invalid/embeddings"),
        )):
            result = self.resume(self.task(chunks=["text-0", "text-1"]))
        self.assertEqual(result.operation["status"], "failed")
        self.assertEqual(result.error_code, "embedding_timeout")
        self.assertEqual(self.collection.upsert_calls, [])

    def test_retry_overwrites_identical_vector_ids_after_lost_ack(self):
        task = self.task()
        self.collection.error_after_write = True
        failed = self.resume(task)
        ids = set(self.collection.records)
        self.assertEqual(failed.operation["status"], "failed")
        self.collection.error_after_write = False
        ready = self.service.retry(task["operation_id"], failed.operation["state_version"])
        self.assertEqual(ready.reason, "ready")
        self.assertEqual(set(self.collection.records), ids)
        self.assertEqual(len(self.collection.records), 2)
        self.assertEqual(self.collection.upsert_calls[0]["ids"], self.collection.upsert_calls[1]["ids"])
        self.assertEqual(self.counts(), (1, 2, 1))

    def test_existing_retriever_reads_new_vectors_with_unchanged_filters(self):
        for kb, category, filename in (("enterprise_default", "finance", "finance.md"),
                                       ("enterprise_default", "hr", "hr.md"), ("another-kb", "finance", "other.md")):
            task = self.task(file_name=filename, knowledge_base_id=kb, knowledge_base_type=category)
            self.assertEqual(self.resume(task).reason, "ready")
        with patch.object(vector_store, "generate_embedding", return_value=[1.0, 0.0, 0.0]):
            chunks = vector_store.retrieve_similar_chunks(None, "制度", "finance", "enterprise_default", top_k=3)
        self.assertTrue(chunks)
        self.assertTrue(all(c["file_name"] == "finance.md" for c in chunks))
        self.assertEqual(self.collection.query_calls[0]["where"], {"$and": [
            {"knowledge_base_id": "enterprise_default"}, {"knowledge_base_type": "finance"},
        ]})
        self.assertTrue(all(c["document_id"] and c["db_chunk_id"] and c["chunk_id"] for c in chunks))

    def test_existing_collection_metric_mismatch_is_rejected_before_embedding(self):
        profile = dict(build_index_target_profile(), distance_metric="cosine")
        task = self.task(target_profile=profile)
        result = self.resume(task)
        self.assertEqual(result.error_code, "index_configuration_changed")
        self.model.encode.assert_not_called()
        self.assertEqual(self.collection.upsert_calls, [])
        self.collection.modify.assert_not_called()
        self.collection.delete.assert_not_called()

    def test_unknown_or_conflicting_metric_is_not_guessed_or_rebuilt(self):
        for index, (config, metadata) in enumerate((({}, None),
            ({"hnsw": {"space": "l2"}}, {"hnsw:space": "cosine"}))):
            self.collection.configuration_json = config
            self.collection.metadata = metadata
            task = self.task(file_name=f"unknown-{index}")
            result = self.resume(task)
            self.assertEqual(result.operation["status"], "failed")
            self.assertIn(result.error_code, ("collection_profile_unverified", "index_configuration_changed"))
        self.model.encode.assert_not_called()
        self.assertEqual(self.collection.upsert_calls, [])

    def test_legacy_explicit_metadata_metric_can_be_verified(self):
        self.collection.configuration_json = {}
        self.collection.metadata = {"hnsw:space": "cosine"}
        task = self.task()
        result = self.resume(task)
        self.assertEqual(result.reason, "ready")
        self.assertEqual(result.operation["target_profile"]["distance_metric"], "cosine")

    def test_collection_dimension_mismatch_rejects_write(self):
        self.collection.dimension = 4
        task = self.task()  # 初始未知；生成实际三维向量后，在物理写入前拒绝。
        result = self.resume(task)
        self.assertEqual(result.operation["status"], "failed")
        self.assertEqual(result.error_code, "index_configuration_changed")
        self.assertEqual(self.collection.upsert_calls, [])
        self.assertEqual(self.collection.dimension, 4)

    def test_known_dimension_mismatch_is_rejected_before_model_call(self):
        self.collection.dimension = 4
        task = self.task(target_profile=dict(build_index_target_profile(), embedding_dimension=3))
        result = self.resume(task)
        self.assertEqual(result.error_code, "index_configuration_changed")
        self.model.encode.assert_not_called()

    def test_known_profile_executes_without_overwriting_configuration(self):
        task = self.task(target_profile=dict(build_index_target_profile(), embedding_dimension=3, distance_metric="l2"))
        self.assertEqual(self.resume(task).reason, "ready")
        self.assertEqual(self.collection.upsert_calls[0]["embeddings"], [[1.0, 0.0, 0.0]] * 2)

    def test_inconsistent_or_nonfinite_embedding_shape_never_upserts(self):
        for index, rows in enumerate(([ArrayRow([1, 0, 0])],
            [ArrayRow([1, 0, 0]), ArrayRow([1, 0])], [ArrayRow([math.nan, 0, 0])] * 2)):
            self.model.encode.side_effect = None
            self.model.encode.return_value = rows
            task = self.task(file_name=f"bad-shape-{index}")
            self.assertEqual(self.resume(task).operation["status"], "failed")
        self.assertEqual(self.collection.upsert_calls, [])

    def test_target_builder_does_not_open_collection_or_load_model_or_store_credentials(self):
        self.remote()
        self.config.embedding_base_url = "https://user:private@example.invalid/v1?token=secret#private"
        self.config.embedding_api_key = "secret"
        profile = build_index_target_profile()
        self.assertRegex(profile["endpoint_fingerprint"], r"^sha256:[0-9a-f]{64}$")
        self.assertNotIn("secret", str(profile))
        self.assertNotIn("private", str(profile))
        self.assertIsNone(profile["embedding_dimension"])
        self.assertIsNone(profile["distance_metric"])
        self.collection_factory.assert_not_called()
        self.model_factory.assert_not_called()

    def test_factory_builds_real_adapters_without_io(self):
        service = create_rag_indexing_service(owner_instance_id="factory", retrieval_mode="vector")
        self.assertIsInstance(service.embedding, ExistingEmbeddingAdapter)
        self.assertIsInstance(service.vector_store, ChromaVectorStoreAdapter)
        self.collection_factory.assert_not_called()
        self.model_factory.assert_not_called()

    def test_noncreating_collection_lookup_uses_existing_name_and_no_embedding_function(self):
        fake_client = Mock()
        fake_client.get_collection.return_value = self.collection
        constructor = Mock(return_value=fake_client)
        with patch.dict(sys.modules, {"chromadb": SimpleNamespace(PersistentClient=constructor)}):
            result = self.original_get_collection(create_if_missing=False)
        self.assertIs(result, self.collection)
        fake_client.get_collection.assert_called_once_with(name="rag_chunks", embedding_function=None)
        fake_client.get_or_create_collection.assert_not_called()
        fake_client.delete_collection.assert_not_called()

    def test_precomputed_write_reuses_mapping_without_embedding(self):
        task = self.task()
        loaded = repository.load_index_input(task["operation_id"])
        with patch.object(vector_store, "generate_embeddings", side_effect=AssertionError("must not re-embed")):
            vector_store.upsert_precomputed_document_chunks(task["storage_session_id"], loaded["chunks"],
                                                             [[1.0, 0.0, 0.0]] * 2, collection=self.collection)
        self.assertEqual(len(self.collection.upsert_calls), 1)
        with self.assertRaises(ValueError):
            vector_store.upsert_precomputed_document_chunks(task["storage_session_id"], loaded["chunks"], [],
                                                             collection=self.collection)


if __name__ == "__main__":
    unittest.main()

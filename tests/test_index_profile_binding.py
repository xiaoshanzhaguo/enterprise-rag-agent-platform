"""B0：临时 SQLite 验证 profile 首次绑定、幂等和并发保护。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
import sqlite3
from threading import Barrier
import unittest
from uuid import uuid4

from backend.db.connection import get_connection
from backend.db.index_repository import (
    IndexInputChanged, IndexOperationConflict, bind_index_profile, claim_index_operation,
    finish_index_operation, get_index_operation, load_index_input,
)
from tests import test_index_repository as fixture


class IndexProfileBindingTests(unittest.TestCase):
    setUp = fixture.IndexRepositoryTests.setUp
    prepare = fixture.IndexRepositoryTests.prepare
    execute = fixture.IndexRepositoryTests.execute

    def active(self, **profile_updates):
        profile = dict(fixture.PROFILE, embedding_dimension=None, distance_metric=None)
        profile.update(profile_updates)
        prepared = self.prepare(vector=True, target_profile=profile)
        return claim_index_operation(prepared.operation["operation_id"], 1, "profile-worker")

    def bind(self, operation, **bindings):
        return bind_index_profile(operation["operation_id"], operation["attempt_token"],
                                  operation["state_version"], bindings)

    def test_first_binding_updates_profile_digest_version_without_changing_execution(self):
        active = self.active()
        bound = self.bind(active, embedding_dimension=3, distance_metric="cosine")
        self.assertEqual(bound["state_version"], active["state_version"] + 1)
        self.assertEqual(bound["target_profile"], fixture.PROFILE)
        for key in ("operation_id", "document_id", "attempt_token", "owner_instance_id", "claimed_at",
                    "attempt_count", "status", "input_sha256", "input_manifest", "created_at"):
            self.assertEqual(bound[key], active[key], key)
        with closing(get_connection()) as connection:
            row = connection.execute("SELECT target_profile_json, target_sha256, state_version "
                                     "FROM rag_index_tasks WHERE operation_id=?", (active["operation_id"],)).fetchone()
        persisted_profile = json.loads(row["target_profile_json"])
        canonical = json.dumps({"input_sha256": active["input_sha256"], "profile": persisted_profile},
                               sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        expected_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        self.assertEqual(row["target_sha256"], expected_hash)
        self.assertEqual(bound["target_sha256"], expected_hash)
        self.assertEqual(row["state_version"], bound["state_version"])
        self.assertNotEqual(bound["target_sha256"], active["target_sha256"])
        self.assertEqual(load_index_input(active["operation_id"])["operation"], bound)
        ready = finish_index_operation(bound["operation_id"], bound["attempt_token"], "ready")
        self.assertEqual(ready["status"], "ready")

    def test_unknown_dimension_can_be_bound_independently(self):
        active = self.active(distance_metric="cosine")
        bound = self.bind(active, embedding_dimension=3)
        self.assertEqual(bound["target_profile"], fixture.PROFILE)
        self.assertEqual(bound["state_version"], active["state_version"] + 1)

    def test_unknown_distance_can_be_bound_independently(self):
        active = self.active(embedding_dimension=3)
        bound = self.bind(active, distance_metric="cosine")
        self.assertEqual(bound["target_profile"], fixture.PROFILE)
        self.assertEqual(bound["state_version"], active["state_version"] + 1)

    def test_partial_binding_keeps_other_unknown_and_increments_once_per_change(self):
        active = self.active()
        dimension = self.bind(active, embedding_dimension=3)
        self.assertIsNone(dimension["target_profile"]["distance_metric"])
        both = self.bind(dimension, distance_metric="cosine")
        self.assertEqual(both["state_version"], active["state_version"] + 2)
        self.assertEqual(both["attempt_token"], active["attempt_token"])
        self.assertEqual(both["attempt_count"], 1)

    def test_known_values_cannot_be_changed_or_combined_with_a_new_binding(self):
        active = self.active(embedding_dimension=3)
        with self.assertRaises(IndexOperationConflict):
            self.bind(active, embedding_dimension=4, distance_metric="cosine")
        self.assertEqual(get_index_operation(active["operation_id"]), active)
        bound = self.bind(active, distance_metric="cosine")
        with self.assertRaises(IndexOperationConflict):
            self.bind(bound, distance_metric="l2")
        self.assertEqual(get_index_operation(active["operation_id"]), bound)

    def test_model_and_other_static_configuration_are_not_bindable(self):
        active = self.active()
        for key in ("model", "provider", "collection_name", "normalize_embeddings", "endpoint_fingerprint",
                    "vector_store_fingerprint", "profile_schema_version", "api_key"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.bind(active, **{key: fixture.PROFILE.get(key, "secret")})
        self.assertEqual(get_index_operation(active["operation_id"]), active)

    def test_wrong_or_empty_token_is_rejected(self):
        active = self.active()
        with self.assertRaises(IndexOperationConflict):
            bind_index_profile(active["operation_id"], str(uuid4()), active["state_version"], {"embedding_dimension": 3})
        for token in (None, "", " "):
            with self.subTest(token=token), self.assertRaises(ValueError):
                bind_index_profile(active["operation_id"], token, active["state_version"], {"embedding_dimension": 3})
        self.assertEqual(get_index_operation(active["operation_id"]), active)

    def test_old_version_is_rejected_even_for_identical_binding(self):
        active = self.active()
        bound = self.bind(active, embedding_dimension=3)
        for bindings in ({"embedding_dimension": 3}, {"distance_metric": "cosine"}):
            with self.subTest(bindings=bindings), self.assertRaises(IndexOperationConflict):
                bind_index_profile(active["operation_id"], active["attempt_token"], active["state_version"], bindings)
        self.assertEqual(get_index_operation(active["operation_id"]), bound)

    def test_current_version_identical_binding_is_idempotent_without_timestamp_change(self):
        active = self.active()
        bound = self.bind(active, embedding_dimension=3, distance_metric="cosine")
        repeated = self.bind(bound, embedding_dimension=3, distance_metric="cosine")
        self.assertEqual(repeated, bound)
        self.assertEqual(self.bind(bound, embedding_dimension=3), bound)

    def test_unclaimed_and_terminal_tasks_cannot_be_bound(self):
        for vector in (False, True):
            prepared = self.prepare(vector=vector, file_name=f"unclaimed-{vector}")
            with self.subTest(vector=vector), self.assertRaises(IndexOperationConflict):
                bind_index_profile(prepared.operation["operation_id"], str(uuid4()), 1, {"embedding_dimension": 3})
        for terminal in ("ready", "failed"):
            prepared = self.prepare(vector=True, target_profile=fixture.PROFILE, file_name=terminal)
            active = claim_index_operation(prepared.operation["operation_id"], 1, "worker")
            finished = finish_index_operation(active["operation_id"], active["attempt_token"], terminal,
                                              "embedding_failed" if terminal == "failed" else None)
            with self.subTest(terminal=terminal), self.assertRaises(IndexOperationConflict):
                bind_index_profile(active["operation_id"], active["attempt_token"], finished["state_version"],
                                   {"embedding_dimension": 3})
            self.assertEqual(get_index_operation(active["operation_id"]), finished)

    def test_operation_id_is_validated_and_missing_uuid_is_not_created(self):
        for operation_id in (None, "", " ", "not-a-uuid"):
            with self.subTest(operation_id=operation_id), self.assertRaises(ValueError):
                bind_index_profile(operation_id, str(uuid4()), 1, {"embedding_dimension": 3})
        with self.assertRaises(LookupError):
            bind_index_profile(str(uuid4()), str(uuid4()), 1, {"embedding_dimension": 3})
        with closing(get_connection()) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM rag_index_tasks").fetchone()[0], 0)

    def test_invalid_versions_and_binding_values_do_not_change_task(self):
        active = self.active()
        for version in (0, -1, True, "2", None):
            with self.subTest(version=version), self.assertRaises(ValueError):
                bind_index_profile(active["operation_id"], active["attempt_token"], version, {"embedding_dimension": 3})
        for bindings in ({}, None, [], {"embedding_dimension": None}, {"embedding_dimension": True},
                         {"embedding_dimension": 0}, {"embedding_dimension": "3"},
                         {"distance_metric": None}, {"distance_metric": "unknown"}):
            with self.subTest(bindings=bindings), self.assertRaises(ValueError):
                bind_index_profile(active["operation_id"], active["attempt_token"], active["state_version"], bindings)
        self.assertEqual(get_index_operation(active["operation_id"]), active)

    def test_update_failure_rolls_back_profile_hash_and_version(self):
        active = self.active()
        self.execute("CREATE TRIGGER fail_binding AFTER UPDATE ON rag_index_tasks "
                     "BEGIN SELECT RAISE(ABORT, 'injected'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.bind(active, embedding_dimension=3)
        self.assertEqual(get_index_operation(active["operation_id"]), active)

    def test_ignored_update_is_a_conflict(self):
        active = self.active()
        self.execute("CREATE TRIGGER ignore_binding BEFORE UPDATE ON rag_index_tasks "
                     "BEGIN SELECT RAISE(IGNORE); END")
        with self.assertRaises(IndexOperationConflict):
            self.bind(active, embedding_dimension=3)
        self.assertEqual(get_index_operation(active["operation_id"]), active)

    def test_corrupt_target_digest_is_not_silently_repaired(self):
        active = self.active()
        self.execute("UPDATE rag_index_tasks SET target_sha256=? WHERE operation_id=?",
                     ("0" * 64, active["operation_id"]))
        corrupt = get_index_operation(active["operation_id"])
        with self.assertRaises(IndexInputChanged):
            self.bind(active, embedding_dimension=3)
        self.assertEqual(get_index_operation(active["operation_id"]), corrupt)

    def test_input_corruption_prevents_binding_without_rewriting_status(self):
        active = self.active()
        self.execute("UPDATE document_chunks SET chunk_text='changed' WHERE document_id=?", (active["document_id"],))
        with self.assertRaises(IndexInputChanged):
            self.bind(active, embedding_dimension=3)
        self.assertEqual(get_index_operation(active["operation_id"]), active)

    def test_concurrent_bindings_using_same_version_have_only_one_winner(self):
        active = self.active()
        barrier = Barrier(2)
        def bind(dimension):
            barrier.wait(timeout=5)
            try:
                return self.bind(active, embedding_dimension=dimension)
            except IndexOperationConflict:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(bind, (3, 4)))
        winners = [result for result in results if result is not None]
        self.assertEqual(len(winners), 1)
        current = get_index_operation(active["operation_id"])
        self.assertEqual(current, winners[0])
        self.assertEqual(current["state_version"], active["state_version"] + 1)
        self.assertEqual(current["attempt_token"], active["attempt_token"])
        self.assertEqual(current["attempt_count"], 1)


if __name__ == "__main__":
    unittest.main()

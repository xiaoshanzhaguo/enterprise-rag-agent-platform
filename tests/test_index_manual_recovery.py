"""C0：临时 SQLite 验证人工恢复、CAS 竞争和 CLI 确认，不接入模型或 Chroma。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import sqlite3
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import uuid4

from backend.cli import rag_index_recovery as cli
from backend.db import index_repository as repository
from tests import test_index_repository as fixture


class IndexManualRecoveryTests(unittest.TestCase):
    setUp = fixture.IndexRepositoryTests.setUp
    prepare = fixture.IndexRepositoryTests.prepare
    claimed = fixture.IndexRepositoryTests.claimed
    execute = fixture.IndexRepositoryTests.execute
    counts = fixture.IndexRepositoryTests.counts

    def recover(self, operation):
        return repository.recover_index_operation(
            operation["operation_id"], operation["state_version"], execution_stopped_confirmed=True,
        )

    def call_cli(self, *args):
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = cli.main(list(args))
        return result, stdout.getvalue(), stderr.getvalue()

    def test_recovery_clears_claim_and_preserves_all_input_and_attempt_count(self):
        active = self.claimed()
        before = repository.load_index_input(active["operation_id"])
        recovered = self.recover(active)
        self.assertEqual(recovered, repository.get_index_operation(active["operation_id"]))
        self.assertEqual(recovered["status"], "failed")
        self.assertEqual(recovered["error_code"], "interrupted")
        self.assertEqual(recovered["state_version"], active["state_version"] + 1)
        self.assertEqual(recovered["attempt_count"], 1)
        self.assertFalse(recovered["execution_claimed"])
        for key in ("attempt_token", "owner_instance_id", "claimed_at"):
            self.assertIsNone(recovered[key])
        changed = {"status", "error_code", "error_message", "attempt_token", "owner_instance_id", "claimed_at",
                   "execution_claimed", "state_version", "updated_at"}
        for key in active.keys() - changed:
            self.assertEqual(recovered[key], active[key], key)
        self.assertEqual(repository.load_index_input(active["operation_id"])["chunks"], before["chunks"])
        self.assertEqual(self.counts(), (1, 2, 1))

    def test_confirmation_is_mandatory_and_requires_actual_boolean_true(self):
        active = self.claimed()
        with patch.object(repository, "get_connection", side_effect=AssertionError("must not connect")):
            with self.assertRaises(ValueError):
                repository.recover_index_operation(active["operation_id"], active["state_version"])
            for confirmation in (False, None, 0, 1, "true"):
                with self.subTest(confirmation=confirmation), self.assertRaises(ValueError):
                    repository.recover_index_operation(active["operation_id"], active["state_version"],
                                                       execution_stopped_confirmed=confirmation)
        self.assertEqual(repository.get_index_operation(active["operation_id"]), active)

    def test_invalid_operation_ids_are_rejected_before_sql(self):
        with patch.object(repository, "get_connection", side_effect=AssertionError("must not connect")):
            for operation_id in (None, "", " ", "not-a-uuid", 123):
                with self.subTest(operation_id=operation_id), self.assertRaises(ValueError):
                    repository.recover_index_operation(operation_id, 1, execution_stopped_confirmed=True)

    def test_unknown_operation_id_does_not_create_a_task(self):
        with self.assertRaises(LookupError):
            repository.recover_index_operation(str(uuid4()), 1, execution_stopped_confirmed=True)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_unclaimed_keyword_ready_and_failed_tasks_cannot_be_recovered(self):
        operations = [self.prepare(file_name="keyword.md").operation,
                      self.prepare(vector=True, file_name="unclaimed.md").operation]
        for status in ("ready", "failed"):
            pending = self.prepare(vector=True, file_name=f"{status}.md").operation
            active = repository.claim_index_operation(pending["operation_id"], pending["state_version"], "worker")
            operations.append(repository.finish_index_operation(active["operation_id"], active["attempt_token"],
                              status, "embedding_failed" if status == "failed" else None))
        for operation in operations:
            with self.subTest(status=operation["status"]), self.assertRaises(repository.IndexOperationConflict):
                self.recover(operation)
            self.assertEqual(repository.get_index_operation(operation["operation_id"]), operation)

    def test_stale_version_is_rejected_without_releasing_owner(self):
        active = self.claimed()
        with self.assertRaises(repository.IndexOperationConflict):
            repository.recover_index_operation(active["operation_id"], active["state_version"] - 1,
                                               execution_stopped_confirmed=True)
        self.assertEqual(repository.get_index_operation(active["operation_id"]), active)

    def test_invalid_versions_are_rejected_before_sql(self):
        with patch.object(repository, "get_connection", side_effect=AssertionError("must not connect")):
            for version in (None, True, 0, -1, "2"):
                with self.subTest(version=version), self.assertRaises(ValueError):
                    repository.recover_index_operation(str(uuid4()), version, execution_stopped_confirmed=True)

    def test_old_token_cannot_finish_recovered_or_new_attempt(self):
        active = self.claimed()
        recovered = self.recover(active)
        for status in ("ready", "failed"):
            with self.subTest(status=status), self.assertRaises(repository.IndexOperationConflict):
                repository.finish_index_operation(active["operation_id"], active["attempt_token"], status,
                                                  "interrupted" if status == "failed" else None)
            self.assertEqual(repository.get_index_operation(active["operation_id"]), recovered)
        pending = repository.prepare_index_retry(recovered["operation_id"], recovered["state_version"])
        new = repository.claim_index_operation(pending["operation_id"], pending["state_version"], "new-worker")
        self.assertNotEqual(new["attempt_token"], active["attempt_token"])
        with self.assertRaises(repository.IndexOperationConflict):
            repository.finish_index_operation(new["operation_id"], active["attempt_token"], "ready")
        self.assertEqual(repository.get_index_operation(new["operation_id"]), new)

    def test_explicit_retry_can_claim_and_finish_with_original_document_chunks(self):
        active = self.claimed()
        original = repository.load_index_input(active["operation_id"])
        recovered = self.recover(active)
        # 人工恢复不直接返回可领取 pending；必须显式准备 retry。
        with self.assertRaises(repository.IndexOperationConflict):
            repository.claim_index_operation(recovered["operation_id"], recovered["state_version"], "new-worker")
        pending = repository.prepare_index_retry(recovered["operation_id"], recovered["state_version"])
        new = repository.claim_index_operation(pending["operation_id"], pending["state_version"], "new-worker")
        self.assertEqual(new["attempt_count"], active["attempt_count"] + 1)
        ready = repository.finish_index_operation(new["operation_id"], new["attempt_token"], "ready")
        self.assertEqual(ready["status"], "ready")
        self.assertEqual(repository.load_index_input(new["operation_id"])["chunks"], original["chunks"])
        self.assertEqual(ready["document_id"], active["document_id"])
        self.assertEqual(self.counts(), (1, 2, 1))

    def test_concurrent_recovery_has_exactly_one_winner(self):
        active = self.claimed()
        barrier = Barrier(2)
        def compete():
            barrier.wait(timeout=5)
            try:
                return self.recover(active)
            except repository.IndexOperationConflict:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(compete) for _ in range(2)]
            results = [future.result(timeout=10) for future in futures]
        winners = [result for result in results if result is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(repository.get_index_operation(active["operation_id"]), winners[0])
        self.assertEqual(winners[0]["state_version"], active["state_version"] + 1)
        self.assertEqual(winners[0]["attempt_count"], active["attempt_count"])

    def test_sql_failure_rolls_back_status_claim_and_version(self):
        active = self.claimed()
        self.execute("CREATE TRIGGER reject_manual_recovery AFTER UPDATE ON rag_index_tasks "
                     "BEGIN SELECT RAISE(ABORT, 'injected'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.recover(active)
        self.assertEqual(repository.get_index_operation(active["operation_id"]), active)

    def test_cli_inspect_is_read_only_and_redacts_token(self):
        active = self.claimed()
        code, output, error = self.call_cli("inspect", active["operation_id"])
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(json.loads(output)["state_version"], active["state_version"])
        self.assertNotIn(active["attempt_token"], output)
        self.assertNotIn("target_profile", output)
        self.assertEqual(repository.get_index_operation(active["operation_id"]), active)

    def test_cli_recover_requires_confirmation_and_reports_new_state(self):
        active = self.claimed()
        code, output, error = self.call_cli("recover", active["operation_id"],
            "--expected-state-version", str(active["state_version"]), "--confirm-execution-stopped")
        self.assertEqual((code, error), (0, ""))
        receipt = json.loads(output)
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["error_code"], "interrupted")
        self.assertEqual(receipt["state_version"], active["state_version"] + 1)
        self.assertFalse(receipt["execution_claimed"])
        self.assertNotIn("attempt_token", receipt)

    def test_cli_missing_confirmation_invalid_id_or_version_never_connects(self):
        cases = [("recover", str(uuid4()), "--expected-state-version", "2"),
                 ("inspect", "invalid"),
                 ("recover", str(uuid4()), "--expected-state-version", "0", "--confirm-execution-stopped")]
        with patch.object(repository, "get_connection", side_effect=AssertionError("must not connect")):
            for args in cases:
                with self.subTest(args=args), self.assertRaises(SystemExit) as raised:
                    self.call_cli(*args)
                self.assertEqual(raised.exception.code, 2)

    def test_cli_state_conflict_and_missing_id_have_nonzero_exit_codes(self):
        active = self.claimed()
        code, output, error = self.call_cli("recover", active["operation_id"], "--expected-state-version", "1",
                                             "--confirm-execution-stopped")
        self.assertEqual(code, 4)
        self.assertEqual(output, "")
        self.assertTrue(error)
        self.assertEqual(repository.get_index_operation(active["operation_id"]), active)
        self.assertEqual(self.call_cli("inspect", str(uuid4()))[0], 3)

    def test_cli_storage_failure_does_not_leak_connection_details(self):
        with patch.object(repository, "get_index_operation", side_effect=sqlite3.OperationalError("secret/path")):
            code, output, error = self.call_cli("inspect", str(uuid4()))
        self.assertEqual(code, 5)
        self.assertEqual(output, "")
        self.assertNotIn("secret", error)


if __name__ == "__main__":
    unittest.main()

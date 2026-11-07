"""Frozen Gold integrity and scoring policy; no external services."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from eval.r4.dataset import DIRECTORY, ROOT
from eval.r4.gold import load_gold, verify_files
from eval.r4.run_baseline import reviewed, score_query, summarize


class FrozenGoldTests(unittest.TestCase):
    def test_confirmed_counts_original_states_and_split_isolation(self):
        _, documents, cases = load_gold()
        self.assertEqual(len(documents), 12)
        self.assertEqual(len(cases), 38)
        self.assertTrue(all(reviewed(c) for c in cases))
        self.assertTrue(all(c['original_annotation_status'] == 'pending_review' for c in cases))
        evidence = [e for c in cases for e in c['evidence']]
        self.assertEqual(len(evidence), 36)
        self.assertEqual(sum(e['role'] == 'primary' for e in evidence), 35)
        self.assertTrue(all(e['original_annotation_status'] == 'pending_review' for e in evidence))
        dev = {c['question'] for c in cases if c['split'] == 'development'}
        holdout = {c['question'] for c in cases if c['split'] == 'holdout'}
        self.assertEqual((len(dev), len(holdout)), (30, 8))
        self.assertFalse(dev & holdout)

    def test_approved_scope_and_background_never_enters_qrels(self):
        _, _, cases = load_gold()
        finance = next(c for c in cases if c['id'] == 'finance_02')
        self.assertEqual(len(finance['evidence']), 2)
        self.assertTrue(finance['scope_note'])
        it = next(c for c in cases if c['id'] == 'it_03')
        self.assertEqual([e['id'] for e in it['evidence']], ['it_03:e1'])
        background = it['background_notes'][0]
        self.assertFalse(background['included_in_qrels'])
        self.assertEqual(background['original_evidence']['annotation_status'], 'pending_review')
        result = score_query(it, [], [], 5)
        self.assertEqual(set(result['evidence_to_chunk_keys']), {'it_03:e1'})

    def copy_frozen(self):
        temp = tempfile.TemporaryDirectory(prefix='r4-frozen-')
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        manifest = json.loads((DIRECTORY / 'gold_v1_manifest.json').read_text(encoding='utf-8'))
        for name in list(manifest['files_sha256']) + ['eval/r4/gold_v1_manifest.json']:
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        return root

    def test_tampered_gold_is_rejected_before_scoring(self):
        root = self.copy_frozen()
        path = root / 'eval/r4/gold_v1.json'
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
            load_gold(path, root=root)

    def test_tampered_original_dataset_is_rejected(self):
        root = self.copy_frozen()
        path = root / 'eval/r4/holdout.jsonl'
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
            load_gold(root / 'eval/r4/gold_v1.json', root=root)

    def test_tampered_source_is_rejected(self):
        root = self.copy_frozen()
        path = next((root / 'docs/sample_knowledge_base').rglob('*.md'))
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
            load_gold(root / 'eval/r4/gold_v1.json', root=root)

    def test_unapproved_copy_is_not_accepted_even_with_matching_file_hash(self):
        root = self.copy_frozen()
        path = root / 'eval/r4/gold_v1.json'
        gold = json.loads(path.read_text(encoding='utf-8'))
        gold['dataset_confirmed_by_user'] = False
        path.write_text(json.dumps(gold), encoding='utf-8')
        manifest_path = path.with_name('gold_v1_manifest.json')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest['files_sha256']['eval/r4/gold_v1.json'] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'not been approved'):
            load_gold(path, root=root)

    def test_hash_checker_rejects_path_escape(self):
        with self.assertRaises(ValueError):
            verify_files({'../outside': '0' * 64})

    def test_formal_report_scores_and_summaries_recompute_from_frozen_gold(self):
        _, _, cases = load_gold()
        by_id = {c['id']: c for c in cases}
        report = json.loads((DIRECTORY / 'baseline_gold_v1_keyword.json').read_text(encoding='utf-8'))
        self.assertEqual(len(report['results']), 38 * 2 * 3)
        for result in report['results']:
            expected = score_query(by_id[result['id']], report['chunk_catalog'], result['results'], result['top_k'])
            for field, value in expected.items():
                self.assertEqual(result[field], value)
            self.assertTrue(result['human_confirmed'])
            self.assertFalse(result['unmapped_evidence'])
        self.assertEqual(report['summary'], summarize(report['results'], False))
        self.assertEqual(report['gold']['file_sha256'], hashlib.sha256((DIRECTORY / 'gold_v1.json').read_bytes()).hexdigest())
        self.assertEqual(report['gold']['manifest_sha256'], hashlib.sha256((DIRECTORY / 'gold_v1_manifest.json').read_bytes()).hexdigest())
        self.assertEqual(report['safety']['real_embedding_calls'], 0)
        self.assertEqual(report['safety']['model_or_chroma_attempts'], 0)

    def test_delivery_checksum_index_matches_all_artifacts(self):
        checksums = json.loads((DIRECTORY / 'gold_v1_checksums.json').read_text(encoding='utf-8'))
        verify_files(checksums['files_sha256'])
        self.assertIn('eval/r4/gold_v1_manifest.json', checksums['files_sha256'])
        self.assertIn('eval/r4/gold_v1_freeze_report.md', checksums['files_sha256'])
        self.assertIn('eval/r4/baseline_gold_v1_keyword.json', checksums['files_sha256'])


if __name__ == '__main__':
    unittest.main()

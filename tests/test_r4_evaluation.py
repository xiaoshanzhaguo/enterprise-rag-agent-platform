"""R4 metrics/data integrity, plus actual production keyword filtering on temporary SQLite."""
from contextlib import closing
import json
import math
from pathlib import Path
import shutil
import tempfile
import unittest

from eval.r4.dataset import DIRECTORY, load_dataset, map_evidence, chunk_record, canonical
from eval.r4.metrics import evaluate_ranking, macro_average
from eval.r4.run_baseline import score_query, summarize


def row(key, document='doc', **extra):
    return dict(chunk_key=key, document_key=document, eligible=True, **extra)


class R4MetricTests(unittest.TestCase):
    def test_recall_uses_relevant_chunk_and_document_denominators(self):
        result = evaluate_ranking([row('a', 'd1'), row('noise', 'd2')], {'a': 2, 'b': 2, 'c': 1}, {'d1', 'd2'}, 3)
        self.assertEqual(result['chunk_recall'], 1 / 3)
        self.assertEqual(result['document_recall'], 1)
        self.assertEqual(result['missing_chunk_keys'], ['b', 'c'])

    def test_mrr_is_rank_sensitive_and_truncated_at_k(self):
        results = [row('noise'), row('a')]
        self.assertEqual(evaluate_ranking(results, {'a': 2}, {'doc'}, 1)['mrr_at_k'], 0)
        self.assertEqual(evaluate_ranking(results, {'a': 2}, {'doc'}, 3)['mrr_at_k'], .5)

    def test_graded_ndcg(self):
        result = evaluate_ranking([row('noise'), row('b'), row('a')], {'a': 2, 'b': 1}, {'doc'}, 3)
        expected = (1 / math.log2(3) + 3 / math.log2(4)) / (3 + 1 / math.log2(3))
        self.assertAlmostEqual(result['ndcg_at_k'], expected)
        self.assertEqual(evaluate_ranking([row('a'), row('b')], {'a': 2, 'b': 1}, {'doc'}, 3)['ndcg_at_k'], 1)

    def test_multiple_evidence_in_different_documents(self):
        result = evaluate_ranking([row('a', 'one'), row('b', 'two')], {'a': 2, 'b': 2}, {'one', 'two'}, 3)
        self.assertEqual(result['document_recall'], 1)
        self.assertEqual(result['chunk_recall'], 1)

    def test_empty_results_are_zero_when_gold_exists(self):
        result = evaluate_ranking([], {'a': 2}, {'doc'}, 5)
        for key in ('document_recall', 'chunk_recall', 'mrr_at_k', 'ndcg_at_k'):
            self.assertEqual(result[key], 0)

    def test_no_relevant_evidence_is_not_assessed_not_a_free_pass(self):
        result = evaluate_ranking([], {}, set(), 3)
        for key in ('document_recall', 'chunk_recall', 'mrr_at_k', 'ndcg_at_k'):
            self.assertIsNone(result[key])
        self.assertEqual(macro_average([result])['chunk_recall']['evaluated_questions'], 0)

    def test_duplicates_cannot_inflate_recall_or_gain(self):
        result = evaluate_ranking([row('a'), row('a'), row('b')], {'a': 2, 'b': 1}, {'doc'}, 3)
        self.assertEqual(result['unique_result_count'], 2)
        self.assertEqual(result['raw_result_count'], 3)
        self.assertEqual(result['chunk_recall'], 1)
        self.assertEqual(result['ndcg_at_k'], 1)

    def test_invalid_versions_have_zero_gain_and_remain_visible_as_violations(self):
        invalid = row('a'); invalid['eligible'] = False
        result = evaluate_ranking([invalid], {'a': 2}, {'doc'}, 1)
        self.assertEqual(result['chunk_recall'], 0)
        self.assertEqual(result['document_recall'], 0)
        self.assertEqual(result['ndcg_at_k'], 0)
        self.assertEqual(result['version_filter_violations'], ['a'])

    def test_all_supported_k_and_reject_invalid_k_or_grades(self):
        for k in (1, 3, 5): self.assertEqual(evaluate_ranking([], {}, set(), k)['k'], k)
        for k in (0, 2, True):
            with self.assertRaises(ValueError): evaluate_ranking([], {}, set(), k)
        with self.assertRaises(ValueError): evaluate_ranking([], {'a': -1}, set(), 3)


class R4DatasetTests(unittest.TestCase):
    def setUp(self):
        self.manifest, self.documents, self.cases = load_dataset()

    def mutated(self, filename, mutate):
        directory = tempfile.TemporaryDirectory(prefix='r4-dataset-')
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        for name in ('corpus.json', 'development.jsonl', 'holdout.jsonl'):
            shutil.copyfile(DIRECTORY / name, root / name)
        path = root / filename
        records = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line]
        mutate(records)
        path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records), encoding='utf-8')
        return root

    def test_all_legacy_cases_and_new_disjoint_holdout_are_preserved(self):
        self.assertEqual(len(self.documents), 12)
        dev = [c for c in self.cases if c['split'] == 'development']
        hold = [c for c in self.cases if c['split'] == 'holdout']
        self.assertEqual(len(dev), 30)
        self.assertEqual(len(hold), 8)
        self.assertFalse({canonical(c['question']) for c in dev} & {canonical(c['question']) for c in hold})
        self.assertTrue(all(c['annotation_status'] == 'pending_review' for c in self.cases))

    def test_evidence_requires_exact_source_span_not_filename_only(self):
        case = self.cases[0]; doc = self.documents[case['evidence'][0]['source']]
        irrelevant = chunk_record(doc, {'chunk_id': 1, 'text': '无关内容'})
        qrels, mapping, missing = map_evidence(case, [irrelevant])
        self.assertEqual(qrels, {})
        self.assertEqual(len(missing), 1)

    def test_same_source_wrong_version_cannot_satisfy_evidence(self):
        case = self.cases[0]; evidence = case['evidence'][0]
        doc = dict(self.documents[evidence['source']], document_key='wrong-version')
        chunk = chunk_record(doc, {'chunk_id': 1, 'text': evidence['text']})
        self.assertEqual(map_evidence(case, [chunk])[0], {})

    def test_full_span_maps_and_primary_grade_wins_when_same_chunk(self):
        case = next(c for c in self.cases if c['id'] == 'it_03')
        doc = self.documents[case['evidence'][0]['source']]
        chunk = chunk_record(doc, {'chunk_id': 1, 'text': doc['text']})
        qrels, mapping, missing = map_evidence(case, [chunk])
        self.assertEqual(qrels, {chunk['chunk_key']: 2})
        self.assertFalse(missing)
        self.assertEqual(len(mapping), 2)

    def test_tampered_evidence_is_rejected(self):
        directory = self.mutated('development.jsonl', lambda records: records[0]['evidence'][0].update(text='编造内容'))
        with self.assertRaises(ValueError): load_dataset(directory)

    def test_negative_offsets_are_rejected(self):
        directory = self.mutated('development.jsonl', lambda records: records[0]['evidence'][0].update(start=-1))
        with self.assertRaises(ValueError): load_dataset(directory)

    def test_duplicate_cross_split_question_is_rejected(self):
        question = self.cases[0]['question']
        directory = self.mutated('holdout.jsonl', lambda records: records[0].update(question=question))
        with self.assertRaises(ValueError): load_dataset(directory)

    def test_no_support_or_process_cases_have_no_invented_positive_gold(self):
        unsupported = [c for c in self.cases if c['task'] != 'answerable']
        self.assertEqual(len(unsupported), 12)
        self.assertTrue(all(not c['evidence'] for c in unsupported))

    def test_unmapped_gold_excludes_metrics_instead_of_shrinking_denominators(self):
        result = score_query(self.cases[0], [], [], 3)
        self.assertTrue(result['unmapped_evidence'])
        self.assertIsNone(result['scores']['chunk_recall'])

    def test_pending_annotations_never_count_as_human_confirmed(self):
        case = self.cases[0]; doc = self.documents[case['evidence'][0]['source']]
        chunk = chunk_record(doc, {'chunk_id': 1, 'text': doc['text']})
        sample = dict(id=case['id'], pipeline='keyword', split='development', top_k=3, task='answerable',
            human_confirmed=False, annotation_status='pending_review', failure_reasons=[], **score_query(case, [chunk], [chunk], 3))
        default = summarize([sample], False)['keyword']['development']['3']
        provisional = summarize([sample], True)['keyword']['development']['3']
        self.assertEqual(default['metrics']['chunk_recall']['evaluated_questions'], 0)
        self.assertEqual(provisional['metrics']['chunk_recall']['mean'], 1)
        self.assertEqual(provisional['human_confirmed_metrics']['chunk_recall']['evaluated_questions'], 0)


class R4VersionFilterTests(unittest.TestCase):
    def setUp(self):
        from tests import test_index_repository as fixture
        fixture.IndexRepositoryTests.setUp(self)

    def test_actual_keyword_path_excludes_candidate_history_disabled_and_deleting(self):
        from backend.db.index_repository import prepare_document_index
        from backend.db.version_repository import document_version, publish_document, disable_document
        from backend.db.repository import get_document_chunks
        from backend.rag.service import retrieve_keyword_chunks
        from backend.db.deletion_repository import prepare_deletion
        first = prepare_document_index('r4-test', 'p.md', ['员工差旅报销制度第一版'], retrieval_mode='keyword')
        identity = document_version(first.document_id)['logical_document_id']
        publish_document(first.document_id, 1)
        second = prepare_document_index('r4-test', 'p.md', ['员工差旅报销制度第二版'], retrieval_mode='keyword', logical_document_id=identity)
        self.assertEqual({c['document_id'] for c in retrieve_keyword_chunks('r4-test', '差旅报销', None, top_k=5)}, {first.document_id})
        publish_document(second.document_id, 2)
        self.assertEqual({c['document_id'] for c in retrieve_keyword_chunks('r4-test', '差旅报销', None, top_k=5)}, {second.document_id})
        disable_document(identity, 3)
        self.assertEqual(get_document_chunks('r4-test'), [])
        prepare_deletion(first.document_id, 'session_scoped', 1,
            {'collection_name': 'unused', 'vector_store_fingerprint': 'sha256:' + '0' * 64})
        self.assertEqual(retrieve_keyword_chunks('r4-test', '差旅报销', None, top_k=5), [])


if __name__ == '__main__':
    unittest.main()

"""Context budget, visible-evidence scoring and development-only report checks."""
import json
import math
import sqlite3
import unittest
from contextlib import closing

from eval.r4.context_experiments import (BUDGETS, CANDIDATE_COUNT, context_metrics,
    boundary_prefix, pack_context, run_experiments, summarize)
from eval.r4.dataset import DIRECTORY
from eval.r4.gold import load_gold, verify_files


def chunk(key='c', body='first evidence. later evidence.', headings=None):
    return dict(chunk_key=key, document_key='doc', source='doc.md', chunk_id=1,
        score=6, eligible=True, text=body, heading_context=headings or [])


def render(rows):
    return '[rules]\n'+'\n\n'.join('[reference '+str(i)+']\n'+r['text'] for i,r in enumerate(rows,1))


def evidence(id, text, role='primary', grade=2):
    return dict(id=id, text=text, document_key='doc', role=role, grade=grade)


class ContextExperimentTests(unittest.TestCase):
    def test_metadata_and_heading_overhead_count_towards_budget(self):
        candidate = chunk(body='A'*100, headings=['# Title'])
        result = pack_context([candidate],1,55,render)
        self.assertLessEqual(len(result['text']),55)
        self.assertEqual(result['characters'],len(result['text']))
        self.assertLess(result['visible_body_characters'],55)
        self.assertGreater(result['added_heading_characters'],0)

    def test_large_budget_keeps_complete_body_and_ranking(self):
        candidates = [chunk('one'),chunk('two')]
        result = pack_context(candidates,3,1000,render)
        self.assertEqual([r['chunk_key'] for r in result['rows']],['one','two'])
        self.assertTrue(all(not r['body_truncated'] for r in result['rows']))

    def test_first_overflow_stops_without_lower_rank_backfill(self):
        candidates = [chunk('long','A'*300),chunk('short','small')]
        result = pack_context(candidates,5,65,render)
        self.assertEqual([r['chunk_key'] for r in result['rows']],['long'])

    def test_boundary_cut_is_gold_blind_prefix(self):
        self.assertEqual(boundary_prefix('first。second。tail',10),'first。')
        self.assertEqual(boundary_prefix('abcdefghij',4),'abcd')

    def test_insufficient_budget_is_rejected(self):
        with self.assertRaises(ValueError): pack_context([],3,1,render)

    def test_truncated_evidence_cannot_keep_full_chunk_gain(self):
        original = chunk()
        case = dict(evidence=[evidence('e','later evidence.')])
        visible = dict(original,visible_body='first evidence.')
        result = context_metrics(case,[original],[visible],1)
        self.assertEqual(len(result['qrels']),1)
        self.assertEqual(result['scores']['chunk_recall'],0)
        self.assertEqual(result['scores']['ndcg_at_k'],0)
        self.assertFalse(result['primary_complete'])
        self.assertEqual(result['scores']['document_recall'],1)  # Identity is not support.

    def test_added_heading_never_gets_evidence_credit(self):
        original = chunk(body='actual title. body')
        case = dict(evidence=[evidence('e','actual title.')])
        visible = dict(original,text='actual title.\nbody',visible_body='body')
        self.assertEqual(context_metrics(case,[original],[visible],1)['scores']['chunk_recall'],0)

    def test_auxiliary_only_visibility_does_not_receive_primary_gain(self):
        original = chunk(body='primary text. auxiliary text.')
        case = dict(evidence=[evidence('p','primary text.'),evidence('a','auxiliary text.','auxiliary',1)])
        visible = dict(original,visible_body='auxiliary text.')
        result = context_metrics(case,[original],[visible],1)
        self.assertEqual(result['scores']['ranked_grades'],[1])
        self.assertAlmostEqual(result['scores']['ndcg_at_k'],1/3)
        self.assertFalse(result['primary_complete'])
        self.assertFalse(result['multi_evidence_complete'])

    def test_fixed_denominator_includes_evidence_in_unreturned_chunks(self):
        first,second = chunk('one','first'),chunk('two','second')
        case = dict(evidence=[evidence('e1','first'),evidence('e2','second')])
        result = context_metrics(case,[first,second],[dict(first,visible_body='first')],3)
        self.assertEqual(result['scores']['chunk_recall'],.5)
        self.assertFalse(result['multi_evidence_complete'])
        self.assertAlmostEqual(result['scores']['ndcg_at_k'],3/(3+3/math.log2(3)))

    def test_holdout_is_rejected_before_any_execution(self):
        with closing(sqlite3.connect(':memory:')) as connection:
            with self.assertRaisesRegex(ValueError,'Holdout'):
                run_experiments({},[dict(split='holdout')],render,connection)

    def test_empty_results_and_no_positive_gold_are_not_quality_success(self):
        context = pack_context([],3,700,render)
        result = context_metrics(dict(evidence=[]),[],context['rows'],3)
        self.assertIsNone(result['scores']['chunk_recall'])
        self.assertIsNone(result['primary_complete'])
        self.assertEqual(context['visible_body_characters'],0)

    def test_all_report_rows_and_summaries_are_reproducible(self):
        _,_,cases = load_gold()
        byid = {c['id']:c for c in cases if c['split']=='development'}
        report = json.loads((DIRECTORY/'context_experiments_gold_v1.json').read_text(encoding='utf-8'))
        self.assertFalse(report['holdout_evaluated'])
        self.assertEqual(report['candidate_count'],CANDIDATE_COUNT)
        self.assertEqual(report['budgets'],list(BUDGETS))
        self.assertEqual(sum(len(e['results']) for e in report['experiments']),1080)
        for experiment in report['experiments']:
            self.assertEqual(set(experiment['candidates_by_case']),set(byid))
            for row in experiment['results']:
                candidates = experiment['candidates_by_case'][row['id']]
                self.assertEqual(row['candidate_chunk_keys'],[c['chunk_key'] for c in candidates])
                self.assertLessEqual(len(candidates),CANDIDATE_COUNT)
                self.assertEqual(row['context']['characters'],len(row['context']['text']))
                self.assertLessEqual(row['context']['characters'],row['budget'])
                expected = context_metrics(byid[row['id']],experiment['chunk_catalog'],row['context']['rows'],row['top_k'])
                for key,value in expected.items(): self.assertEqual(row[key],value)
                for retained in row['context']['rows']:
                    self.assertIn(retained['text'],row['context']['text'])
                    original = next(c for c in candidates if c['chunk_key']==retained['chunk_key'])
                    self.assertTrue(original['text'].startswith(retained['visible_body']))
            self.assertEqual(experiment['summary'],summarize(experiment['results']))
        verify_files(report['file_sha256'])
        frozen = json.loads((DIRECTORY/'gold_v1_checksums.json').read_text(encoding='utf-8'))
        verify_files(frozen['files_sha256'])
        self.assertEqual(report['safety']['model_or_chroma_attempts'],0)
        self.assertEqual(report['safety']['non_test_sqlite_attempts'],0)


if __name__ == '__main__':
    unittest.main()

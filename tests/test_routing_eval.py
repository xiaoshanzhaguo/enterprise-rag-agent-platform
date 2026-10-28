"""路由报告只比较同模型、同问题与同参数，失败不冒充模型正确结果。"""
import unittest
import json
from pathlib import Path
from eval.run_routing_eval import compare, digest, load_cases, summarize


class RoutingEvalTests(unittest.TestCase):
    def test_security_reports_keep_original_inputs_and_frozen_extra_cases(self):
        root = Path(__file__).resolve().parents[1]/'eval'
        cases = load_cases(root/'security_routing_cases.jsonl')
        self.assertEqual(len(cases), 46)
        self.assertEqual(cases[:38], load_cases())
        reports = [json.loads((root/name).read_text(encoding='utf-8')) for name in ('security_routing_before_report.json', 'security_routing_after_report.json')]
        for report in reports:
            self.assertEqual(report['cases_sha256'], digest(cases))
            for case, item in zip(cases, report['results'], strict=True):
                self.assertEqual({key: item[key] for key in case}, case)
                self.assertEqual(item['correct'], item['actual_route'] == case['expected_route'])
            self.assertEqual(report['summary'], summarize(report['results']))
        self.assertEqual(reports[1]['changes'], compare(*reports))
        self.assertNotEqual(reports[0]['prompt_sha256'], reports[1]['prompt_sha256'])

    def test_committed_reports_match_frozen_inputs_and_computed_results(self):
        root = Path(__file__).resolve().parents[1]/'eval'
        reports = [json.loads((root/name).read_text(encoding='utf-8')) for name in ('routing_before_report.json', 'routing_after_report.json')]
        cases = load_cases()
        for report in reports:
            self.assertEqual(report['cases_sha256'], digest(cases))
            self.assertEqual([item['id'] for item in report['results']], [case['id'] for case in cases])
            for case, item in zip(cases, report['results']):
                self.assertEqual({key: item[key] for key in case}, case)
                self.assertEqual(item['correct'], item['actual_route'] == item['expected_route'])
            self.assertEqual(report['summary'], summarize(report['results']))
        self.assertEqual(reports[1]['changes'], compare(*reports))
        self.assertNotEqual(reports[0]['prompt_sha256'], reports[1]['prompt_sha256'])

    def test_frozen_cases_and_holdout_groups(self):
        cases = load_cases()
        self.assertEqual(len(cases), 38)
        self.assertEqual(sum(case['group'] == 'original' for case in cases), 30)
        self.assertEqual(len({case['id'] for case in cases}), 38)
        self.assertEqual(digest(cases), digest(load_cases()))

    def test_metrics_count_failure_and_process_false_positive(self):
        items = [dict(group='original', expected_route='product_doc', actual_route=None, correct=False),
                 dict(group='holdout', expected_route='it_support', actual_route='process_apply', correct=False),
                 dict(group='original', expected_route='product_doc', actual_route='product_doc', correct=True)]
        result = summarize(items)
        self.assertEqual(result['all'], dict(total=3, correct=1, model_errors=1, process_intent_correct=1))

    def test_comparison_rejects_incompatible_and_tracks_regressions(self):
        common = dict(cases_sha256='same', model='test', temperature=0, scope='production_model_routing_only')
        before = dict(**common, results=[dict(id='a', actual_route='knowledge_qa', correct=False), dict(id='b', actual_route='it_support', correct=True)])
        after = dict(**common, results=[dict(id='a', actual_route='product_doc', expected_route='product_doc', correct=True),
                                      dict(id='b', actual_route='knowledge_qa', expected_route='it_support', correct=False)])
        self.assertEqual([item['change'] for item in compare(before, after)], ['improved', 'regressed'])
        for key, value in [('cases_sha256', 'different'), ('model', 'another'), ('temperature', 1), ('scope', 'rules')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                compare(before, dict(after, **{key: value}))


if __name__ == '__main__':
    unittest.main()

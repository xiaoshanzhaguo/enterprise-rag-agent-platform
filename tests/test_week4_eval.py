"""验证指标不会把上下文、假引用、未运行结果或运行错误计为成功。"""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from eval.week4_metrics import answer_hash, score_case, summarize
from eval.run_week4 import load_dataset


class Week4EvalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases, cls.docs = load_dataset()

    def result(self, case, **updates):
        result = {'id': case['id'], 'mode': 'live', 'route': case['expected_route'],
                  'answer': '', 'chunks': []}
        result.update(updates)
        return result

    def test_real_corpus_and_coverage(self):
        from collections import Counter
        self.assertEqual(len(self.docs), 12)
        self.assertEqual(set(Counter(c['category'] for c in self.cases).values()), {5})
        self.assertEqual(sum(c['dify_comparable'] for c in self.cases), 14)

    def test_context_is_not_answer_or_citation(self):
        c = self.cases[0]
        result = self.result(c, chunks=[{'file_name': 'leave_policy.md', 'chunk_id': 2,
                                        'text': c['reference_answer']}])
        score = score_case(c, result)
        self.assertTrue(score['retrieval_hit'])
        self.assertFalse(score['citation_hit'])
        self.assertEqual(score['keyword_hits'], [False, False])

    def test_citation_must_be_actual_chunk_and_expected_file(self):
        c = self.cases[0]
        chunks = [{'file_name': 'leave_policy.md', 'chunk_id': 2}]
        for citation, expected in [('leave_policy.md#chunk-99', False),
                                   ('other.md#chunk-2', False), ('leave_policy.md#chunk-2', True)]:
            with self.subTest(citation=citation):
                self.assertEqual(score_case(c, self.result(c, chunks=chunks,
                    answer=f'年假需提前3个工作日在HR系统提交。[来源: {citation}]'))['citation_hit'], expected)

    def test_keyword_aliases_and_whitespace(self):
        c = self.cases[0]
        score = score_case(c, self.result(c, answer='提前三个工作日，在 HR 系统提交。'))
        self.assertEqual(score['keyword_hits'], [True, True])
        c = copy.deepcopy(c)
        c['keyword_groups'] = [['leave_policy.md']]
        self.assertEqual(score_case(c, self.result(c, answer='[来源: leave_policy.md#chunk-2]'))['keyword_hits'], [False])

    def test_refusal_needs_review_bound_to_exact_answer(self):
        c = next(c for c in self.cases if c['should_reject'])
        answer = '知识库中没有找到依据，但奖金是100万元。'
        r = self.result(c, answer=answer)
        self.assertTrue(score_case(c, r)['refusal_marker'])
        self.assertIsNone(score_case(c, r)['refusal_correct'])
        review = {'answer_sha256': answer_hash(answer), 'correct': False, 'reason': '仍编造金额'}
        self.assertFalse(score_case(c, r, review)['refusal_correct'])
        with self.assertRaises(ValueError):
            score_case(c, self.result(c, answer='答案已改变'), review)

    def test_process_null_source_is_not_refusal_or_retrieval_failure(self):
        c = next(c for c in self.cases if c['id'] == 'process_03')
        r = self.result(c, extraction={'status': 'extracted', 'fields': c['expected_fields']})
        s = score_case(c, r)
        self.assertIsNone(s['retrieval_hit'])
        self.assertIsNone(s['refusal_correct'])
        self.assertTrue(all(s['field_results'].values()))
        r['extraction']['fields'] = {**c['expected_fields'], 'department': '研发部'}
        self.assertFalse(score_case(c, r)['field_results']['department'])

    def test_denominators_errors_and_offline_na(self):
        results = [self.result(c, mode='offline', answer=None) for c in self.cases]
        summary = summarize(self.cases, results)
        self.assertEqual(summary['metrics']['retrieval_hit']['total'], 20)
        self.assertEqual(summary['metrics']['citation_hit']['total'], 0)
        self.assertIsNone(summary['metrics']['keyword_coverage']['rate'])
        self.assertIsNone(summary['metrics']['refusal_correct']['rate'])
        self.assertEqual(summary['metrics']['process_intent']['tp'], 5)
        results[0]['route'] = 'process_apply'
        results[1]['error'] = 'network_error'
        summary = summarize(self.cases, results)
        self.assertEqual(summary['metrics']['process_intent']['fp'], 1)
        self.assertEqual(summary['metrics']['process_intent']['passed'], 28)
        self.assertEqual(summary['errors'], 1)
        with self.assertRaises(ValueError):
            summarize(self.cases, results[:-1])

    def test_corpus_change_is_rejected_before_running(self):
        import eval.run_week4 as runner
        manifest = json.loads(runner.CORPUS.read_text(encoding='utf-8'))
        manifest['documents'][0]['sha256'] = '0' * 64
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'corpus.json'
            path.write_text(json.dumps(manifest), encoding='utf-8')
            with patch.object(runner, 'CORPUS', path), self.assertRaisesRegex(ValueError, '语料已变更'):
                runner.load_dataset()

    def test_dataset_fingerprint_survives_git_newline_conversion(self):
        from eval.run_week4 import fingerprint
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'cases.jsonl'
            path.write_bytes(b'{"id":"a"}\n{"id":"b"}\n')
            before = fingerprint(path)
            path.write_bytes(b'{"id":"a"}\r\n{"id":"b"}\r\n')
            self.assertEqual(before, fingerprint(path))

    def test_live_adapter_consumes_production_sse_and_never_submits(self):
        import asyncio
        from fastapi.responses import StreamingResponse
        from eval.run_week4 import live_case
        events = [
            {'event_type': 'step_start', 'step_name': 'judge_knowledge'},
            {'event_type': 'step_complete', 'step_name': 'judge_knowledge', 'content': '路由类型：流程申请 process_apply'},
            {'event_type': 'final', 'content': json.dumps({'generate_answer': 'actual output'}),
             'metadata': {'process_extraction': {'fields': {}, 'status': 'extracted'}}},
        ]
        response = StreamingResponse(iter('data: '+json.dumps(e)+'\n\n' for e in events))
        with patch('backend.db.repository.ensure_chat_session'), \
             patch('backend.services.agent_service.run_agent_stream', return_value=response), \
             patch('backend.services.process_submission.submit_process_application') as submit:
            result = asyncio.run(live_case(self.cases[20], object(), 'eval-session', 3))
        self.assertEqual(result['route'], 'process_apply')
        self.assertEqual(result['answer'], 'actual output')
        self.assertEqual(result['trace'][0]['stage'], 'judge_knowledge')
        submit.assert_not_called()

    def test_partial_sse_is_error(self):
        import asyncio
        from fastapi.responses import StreamingResponse
        from eval.run_week4 import live_case
        response = StreamingResponse(iter(['data: {"event_type":"error","error_message":"private detail"}\n\n']))
        with patch('backend.db.repository.ensure_chat_session'), \
             patch('backend.services.agent_service.run_agent_stream', return_value=response):
            result = asyncio.run(live_case(self.cases[0], object(), 'eval-session', 3))
        self.assertEqual(result['error'], 'agent_error')
        self.assertNotIn('private detail', json.dumps(result))


if __name__ == '__main__':
    unittest.main()

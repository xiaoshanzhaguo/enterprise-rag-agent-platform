"""R4-02 mapping, development isolation and report invariants."""
import json
import unittest

from backend.rag.chunker import split_text_into_chunks
from eval.r4.chunk_experiments import (CONFIGURATIONS, build_chunks, heading_prefix,
    paragraph_pack, run_configuration, selection_key)
from eval.r4.dataset import DIRECTORY, canonical, map_evidence, sha256
from eval.r4.gold import load_gold, verify_files


class ChunkExperimentTests(unittest.TestCase):
    def test_production_overlap_is_exercised_on_long_plain_text(self):
        source = '\n\n'.join(['A' * 35, 'B' * 35, 'C' * 35])
        zero = split_text_into_chunks(source, 60, 0)
        overlapping = split_text_into_chunks(source, 60, 10)
        self.assertNotEqual(zero, overlapping)
        self.assertTrue(overlapping[1].startswith('A' * 10))

    def test_section_boundary_clears_overlap(self):
        chunks = split_text_into_chunks('# First\n\nalpha\n\n## Second\n\nbeta', 360, 40)
        self.assertEqual(len(chunks), 2)
        self.assertNotIn('alpha', chunks[1])

    def test_paragraph_packing_preserves_source_order(self):
        source = '# Title\n\nfirst sentence\n\n## Section\n\nsecond sentence'
        chunks = paragraph_pack(source, 30)
        self.assertEqual(canonical(''.join(chunks)), canonical(source))
        self.assertTrue(all(len(c) <= 30 for c in chunks))

    def test_heading_context_is_from_correct_ancestor_not_previous_section(self):
        source = '# Title\n\nintro\n\n## First\n\nalpha\n\n## Second\n\nbeta'
        self.assertEqual(heading_prefix(source, 'beta'), ['# Title', '## Second'])
        self.assertEqual(heading_prefix(source, '## Second\n\nbeta'), ['# Title'])

    def test_synthetic_heading_cannot_create_scoring_evidence(self):
        source = '# Original Title\n\nintro\n\n## Section\n\nbody'
        doc = dict(text=source, source='test.md', document_key='test::v1', version_number=1)
        config = dict(family='heading_prefix', size=360, overlap=40)
        chunks = build_chunks({'test.md': doc}, config)
        last = chunks[-1]
        self.assertIn('# Original Title', last['retrieval_text'])
        self.assertNotIn('# Original Title', last['text'])
        case = dict(evidence=[dict(id='e', text='# Original Title', document_key='test::v1', grade=2)])
        self.assertEqual(map_evidence(case, [last])[2], ['e'])

    def test_unmappable_span_blocks_entire_configuration(self):
        source = 'abcdefghij'
        doc = dict(text=source, source='test.md', document_key='test::v1', version_number=1)
        case = dict(id='c', split='development', evidence=[dict(id='e', text='defg', document_key='test::v1', grade=2)])
        result = run_configuration(dict(family='production', size=5, overlap=0), {'test.md': doc}, [case])
        self.assertEqual(result['status'], 'blocked_evidence_mapping')
        self.assertIsNone(result['summary'])
        self.assertEqual(result['unmapped_evidence'], {'c': ['e']})

    def test_holdout_cannot_be_evaluated_by_experiment(self):
        with self.assertRaisesRegex(ValueError, 'Holdout'):
            run_configuration({}, {}, [dict(split='holdout')])

    def test_report_recomputation_and_frozen_files_unchanged(self):
        _, _, cases = load_gold()
        checksums = json.loads((DIRECTORY / 'gold_v1_checksums.json').read_text(encoding='utf-8'))
        verify_files(checksums['files_sha256'])
        report = json.loads((DIRECTORY / 'chunk_experiments_gold_v1.json').read_text(encoding='utf-8'))
        self.assertFalse(report['holdout_evaluated'])
        self.assertEqual(report['configuration_grid_predeclared'], CONFIGURATIONS)
        self.assertEqual(len(report['experiments']), 13)
        dev_ids = {c['id'] for c in cases if c['split'] == 'development'}
        viable = [e for e in report['experiments'] if e['status'] == 'evaluated']
        self.assertEqual(max(viable, key=selection_key)['config']['id'], report['selected_configuration'])
        from eval.r4.run_baseline import score_query
        byid = {c['id']: c for c in cases if c['split'] == 'development'}
        for experiment in viable:
            self.assertEqual({r['id'] for r in experiment['results']}, dev_ids)
            self.assertEqual(len(experiment['results']), 90)
            for row in experiment['results']:
                expected = score_query(byid[row['id']], experiment['chunk_catalog'], row['results'], row['top_k'])
                for key, value in expected.items():
                    self.assertEqual(row[key], value)
                self.assertFalse(row['unmapped_evidence'])
        verify_files(report['file_sha256'])


if __name__ == '__main__':
    unittest.main()

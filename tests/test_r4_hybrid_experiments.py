"""Offline unit tests only; never invoke live prepare/evaluate entrypoints."""
import array
import base64
import math
import unittest
import json
import copy

from eval.r4.hybrid_experiments import (decode_wire_embedding, fuse_rrf, validate_vectors,
    validate_cache, frozen_inputs, summarize)
from eval.r4.dataset import DIRECTORY,sha256
from eval.r4.gold import verify_files
from eval.r4.context_experiments import context_metrics
from eval.r4.run_baseline import score_query


def row(key,score=1):
    return dict(chunk_key=key,score=score)


class HybridExperimentTests(unittest.TestCase):
    def test_base64_length_is_not_vector_dimension(self):
        encoded=base64.b64encode(array.array('f',[.25]*1024).tobytes()).decode('ascii')
        self.assertEqual(len(encoded),5464)
        vector=decode_wire_embedding(encoded)
        self.assertEqual(len(vector),1024)
        validate_vectors([vector],1)

    def test_float_wire_format_supported(self):
        vector=[.25]*1024
        self.assertEqual(decode_wire_embedding(vector),vector)

    def test_bad_base64_or_non_float32_bytes_rejected(self):
        for value in ('not base64!',base64.b64encode(b'abc').decode('ascii')):
            with self.assertRaises(ValueError): decode_wire_embedding(value)

    def test_invalid_dimension_values_and_zero_norm_rejected(self):
        for vector in ([.25]*3,[0.0]*1024,[math.nan]*1024,[math.inf]*1024,[True]*1024):
            with self.assertRaises(ValueError): validate_vectors([vector],1)
        with self.assertRaises(ValueError): validate_vectors([],1)

    def test_rrf_uses_ranks_not_score_scales(self):
        first=fuse_rrf([row('a',10000),row('b',.01)],[row('b',.99),row('c',.98)])
        second=fuse_rrf([row('a',1),row('b',10000)],[row('b',.01),row('c',999)])
        self.assertEqual([r['chunk_key'] for r in first],[r['chunk_key'] for r in second])
        self.assertEqual(first[0]['chunk_key'],'b')
        self.assertAlmostEqual(first[0]['rrf_score'],1/62+1/61)
        self.assertEqual((first[0]['keyword_rank'],first[0]['vector_rank']),(2,1))

    def test_rrf_duplicates_do_not_add_extra_votes(self):
        ranked=fuse_rrf([row('a'),row('a'),row('b')],[])
        self.assertEqual(len(ranked),2)
        self.assertAlmostEqual(ranked[0]['rrf_score'],1/61)
        self.assertEqual(ranked[1]['keyword_rank'],2)

    def test_rrf_candidate_limit_and_parameter_validation(self):
        self.assertEqual(len(fuse_rrf([row(str(i)) for i in range(10)],[],limit=5)),5)
        with self.assertRaises(ValueError): fuse_rrf([],[],constant=0)

    def test_partial_cache_is_rejected_before_any_evaluation(self):
        with self.assertRaisesRegex(ValueError,'Incomplete'):
            validate_cache(dict(status='failed'),dict(status='partial'),[],[])


class HybridArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest=json.loads((DIRECTORY/'embedding_cache_manifest.json').read_text(encoding='utf-8'))
        cls.cache=json.loads((DIRECTORY/'embedding_cache.json').read_text(encoding='utf-8'))
        cls.vector=json.loads((DIRECTORY/'vector_baseline_gold_v1.json').read_text(encoding='utf-8'))
        cls.hybrid=json.loads((DIRECTORY/'hybrid_rrf_gold_v1.json').read_text(encoding='utf-8'))
        _,cls.cases,cls.catalog=frozen_inputs()

    def test_complete_real_cache_and_all_mappings(self):
        validate_cache(self.manifest,self.cache,self.catalog,self.cases)
        self.assertEqual(len(self.cache['inputs']),109)
        self.assertEqual(len(self.manifest['chunk_bindings']),79)
        self.assertEqual(len(self.manifest['query_bindings']),30)
        self.assertTrue(self.manifest['dimension_confirmed_by_response'])
        self.assertFalse(self.manifest['holdout_embedded'])

    def test_existing_ten_vectors_preserved_and_not_requested_again(self):
        run=self.manifest['continuation_runs'][-1]
        self.assertEqual(run['preserved_input_count'],10)
        for key,digest in run['preserved_records_sha256'].items():
            self.assertEqual(sha256(json.dumps(self.cache['inputs'][key],sort_keys=True,separators=(',',':'))),digest)
        requests=self.manifest['requests']
        self.assertEqual(len(requests),12)  # Includes the original local-validation interruption.
        self.assertEqual(run['new_http_attempts'],10)
        self.assertEqual(self.manifest['sdk_max_retries'],0)
        new_keys=[key for request in requests[2:] for key in request['input_sha256']]
        self.assertEqual(len(new_keys),99)
        self.assertEqual(len(set(new_keys)),99)
        self.assertFalse(set(new_keys)&set(run['preserved_records_sha256']))
        self.assertTrue(all(r['status']=='validated' and r['response_model']==self.manifest['profile']['model'] for r in requests[1:]))

    def test_all_metrics_and_summaries_recompute(self):
        byid={c['id']:c for c in self.cases}
        pipelines=list(self.vector['pipelines'].values())+[self.hybrid['pipeline']]
        for pipeline in pipelines:
            self.assertEqual(len(pipeline['results']),90)
            self.assertEqual(set(pipeline['rankings']),set(byid))
            for row in pipeline['results']:
                case=byid[row['id']]
                self.assertEqual(case['split'],'development')
                self.assertLessEqual(row['context']['characters'],700)
                self.assertEqual(row['context']['characters'],len(row['context']['text']))
                expected=context_metrics(case,self.vector['chunk_catalog'],row['context']['rows'],row['top_k'])
                for key,value in expected.items():self.assertEqual(row[key],value)
                self.assertEqual(row['retrieval'],score_query(case,self.vector['chunk_catalog'],pipeline['rankings'][row['id']][:row['top_k']],row['top_k']))
            self.assertEqual(pipeline['summary'],summarize(pipeline['results']))

    def test_rrf_rankings_exactly_recompute_from_recorded_real_legs(self):
        for case in self.cases:
            id=case['id'];legs=self.vector['pipelines']
            expected=fuse_rrf(legs['keyword']['rankings'][id],legs['vector']['rankings'][id])
            self.assertEqual(expected,self.hybrid['pipeline']['rankings'][id])
            self.assertEqual(len({r['chunk_key'] for r in expected}),len(expected))
        self.assertEqual(self.hybrid['rrf']['constant'],60)
        self.assertFalse(self.hybrid['rrf']['tuned'])

    def test_recorded_real_cosine_distances_agree_with_cached_vectors(self):
        for case in self.cases:
            query=self.cache['inputs'][sha256(case['question'])]['embedding']
            for row in self.vector['pipelines']['vector']['rankings'][case['id']]:
                document=self.cache['inputs'][sha256(row['text'])]['embedding']
                cosine=sum(a*b for a,b in zip(query,document))/math.sqrt(sum(a*a for a in query)*sum(b*b for b in document))
                self.assertAlmostEqual(row['distance'],1-cosine,delta=2e-5)
                self.assertEqual(row['score'],round(1/(1+row['distance']),4))

    def test_keyword_control_matches_frozen_ranking(self):
        baseline=json.loads((DIRECTORY/'baseline_gold_v1_keyword.json').read_text(encoding='utf-8'))
        for case in self.cases:
            expected=next(r for r in baseline['results'] if r['id']==case['id'] and r['pipeline']=='keyword' and r['top_k']==5)
            actual=self.vector['pipelines']['keyword']['rankings'][case['id']]
            self.assertEqual([(r['chunk_key'],r['score']) for r in actual],[(r['chunk_key'],r['score']) for r in expected['results']])

    def test_isolation_profile_and_report_hashes(self):
        for report in (self.vector,self.hybrid):
            self.assertFalse(report['holdout_evaluated'])
            self.assertFalse(report['query_rewrite'])
            self.assertIsNone(report['category_filter'])
            self.assertEqual(report['storage']['distance_metric'],'cosine')
            self.assertEqual(report['storage']['dimension'],1024)
            self.assertEqual(report['storage']['vector_count'],79)
            self.assertEqual(report['safety']['external_or_local_network_attempts'],0)
            self.assertEqual(report['safety']['non_test_sqlite_attempts'],0)
            self.assertEqual(report['safety']['blocked_embedding_attempts'],0)
            self.assertEqual(report['safety']['real_embedding_calls_during_evaluation'],0)
            self.assertEqual(report['cache_manifest_sha256'],sha256((DIRECTORY/'embedding_cache_manifest.json').read_bytes()))
            verify_files(report['source_sha256'])
        self.assertEqual(self.hybrid['vector_report_sha256'],sha256((DIRECTORY/'vector_baseline_gold_v1.json').read_bytes()))

    def test_changed_profile_or_chunk_version_is_rejected(self):
        manifest=copy.deepcopy(self.manifest)
        manifest['profile']['model']='different-model'
        with self.assertRaises(ValueError):validate_cache(manifest,self.cache,self.catalog,self.cases)
        manifest=copy.deepcopy(self.manifest)
        manifest['chunk_bindings'][0]['version_number']=2
        with self.assertRaises(ValueError):validate_cache(manifest,self.cache,self.catalog,self.cases)

    def test_non_finite_cache_and_original_response_digest(self):
        cache=copy.deepcopy(self.cache)
        next(iter(cache['inputs'].values()))['embedding'][0]=math.nan
        with self.assertRaises(ValueError):validate_cache(self.manifest,cache,self.catalog,self.cases)
        recovery=self.manifest['recovery_runs'][0]
        self.assertEqual(recovery['raw_response_sha256'],sha256((DIRECTORY/recovery['raw_response_file']).read_bytes()))


if __name__=='__main__':
    unittest.main()

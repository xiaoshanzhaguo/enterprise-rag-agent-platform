"""Real current Keyword baseline in disposable SQLite; no model, vector client or business DB access.

python -m eval.r4.run_baseline --split all --include-pending --output eval/r4/baseline_7677788_keyword.json
"""
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import gc
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

from eval.r4.dataset import ROOT, DIRECTORY, load_dataset, chunk_record, map_evidence, sha256
from eval.r4.metrics import evaluate_ranking, macro_average, unique_results


def git(*args):
    return subprocess.check_output(['git', '-c', 'safe.directory=' + ROOT.as_posix(), *args], cwd=ROOT, text=True).strip()


def read_production_configuration():
    """Read only non-secret retrieval flags. Never load .env credentials into this evaluator."""
    values = {'RAG_RETRIEVAL_MODE': 'keyword', 'RAG_KEYWORD_FALLBACK_ENABLED': 'true', 'RAG_VECTOR_SCORE_THRESHOLD': '0.6'}
    origins = dict.fromkeys(values, 'code_default')
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            match = re.match(r'^\s*(RAG_RETRIEVAL_MODE|RAG_KEYWORD_FALLBACK_ENABLED|RAG_VECTOR_SCORE_THRESHOLD)\s*=\s*(.*?)\s*$', line)
            if match:
                key, value = match.groups()
                values[key] = value.split(' #', 1)[0].strip().strip('"\'')
                origins[key] = '.env_literal'
    for key in values:
        if key in os.environ:
            values[key] = os.environ[key]
            origins[key] = 'inherited_environment'
    return {'values': values, 'origins': origins, 'running_service_configuration_verified': False}


def configure_isolation(directory):
    if 'backend.config' in sys.modules:
        raise RuntimeError('Run baseline in a fresh process; business settings must not already be imported')
    import dotenv
    dotenv.load_dotenv = lambda *a, **kw: False
    os.environ['DATABASE_URL'] = 'sqlite:///' + (directory / 'evaluation.db').as_posix()
    os.environ['VECTOR_STORE_DIR'] = str(directory / 'unused-vector-store')
    os.environ['RAG_RETRIEVAL_MODE'] = 'keyword'
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['LANGSMITH_TRACING'] = 'false'
    os.environ['LANGCHAIN_TRACING_V2'] = 'false'
    counts = {'external_or_local_network_attempts': 0, 'non_test_sqlite_attempts': 0, 'model_or_chroma_attempts': 0}
    def audit(event, args):
        if event == 'socket.connect':
            counts['external_or_local_network_attempts'] += 1
            raise RuntimeError('Network disabled for R4-01')
        if event == 'sqlite3.connect' and str(args[0]) != ':memory:' and not Path(args[0]).resolve().is_relative_to(directory):
            counts['non_test_sqlite_attempts'] += 1
            raise RuntimeError('Only the isolated evaluation SQLite is allowed')
    forbidden = {'generate_embedding', 'generate_embeddings', 'generate_local_embeddings', 'generate_remote_embeddings',
                 'get_local_embedding_model', 'get_remote_embedding_client', 'get_chroma_client', 'get_chroma_collection'}
    def guard(frame, event, _arg):
        if event != 'call':
            return
        file = frame.f_code.co_filename.replace('\\', '/')
        if ('backend/rag/' in file and frame.f_code.co_name in forbidden) or ('chromadb' in file and frame.f_code.co_name == 'PersistentClient'):
            counts['model_or_chroma_attempts'] += 1
            raise RuntimeError('Embedding/Chroma is disabled, not faked')
    sys.addaudithook(audit)
    sys.setprofile(guard)
    return counts


def prepare_corpus(documents):
    from backend.db.init_db import init_database
    from backend.schema.chat_schema import IndexDocumentRequest
    from backend.services.rag_documents import IndexManagementService
    from backend.db.repository import get_document_chunks, get_active_document_ids
    init_database()
    service = IndexManagementService(mode='keyword')
    by_id = {}
    for doc in documents.values():
        receipt = service.upload(IndexDocumentRequest(session_id='r4-upload', knowledge_base_id='r4_offline',
            user_role='kb_admin', file_name=doc['filename'], document_text=doc['text'],
            knowledge_base_type=doc['category'])).receipt
        if not receipt['is_current_version'] or receipt['version_number'] != doc['version_number']:
            raise RuntimeError('Corpus was not published through the actual R3 keyword path')
        by_id[receipt['document_id']] = doc
    chunks = get_document_chunks(None, 'r4_offline')
    active = set(get_active_document_ids(None, 'r4_offline'))
    return [chunk_record(by_id[c['document_id']], c, eligible=c['document_id'] in active) for c in chunks], by_id


def reviewed(case):
    return case['annotation_status'] == 'confirmed' and all(e['annotation_status'] == 'confirmed' for e in case['evidence'])


def score_query(case, chunks, results, k):
    qrels, mapping, unmapped = map_evidence(case, chunks)
    docs = {e['document_key'] for e in case['evidence']}
    score = evaluate_ranking(results, qrels, docs, k)
    retrieved = {row['chunk_key'] for row in unique_results(results)[:k] if row['eligible']}
    missing = [e['id'] for e in case['evidence'] if not (set(mapping[e['id']]) & retrieved)]
    if unmapped:
        # Do not silently remove unrepresented evidence from metric denominators.
        for metric in ('document_recall', 'chunk_recall', 'mrr_at_k', 'ndcg_at_k'):
            score[metric] = None
    return {'scores': score, 'qrels': qrels, 'evidence_to_chunk_keys': mapping,
            'unmapped_evidence': unmapped, 'missing_evidence_ids': missing,
            'missing_primary_evidence': [e['id'] for e in case['evidence'] if e['role'] == 'primary' and e['id'] in missing],
            'missing_auxiliary_evidence': [e['id'] for e in case['evidence'] if e['role'] == 'auxiliary' and e['id'] in missing]}


def run_keyword(cases, chunks, by_id):
    from backend.rag.service import retrieve_keyword_chunks, retrieve_rag_chunks_with_mode
    from backend.rag.retriever import retrieve_top_chunks
    from backend.db.repository import filter_active_chunks
    results = []
    for case in cases:
        query = case['question'].strip()
        # Same production ranking function with the entire corpus as K enumerates post-threshold candidates.
        candidates = retrieve_top_chunks(query, chunks, top_k=len(chunks))
        for path in ('keyword', 'production_keyword_control'):
            for k in (1, 3, 5):
                started = time.perf_counter()
                if path == 'keyword':
                    found = retrieve_keyword_chunks(None, query, None, 'r4_offline', k)
                    mode = 'keyword' if found else 'no_hit'
                else:
                    found, mode = retrieve_rag_chunks_with_mode(None, query, None, 'r4_offline', k)
                valid_ids = {c['db_chunk_id'] for c in filter_active_chunks(found)}
                ranked = [chunk_record(by_id[c['document_id']], c, eligible=c['db_chunk_id'] in valid_ids) for c in found]
                scored = score_query(case, chunks, ranked, k)
                results.append({'id': case['id'], 'split': case['split'], 'task': case['task'],
                    'question': case['question'], 'annotation_status': case['annotation_status'], 'human_confirmed': reviewed(case),
                    'pipeline': path, 'query': query, 'query_rewrite': 'disabled_no_model_calls',
                    'category_filter': None, 'top_k': k, 'effective_retrieval_mode': mode,
                    'scoped_current_chunk_count': len(chunks), 'post_threshold_candidate_count': len(candidates),
                    'candidate_count_method': 'production retrieve_top_chunks with K=all current chunks; not ANN internal candidate count',
                    'final_result_count': len(ranked), 'current_version_validation': 'SQLite whitelist and post-read validity check',
                    'duration_ms_audited_not_performance_baseline': round((time.perf_counter() - started) * 1000, 3),
                    'evidence': case['evidence'], 'results': ranked, **scored,
                    'failure_reasons': (['unmapped_evidence'] if scored['unmapped_evidence'] else [])
                        + (['missing_primary_evidence'] if scored['missing_primary_evidence'] else [])
                        + (['version_filter_violation'] if scored['scores']['version_filter_violations'] else [])})
    return results


def summarize(results, include_pending):
    summary = {}
    for path in ('keyword', 'production_keyword_control'):
        summary[path] = {}
        for split in ('development', 'holdout'):
            summary[path][split] = {}
            for k in (1, 3, 5):
                group = [r for r in results if r['pipeline'] == path and r['split'] == split and r['top_k'] == k]
                eligible = [r for r in group if r['task'] == 'answerable' and not r['unmapped_evidence']
                            and (r['human_confirmed'] or include_pending and r['annotation_status'] == 'pending_review')]
                official = [r for r in group if r['task'] == 'answerable' and r['human_confirmed'] and not r['unmapped_evidence']]
                summary[path][split][str(k)] = {
                    'gold_policy': 'provisional_pending_included' if include_pending else 'human_confirmed_only',
                    'metrics': macro_average([r['scores'] for r in eligible]),
                    'human_confirmed_metrics': macro_average([r['scores'] for r in official]),
                    'failed_evidence_cases': [r['id'] for r in group if r['failure_reasons']],
                    'excluded_process_cases': [r['id'] for r in group if r['task'] == 'process_not_applicable'],
                    'negative_retrieval_observations_not_refusal_accuracy': [
                        {'id': r['id'], 'retrieved_count': r['final_result_count'], 'sources': sorted({v['source'] for v in r['results']})}
                        for r in group if r['task'] == 'no_positive_evidence'],
                    'mapping_exclusions': [r['id'] for r in group if r['unmapped_evidence']],
                }
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split', choices=('development', 'holdout', 'all'), default='development')
    parser.add_argument('--include-pending', action='store_true', help='Report provisional annotations, never call them human-confirmed gold')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--gold', type=Path, help='Verified frozen Gold v1; cannot be combined with --include-pending')
    args = parser.parse_args()
    if args.gold and args.include_pending:
        raise ValueError('Frozen Gold scoring cannot include pending annotations')
    output = args.output.resolve()
    if not any(output.is_relative_to(base) for base in (DIRECTORY, ROOT / 'eval/local')) or output.exists():
        raise ValueError('Use a new output under eval/r4 or eval/local; existing/historical reports are never overwritten')
    configuration = read_production_configuration()
    if args.gold:
        from eval.r4.gold import load_gold
        manifest, documents, cases = load_gold(args.gold)
    else:
        manifest, documents, cases = load_dataset()
    cases = [case for case in cases if args.split == 'all' or case['split'] == args.split]
    files = ['eval/r4/' + name for name in ('corpus.json', 'development.jsonl', 'holdout.jsonl', 'dataset.py', 'metrics.py', 'run_baseline.py')]
    implementation = ['backend/rag/' + name for name in ('service.py', 'retriever.py', 'chunker.py', 'vector_store.py', 'embedding_client.py')]
    implementation += ['backend/db/repository.py', 'backend/db/version_repository.py', 'backend/config.py']
    report = {'schema_version': 1, 'head': git('rev-parse', 'HEAD'), 'executed_at_utc': datetime.now(timezone.utc).isoformat(),
              'tracked_worktree_changes': git('diff', 'HEAD', '--name-only').splitlines(),
              'uncommitted_evaluation_files': git('ls-files', '--others', '--exclude-standard', '--', 'eval/r4', 'tests/test_r4_evaluation.py').splitlines(),
              'file_lf_sha256': {p: sha256((ROOT / p).read_text(encoding='utf-8')) for p in files + implementation},
              'configuration_observed_not_runtime_verified': configuration, 'query_rewrite': 'disabled',
              'scope': 'retrieval components only, no Agent model routing/generation/refusal judgement',
              'metric_definitions': {
                  'document_recall': 'Unique relevant versioned document keys returned / all positive-evidence document keys',
                  'chunk_recall': 'Unique positive-grade actual Chunk keys returned / all actual current Chunk keys fully containing a labelled span',
                  'mrr_at_k': '1 / first positive-grade rank in unique Top-K, else 0; not untruncated MRR',
                  'ndcg_at_k': 'sum((2^grade-1)/log2(rank+1)) / ideal sorted positive-grade DCG@K',
                  'aggregation': 'Macro mean over applicable questions, development/holdout separate; no-positive/process cases N/A',
                  'deduplication': 'Stable Chunk key first-occurrence deduplication before K; invalid versions retain rank with zero gain',
                  'qrels': 'Exact normalized source-span containment, not an exhaustive semantic annotation; primary grade 2, auxiliary grade 1',
                  'pending_review': 'Only included by explicit --include-pending; provisional metrics separate from human-confirmed metrics',
              },
              'split_policy': manifest['splits'], 'annotation_counts': dict(Counter(c['annotation_status'] for c in cases)),
              'vector': {'status': 'blocked', 'metrics': None, 'model': 'not_loaded_or_changed',
                  'reason': 'No verified precomputed corpus/query embeddings supplied. Real Embedding/downloads and business Collection access are forbidden; no Fake quality results.'},
              'production': {'configured_mode': configuration['values']['RAG_RETRIEVAL_MODE'],
                  'status': ('blocked_vector_path' if configuration['values']['RAG_RETRIEVAL_MODE'] == 'vector' else
                             'retrieval_only_keyword_no_llm_rewrite' if configuration['values']['RAG_RETRIEVAL_MODE'] == 'keyword' else 'blocked_unknown_configuration'),
                  'vector_fallback_executed': False, 'is_hybrid': False,
                  'keyword_control': 'Actual production dispatcher forced to keyword in temporary process; not a measured vector fallback or full Agent baseline'},
              'not_assessed': ['real vector retrieval', 'LLM Query Rewrite/routing', 'answer/citation semantic correctness', 'refusal correctness']}
    if args.gold:
        gold_path = args.gold.resolve()
        report['gold'] = {'version': 'v1', 'state': 'frozen', 'file': gold_path.relative_to(ROOT).as_posix(),
            'file_sha256': sha256(gold_path.read_bytes()),
            'manifest_sha256': sha256(gold_path.with_name('gold_v1_manifest.json').read_bytes())}
        report['file_lf_sha256']['eval/r4/gold.py'] = sha256((DIRECTORY / 'gold.py').read_text(encoding='utf-8'))
    try:
        with tempfile.TemporaryDirectory(prefix='r4-keyword-') as folder:
            counts = configure_isolation(Path(folder).resolve())
            chunks, by_id = prepare_corpus(documents)
            report['corpus'] = {'documents': len(documents), 'current_chunks': len(chunks), 'versions': 'published V1 through actual R1/R3 keyword management'}
            report['chunk_catalog'] = chunks
            report['results'] = run_keyword(cases, chunks, by_id)
            report['summary'] = summarize(report['results'], args.include_pending)
            report['safety'] = dict(counts, real_embedding_calls=0, fake_quality_results=False)
            gc.collect()
    finally:
        sys.setprofile(None)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents accidental replacement even if another process creates it meanwhile.
    with output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print('Report:', output.relative_to(ROOT))
    print(json.dumps(report['summary']['keyword'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

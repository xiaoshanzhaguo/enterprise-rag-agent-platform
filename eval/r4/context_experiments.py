"""R4-03: fixed-budget contexts on development Gold, isolated SQLite, no models.

Set USE_TORCH=0, USE_TF=0, HF_HUB_OFFLINE=1 before invoking in a fresh process.
python -m eval.r4.context_experiments --output eval/r4/context_experiments_gold_v1.json
"""
import argparse
from collections import Counter
import gc
import json
import math
from pathlib import Path
import re
import sqlite3
import statistics
import subprocess
import sys
import tempfile

from backend.rag.retriever import retrieve_top_chunks, tokenize, is_reliable_keyword_match
from eval.r4.chunk_experiments import build_chunks
from eval.r4.dataset import DIRECTORY, ROOT, canonical, map_evidence, sha256
from eval.r4.gold import load_gold, verify_files
from eval.r4.metrics import evaluate_ranking, macro_average
from eval.r4.run_baseline import configure_isolation


CONFIGURATIONS = [
    dict(id='production_360_40', family='production', size=360, overlap=40),
    dict(id='paragraph_pack_360', family='paragraph_pack', size=360, overlap=0),
    dict(id='paragraph_pack_600', family='paragraph_pack', size=600, overlap=0),
    dict(id='heading_prefix_360', family='heading_prefix', size=360, overlap=40),
]
CANDIDATE_COUNT = 5
# Predeclared after inspecting production: Top-3 median 633, maximum 924;
# 1800 also covers roughly 3 x 360 bodies plus production rules/reference labels.
BUDGETS = (700, 1000, 1800)


def renderer_adapter(builder):
    def render(rows):
        # Experiment identities are not business document/Chunk IDs. Corpus validity is checked separately.
        return builder([dict(text=r['text'], file_name=Path(r['source']).name,
            chunk_id=r['chunk_id'], score=r['score'], retrieval_mode='keyword') for r in rows])
    return render


def boundary_prefix(body, capacity):
    """Query/gold-blind prefix, preferring paragraph, sentence and line ends."""
    if capacity >= len(body):
        return body
    prefix = body[:capacity]
    ends = [m.end() for m in re.finditer(r'\n\n|[。！？!?](?:\s*)|\n', prefix)]
    return prefix[:max(ends)].rstrip() if ends else prefix.rstrip()


def pack_context(candidates, k, budget, render):
    """Preserve rank; truncate only the last admitted body, never select by gold/query."""
    if k not in (1, 3, 5) or budget <= 0:
        raise ValueError('Invalid context budget or K')
    if len(render([])) > budget:
        raise ValueError('Budget cannot hold production empty-evidence instructions')
    retained = []
    for candidate in candidates[:k]:
        body = candidate['text']  # Unaugmented original body, used for evidence visibility.
        prefix = '\n\n'.join(candidate['heading_context'])
        prefix = prefix + '\n\n' if prefix else ''
        def row(visible):
            return dict({key:candidate[key] for key in ('chunk_key','document_key','source','chunk_id','score','eligible')},
                text=prefix+visible, visible_body=visible,
                body_truncated=len(visible) != len(body))
        if len(render(retained+[row(body)])) <= budget:
            retained.append(row(body))
            continue
        low, high = 0, len(body)
        while low < high:
            middle = (low+high+1)//2
            if len(render(retained+[row(body[:middle])])) <= budget:
                low = middle
            else:
                high = middle-1
        visible = boundary_prefix(body, low)
        if visible:
            retained.append(row(visible))
        break
    text = render(retained)
    if len(text) > budget:
        raise ValueError('Context budget exceeded')
    return dict(text=text, characters=len(text), budget=budget, utilization=len(text)/budget,
        rows=retained, visible_body_characters=sum(len(r['visible_body']) for r in retained),
        added_heading_characters=sum(len(r['text'])-len(r['visible_body']) for r in retained))


def context_metrics(case, catalog, visible, k):
    """Keep full catalog qrels/denominators; gains require full spans in visible bodies.

    Document Recall remains document identity recall, not answer support. Context
    gains can be lower than full-chunk qrels after truncation, including auxiliary-only visibility.
    """
    qrels, mapping, unmapped = map_evidence(case, catalog)
    if unmapped:
        raise ValueError('Unmapped gold; stop this configuration')
    docs = {e['document_key'] for e in case['evidence']}
    raw = evaluate_ranking(visible, qrels, docs, k)
    grades, covered, hits = [], set(), set()
    for row in visible:
        contained = [e for e in case['evidence'] if row['chunk_key'] in mapping[e['id']]
            and canonical(e['text']) in canonical(row['visible_body'])]
        grades.append(max((e['grade'] for e in contained), default=0))
        covered.update(e['id'] for e in contained)
        if contained:
            hits.add(row['chunk_key'])
    positive = {key for key, grade in qrels.items() if grade > 0}
    dcg = sum((2**grade-1)/math.log2(rank+2) for rank, grade in enumerate(grades))
    raw.update(chunk_recall=len(hits)/len(positive) if positive else None,
        mrr_at_k=next((1/rank for rank, grade in enumerate(grades, 1) if grade), 0) if positive else None,
        ndcg_at_k=dcg/raw['ideal_dcg'] if raw['ideal_dcg'] else None, dcg=dcg,
        ranked_grades=grades, hit_chunk_keys=sorted(hits), missing_chunk_keys=sorted(positive-hits))
    missing = [e['id'] for e in case['evidence'] if e['id'] not in covered]
    primary = [e for e in case['evidence'] if e['role'] == 'primary']
    return dict(scores=raw, qrels=qrels, evidence_to_chunk_keys=mapping, covered_evidence_ids=sorted(covered),
        missing_evidence_ids=missing, primary_complete=all(e['id'] in covered for e in primary) if primary else None,
        multi_evidence_complete=not missing if len(case['evidence']) > 1 else None)


def summarize(rows):
    summary = {}
    for budget in BUDGETS:
        summary[str(budget)] = {}
        for k in (1, 3, 5):
            group = [r for r in rows if r['budget'] == budget and r['top_k'] == k]
            applicable = [r for r in group if r['task'] == 'answerable']
            multi = [r for r in applicable if r['multi_evidence_complete'] is not None]
            summary[str(budget)][str(k)] = dict(metrics=macro_average([r['scores'] for r in applicable]),
                primary_evidence_complete_rate=sum(r['primary_complete'] for r in applicable)/len(applicable),
                multi_evidence_complete_rate=sum(r['multi_evidence_complete'] for r in multi)/len(multi),
                multi_evidence_question_count=len(multi),
                average_context_characters=sum(r['context']['characters'] for r in applicable)/len(applicable),
                average_budget_utilization=sum(r['context']['utilization'] for r in applicable)/len(applicable),
                average_context_characters_all_development=sum(r['context']['characters'] for r in group)/len(group),
                incomplete_primary_cases=[r['id'] for r in applicable if not r['primary_complete']],
                negative_observations=[dict(id=r['id'], raw_candidate_count=r['candidate_count'],
                    visible_candidate_count=len(r['context']['rows']), context_characters=r['context']['characters'])
                    for r in group if r['task'] == 'no_positive_evidence'])
    return summary


def run_experiments(documents, cases, render, connection):
    if any(c['split'] != 'development' for c in cases):
        raise ValueError('Holdout must not be evaluated')
    connection.execute('CREATE TABLE experiment_chunks(strategy TEXT, ordinal INTEGER, record_json TEXT, PRIMARY KEY(strategy, ordinal))')
    experiments = []
    for config in CONFIGURATIONS:
        built = build_chunks(documents, config)
        for case in cases:
            if map_evidence(case, built)[2]:
                raise ValueError('Unmapped gold: '+config['id']+'/'+case['id'])
        connection.executemany('INSERT INTO experiment_chunks VALUES(?,?,?)',
            [(config['id'], i, json.dumps(c, ensure_ascii=False)) for i, c in enumerate(built)])
        connection.commit()
        catalog = [json.loads(r[0]) for r in connection.execute(
            'SELECT record_json FROM experiment_chunks WHERE strategy=? ORDER BY ordinal', (config['id'],))]
        candidates_by_case, rows = {}, []
        for case in cases:
            ranked = retrieve_top_chunks(case['question'].strip(),
                [dict(c, text=c['retrieval_text']) for c in catalog], top_k=CANDIDATE_COUNT)
            # Restore the body; keep ranking score and augmented retrieval input as separate fields.
            bodies = {c['chunk_key']: c for c in catalog}
            candidates = [dict(bodies[c['chunk_key']], score=c['score']) for c in ranked]
            candidates_by_case[case['id']] = candidates
            for budget in BUDGETS:
                for k in (1, 3, 5):
                    context = pack_context(candidates, k, budget, render)
                    rows.append(dict(id=case['id'], question=case['question'], task=case['task'],
                        top_k=k, budget=budget, candidate_count=len(candidates),
                        candidate_chunk_keys=[c['chunk_key'] for c in candidates], context=context,
                        **context_metrics(case, catalog, context['rows'], k)))
        experiments.append(dict(config=config, chunk_count=len(catalog), chunk_catalog=catalog,
            candidates_by_case=candidates_by_case, results=rows, summary=summarize(rows)))
    return experiments


def diagnose(experiments, cases):
    by_id = {c['id']:c for c in cases}
    diagnostics = {}
    for experiment in experiments:
        catalog = experiment['chunk_catalog']
        case = by_id['product_01']
        qrels, _, _ = map_evidence(case, catalog)
        query_tokens = Counter(tokenize(case['question']))
        traces = []
        for chunk in catalog:
            if chunk['chunk_key'] not in qrels:
                continue
            tokens = Counter(tokenize(chunk['retrieval_text']))
            overlap = sum(min(count,tokens[token]) for token,count in query_tokens.items())
            traces.append(dict(chunk_key=chunk['chunk_key'], query_token_count=sum(query_tokens.values()),
                overlap=overlap, coverage=overlap/sum(query_tokens.values()),
                passes_current_keyword_gate=is_reliable_keyword_match(overlap,sum(query_tokens.values()),case['question'])))
        positive_scores = [c['score'] for case in cases if case['task']=='answerable'
            for c in experiment['candidates_by_case'][case['id']]
            if c['chunk_key'] in map_evidence(case,catalog)[0]]
        diagnostics[experiment['config']['id']] = dict(product_01_gate_traces=traces,
            positive_candidate_score_min=min(positive_scores), positive_candidate_score_max=max(positive_scores),
            negative_probes=[dict(id=id, question=by_id[id]['question'],
                candidate_keys=[c['chunk_key'] for c in experiment['candidates_by_case'][id]],
                scores=[c['score'] for c in experiment['candidates_by_case'][id]],
                support_assessment=reason) for id,reason in (
                    ('refusal_02','Retrieved reimbursement/invoice rules do not specify the requested company tax-ID digits.'),
                    ('refusal_05','Retrieved general account/security rules do not provide reimbursement-system admin credentials.'))])
    return diagnostics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(DIRECTORY) or output.exists():
        raise ValueError('Use a new output under eval/r4')
    _, documents, all_cases = load_gold()
    cases = [c for c in all_cases if c['split'] == 'development']
    baseline = json.loads((DIRECTORY/'baseline_gold_v1_keyword.json').read_text(encoding='utf-8'))
    order = list(dict.fromkeys(c['source'] for c in baseline['chunk_catalog']))
    documents = {source: documents[source] for source in order}
    checksums = json.loads((DIRECTORY/'gold_v1_checksums.json').read_text(encoding='utf-8'))
    verify_files(checksums['files_sha256'])
    with tempfile.TemporaryDirectory(prefix='r4-context-') as folder:
        safety = configure_isolation(Path(folder).resolve())
        try:
            from backend.rag.service import build_rag_context_from_chunks
            render = renderer_adapter(build_rag_context_from_chunks)
            baseline_lengths = [len(render([dict(c, source=c['source']) for c in r['results']]))
                for r in baseline['results'] if r['split'] == 'development' and r['pipeline'] == 'keyword'
                and r['top_k'] == 3 and r['task'] == 'answerable']
            with sqlite3.connect(str(Path(folder)/'catalog.sqlite')) as connection:
                experiments = run_experiments(documents, cases, render, connection)
            # sqlite3 context manager commits but does not close.
            connection.close()
            for case in cases:
                original = next(r for r in baseline['results'] if r['id'] == case['id']
                    and r['pipeline'] == 'keyword' and r['top_k'] == 5)
                current = experiments[0]['candidates_by_case'][case['id']]
                if [(r['chunk_key'],r['score']) for r in current] != [(r['chunk_key'],r['score']) for r in original['results']]:
                    raise ValueError('Default retrieval differs from frozen baseline')
            safety = dict(safety, real_model_calls=0, fake_quality_results=False)
            gc.collect()
        finally:
            sys.setprofile(None)
    verify_files(checksums['files_sha256'])
    paths = ['eval/r4/gold_v1.json','eval/r4/gold_v1_manifest.json','eval/r4/baseline_gold_v1_keyword.json',
        'eval/r4/chunk_experiments.py','eval/r4/context_experiments.py','backend/rag/chunker.py',
        'backend/rag/retriever.py','backend/rag/service.py','backend/schema/chat_schema.py']
    report = dict(schema_version=1, head=subprocess.check_output(['git','-c','safe.directory='+ROOT.as_posix(),
        'rev-parse','HEAD'],text=True).strip(), split='development', holdout_evaluated=False,
        candidate_count=CANDIDATE_COUNT, budgets=BUDGETS, production_context=dict(
            median_characters=statistics.median(baseline_lengths), maximum_characters=max(baseline_lengths),
            native_global_budget=False, preview_limit_is_not_prompt_limit=True),
        budget_policy='Actual production RAG context string including rules, reference labels, added headings and visible bodies; excludes other conversation/system prompts. Python Unicode characters, not tokens.',
        packing_policy='Rank order, no rerank or gold/query-aware selection; first overflow truncates body at preceding paragraph/sentence/line boundary, then stops. No lower-rank backfill.',
        scoring_policy='Original full-body qrels/IDCG/denominators unchanged; gain requires complete exact normalized evidence within retained original body. Added headings get no gain. Document Recall counts document identity, not answer sufficiency.',
        metric_definitions=dict(primary_complete='All primary evidence spans visible / answerable questions',
            multi_complete='All positive evidence spans visible / questions with more than one positive evidence span',
            averages='Context mean/utilization over 20 answerable questions; all-30 mean separately recorded.'),
        safety=safety, diagnostics=diagnose(experiments,cases), experiments=experiments,
        file_sha256={p:sha256((ROOT/p).read_bytes()) for p in paths})
    with output.open('x',encoding='utf-8',newline='\n') as stream:
        json.dump(report,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print('Report:',output.relative_to(ROOT),'strategies:',len(experiments),'rows:',sum(len(e['results']) for e in experiments))
    for experiment in experiments:
        print(experiment['config']['id'],[(b,experiment['summary'][str(b)]['3']['multi_evidence_complete_rate']) for b in BUDGETS])


if __name__ == '__main__':
    main()

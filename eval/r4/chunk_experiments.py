"""R4-02 development-only chunk ablations. No DB, model or vector imports.

python -m eval.r4.chunk_experiments --output eval/r4/chunk_experiments_gold_v1.json
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import subprocess

from backend.rag.chunker import normalize_text, split_long_paragraph, split_text_into_chunks
from backend.rag.retriever import retrieve_top_chunks
from eval.r4.dataset import ROOT, DIRECTORY, canonical, chunk_record, map_evidence, sha256
from eval.r4.gold import load_gold, verify_files
from eval.r4.metrics import macro_average
from eval.r4.run_baseline import score_query


# Declared before evaluation; all candidates receive identical ranking and mapping rules.
CONFIGURATIONS = [dict(id=f'production_{size}_{overlap}', family='production', size=size, overlap=overlap)
                  for size in (180, 360, 600) for overlap in (0, 40, 100)] + [
    dict(id=f'paragraph_pack_{size}', family='paragraph_pack', size=size, overlap=0) for size in (360, 600)
] + [dict(id=f'heading_prefix_{size}', family='heading_prefix', size=size, overlap=40) for size in (360, 600)]


def paragraph_pack(text, size):
    """Paragraph/line preserving size packing, allowing adjacent sections to share a chunk."""
    chunks, current = [], ''
    for paragraph in normalize_text(text).split('\n\n'):
        for part in split_long_paragraph(paragraph.strip(), size):
            candidate = (current + '\n\n' + part).strip()
            if current and len(candidate) > size:
                chunks.append(current)
                current = part
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def heading_prefix(source, body):
    """Extract original Markdown ancestor headings at the body's source location.

    Added headings affect retrieval input only. Qrels always use the unprefixed body.
    """
    start = canonical(source).find(canonical(body))
    if start < 0:
        raise ValueError('Cannot locate candidate body in original source')
    headings = {}
    for match in re.finditer(r'(?m)^(#{1,6})\s+([^\n]+)', source):
        if len(canonical(source[:match.start()])) > start:
            break
        level = len(match.group(1))
        headings = {key: value for key, value in headings.items() if key < level}
        headings[level] = match.group(0).strip()
    return [headings[level] for level in sorted(headings) if headings[level] not in body]


def build_chunks(documents, config):
    catalog = []
    for doc in documents.values():
        if config['family'] == 'paragraph_pack':
            bodies = paragraph_pack(doc['text'], config['size'])
        else:
            bodies = split_text_into_chunks(doc['text'], config['size'], config['overlap'])
        for index, body in enumerate(bodies, 1):
            # A real normalized source substring is required; synthetic concatenation cannot create evidence.
            source_start = canonical(doc['text']).find(canonical(body))
            if source_start < 0:
                raise ValueError('Candidate body has no contiguous normalized source mapping')
            headings = heading_prefix(doc['text'], body) if config['family'] == 'heading_prefix' else []
            record = chunk_record(doc, dict(chunk_id=index, text=body))
            catalog.append(dict(record, source_normalized_start=source_start,
                retrieval_text='\n\n'.join(headings + [body]), heading_context=headings))
    return catalog


def run_configuration(config, documents, cases):
    if any(c['split'] != 'development' for c in cases):
        raise ValueError('Holdout is forbidden for chunk parameter selection')
    try:
        chunks = build_chunks(documents, config)
    except ValueError as exc:
        return dict(config=config, status='blocked_source_mapping', reason=str(exc), summary=None)
    unmapped = {c['id']: map_evidence(c, chunks)[2] for c in cases if c['evidence']}
    unmapped = {key: ids for key, ids in unmapped.items() if ids}
    common = dict(config=config, chunk_count=len(chunks), chunk_catalog=chunks,
        body_characters=sum(len(c['text']) for c in chunks),
        retrieval_characters=sum(len(c['retrieval_text']) for c in chunks),
        maximum_body_characters=max(len(c['text']) for c in chunks),
        unmapped_evidence=unmapped)
    if unmapped:
        # Stop this entire configuration; never remove missing spans from denominators.
        return dict(common, status='blocked_evidence_mapping', summary=None)
    retrieval_chunks = [dict(c, text=c['retrieval_text']) for c in chunks]
    rows = []
    for case in cases:
        for k in (1, 3, 5):
            results = retrieve_top_chunks(case['question'].strip(), retrieval_chunks, top_k=k)
            scored = score_query(case, chunks, results, k)
            evidence_count = len(case['evidence'])
            rows.append(dict(id=case['id'], question=case['question'], task=case['task'], top_k=k,
                evidence=case['evidence'], results=results, **scored,
                # Stable span-based diagnostic avoids overlap inflating relevant-chunk denominators.
                evidence_span_coverage=(1-len(scored['missing_evidence_ids'])/evidence_count) if evidence_count else None,
                returned_characters=sum(len(c['text']) for c in results)))
    summary = {}
    for k in (1, 3, 5):
        group = [r for r in rows if r['top_k'] == k and r['task'] == 'answerable']
        summary[str(k)] = dict(metrics=macro_average([r['scores'] for r in group]),
            evidence_span_coverage=sum(r['evidence_span_coverage'] for r in group)/len(group),
            complete_evidence_questions=sum(not r['missing_evidence_ids'] for r in group),
            average_returned_characters=sum(r['returned_characters'] for r in group)/len(group),
            incomplete_cases=[r['id'] for r in group if r['missing_evidence_ids']],
            negative_observations=[dict(id=r['id'], count=len(r['results'])) for r in rows
                if r['top_k'] == k and r['task'] == 'no_positive_evidence'])
    return dict(common, status='evaluated', results=rows, summary=summary)


def selection_key(experiment):
    """Predeclared development-only selection, penalizing incomplete evidence and context cost."""
    s = experiment['summary']
    return (s['3']['evidence_span_coverage'], s['3']['metrics']['chunk_recall']['mean'],
            s['3']['metrics']['ndcg_at_k']['mean'], s['1']['evidence_span_coverage'],
            -s['3']['average_returned_characters'], -experiment['chunk_count'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(DIRECTORY) or output.exists():
        raise ValueError('Use a new report path under eval/r4')
    _, documents, all_cases = load_gold()
    checksums = json.loads((DIRECTORY / 'gold_v1_checksums.json').read_text(encoding='utf-8'))
    verify_files(checksums['files_sha256'])
    cases = [c for c in all_cases if c['split'] == 'development']
    baseline_path = DIRECTORY / 'baseline_gold_v1_keyword.json'
    baseline = json.loads(baseline_path.read_text(encoding='utf-8'))
    # Use only the frozen corpus ordering, never holdout question/results for tuning.
    order = list(dict.fromkeys(c['source'] for c in baseline['chunk_catalog']))
    documents = {key: documents[key] for key in order}
    experiments = [run_configuration(config, documents, cases) for config in CONFIGURATIONS]
    default = next(e for e in experiments if e['config']['id'] == 'production_360_40')
    assert default['status'] == 'evaluated'
    assert [c['chunk_key'] for c in default['chunk_catalog']] == [c['chunk_key'] for c in baseline['chunk_catalog']]
    baseline_dev = {(r['id'], r['top_k']): r for r in baseline['results']
                    if r['pipeline'] == 'keyword' and r['split'] == 'development'}
    for row in default['results']:
        reference = baseline_dev[row['id'], row['top_k']]
        assert row['scores'] == reference['scores']
        assert [r['chunk_key'] for r in row['results']] == [r['chunk_key'] for r in reference['results']]
    viable = [e for e in experiments if e['status'] == 'evaluated']
    selected = max(viable, key=selection_key)
    paths = ['eval/r4/gold_v1.json', 'eval/r4/gold_v1_manifest.json',
             'eval/r4/baseline_gold_v1_keyword.json', 'eval/r4/chunk_experiments.py',
             'eval/r4/dataset.py', 'eval/r4/metrics.py', 'backend/rag/chunker.py', 'backend/rag/retriever.py']
    report = dict(schema_version=1, head=subprocess.check_output(['git', '-c', 'safe.directory='+ROOT.as_posix(),
        'rev-parse', 'HEAD'], text=True).strip(), split='development', case_count=len(cases),
        applicable_case_count=Counter(c['task'] for c in cases)['answerable'],
        holdout_evaluated=False, production_modified=False, configuration_grid_predeclared=CONFIGURATIONS,
        mapping_policy='Existing exact normalized full-span containment against body only; no heading-prefix scoring credit; missing mappings block whole configuration.',
        limitations=['Chunk Recall denominators vary by segmentation; inspect fixed-span coverage and context cost.',
                     'Not an answer-quality or real Vector evaluation; no production DB lifecycle execution.',
                     'Baseline corpus ordering reused, default all development rankings/scores exactly reproduced.'],
        selection_policy='lexicographic max: span coverage@3, Chunk Recall@3, nDCG@3, span coverage@1, negative context characters@3, negative chunk count; development only',
        selected_configuration=selected['config']['id'], experiments=experiments,
        file_sha256={p: sha256((ROOT / p).read_bytes()) for p in paths})
    verify_files(checksums['files_sha256'])
    with output.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2); stream.write('\n')
    print('Report:', output.relative_to(ROOT), 'selected:', selected['config']['id'])
    for e in experiments:
        print(e['config']['id'], e['chunk_count'] if 'chunk_count' in e else '-', e['status'],
              [round(e['summary'][str(k)]['metrics']['chunk_recall']['mean'], 6) for k in (1,3,5)] if e['summary'] else e.get('unmapped_evidence'))


if __name__ == '__main__':
    main()

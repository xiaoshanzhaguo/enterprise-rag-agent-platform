"""Frozen source spans, review policy and deterministic mapping to actual production Chunks."""
import hashlib
import json
from pathlib import Path
import re
import unicodedata

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = Path(__file__).resolve().parent


def sha256(value):
    return hashlib.sha256(value.encode('utf-8') if isinstance(value, str) else value).hexdigest()


def canonical(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text))


def load_dataset(directory=DIRECTORY, root=ROOT):
    manifest = json.loads((directory / 'corpus.json').read_text(encoding='utf-8'))
    original = [json.loads(line) for line in (root / 'eval/week4_questions.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    if sha256(json.dumps(original, sort_keys=True, ensure_ascii=False, separators=(',', ':'))) != manifest['legacy_questions_sha256']:
        raise ValueError('Historical dataset changed; do not silently rescore against different gold')
    documents, cases, ids, questions = {}, [], set(), set()
    if manifest['schema_version'] != 1 or len(manifest['documents']) != 12:
        raise ValueError('Expected frozen R4 schema and original 12 documents')
    for doc in manifest['documents']:
        path = (root / doc['source']).resolve()
        if not path.is_relative_to((root / 'docs/sample_knowledge_base').resolve()):
            raise ValueError('Source outside frozen sample corpus')
        text = path.read_text(encoding='utf-8')
        if sha256(text) != doc['text_sha256']:
            raise ValueError('Frozen corpus changed: ' + doc['source'])
        if doc['source'] in documents or type(doc['version_number']) is not int or doc['version_number'] != 1:
            raise ValueError('Duplicate document')
        documents[doc['source']] = {**doc, 'text': text}
    for split in ('development', 'holdout'):
        for line in (directory / (split + '.jsonl')).read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            case = json.loads(line)
            if case['split'] != split or case['id'] in ids or canonical(case['question']) in questions:
                raise ValueError('Duplicate question/ID or split leakage')
            if case['annotation_status'] not in ('pending_review', 'confirmed', 'rejected'):
                raise ValueError('Unknown review status')
            ids.add(case['id']); questions.add(canonical(case['question']))
            evidence_ids = set()
            for evidence in case['evidence']:
                doc = documents[evidence['source']]
                if (type(evidence['start']) is not int or type(evidence['end']) is not int
                        or not 0 <= evidence['start'] < evidence['end'] <= len(doc['text'])):
                    raise ValueError('Invalid span offsets')
                if (evidence['annotation_status'] not in ('pending_review', 'confirmed', 'rejected')
                        or evidence['id'] in evidence_ids or evidence['text'] != doc['text'][evidence['start']:evidence['end']]
                        or not evidence['text'].strip() or evidence['text_sha256'] != sha256(evidence['text'])
                        or evidence['document_key'] != doc['document_key']):
                    raise ValueError('Invalid/duplicate evidence span: ' + case['id'])
                if (evidence['role'], evidence['grade']) not in (('primary', 2), ('auxiliary', 1)):
                    raise ValueError('Invalid relevance grade/role')
                evidence_ids.add(evidence['id'])
            if case['task'] == 'answerable' and not any(e['role'] == 'primary' for e in case['evidence']):
                raise ValueError('Answerable question without primary evidence')
            if case['task'] not in ('answerable', 'process_not_applicable', 'no_positive_evidence'):
                raise ValueError('Unknown retrieval task')
            if case['task'] != 'answerable' and case['evidence']:
                raise ValueError('Do not invent positive evidence for an unsupported/process case')
            cases.append(case)
    preserved = [case['legacy_case'] for case in cases if case.get('legacy_case') is not None]
    if preserved != original:
        raise ValueError('All 30 historical questions/gold must be preserved exactly and in order')
    if not any(case['split'] == 'holdout' for case in cases):
        raise ValueError('Missing holdout set')
    for split in ('development', 'holdout'):
        if sum(case['split'] == split for case in cases) != manifest['splits'][split]['count']:
            raise ValueError('Unexpected split size')
    return manifest, documents, cases


def chunk_record(document, chunk, *, eligible=True):
    text_hash = sha256(canonical(chunk['text']))
    return {**chunk, 'source': document['source'], 'document_key': document['document_key'],
            'version_number': document['version_number'], 'eligible': eligible,
            'chunk_key': f"{document['document_key']}::chunk-{chunk['chunk_id']}::{text_hash}"}


def map_evidence(case, chunks):
    """Enumerate all actual current chunks fully containing each normalized source span.

    This is an explicit exact-span relevance policy, not an exhaustive human semantic annotation.
    """
    qrels, mapping, unmapped = {}, {}, []
    for evidence in case['evidence']:
        matches = [chunk['chunk_key'] for chunk in chunks if chunk['eligible']
                   and chunk['document_key'] == evidence['document_key']
                   and canonical(evidence['text']) in canonical(chunk['text'])]
        mapping[evidence['id']] = matches
        if not matches:
            unmapped.append(evidence['id'])
        for key in matches:
            qrels[key] = max(qrels.get(key, 0), evidence['grade'])
    return qrels, mapping, unmapped

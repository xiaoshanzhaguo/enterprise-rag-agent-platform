"""Verify immutable, user-approved Gold v1 before offline scoring."""
import json
from pathlib import Path

from eval.r4.dataset import DIRECTORY, ROOT, canonical, load_dataset, sha256


def verify_files(records, root=ROOT):
    for name, expected in records.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or sha256(path.read_bytes()) != expected:
            raise ValueError('Frozen file SHA-256 mismatch: ' + name)


def load_gold(path=DIRECTORY / 'gold_v1.json', *, root=ROOT):
    path = Path(path).resolve()
    manifest_path = path.with_name('gold_v1_manifest.json')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest['state'] != 'frozen' or manifest['gold_version'] != 'v1':
        raise ValueError('Expected frozen Gold v1 manifest')
    verify_files(manifest['files_sha256'], root)
    if manifest['gold_file'] != path.relative_to(root.resolve()).as_posix():
        raise ValueError('Gold path does not match manifest')
    gold = json.loads(path.read_text(encoding='utf-8'))
    if (gold['artifact_type'] != 'frozen_gold' or gold['state'] != 'frozen'
            or not gold['formally_frozen'] or not gold['dataset_confirmed_by_user']):
        raise ValueError('Gold has not been approved and frozen')
    source_manifest, documents, originals = load_dataset(root=root, directory=path.parent)
    if gold['corpus_manifest_snapshot'] != source_manifest:
        raise ValueError('Corpus identity changed')
    cases = gold['development'] + gold['holdout']
    if len(cases) != 38 or [c['id'] for c in cases] != [c['id'] for c in originals]:
        raise ValueError('Unexpected frozen case identities/order')
    expected = {c['id']: c for c in originals}
    ids, questions, evidence_ids = set(), set(), set()
    for case in cases:
        old = expected[case['id']]
        if (case['split'] != old['split'] or case['question'] != old['question']
                or case.get('legacy_case') != old.get('legacy_case')
                or case['id'] in ids or canonical(case['question']) in questions
                or case['annotation_status'] != 'confirmed'
                or case['original_annotation_status'] != old['annotation_status']):
            raise ValueError('Invalid frozen case: ' + case['id'])
        ids.add(case['id']); questions.add(canonical(case['question']))
        original_evidence = {e['id']: e for e in old['evidence']}
        for evidence in case['evidence']:
            restored = dict(evidence)
            restored['annotation_status'] = restored.pop('original_annotation_status')
            doc = documents[evidence['source']]
            if (restored != original_evidence.get(evidence['id'])
                    or evidence['id'] in evidence_ids or evidence['annotation_status'] != 'confirmed'
                    or evidence['text'] != doc['text'][evidence['start']:evidence['end']]
                    or sha256(evidence['text']) != evidence['text_sha256']
                    or evidence['document_key'] != doc['document_key']
                    or (evidence['role'], evidence['grade']) not in (('primary', 2), ('auxiliary', 1))):
                raise ValueError('Invalid frozen evidence: ' + evidence['id'])
            evidence_ids.add(evidence['id'])
        wanted = set(original_evidence) - ({'it_03:e2'} if case['id'] == 'it_03' else set())
        if {e['id'] for e in case['evidence']} != wanted:
            raise ValueError('Frozen evidence set changed')
    if (len(evidence_ids) != 36 or sum(e['role'] == 'primary' for c in cases for e in c['evidence']) != 35
            or sum(c['split'] == 'development' for c in cases) != 30):
        raise ValueError('Frozen evidence/split counts changed')
    encoded = json.dumps(cases, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    if sha256(encoded) != gold['cases_canonical_sha256']:
        raise ValueError('Frozen case semantic digest changed')
    return source_manifest, documents, cases

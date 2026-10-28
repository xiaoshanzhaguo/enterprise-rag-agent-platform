"""仅评测生产模型路由，不生成答案、不读写业务数据库、不触发申请。"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def load_cases(extra_cases: Path | None = None):
    result = []
    for name, group in [('week4_questions.jsonl', 'original'), ('routing_holdout.jsonl', 'holdout')]:
        for line in (ROOT/'eval'/name).read_text(encoding='utf-8').splitlines():
            if line.strip():
                case = json.loads(line)
                result.append(dict(id=case['id'], question=case['question'], expected_route=case['expected_route'], group=group))
    if extra_cases:
        for line in extra_cases.read_text(encoding='utf-8').splitlines():
            if line.strip():
                case = json.loads(line)
                result.append(dict(id=case['id'], question=case['question'], expected_route=case['expected_route'],
                                   expected_need_knowledge_base=case['expected_need_knowledge_base'], group='security'))
    if len({case['id'] for case in result}) != len(result):
        raise ValueError('用例编号重复')
    return result


def summarize(results):
    summary = {}
    for group in ('all', 'original', 'holdout', *sorted({item['group'] for item in results}-{'original', 'holdout'})):
        selected = [item for item in results if group == 'all' or item['group'] == group]
        summary[group] = dict(total=len(selected), correct=sum(item['correct'] for item in selected),
                              model_errors=sum(item['actual_route'] is None for item in selected),
                              process_intent_correct=sum(item['actual_route'] is not None and
                                  ((item['actual_route'] == 'process_apply') == (item['expected_route'] == 'process_apply')) for item in selected))
        knowledge_cases = [item for item in selected if 'expected_need_knowledge_base' in item]
        if knowledge_cases:
            summary[group]['knowledge_base_correct'] = sum(item.get('need_knowledge_base') == item['expected_need_knowledge_base'] for item in knowledge_cases)
            summary[group]['knowledge_base_total'] = len(knowledge_cases)
    return summary


def compare(before, after):
    if any(before[key] != after[key] for key in ('cases_sha256', 'model', 'temperature', 'scope')):
        raise ValueError('模型、参数、范围或评测集不同，不能直接对照')
    previous = {item['id']: item for item in before['results']}
    return [dict(id=item['id'], expected=item['expected_route'], before=previous[item['id']]['actual_route'],
                 after=item['actual_route'], change='improved' if item['correct'] else 'regressed')
            for item in after['results'] if item['correct'] != previous[item['id']]['correct']]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--compare', type=Path)
    parser.add_argument('--extra-cases', type=Path, help='追加固定的虚拟安全规范回归样例')
    args = parser.parse_args()
    from backend.config import settings
    from backend.llm.client import get_client
    from backend.prompt.agent_prompts import build_agent_decision_messages
    from backend.services.agent_router import _decide_need_knowledge_base_by_llm
    cases = load_cases(args.extra_cases)
    report = dict(executed_at_utc=datetime.now(timezone.utc).isoformat(), model=settings.llm_model,
                  temperature=0, scope='production_model_routing_only', cases_sha256=digest(cases),
                  prompt_sha256=digest(build_agent_decision_messages('')[0]['content']))
    client = get_client().model_copy(update={'request_timeout': 30, 'max_retries': 0})
    def run(case):
        started = time.perf_counter()
        decision = _decide_need_knowledge_base_by_llm(case['question'], client)
        actual = decision.route_type.value if decision else None
        print(case['id'] + ': ' + (actual or 'model_error'), flush=True)
        return dict(**case, actual_route=actual, correct=actual == case['expected_route'],
                    need_knowledge_base=decision.should_use_rag if decision else None,
                    reason=decision.reason if decision else '模型调用或解析失败，未用规则结果冒充模型结果',
                    duration_ms=round((time.perf_counter()-started)*1000, 2))
    with ThreadPoolExecutor(max_workers=3) as pool:
        report['results'] = list(pool.map(run, cases))
    report['summary'] = summarize(report['results'])
    if args.compare:
        report['changes'] = compare(json.loads(args.compare.read_text(encoding='utf-8')), report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report['summary'], ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()

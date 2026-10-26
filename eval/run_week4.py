"""真实 Agent 服务评测或离线检索基线；始终使用独立临时数据库，不提交申请。"""

import argparse
import asyncio
from collections import Counter
import gc
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eval.week4_metrics import summarize

CASES = ROOT / 'eval/week4_questions.jsonl'
CORPUS = ROOT / 'eval/week4_corpus.json'
KB = 'enterprise_default'


def fingerprint(path):
    # 评测定义按 JSON 内容签名，避免 Git 的 CRLF/LF 转换使复核报告失效。
    # 原始 Markdown 仍按字节校验，与第 24 天的语料清单一致。
    if path.suffix in {'.json', '.jsonl'}:
        text = path.read_text(encoding='utf-8')
        value = ([json.loads(line) for line in text.splitlines() if line.strip()]
                 if path.suffix == '.jsonl' else json.loads(text))
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_dataset():
    cases = [json.loads(line) for line in CASES.read_text(encoding='utf-8').splitlines() if line.strip()]
    manifest = json.loads(CORPUS.read_text(encoding='utf-8'))
    docs = manifest['documents']
    if len(cases) != 30 or len({c['id'] for c in cases}) != len(cases):
        raise ValueError('预期 30 个唯一用例')
    if Counter(c['category'] for c in cases) != dict.fromkeys(('hr', 'finance', 'it', 'product', 'process', 'refusal'), 5):
        raise ValueError('预期六类场景各五题')
    filenames = [d['filename'] for d in docs]
    if len(set(filenames)) != len(filenames):
        raise ValueError('文件名重复，无法可靠映射引用')
    for d in docs:
        path = (ROOT / d['source']).resolve()
        if not path.is_relative_to((ROOT / 'docs/sample_knowledge_base').resolve()):
            raise ValueError('语料超出样例目录')
        if fingerprint(path) != d['sha256']:
            raise ValueError(f"语料已变更，请先复核金标：{d['source']}")
    sources = {d['source'] for d in docs}
    for c in cases:
        if c.get('expected_source'):
            if c['expected_source'] not in sources:
                raise ValueError(f"金标来源不在语料中：{c['id']}")
            if c['evidence_quote'] not in (ROOT / c['expected_source']).read_text(encoding='utf-8'):
                raise ValueError(f"金标摘录与语料不一致：{c['id']}")
    return cases, docs


def configure_runtime(directory, retrieval_mode):
    if 'backend.config' in sys.modules:
        raise RuntimeError('请在独立进程中运行评测，不能复用业务配置')
    os.environ['DATABASE_URL'] = 'sqlite:///' + (directory / 'eval.db').as_posix()
    os.environ['VECTOR_STORE_DIR'] = str(directory / 'chroma')
    os.environ['RAG_RETRIEVAL_MODE'] = retrieval_mode
    os.environ['RAG_KEYWORD_FALLBACK_ENABLED'] = 'true'


def prepare_corpus(docs):
    from backend.db.init_db import init_database
    from backend.rag.chunker import split_text_into_chunks
    from backend.rag.store import save_document_chunks
    init_database()
    for d in docs:
        save_document_chunks(session_id='week4-corpus', file_name=d['filename'],
                             chunks=split_text_into_chunks((ROOT / d['source']).read_text(encoding='utf-8')),
                             knowledge_base_id=KB, knowledge_base_type=Path(d['source']).parent.name)


def offline_case(case, top_k):
    from backend.services.agent_router import route_question_by_rules
    from backend.services.agent_service import _resolve_agent_knowledge_base_type_filter, _retrieve_agent_rag_chunks
    started = time.perf_counter()
    decision = route_question_by_rules(case['question'])
    route_ms = round((time.perf_counter() - started) * 1000, 2)
    selected, _ = _resolve_agent_knowledge_base_type_filter(decision.route_type, case['question'], None)
    chunks, mode, query, note = _retrieve_agent_rag_chunks(
        session_id=None, rewritten_query=decision.rewritten_query, original_question=case['question'],
        knowledge_base_id=KB, knowledge_base_type_filter=selected, top_k=top_k)
    return {'route': decision.route_type.value, 'answer': None, 'chunks': chunks,
            'retrieval': {'mode': mode, 'query': query, 'note': note},
            'trace': [{'stage': 'rule_route', 'status': 'completed', 'duration_ms': route_ms},
                      {'stage': 'retrieval', 'status': 'completed', 'duration_ms': round((time.perf_counter()-started)*1000-route_ms, 2)},
                      {'stage': 'answer', 'status': 'skipped', 'reason': 'offline 模式不生成或模拟答案'}]}


async def live_case(case, client, request_id, top_k):
    from backend.db.repository import ensure_chat_session
    from backend.schema.chat_schema import ChatRequest
    from backend.services.agent_service import run_agent_stream
    # 提前给独立会话设置标题，避免额外调用模型生成标题。
    ensure_chat_session(session_id=request_id, mode='default', title='Week4 Eval')
    response = run_agent_stream(ChatRequest(session_id=request_id, input_text=case['question'],
        task_type='agent', use_rag=True, rag_top_k=top_k, user_options={'knowledge_base_id': KB}), client)
    result = {'route': None, 'answer': None, 'chunks': [], 'trace': []}
    starts = {}
    final_seen = False
    async for block in response.body_iterator:
        if isinstance(block, bytes):
            block = block.decode('utf-8')
        event = json.loads(block.removeprefix('data:').strip())
        stage = event.get('step_name')
        kind = event['event_type']
        if kind == 'step_start':
            starts[stage] = time.perf_counter()
        elif kind == 'step_complete':
            result['trace'].append({'stage': stage, 'status': 'completed',
                'duration_ms': round((time.perf_counter()-starts.get(stage, time.perf_counter()))*1000, 2)})
            if stage == 'judge_knowledge':
                match = re.search(r'\b(knowledge_qa|process_apply|it_support|finance_process|product_doc|irrelevant)\b', event['content'])
                result['route'] = match.group(0) if match else None
        elif kind == 'error':
            # 不把底层异常原文（可能包含连接信息）写入报告。
            result['error'] = 'agent_error'
        elif kind == 'final':
            final_seen = True
            result['answer'] = json.loads(event['content'])['generate_answer']
            metadata = event.get('metadata') or {}
            result['chunks'] = metadata.get('rag_preview_chunks', [])
            result['retrieval'] = metadata.get('agent_route', {})
            result['extraction'] = metadata.get('process_extraction')
    if not final_seen:
        result.setdefault('error', 'missing_final')
    elif result['route'] is None:
        result.setdefault('error', 'missing_route')
    if result.get('error'):
        result['trace'].append({'stage': 'agent', 'status': 'failed', 'error': result['error']})
    return result


def write_report(report, cases, output, reviews):
    report['refusal_reviews'] = reviews
    report.update(summarize(cases, report['results'], reviews))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    labels = {'retrieval_hit': '检索命中', 'citation_hit': '答案引用命中', 'keyword_coverage': '答案关键词覆盖',
              'refusal_correct': '无依据拒答（逐条复核）', 'process_intent': '流程意图二分类', 'route_correct': '六类路由完全匹配'}
    lines = ['# 第四周评测报告', '', f"- 模式：{report['mode']}；模型：{report['model']}；检索：{report['retrieval_mode']}；top_k={report['top_k']}",
             f"- 时间：{report['executed_at_utc']}；错误：{report['errors']}；待复核拒答：{len(report['pending_refusal_reviews'])}",
             '- 范围：真实 Agent 服务或离线规则/检索，不包含 HTTP、浏览器与 n8n 提交。', '',
             '| 指标 | 通过/适用数 | 比率 |', '| --- | ---: | ---: |']
    for key, label in labels.items():
        m = report['metrics'][key]
        rate = f"{m['rate']:.1%}" if m['rate'] is not None else 'N/A'
        lines.append(f"| {label} | {m['passed']}/{m['total']} | {rate} |")
    lines += ['', f"流程意图混淆统计：{report['metrics']['process_intent']}", '',
              '引用需匹配实际召回 chunk；关键词覆盖为事实组初筛，不代表语义正确率。拒答复核未完成时，汇总仅覆盖已复核或运行出错的题，不能解释为全部拒答题的准确率。', '',
              '| 用例 | 实际路由 | 检索命中 | 引用命中 | 关键词组 | 拒答复核 | 字段严格匹配 | 错误 |',
              '| --- | --- | --- | --- | --- | --- | --- | --- |']
    for r in report['results']:
        s = report['scores'][r['id']]
        keywords = f"{sum(s['keyword_hits'])}/{len(s['keyword_hits'])}" if s['keyword_hits'] is not None else 'N/A'
        fields = s['field_results']
        field_count = f"{sum(fields.values())}/{len(fields)}" if fields is not None else 'N/A'
        lines.append(f"| {r['id']} | {r.get('route')} | {s['retrieval_hit']} | {s['citation_hit']} | {keywords} | {s['refusal_correct']} | {field_count} | {r.get('error', '')} |")
    output.with_suffix('.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['offline', 'live'], default='offline')
    parser.add_argument('--retrieval-mode', choices=['keyword', 'vector'], default='keyword')
    parser.add_argument('--top-k', type=int, choices=range(1, 6), default=3)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--refusal-review', type=Path)
    parser.add_argument('--rescore', type=Path, help='只重算保存报告，不调用模型')
    args = parser.parse_args()
    cases, docs = load_dataset()
    reviews = json.loads(args.refusal_review.read_text(encoding='utf-8')) if args.refusal_review else {}
    if args.rescore:
        report = json.loads(args.rescore.read_text(encoding='utf-8'))
        if report['cases_sha256'] != fingerprint(CASES) or report['corpus_sha256'] != fingerprint(CORPUS):
            raise ValueError('报告与当前评测集或语料清单不匹配')
        if not args.refusal_review:
            reviews = report.get('refusal_reviews', {})
        output = args.output or args.rescore
    else:
        output = args.output or ROOT / f'eval/week4_{args.mode}_report.json'
        report = {'executed_at_utc': datetime.now(timezone.utc).isoformat(), 'mode': args.mode,
                  'cases_sha256': fingerprint(CASES), 'corpus_sha256': fingerprint(CORPUS),
                  'implementation_sha256': {str(p.relative_to(ROOT)): fingerprint(p) for p in (
                      ROOT / 'backend/services/agent_service.py', ROOT / 'backend/services/agent_router.py',
                      ROOT / 'backend/prompt/agent_prompts.py', ROOT / 'backend/prompt/process_prompts.py')},
                  'retrieval_mode': args.retrieval_mode, 'top_k': args.top_k, 'results': [], 'model': None}
        with tempfile.TemporaryDirectory(prefix='enterprise-week4-', ignore_cleanup_errors=True) as directory:
            configure_runtime(Path(directory), args.retrieval_mode)
            prepare_corpus(docs)
            client = None
            if args.mode == 'live':
                from backend.llm.client import get_client
                client = get_client().model_copy(update={'request_timeout': 60, 'max_retries': 0})
                report['model'] = client.model_name
                report['generation'] = {'temperature': client.temperature, 'timeout_seconds': 60, 'max_retries': 0}
            for case in cases:
                request_id = str(uuid4())
                started = time.perf_counter()
                try:
                    result = (asyncio.run(live_case(case, client, request_id, args.top_k))
                              if args.mode == 'live' else offline_case(case, args.top_k))
                except Exception as exc:
                    result = {'error': type(exc).__name__, 'answer': None, 'chunks': [], 'trace': []}
                result.update(id=case['id'], request_id=request_id, mode=args.mode,
                              duration_ms=round((time.perf_counter()-started)*1000, 2))
                report['results'].append(result)
                print(f"{case['id']}: {result.get('error') or 'recorded'}", flush=True)
            # 仓储层 sqlite 连接在上下文提交后由 GC 释放；Windows 清理前先关闭悬空连接。
            gc.collect()
    write_report(report, cases, output, reviews)
    print(f"Report: {output}", flush=True)
    if report['errors']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()

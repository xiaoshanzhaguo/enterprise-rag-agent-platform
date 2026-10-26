"""第四周指标：只读取实际输出，不将检索上下文当作生成答案。"""

import hashlib
import re
import unicodedata


def normalize(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).casefold()


def answer_hash(answer):
    return hashlib.sha256(answer.encode("utf-8")).hexdigest()


def score_case(case, result, review=None):
    chunks = result.get("chunks", [])
    expected_file = (case.get("expected_source") or "").split("/")[-1]
    evidence_case = bool(expected_file)
    answer = result.get("answer")
    error = bool(result.get("error"))
    # 只有实际召回的精确 chunk 标签才算有效引用，凭空写对文件名不算。
    citations = re.findall(r"\[来源\s*[:：]\s*([^\]]+)\]", answer or "")
    actual_labels = {f"{c['file_name']}#chunk-{c['chunk_id']}" for c in chunks}
    citation_hit = any(
        label.strip() in actual_labels and label.strip().split("#chunk-")[0] == expected_file
        for label in citations
    )
    # 不把引用文件名里的关键词计入答案正文覆盖率。
    body = normalize(re.sub(r"\[来源\s*[:：][^\]]+\]", "", answer or ""))
    keyword_hits = [not error and any(normalize(word) in body for word in group) for group in case["keyword_groups"]]
    answer_available = result.get("mode") == "live"
    extraction = result.get("extraction") or {}
    field_results = None
    if case["category"] == "process" and answer_available:
        fields = extraction.get("fields", {})
        field_results = {
            name: not error and extraction.get("status") == "extracted" and (
                fields.get(name) is None if value is None else
                isinstance(fields.get(name), str) and normalize(fields[name]) == normalize(value)
            ) for name, value in case["expected_fields"].items()
        }
    refusal_correct = None
    if case["should_reject"] and answer_available:
        if error:
            refusal_correct = False
        elif review is not None:
            if review.get("answer_sha256") != answer_hash(answer or ""):
                raise ValueError(f"拒答复核与实际答案不匹配：{case['id']}")
            if type(review.get("correct")) is not bool or not review.get("reason"):
                raise ValueError(f"拒答复核缺少结论或理由：{case['id']}")
            refusal_correct = review["correct"]
    return {
        "retrieval_hit": (not error and any(c["file_name"] == expected_file for c in chunks)) if evidence_case else None,
        "citation_hit": (not error and citation_hit) if evidence_case and answer_available else None,
        "keyword_hits": keyword_hits if evidence_case and answer_available else None,
        "refusal_marker": ("知识库中没有找到依据" in body and not error) if case["should_reject"] and answer_available else None,
        "refusal_correct": refusal_correct,
        "route_correct": not error and result.get("route") == case["expected_route"],
        "process_prediction": result.get("route") == "process_apply" and not error,
        "field_results": field_results,
    }


def ratio(values):
    measured = [v for v in values if v is not None]
    return {"passed": sum(measured), "total": len(measured),
            "rate": round(sum(measured) / len(measured), 4) if measured else None}


def summarize(cases, results, reviews=None):
    reviews = reviews or {}
    if len(results) != len(cases) or {r['id'] for r in results} != {c['id'] for c in cases}:
        raise ValueError("报告必须包含全部用例且编号唯一")
    by_id = {r['id']: r for r in results}
    scores = {c['id']: score_case(c, by_id[c['id']], reviews.get(c['id'])) for c in cases}
    metrics = {key: ratio([s[key] for s in scores.values()]) for key in
               ("retrieval_hit", "citation_hit", "refusal_correct", "route_correct")}
    metrics['keyword_coverage'] = ratio([hit for s in scores.values() for hit in (s['keyword_hits'] or [])])
    confusion = dict.fromkeys(('tp', 'fp', 'fn', 'tn'), 0)
    for c in cases:
        expected = c['expected_route'] == 'process_apply'
        predicted = scores[c['id']]['process_prediction']
        confusion['tp' if expected and predicted else 'fn' if expected else 'fp' if predicted else 'tn'] += 1
    metrics['process_intent'] = {**ratio([
        not by_id[c['id']].get('error') and scores[c['id']]['process_prediction'] == (c['expected_route'] == 'process_apply')
        for c in cases]), **confusion}
    return {'metrics': metrics, 'scores': scores,
            'errors': sum(bool(r.get('error')) for r in results),
            'pending_refusal_reviews': [c['id'] for c in cases if c['should_reject']
                                        and by_id[c['id']]['mode'] == 'live' and scores[c['id']]['refusal_correct'] is None]}

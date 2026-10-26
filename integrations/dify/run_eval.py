"""调用真实 Dify Chat API，保存回答及来源，未配置时不生成假验收结果。"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from dotenv import dotenv_values
import httpx


ROOT = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "acceptance_result.json")
    args = parser.parse_args()
    config = {**dotenv_values(ROOT / ".env.local"), **os.environ}
    key = config.get("DIFY_APP_API_KEY")
    if not key:
        raise SystemExit("请先在 integrations/dify/.env.local 配置 DIFY_APP_API_KEY。")
    base = config.get("DIFY_API_BASE_URL", "http://127.0.0.1:18080/v1").rstrip("/")
    cases = [json.loads(line) for line in (ROOT / "eval_cases.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    results = []
    with httpx.Client(timeout=120, trust_env=False, follow_redirects=False) as client:
        for case in cases:
            response = client.post(
                base + "/chat-messages", headers={"Authorization": f"Bearer {key}"},
                json={"inputs": {}, "query": case["query"], "response_mode": "blocking", "user": "day22-acceptance"},
            )
            if response.status_code != 200:
                raise SystemExit(f"Dify 请求失败：HTTP {response.status_code}，未写入完成报告。")
            payload = response.json()
            answer = payload.get("answer", "")
            sources = [item.get("document_name") for item in payload.get("metadata", {}).get("retriever_resources", [])]
            keywords_ok = bool(answer) and all(word in answer for word in case["expected_keywords"])
            source_ok = case["should_refuse"] or case["source"] in sources
            results.append({"id": case["id"], "query": case["query"], "answer": answer, "sources": sources, "passed": keywords_ok and source_ok})
            print(f"{case['id']}: {'PASS' if keywords_ok and source_ok else 'REVIEW'}", flush=True)
    report = {
        "executed_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "real_dify_api", "passed": all(item["passed"] for item in results), "results": results,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not report["passed"]:
        raise SystemExit("部分用例需人工核对，不能标记整体验收完成。")


if __name__ == "__main__":
    main()

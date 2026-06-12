"""
RAG 评测脚本。

职责：
1. 读取 eval/questions.jsonl 中的企业知识库评测问题。
2. 构建独立的评测数据库，避免污染 data/app.db。
3. 写入一份固定企业知识库文档，并基于当前 RAG 检索链路进行评测。
4. 计算检索命中率、引用命中率、关键词包含率和无依据拒答准确率。
5. 生成 eval/report.md，便于后续截图展示和复盘失败案例。

说明：
- 默认使用 keyword 检索，保证 python eval/run_eval.py 能稳定快速运行。
- 如需评测向量检索，可在运行前设置 EVAL_RETRIEVAL_MODE=vector。
- 评测脚本只评估 RAG 检索与引用上下文，不调用大模型生成答案。
"""

from __future__ import annotations

# 导入 JSON 模块，用于读取 jsonl 评测问题
import json
# 导入操作系统环境变量模块，用于在导入项目配置前设置评测专用配置
import os
# 导入高级文件操作模块，用于删除评测生成的旧向量库目录
import shutil
# 导入系统模块，用于把项目根目录加入 Python 模块搜索路径
import sys
# 导入临时目录工具，用于获取系统临时目录，存放评测数据库和向量库
import tempfile
# 导入时间模块，用于生成报告时间
from datetime import datetime
# 导入路径工具，用于定位 eval 目录、项目根目录和报告文件
from pathlib import Path
# 导入类型标注工具，提升评测数据结构可读性
from typing import Any


# 当前 eval 目录
EVAL_DIR = Path(__file__).resolve().parent
# 项目根目录
PROJECT_ROOT = EVAL_DIR.parent
# 评测问题文件路径
QUESTIONS_PATH = EVAL_DIR / "questions.jsonl"
# 评测报告输出路径
REPORT_PATH = EVAL_DIR / "report.md"
# 评测运行时目录。放在系统临时目录，避免影响项目业务数据
EVAL_RUNTIME_DIR = Path(tempfile.gettempdir()) / "ai_assistant_platform_eval"
# 评测数据库路径。每次运行会重建该文件
EVAL_DB_PATH = EVAL_RUNTIME_DIR / "eval_app.db"
# 评测向量库路径。每次运行会重建该目录
EVAL_VECTOR_DIR = EVAL_RUNTIME_DIR / "chroma"
# 评测使用的固定 session_id，便于数据库中定位本轮记录
EVAL_SESSION_ID = "eval-session"
# 评测使用的固定文档名，需要和 questions.jsonl 中的 expected_source 保持一致
EVAL_FILE_NAME = "企业知识库.md"
# 默认检索片段数量
DEFAULT_TOP_K = 3


# 固定企业知识库文本块。这里直接按 chunk 写入，保证 expected_source 与 chunk_id 稳定对应。
EVAL_KNOWLEDGE_CHUNKS = [
    "员工手册：公司试用期为三个月。员工每年有五天带薪年假。公司不提供免费晚餐。远程办公需要提前向直属主管申请。",
    "信息安全制度：员工入职后需要在七天内完成信息安全培训。公司账号必须开启双因素认证。笔记本电脑遗失后需要在二十四小时内向IT服务台报备。",
    "财务报销制度：差旅报销需要在出差结束后十五个工作日内提交。单张发票金额超过五千元需要部门负责人审批。打车报销需要上传行程单和发票。",
    "考勤制度：弹性上班时间为早上八点到十点。每月迟到超过三次需要主管面谈。请假需要提前在系统提交申请。",
    "采购流程：采购金额超过一万元需要走采购评审。紧急采购需要补充业务紧急性说明。供应商准入需要完成合规审核。",
    "客服SLA：P1故障需要十五分钟内响应，四小时内给出阶段性处理结果。普通问题需要两个工作日内回复。客户投诉需要记录在工单系统中。",
]


def configure_eval_environment() -> None:
    """
    配置评测运行所需环境变量。

    函数说明：
    1. 将项目根目录加入 sys.path，确保可以导入 backend 模块。
    2. 将数据库地址设置为系统临时目录下的 eval_app.db，避免影响 data/app.db。
    3. 将向量库目录设置为系统临时目录下的 chroma，避免影响 data/chroma。
    4. 默认使用 keyword 检索；如需向量评测，可通过 EVAL_RETRIEVAL_MODE 覆盖。

    :return: None
    """
    # 将项目根目录加入模块搜索路径，保证脚本从 eval 目录运行时也能导入 backend
    if str(PROJECT_ROOT) not in sys.path:
        # 将项目根目录放到Python模块搜索路径的最前面
        sys.path.insert(0, str(PROJECT_ROOT))

    # 固定使用评测专用 SQLite 数据库
    os.environ["DATABASE_URL"] = "sqlite:///" + str(EVAL_DB_PATH).replace("\\", "/")
    # 固定使用评测专用向量库目录
    os.environ["VECTOR_STORE_DIR"] = str(EVAL_VECTOR_DIR)
    # 默认 keyword，保证评测命令不依赖本地 embedding 模型加载
    os.environ.setdefault("RAG_RETRIEVAL_MODE", os.getenv("EVAL_RETRIEVAL_MODE", "keyword"))
    # 允许向量检索失败后回退关键词，保持评测链路和前端体验一致
    os.environ.setdefault("RAG_KEYWORD_FALLBACK_ENABLED", "true")


def reset_eval_database() -> None:
    """
    重置评测数据库。

    函数说明：
    1. 删除上一次生成的评测数据库。
    2. 删除 SQLite 可能残留的 journal 文件。
    3. 删除上一次生成的评测向量库。
    4. 不删除业务数据库 data/app.db。

    :return: None
    """
    # 创建评测运行时目录
    EVAL_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    # 删除评测数据库文件，保证每次报告都来自干净数据
    EVAL_DB_PATH.unlink(missing_ok=True)
    # 删除 SQLite journal 文件，避免异常退出后影响下一次评测
    EVAL_DB_PATH.with_name(f"{EVAL_DB_PATH.name}-journal").unlink(missing_ok=True)
    # 删除评测向量库目录，避免上一次评测的向量影响本次结果
    if EVAL_VECTOR_DIR.exists():
        shutil.rmtree(EVAL_VECTOR_DIR)


def load_eval_cases(path: Path) -> list[dict[str, Any]]:
    """
    读取 jsonl 评测集。

    函数说明：
    1. 逐行读取 questions.jsonl。
    2. 跳过空行。
    3. 校验每条评测数据至少包含 question、expected_source、must_contain。

    :param path: 评测问题文件路径
    :return: 评测用例列表
    """
    # 存放解析后的评测用例
    cases: list[dict[str, Any]] = []

    # 逐行读取 jsonl 文件
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            # 去掉首尾空白
            stripped_line = line.strip()
            # 空行直接跳过
            if not stripped_line:
                continue
            # 将当前行解析为字典
            case = json.loads(stripped_line)

            # 校验必要字段，避免评测时才出现难定位错误
            for field_name in ("question", "expected_source", "must_contain"):
                if field_name not in case:
                    raise ValueError(f"{path} 第 {line_number} 行缺少字段：{field_name}")

            # 将合法用例加入列表
            cases.append(case)

    # 返回所有评测用例
    return cases


def prepare_eval_knowledge_base() -> None:
    """
    初始化评测知识库。

    函数说明：
    1. 初始化 SQLite 表结构。
    2. 将固定企业知识库 chunks 写入 documents 和 document_chunks。
    3. 如果当前配置为 vector，会同步写入 eval/chroma。

    :return: None
    """
    # 延迟导入，确保 configure_eval_environment() 先完成环境变量设置
    from backend.db.init_db import init_database
    from backend.rag.store import save_document_chunks

    # 创建评测库表结构
    init_database()
    # 写入固定评测文档
    save_document_chunks(
        session_id=EVAL_SESSION_ID,
        file_name=EVAL_FILE_NAME,
        chunks=EVAL_KNOWLEDGE_CHUNKS,
    )


def build_source_label(chunk: dict[str, Any]) -> str:
    """
    构造评测使用的来源标识。

    函数说明：
    1. 使用 chunk 的 file_name 和 chunk_id 拼出来源。
    2. 与 RAG 回答引用格式保持一致。

    :param chunk: RAG 命中片段
    :return: 来源字符串，例如 企业知识库.md#chunk-1
    """
    # 读取文件名
    file_name = chunk.get("file_name") or EVAL_FILE_NAME
    # 读取 chunk 编号
    chunk_id = chunk.get("chunk_id")
    # 返回统一来源格式
    return f"{file_name}#chunk-{chunk_id}"


def evaluate_case(case: dict[str, Any], top_k: int) -> dict[str, Any]:
    """
    评测单条问题。

    函数说明：
    1. 调用 RAG 检索链路获取命中 chunks 和检索状态。
    2. 将本次查询和命中结果写入 rag_queries / rag_hits。
    3. 判断 expected_source 是否命中。
    4. 判断 must_contain 关键词是否出现在命中文本中。
    5. 判断无依据问题是否被正确拒答。

    :param case: 单条评测用例
    :param top_k: 检索片段数量
    :return: 单条评测结果
    """
    # 延迟导入，确保脚本启动时已完成评测环境配置
    from backend.db.repository import save_rag_query_with_hits
    from backend.rag.service import (
        NO_RAG_EVIDENCE_MESSAGE,
        build_rag_context_from_chunks,
        retrieve_rag_chunks_with_mode,
    )

    # 读取问题文本
    question = case["question"]
    # 读取期望来源；无依据问题允许为 None
    expected_source = case.get("expected_source")
    # 读取必须包含的关键词列表
    must_contain = case.get("must_contain", [])
    # 判断当前用例是否是无依据拒答用例
    # 如果 case 明确标记 should_reject=True，或者 expected_source 为空，那么这个问题就应该拒答
    should_reject = bool(case.get("should_reject")) or not expected_source

    # 执行 RAG 检索，并拿到实际检索状态
    matched_chunks, retrieval_mode = retrieve_rag_chunks_with_mode(
        session_id=EVAL_SESSION_ID,
        query=question,
        top_k=top_k,
    )

    # 将本次评测查询写入数据库日志，满足后续可查历史记录的要求
    save_rag_query_with_hits(
        session_id=EVAL_SESSION_ID,
        query_text=question,
        top_k=top_k,
        matched_chunks=matched_chunks,
        retrieval_mode=retrieval_mode,
        mode="eval",
    )

    # 构造 prompt 上下文，用于判断引用来源和无依据提示是否可被模型使用
    context = build_rag_context_from_chunks(matched_chunks)
    # 生成当前命中的来源列表
    retrieved_sources = [build_source_label(chunk) for chunk in matched_chunks]
    # 拼接命中文本，便于关键词包含检查
    retrieved_text = "\n".join(chunk.get("text", "") for chunk in matched_chunks)

    # 有依据问题：expected_source 出现在命中来源中即认为检索命中
    retrieval_hit = bool(expected_source and expected_source in retrieved_sources)
    # 有依据问题：expected_source 出现在上下文中即认为引用命中
    citation_hit = bool(expected_source and expected_source in context)
    # 有依据问题：must_contain 中所有关键词都出现在命中文本中才认为关键词包含通过
    keyword_hit = all(keyword in retrieved_text for keyword in must_contain)
    # 无依据问题：没有命中片段，并且上下文包含明确无依据提示
    reject_correct = should_reject and not matched_chunks and NO_RAG_EVIDENCE_MESSAGE in context

    # 收集失败原因，报告中会集中展示
    failure_reasons = []
    # 来源没命中
    if not should_reject and not retrieval_hit:
        failure_reasons.append("expected_source 未进入 top_k")
    # 引用上下文没命中
    if not should_reject and not citation_hit:
        failure_reasons.append("引用上下文未包含 expected_source")
    # 关键词没包含完整
    if not should_reject and not keyword_hit:
        failure_reasons.append("must_contain 未全部出现在命中文本中")
    # 无依据问题没拒答
    if should_reject and not reject_correct:
        failure_reasons.append("无依据问题未正确拒答")

    # 返回单条评测结果
    return {
        "question": question,
        "expected_source": expected_source,
        "must_contain": must_contain,
        "should_reject": should_reject,
        "retrieval_mode": retrieval_mode,
        "retrieved_sources": retrieved_sources,
        "retrieval_hit": retrieval_hit,
        "citation_hit": citation_hit,
        "keyword_hit": keyword_hit,
        "reject_correct": reject_correct,
        "failure_reasons": failure_reasons,
    }


def calculate_rate(success_count: int, total_count: int) -> float:
    """
    计算百分比指标。

    :param success_count: 成功数量
    :param total_count: 总数量
    :return: 0 到 100 之间的百分比
    """
    # 分母为 0 时返回 0，避免除零错误
    if total_count <= 0:
        return 0.0
    # 返回百分比，保留两位小数
    return round(success_count / total_count * 100, 2)


def build_report(cases: list[dict[str, Any]], results: list[dict[str, Any]], top_k: int) -> str:
    """
    构造 Markdown 评测报告。

    函数说明：
    1. 统计四类核心指标。
    2. 输出评测配置和数据库位置。
    3. 输出失败案例，方便定位 RAG 检索弱点。

    :param cases: 原始评测用例列表
    :param results: 单条评测结果列表
    :param top_k: 本次检索片段数量
    :return: Markdown 报告文本
    """
    # 延迟导入配置，保证读取到评测脚本设置后的值
    from backend.config import settings

    # 有依据用例
    evidence_results = [result for result in results if not result["should_reject"]]
    # 无依据用例
    reject_results = [result for result in results if result["should_reject"]]

    # 计算指标分子
    retrieval_hit_count = sum(1 for result in evidence_results if result["retrieval_hit"])
    citation_hit_count = sum(1 for result in evidence_results if result["citation_hit"])
    keyword_hit_count = sum(1 for result in evidence_results if result["keyword_hit"])
    reject_correct_count = sum(1 for result in reject_results if result["reject_correct"])

    # 收集失败用例
    failed_results = [result for result in results if result["failure_reasons"]]

    # 报告头部
    lines = [
        "# RAG 评测报告",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 问题总数：{len(cases)}",
        f"- 有依据问题数：{len(evidence_results)}",
        f"- 无依据问题数：{len(reject_results)}",
        f"- top_k：{top_k}",
        f"- 检索模式：{settings.rag_retrieval_mode}",
        f"- 评测数据库：{EVAL_DB_PATH}",
        "",
        "## 指标汇总",
        "",
        "| 指标 | 结果 | 说明 |",
        "| --- | ---: | --- |",
        f"| 检索命中率 | {calculate_rate(retrieval_hit_count, len(evidence_results))}% | expected_source 出现在 top_k 命中来源中 |",
        f"| 引用命中率 | {calculate_rate(citation_hit_count, len(evidence_results))}% | prompt 引用上下文包含 expected_source |",
        f"| 关键词包含率 | {calculate_rate(keyword_hit_count, len(evidence_results))}% | 命中文本包含 must_contain 全部关键词 |",
        f"| 无依据拒答准确率 | {calculate_rate(reject_correct_count, len(reject_results))}% | 无依据问题无命中且上下文包含拒答提示 |",
        "",
        "## 失败案例",
        "",
    ]

    # 如果没有失败案例，明确写出全部通过
    if not failed_results:
        lines.append("本次评测没有失败案例。")
    else:
        # 输出失败案例表格
        lines.extend([
            "| 问题 | 期望来源 | 实际来源 | 检索方式 | 失败原因 |",
            "| --- | --- | --- | --- | --- |",
        ])
        for result in failed_results:
            # 将实际来源列表转成可读字符串。有来源就显示来源，无来源就显示“无命中”
            actual_sources = ", ".join(result["retrieved_sources"]) or "无命中"
            # 将失败原因转成可读字符串
            reasons = "；".join(result["failure_reasons"])
            # 写入表格行
            lines.append(
                f"| {result['question']} | {result.get('expected_source') or '无依据'} | "
                f"{actual_sources} | {result['retrieval_mode']} | {reasons} |"
            )

    # 追加全部用例概览，便于报告截图和人工复核
    lines.extend([
        "",
        "## 全部用例概览",
        "",
        "| 问题 | 期望来源 | 实际来源 | 检索方式 | 结果 |",
        "| --- | --- | --- | --- | --- |",
    ])

    for result in results:
        # 将实际来源列表转成可读字符串
        actual_sources = ", ".join(result["retrieved_sources"]) or "无命中"
        # 没有失败原因则视为通过
        status = "通过" if not result["failure_reasons"] else "失败"
        # 写入概览表格
        lines.append(
            f"| {result['question']} | {result.get('expected_source') or '无依据'} | "
            f"{actual_sources} | {result['retrieval_mode']} | {status} |"
        )

    # 返回最终 Markdown 文本
    return "\n".join(lines) + "\n"


def main() -> None:
    """
    执行完整 RAG 评测流程。

    函数说明：
    1. 配置评测环境。
    2. 重置评测数据库。
    3. 读取评测用例。
    4. 准备评测知识库。
    5. 执行所有用例并生成报告。

    :return: None
    """
    # 设置评测专用环境变量
    configure_eval_environment()
    # 重置评测数据库
    reset_eval_database()
    # 读取评测问题
    cases = load_eval_cases(QUESTIONS_PATH)
    # 准备固定知识库
    prepare_eval_knowledge_base()
    # 读取 top_k 配置
    top_k = int(os.getenv("EVAL_TOP_K", str(DEFAULT_TOP_K)))
    # 执行全部评测用例
    results = [evaluate_case(case, top_k=top_k) for case in cases]
    # 构造 Markdown 报告
    report = build_report(cases=cases, results=results, top_k=top_k)
    # 写入报告文件
    REPORT_PATH.write_text(report, encoding="utf-8")
    # 打印结果路径，方便命令行确认
    print(f"评测完成：{REPORT_PATH}")


if __name__ == "__main__":
    main()

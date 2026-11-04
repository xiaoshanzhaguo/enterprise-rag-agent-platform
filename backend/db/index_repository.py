"""索引任务的 SQLite 原子准备与条件更新；不执行模型、向量库或 HTTP 操作。"""

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
import sqlite3
from typing import Any
from uuid import UUID, uuid4

from backend.db.connection import get_connection
from backend.db.version_repository import find_duplicate
from backend.db.repository import (
    _prepare_document_storage_scope,
    _save_document_with_chunks_in_connection,
)


class IndexOperationConflict(ValueError):
    """状态版本、执行权或固定任务输入不匹配，调用方应刷新状态。"""


class IndexInputChanged(IndexOperationConflict):
    """当前持久化块与已保存的任务快照不一致。"""


@dataclass(frozen=True)
class PreparedIndex:
    document_id: int
    chunks: list[dict[str, Any]]
    operation: dict[str, Any] | None
    document_reused: bool


_METADATA_FIELDS = (
    "file_name", "knowledge_base_id", "knowledge_base_type", "department",
    "process_type", "process_status",
)
_PROFILE_FIELDS = {
    "profile_schema_version", "provider", "model", "collection_name",
    "endpoint_fingerprint", "vector_store_fingerprint", "embedding_dimension",
    "normalize_embeddings", "distance_metric",
}
_ERROR_MESSAGES = {
    "embedding_failed": "向量生成失败。",
    "embedding_timeout": "向量生成超时。",
    "embedding_count_mismatch": "生成向量数量与文本块不一致。",
    "collection_profile_unverified": "无法确认向量集合配置。",
    "upsert_failed": "向量写入失败。",
    "upsert_outcome_unknown": "向量写入结果未确认。",
    "index_input_changed": "索引输入与任务快照不一致。",
    "index_configuration_changed": "当前索引配置与任务目标不一致。",
    "attempt_budget_exceeded": "索引执行预算已耗尽。",
    "interrupted": "上次索引执行已中断。",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _validate_profile(profile: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(profile, dict) or set(profile) != _PROFILE_FIELDS:
        raise ValueError("索引目标必须使用完整的非敏感 profile 字段。")
    if type(profile["profile_schema_version"]) is not int or profile["profile_schema_version"] != 2:
        raise ValueError("不支持的索引目标结构版本。")
    if profile["provider"] not in ("local", "openai"):
        raise ValueError("不支持的向量提供方。")
    for field in ("model", "collection_name"):
        value = profile[field]
        if not isinstance(value, str) or not value.strip() or len(value) > 200 or "://" in value:
            raise ValueError("模型和集合标识不合法。")
    for field in ("endpoint_fingerprint", "vector_store_fingerprint"):
        value = profile[field]
        if value is None and field == "endpoint_fingerprint" and profile["provider"] == "local":
            continue
        if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
            raise ValueError("索引位置只允许非敏感 SHA-256 标识。")
    dimension = profile["embedding_dimension"]
    if dimension is not None and (type(dimension) is not int or dimension <= 0):
        raise ValueError("向量维度必须为正整数或未知。")
    if type(profile["normalize_embeddings"]) is not bool:
        raise ValueError("客户端归一化策略必须明确。")
    if profile["distance_metric"] not in (None, "l2", "cosine", "ip"):
        raise ValueError("距离度量不合法。")
    return json.loads(_json(profile))


def _compatible_profile(stored: dict[str, Any], requested: dict[str, Any]) -> bool:
    # 缺省的运行时参数不覆盖已绑定值；未知值的首次绑定留给后续执行阶段。
    return all(
        requested[key] == stored[key]
        or (key in ("embedding_dimension", "distance_metric") and requested[key] is None)
        for key in _PROFILE_FIELDS
    )


def _manifest(storage_session_id: str, chunks: list[dict[str, Any]]) -> dict[str, Any]:
    if not chunks:
        raise IndexInputChanged("文档没有可用的持久化文本块。")
    if any(any(chunk[key] != chunks[0][key] for key in _METADATA_FIELDS) for chunk in chunks):
        raise IndexInputChanged("同文档块的来源元数据不一致。")
    return {
        "manifest_schema_version": 1,
        "document_id": chunks[0]["document_id"],
        "storage_session_id": storage_session_id,
        "metadata": {key: chunks[0][key] for key in _METADATA_FIELDS},
        "chunks": [
            {"db_chunk_id": chunk["db_chunk_id"], "chunk_index": chunk["chunk_id"],
             "text_sha256": hashlib.sha256(chunk["text"].encode("utf-8")).hexdigest()}
            for chunk in chunks
        ],
    }


def _read_chunks(connection: sqlite3.Connection, document_id: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        """SELECT c.id AS db_chunk_id, c.document_id, c.file_name, c.chunk_index AS chunk_id,
                  c.chunk_text AS text, c.text_length, c.created_at, d.knowledge_base_id,
                  d.knowledge_base_type, d.department, d.process_type, d.process_status
           FROM document_chunks c JOIN documents d ON d.id = c.document_id
           WHERE d.id = ? ORDER BY c.chunk_index, c.id""", (document_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _get_operation(connection: sqlite3.Connection, operation_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        """SELECT t.*, d.file_name, d.knowledge_base_id, d.session_id AS storage_session_id, d.lifecycle_state
           FROM rag_index_tasks t JOIN documents d ON d.id = t.document_id
           WHERE t.operation_id = ?""", (operation_id,),
    ).fetchone()
    if row is None:
        return None
    return _decode_operation(row)


def _decode_operation(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["input_manifest"] = json.loads(result.pop("input_manifest_json"))
    profile = result.pop("target_profile_json")
    result["target_profile"] = json.loads(profile) if profile is not None else None
    result["retry_count"] = max(result["attempt_count"] - 1, 0)
    result["execution_claimed"] = result["attempt_token"] is not None
    return result


def _require_operation(connection: sqlite3.Connection, operation_id: str) -> dict[str, Any]:
    operation = _get_operation(connection, operation_id)
    if operation is None:
        raise LookupError("索引任务不存在。")
    return operation


def _verify_input(connection: sqlite3.Connection, operation: dict[str, Any]) -> list[dict[str, Any]]:
    if operation.get('lifecycle_state', 'active') != 'active':
        raise IndexOperationConflict('文档正在删除，禁止执行索引。')
    chunks = _read_chunks(connection, operation["document_id"])
    actual = _manifest(operation["storage_session_id"], chunks)
    if (operation["file_name"] != chunks[0]["file_name"]
            or len(chunks) != operation["expected_chunk_count"]
            or _digest(actual) != operation["input_sha256"]
            or _digest(operation["input_manifest"]) != operation["input_sha256"]):
        raise IndexInputChanged("索引输入与任务快照不一致。")
    if operation["target_profile"] is not None:
        expected_target = _digest({"input_sha256": operation["input_sha256"],
                                   "profile": operation["target_profile"]})
        if expected_target != operation["target_sha256"]:
            raise IndexInputChanged("索引目标摘要与任务快照不一致。")
    return chunks


def prepare_document_index(
    session_id: str, file_name: str | None, chunks: list[str], *,
    retrieval_mode: str, target_profile: dict[str, Any] | None = None,
    mode: str = "unknown", source_type: str = "upload", knowledge_base_id: str | None = None,
    knowledge_base_type: str = "general", department: str | None = None,
    process_type: str | None = None, process_status: str = "active",
    logical_document_id: str | None = None,
) -> PreparedIndex:
    """原子保存/复用文档、块和任务；父级记录可独立提交，不执行向量化。"""
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("会话 ID 不能为空。")
    if not isinstance(chunks, list) or not chunks or any(not isinstance(c, str) or not c.strip() for c in chunks):
        raise ValueError("需要非空文本块列表。")
    if retrieval_mode not in ("keyword", "vector"):
        raise ValueError("索引模式不合法。")
    profile = _validate_profile(target_profile) if retrieval_mode == "vector" else None
    if retrieval_mode == "keyword" and target_profile is not None:
        raise ValueError("关键词模式不接受向量目标。")
    normalized_id, storage_id, document_kb_id = _prepare_document_storage_scope(
        session_id, mode=mode, knowledge_base_id=knowledge_base_id,
    )
    with closing(get_connection()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = find_duplicate(connection, storage_id, document_kb_id, file_name,
            hashlib.sha256("\n\n".join(chunks).encode()).hexdigest(), knowledge_base_type,
            department, process_type, process_status, logical_document_id)
        task_row = connection.execute(
            "SELECT operation_id FROM rag_index_tasks WHERE document_id = ?", (existing["id"],),
        ).fetchone() if existing else None
        operation = _require_operation(connection, task_row["operation_id"]) if task_row else None
        if existing and connection.execute('SELECT lifecycle_state FROM documents WHERE id = ?',
                                            (existing['id'],)).fetchone()[0] != 'active':
            raise IndexOperationConflict('文档正在删除，不能重复上传。')
        if operation:
            stored_chunks = _verify_input(connection, operation)
            requested_chunks = [dict(chunk, file_name=chunk["file_name"], knowledge_base_id=document_kb_id,
                                     knowledge_base_type=knowledge_base_type or "general", department=department,
                                     process_type=process_type, process_status=process_status or "active")
                                for chunk in stored_chunks]
            if ([c["text"] for c in stored_chunks] != chunks
                    or _digest(_manifest(storage_id, requested_chunks)) != operation["input_sha256"]):
                raise IndexOperationConflict("已跟踪任务的输入不能在重复上传时改变。")
            if profile is not None and operation["target_profile"] is not None:
                if not _compatible_profile(operation["target_profile"], profile):
                    raise IndexOperationConflict("重复上传的索引目标与原任务不一致。")
        saved, reused = _save_document_with_chunks_in_connection(
            connection, session_id=session_id, file_name=file_name, chunks=chunks,
            storage_session_id=storage_id, normalized_knowledge_base_id=normalized_id,
            document_knowledge_base_id=document_kb_id, source_type=source_type,
            knowledge_base_type=knowledge_base_type, department=department,
            process_type=process_type, process_status=process_status,
            logical_document_id=logical_document_id, managed=True,
        )
        document_id = saved[0]["document_id"]
        # 历史 keyword 文档不凭空纳管成 not_requested；没有任务即 legacy/unknown。
        if reused and operation is None and retrieval_mode == "keyword":
            return PreparedIndex(document_id, saved, None, True)
        now = _now()
        if operation is None:
            manifest = _manifest(storage_id, saved)
            input_hash = _digest(manifest)
            operation_id = str(uuid4())
            connection.execute(
                """INSERT INTO rag_index_tasks
                   (operation_id, document_id, status, input_manifest_json, input_sha256,
                    target_profile_json, target_sha256, expected_chunk_count, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (operation_id, document_id, "pending" if profile is not None else "not_requested",
                 _json(manifest), input_hash, _json(profile) if profile is not None else None,
                 _digest({"input_sha256": input_hash, "profile": profile}) if profile is not None else None,
                 len(saved), now, now),
            )
        else:
            operation_id = operation["operation_id"]
            if operation["status"] == "not_requested" and profile is not None:
                cursor = connection.execute(
                    """UPDATE rag_index_tasks SET status = 'pending', target_profile_json = ?,
                       target_sha256 = ?, state_version = state_version + 1, updated_at = ?
                       WHERE operation_id = ? AND status = 'not_requested' AND state_version = ?""",
                    (_json(profile), _digest({"input_sha256": operation["input_sha256"], "profile": profile}),
                     now, operation_id, operation["state_version"]),
                )
                if cursor.rowcount != 1:
                    raise IndexOperationConflict("索引请求状态转换未生效。")
        return PreparedIndex(document_id, saved, _require_operation(connection, operation_id), reused)


def get_index_operation(operation_id: str) -> dict[str, Any] | None:
    """只读回执，含供后续 Service 使用的内部 token；未来 API 必须按 schema 过滤。"""
    with closing(get_connection()) as connection:
        return _get_operation(connection, operation_id)


def get_document_index_operations(document_ids: list[int]) -> dict[int, dict[str, Any]]:
    """状态列表按已授权的文档 ID 批量读取任务，不连接 Chunk 或启动执行。"""
    if not document_ids:
        return {}
    if any(type(document_id) is not int or document_id <= 0 for document_id in document_ids):
        raise ValueError("文档 ID 必须是正整数。")
    operations = {}
    with closing(get_connection()) as connection:
        connection.execute("BEGIN")
        for start in range(0, len(document_ids), 500):
            batch = document_ids[start:start + 500]
            rows = connection.execute(
                "SELECT t.*, d.file_name, d.knowledge_base_id, d.session_id AS storage_session_id, d.lifecycle_state "
                "FROM rag_index_tasks t JOIN documents d ON d.id = t.document_id "
                "WHERE t.document_id IN (" + ",".join("?" for _ in batch) + ")", batch,
            ).fetchall()
            operations.update({row["document_id"]: _decode_operation(row) for row in rows})
    return operations


def _validate_version(version: int) -> None:
    if type(version) is not int or version <= 0:
        raise ValueError("状态版本必须是正整数。")


def claim_index_operation(
    operation_id: str, expected_state_version: int, owner_instance_id: str,
) -> dict[str, Any]:
    """只有匹配版本且未领取的 pending 能取得执行权；不执行任何外部调用。"""
    _validate_version(expected_state_version)
    if not isinstance(owner_instance_id, str) or not owner_instance_id.strip():
        raise ValueError("执行实例标识不能为空。")
    now = _now()
    with closing(get_connection()) as connection, connection:
        cursor = connection.execute(
            """UPDATE rag_index_tasks SET attempt_token = ?, owner_instance_id = ?, claimed_at = ?,
               attempt_count = attempt_count + 1, state_version = state_version + 1, updated_at = ?
               WHERE operation_id = ? AND status = 'pending' AND attempt_token IS NULL AND state_version = ?
               AND EXISTS (SELECT 1 FROM documents d WHERE d.id = rag_index_tasks.document_id AND d.lifecycle_state = 'active')""",
            (str(uuid4()), owner_instance_id, now, now, operation_id, expected_state_version),
        )
        if cursor.rowcount != 1:
            raise IndexOperationConflict("任务不存在、版本已变更或已被领取。")
        return _require_operation(connection, operation_id)


def bind_index_profile(
    operation_id: str, attempt_token: str, expected_state_version: int,
    bindings: dict[str, Any],
) -> dict[str, Any]:
    """当前 attempt 仅能首次绑定维度/度量；同版本同值幂等，旧版本始终拒绝。"""
    if not isinstance(operation_id, str):
        raise ValueError("操作 ID 必须是 UUID 字符串。")
    try:
        operation_id = str(UUID(operation_id))
    except ValueError as exc:
        raise ValueError("操作 ID 必须是 UUID 字符串。") from exc
    _validate_version(expected_state_version)
    if not isinstance(attempt_token, str) or not attempt_token.strip():
        raise ValueError("需要当前执行 token。")
    if (not isinstance(bindings, dict) or not bindings
            or not set(bindings) <= {"embedding_dimension", "distance_metric"}
            or any(value is None for value in bindings.values())):
        raise ValueError("只允许绑定非空的向量维度或距离度量。")

    with closing(get_connection()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        operation = _require_operation(connection, operation_id)
        if (operation["status"] != "pending" or operation["attempt_token"] != attempt_token
                or operation["state_version"] != expected_state_version):
            raise IndexOperationConflict("任务状态、执行 token 或版本已变更。")
        _verify_input(connection, operation)
        stored = _validate_profile(operation["target_profile"])
        profile = _validate_profile(dict(stored, **bindings))
        if any(stored[key] is not None and stored[key] != value for key, value in bindings.items()):
            raise IndexOperationConflict("已绑定的索引参数不能被覆盖。")
        if profile == stored:
            return operation
        cursor = connection.execute(
            """UPDATE rag_index_tasks SET target_profile_json = ?, target_sha256 = ?,
               state_version = state_version + 1, updated_at = ?
               WHERE operation_id = ? AND status = 'pending' AND attempt_token = ? AND state_version = ?""",
            (_json(profile), _digest({"input_sha256": operation["input_sha256"], "profile": profile}),
             _now(), operation_id, attempt_token, expected_state_version),
        )
        if cursor.rowcount != 1:
            raise IndexOperationConflict("索引参数绑定未生效。")
        return _require_operation(connection, operation_id)


def recover_index_operation(
    operation_id: str, expected_state_version: int, *, execution_stopped_confirmed: bool = False,
) -> dict[str, Any]:
    """停写确认后的人工恢复：仅将指定版本的 claimed pending 转为 failed/interrupted。

    确认必须来自操作人员；它不是外部写入 fencing 或自动进程探测。
    不重试、不接管、不更改输入和 attempt_count；随后须显式 retry/claim。
    """
    if execution_stopped_confirmed is not True:
        raise ValueError("必须明确确认原执行进程及其他可能继续写入的实例已经停止。")
    if not isinstance(operation_id, str):
        raise ValueError("操作 ID 必须是 UUID 字符串。")
    try:
        operation_id = str(UUID(operation_id))
    except ValueError as exc:
        raise ValueError("操作 ID 必须是 UUID 字符串。") from exc
    _validate_version(expected_state_version)
    with closing(get_connection()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        operation = _require_operation(connection, operation_id)
        if (operation["status"] != "pending" or not operation["attempt_token"]
                or operation["state_version"] != expected_state_version):
            raise IndexOperationConflict("仅允许恢复匹配版本且已领取的 pending 任务。")
        cursor = connection.execute(
            """UPDATE rag_index_tasks SET status = 'failed', error_code = 'interrupted', error_message = ?,
               attempt_token = NULL, owner_instance_id = NULL, claimed_at = NULL,
               state_version = state_version + 1, updated_at = ?
               WHERE operation_id = ? AND status = 'pending' AND attempt_token = ? AND state_version = ?""",
            (_ERROR_MESSAGES["interrupted"], _now(), operation_id,
             operation["attempt_token"], expected_state_version),
        )
        if cursor.rowcount != 1:
            raise IndexOperationConflict("人工恢复未生效，请重新检查任务状态。")
        return _require_operation(connection, operation_id)


def prepare_index_retry(operation_id: str, expected_state_version: int) -> dict[str, Any]:
    """failed 的短事务状态转换；ready/正在执行的重复请求只读返回，不增加 attempt。"""
    _validate_version(expected_state_version)
    with closing(get_connection()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        operation = _require_operation(connection, operation_id)
        if operation['lifecycle_state'] != 'active':
            raise IndexOperationConflict('文档正在删除，不能重试索引。')
        if operation["status"] == "ready" or operation["execution_claimed"]:
            return operation
        if operation["status"] != "failed" or operation["state_version"] != expected_state_version:
            raise IndexOperationConflict("任务状态或版本已变更；未领取的 pending 应使用 claim/resume。")
        cursor = connection.execute(
            """UPDATE rag_index_tasks SET status = 'pending', error_code = NULL, error_message = NULL,
               state_version = state_version + 1, updated_at = ?
               WHERE operation_id = ? AND status = 'failed' AND state_version = ? AND attempt_token IS NULL""",
            (_now(), operation_id, expected_state_version),
        )
        if cursor.rowcount != 1:
            raise IndexOperationConflict("重试状态转换未生效。")
        return _require_operation(connection, operation_id)


def finish_index_operation(
    operation_id: str, attempt_token: str, status: str,
    error_code: str | None = None, error_message: str | None = None,
) -> dict[str, Any]:
    """只允许当前 token 完成；错误正文由代码表生成，不存调用方原始异常。"""
    if status not in ("ready", "failed") or not isinstance(attempt_token, str) or not attempt_token:
        raise ValueError("需要当前执行 token 及 ready/failed 终态。")
    if status == "failed":
        if error_code not in _ERROR_MESSAGES:
            raise ValueError("需要已知的受控错误代码。")
        error_message = _ERROR_MESSAGES[error_code]
    elif error_code is not None or error_message is not None:
        raise ValueError("成功状态不能携带失败信息。")
    with closing(get_connection()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        operation = _require_operation(connection, operation_id)
        if operation["status"] != "pending" or operation["attempt_token"] != attempt_token:
            raise IndexOperationConflict("执行 token 已失效。")
        if status == "ready":
            profile = operation["target_profile"]
            if profile["embedding_dimension"] is None or profile["distance_metric"] is None:
                raise IndexOperationConflict("向量维度与距离度量尚未绑定，不能确认 ready。")
        cursor = connection.execute(
            """UPDATE rag_index_tasks SET status = ?, error_code = ?, error_message = ?,
               attempt_token = NULL, owner_instance_id = NULL, claimed_at = NULL,
               state_version = state_version + 1, updated_at = ?
               WHERE operation_id = ? AND status = 'pending' AND attempt_token = ?""",
            (status, error_code, error_message, _now(), operation_id, attempt_token),
        )
        if cursor.rowcount != 1:
            raise IndexOperationConflict("执行 token 已失效。")
        return _require_operation(connection, operation_id)


def load_index_input(operation_id: str) -> dict[str, Any]:
    """在一致只读快照中验证并返回持久化输入；不重切块、不自行改变任务状态。"""
    with closing(get_connection()) as connection:
        connection.execute("BEGIN")
        operation = _require_operation(connection, operation_id)
        chunks = _verify_input(connection, operation)
        return {"operation": operation, "chunks": chunks,
                "storage_session_id": operation["storage_session_id"]}

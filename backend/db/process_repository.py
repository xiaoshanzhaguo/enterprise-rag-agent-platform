"""流程申请执行记录。每次 HTTP 调用之外独立提交事务，不长时间持有写锁。"""

from contextlib import closing
import json
import sqlite3
from typing import Any

from backend.db.connection import get_connection


class DuplicateProcessRequest(Exception):
    """申请编号已存在，禁止再次调用 Webhook。"""


def start_process_execution(payload: dict[str, Any]) -> int:
    """先持久化 running 记录；唯一索引负责并发重复提交保护。"""
    with closing(get_connection()) as connection:
        try:
            with connection:
                session_id = payload.get("session_id")
                if session_id:
                    connection.execute(
                        "INSERT INTO chat_sessions (id, mode, title) VALUES (?, 'agent', '流程申请') "
                        "ON CONFLICT(id) DO NOTHING", (session_id,),
                    )
                cursor = connection.execute(
                    """INSERT INTO n8n_execution_records
                       (request_id, session_id, workflow_name, trigger_source, status, input_json, started_at)
                       VALUES (?, ?, 'process_application', 'api', 'running', ?, datetime('now'))""",
                    (payload["request_id"], session_id, json.dumps(payload, ensure_ascii=False)),
                )
                return cursor.lastrowid
        except sqlite3.IntegrityError:
            existing = connection.execute(
                "SELECT id FROM n8n_execution_records WHERE request_id = ?", (payload["request_id"],),
            ).fetchone()
            if existing:
                raise DuplicateProcessRequest(payload["request_id"]) from None
            raise


def finish_process_execution(
    record_id: int, *, status: str, response_status_code: int,
    output: dict[str, Any] | None = None, error_message: str | None = None,
) -> None:
    """仅保存经过校验的回执或脱敏错误，不保存 Webhook URL、Token 和原始错误响应。"""
    with closing(get_connection()) as connection, connection:
        cursor = connection.execute(
            """UPDATE n8n_execution_records SET status = ?, response_status_code = ?,
               output_json = ?, error_message = ?, finished_at = datetime('now')
               WHERE id = ? AND status = 'running'""",
            (status, response_status_code, json.dumps(output, ensure_ascii=False) if output is not None else None,
             error_message, record_id),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("执行记录不存在或已结束。")


def get_process_execution(request_id: str) -> dict[str, Any] | None:
    with closing(get_connection()) as connection:
        row = connection.execute(
            """SELECT id, request_id, session_id, workflow_name, status, response_status_code,
                      input_json, output_json, error_message, started_at, finished_at
               FROM n8n_execution_records WHERE request_id = ?""", (request_id,),
        ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["input"] = json.loads(result.pop("input_json"))
    output = result.pop("output_json")
    result["output"] = json.loads(output) if output else None
    return result


def list_process_executions(*, page: int = 1, page_size: int = 20,
                            status: str | None = None, request_id: str | None = None) -> dict[str, Any]:
    """只读分页查询；统计覆盖全部有效流程记录，不随列表筛选变化。"""
    base = "workflow_name = 'process_application' AND request_id IS NOT NULL AND status IN ('running','simulated','failed','timeout')"
    conditions, parameters = [base], []
    if status:
        conditions.append('status = ?')
        parameters.append(status)
    if request_id:
        conditions.append('request_id = ?')
        parameters.append(request_id)
    where = ' AND '.join(conditions)
    with closing(get_connection()) as connection:
        # 统计和当前页在同一只读快照内，避免更新中的状态导致数量不一致。
        connection.execute('BEGIN')
        counts = {s: 0 for s in ('running', 'simulated', 'failed', 'timeout')}
        for row in connection.execute(f'SELECT status, COUNT(*) AS n FROM n8n_execution_records WHERE {base} GROUP BY status'):
            counts[row['status']] = row['n']
        total = connection.execute(f'SELECT COUNT(*) FROM n8n_execution_records WHERE {where}', parameters).fetchone()[0]
        rows = connection.execute(
            f'''SELECT id, request_id, status, input_json, output_json, response_status_code, started_at, finished_at
                FROM n8n_execution_records WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?''',
            [*parameters, page_size, (page - 1) * page_size],
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        fields = json.loads(item.pop('input_json')).get('fields', {})
        output = json.loads(item.pop('output_json') or 'null')
        item.update(application_type=fields.get('application_type'), applicant=fields.get('applicant'),
                    department=fields.get('department'), ticket_id=output.get('ticket_id') if output else None)
        items.append(item)
    return dict(items=items, total=total, page=page, page_size=page_size, status_counts=counts)

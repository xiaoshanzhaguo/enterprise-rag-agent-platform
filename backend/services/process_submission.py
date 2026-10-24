"""将确认后的申请发送到服务端配置的 n8n Webhook，不自动重试。"""

import math
import sqlite3
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from backend.config import settings
from backend.db.process_repository import (
    DuplicateProcessRequest, finish_process_execution, start_process_execution,
)
from backend.schema.process_submission_schema import ProcessSubmissionRequest, ProcessSubmissionResponse


class ProcessSubmissionError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


def _call_n8n_webhook(request: ProcessSubmissionRequest) -> ProcessSubmissionResponse:
    """成功必须收到匹配申请编号的模拟回执；超时不代表对方未执行。"""
    url = settings.n8n_webhook_url
    token = settings.n8n_webhook_token
    timeout = settings.n8n_webhook_timeout_seconds
    try:
        parsed = urlsplit(url or "")
        valid_url = parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password
    except ValueError:
        valid_url = False
    if not valid_url or not token or not math.isfinite(timeout) or timeout <= 0:
        raise ProcessSubmissionError(503, "流程 Webhook 尚未正确配置。")

    payload = {
        "event": "process_application.submitted",
        **request.model_dump(mode="json"),
    }
    try:
        # 不跟随重定向，不自动重试，避免重复执行有副作用的下游流程。
        with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
            response = client.post(url, json=payload, headers={"X-Webhook-Token": token})
        response.raise_for_status()
        result = ProcessSubmissionResponse.model_validate(response.json())
        if result.request_id != request.request_id:
            raise ValueError("回执编号不匹配")
        return result
    except httpx.TimeoutException as exc:
        raise ProcessSubmissionError(504, "流程服务响应超时，执行状态未知，请先在 n8n 核对申请编号。") from exc
    except (httpx.HTTPError, ValidationError, ValueError) as exc:
        raise ProcessSubmissionError(502, "未收到有效流程回执，请在 n8n 核对执行记录后再提交。") from exc


def submit_process_application(request: ProcessSubmissionRequest) -> ProcessSubmissionResponse:
    """先记录再发送，按 request_id 拒绝重复提交；记录完成前不报告成功。"""
    payload = {"event": "process_application.submitted", **request.model_dump(mode="json")}
    try:
        record_id = start_process_execution(payload)
    except DuplicateProcessRequest as exc:
        raise ProcessSubmissionError(409, "申请编号已存在，请查询执行记录，不要重复提交。") from exc
    except sqlite3.Error as exc:
        raise ProcessSubmissionError(503, "申请记录创建失败，未发送 Webhook。") from exc

    try:
        result = _call_n8n_webhook(request)
    except ProcessSubmissionError as exc:
        try:
            finish_process_execution(
                record_id, status="timeout" if exc.status_code == 504 else "failed",
                response_status_code=exc.status_code, error_message=str(exc),
            )
        except (sqlite3.Error, RuntimeError) as storage_error:
            raise ProcessSubmissionError(503, "执行状态保存失败，请按申请编号核对 n8n，勿重复提交。") from storage_error
        raise
    try:
        finish_process_execution(record_id, status="simulated", response_status_code=200, output=result.model_dump(mode="json"))
    except (sqlite3.Error, RuntimeError) as exc:
        raise ProcessSubmissionError(503, "已收到流程回执但保存失败，请按申请编号核对，勿重复提交。") from exc
    return result

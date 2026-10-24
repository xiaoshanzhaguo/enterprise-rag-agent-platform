"""将确认后的申请发送到服务端配置的 n8n Webhook，不自动重试。"""

import math
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from backend.config import settings
from backend.schema.process_submission_schema import ProcessSubmissionRequest, ProcessSubmissionResponse


class ProcessSubmissionError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


def submit_process_application(request: ProcessSubmissionRequest) -> ProcessSubmissionResponse:
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

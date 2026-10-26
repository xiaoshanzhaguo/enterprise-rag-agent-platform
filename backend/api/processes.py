"""流程申请提交接口；仅在显式请求时触发 n8n。"""

import sqlite3
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from backend.db.process_repository import get_process_execution, list_process_executions
from backend.llm.client import get_client
from backend.schema.process_schema import ProcessExtractionRequest, ProcessExtractionResult
from backend.schema.process_submission_schema import ProcessExecutionPage, ProcessExecutionRecord, ProcessSubmissionRequest, ProcessSubmissionResponse
from backend.services.process_extraction import extract_process_fields
from backend.services.process_submission import ProcessSubmissionError, submit_process_application


router = APIRouter(tags=["流程申请"])


@router.post("/process_fields/extract", response_model=ProcessExtractionResult)
def extract_application_fields(request: ProcessExtractionRequest):
    """只提取字段，不提交申请或创建执行记录。"""
    try:
        client = get_client()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="字段提取模型暂不可用，请检查后端模型配置。") from exc
    result = extract_process_fields(request.input_text, client)
    if result.status == "failed":
        raise HTTPException(status_code=502, detail=result.error or "字段提取失败，请重试。")
    return result


@router.post("/process_applications", response_model=ProcessSubmissionResponse)
def submit_application(request: ProcessSubmissionRequest):
    """提交已经确认的申请字段到 n8n 模拟流程。"""
    try:
        return submit_process_application(request)
    except ProcessSubmissionError as exc:
        raise HTTPException(
            status_code=exc.status_code, detail=str(exc), headers={"X-Request-ID": str(request.request_id)},
        ) from exc


@router.get('/process_applications', response_model=ProcessExecutionPage)
def list_application_executions(
    page: int = Query(default=1, ge=1, le=100000),
    page_size: int = Query(default=20, ge=1, le=100),
    status: Literal['running', 'simulated', 'failed', 'timeout'] | None = None,
    request_id: UUID | None = None,
):
    """读取真实申请记录；筛选与翻页均不触发 Webhook。"""
    try:
        return list_process_executions(page=page, page_size=page_size, status=status,
                                       request_id=str(request_id) if request_id else None)
    except (sqlite3.Error, ValueError, TypeError) as exc:
        raise HTTPException(status_code=503, detail='执行记录暂不可用，请稍后刷新。') from exc


@router.get("/process_applications/{request_id}", response_model=ProcessExecutionRecord)
def get_application_execution(request_id: UUID):
    """查询 SQLite 中的申请输入、执行状态和回执。"""
    try:
        record = get_process_execution(str(request_id))
    except sqlite3.Error as exc:
        raise HTTPException(status_code=503, detail="执行记录暂不可用。") from exc
    if record is None:
        raise HTTPException(status_code=404, detail="申请执行记录不存在。")
    return record

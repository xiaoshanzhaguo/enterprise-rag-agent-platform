"""流程申请提交接口；仅在显式请求时触发 n8n。"""

import sqlite3
from uuid import UUID

from fastapi import APIRouter, HTTPException

from backend.db.process_repository import get_process_execution
from backend.schema.process_submission_schema import ProcessExecutionRecord, ProcessSubmissionRequest, ProcessSubmissionResponse
from backend.services.process_submission import ProcessSubmissionError, submit_process_application


router = APIRouter(tags=["流程申请"])


@router.post("/process_applications", response_model=ProcessSubmissionResponse)
def submit_application(request: ProcessSubmissionRequest):
    """提交已经确认的申请字段到 n8n 模拟流程。"""
    try:
        return submit_process_application(request)
    except ProcessSubmissionError as exc:
        raise HTTPException(
            status_code=exc.status_code, detail=str(exc), headers={"X-Request-ID": str(request.request_id)},
        ) from exc


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

"""流程申请提交接口；仅在显式请求时触发 n8n。"""

from fastapi import APIRouter, HTTPException

from backend.schema.process_submission_schema import ProcessSubmissionRequest, ProcessSubmissionResponse
from backend.services.process_submission import ProcessSubmissionError, submit_process_application


router = APIRouter(tags=["流程申请"])


@router.post("/process_applications", response_model=ProcessSubmissionResponse)
def submit_application(request: ProcessSubmissionRequest):
    """提交已经确认的申请字段到 n8n 模拟流程。"""
    try:
        return submit_process_application(request)
    except ProcessSubmissionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

"""索引管理公开协议；只列出允许对外暴露的字段。"""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class IndexActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_state_version: int = Field(gt=0, strict=True)


class VectorIndexStatus(BaseModel):
    operation_id: str | None = None
    status: Literal["not_requested", "pending", "ready", "failed", "unknown"]
    state_version: int | None = None
    attempt_count: int = 0
    retry_count: int = 0
    execution_claimed: bool = False
    current_config_compatible: bool = False
    runtime_validation_required: bool = False
    resumable: bool = False
    retryable: bool = False
    error_code: str | None = None
    error_message: str | None = None
    updated_at: str | None = None
    status_url: str | None = None
    resume_url: str | None = None
    retry_url: str | None = None


class IndexOperationResponse(BaseModel):
    operation_id: str
    document_id: int
    file_name: str | None = None
    knowledge_base_id: str | None = None
    text_status: Literal["persisted"] = "persisted"
    state_confirmed: bool = True
    vector_index: VectorIndexStatus | None = None
    status_url: str
    reason: str | None = None
    resume_applied: bool = False
    retry_applied: bool = False


class IndexErrorResponse(BaseModel):
    detail: str
    receipt: dict | None = None

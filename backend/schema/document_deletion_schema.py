from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class DeleteDocumentRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_document_version: int = Field(gt=0, strict=True)


class RetryDeletionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_state_version: int = Field(gt=0, strict=True)


class DocumentDeletionResponse(BaseModel):
    operation_id: str
    document_id: int
    knowledge_base_id: str
    file_name: str | None
    status: Literal['pending', 'failed', 'completed']
    state_version: int
    attempt_count: int
    execution_claimed: bool
    error_message: str | None
    created_at: str
    updated_at: str
    started_at: str | None
    completed_at: str | None
    state_confirmed: bool
    retryable: bool
    status_url: str

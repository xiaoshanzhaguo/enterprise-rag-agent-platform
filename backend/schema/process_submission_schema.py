"""FastAPI 与 n8n 模拟申请流程的数据契约。"""

from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.schema.process_schema import ProcessFields


class ProcessSubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID = Field(default_factory=uuid4)
    session_id: str | None = Field(default=None, min_length=1, max_length=200)
    fields: ProcessFields

    @model_validator(mode="after")
    def require_application_identity(self):
        for name in ("application_type", "applicant", "department"):
            if getattr(self.fields, name) is None:
                raise ValueError(f"提交申请必须填写 {name}。")
        return self


class ProcessSubmissionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    status: Literal["simulated"]
    ticket_id: str = Field(min_length=1)
    actions: list[Literal["table_row_simulated", "notification_simulated", "ticket_simulated"]] = Field(min_length=1)


class ProcessExecutionRecord(BaseModel):
    id: int
    request_id: UUID
    session_id: str | None
    workflow_name: str
    status: Literal["running", "simulated", "failed", "timeout"]
    response_status_code: int | None
    input: dict
    output: ProcessSubmissionResponse | None
    error_message: str | None
    started_at: str
    finished_at: str | None


class ProcessExecutionSummary(BaseModel):
    id: int
    request_id: UUID
    status: Literal['running', 'simulated', 'failed', 'timeout']
    application_type: str | None
    applicant: str | None
    department: str | None
    ticket_id: str | None
    response_status_code: int | None
    started_at: str
    finished_at: str | None


class ProcessExecutionPage(BaseModel):
    items: list[ProcessExecutionSummary]
    total: int
    page: int
    page_size: int
    status_counts: dict[str, int]

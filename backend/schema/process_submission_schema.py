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

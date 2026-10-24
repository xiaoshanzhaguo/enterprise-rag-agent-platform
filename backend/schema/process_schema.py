"""流程申请字段协议；提取结果不代表已提交或获批。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProcessFields(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    application_type: str | None = Field(default=None, description="申请类型，如 VPN 权限、请假或报销")
    applicant: str | None = Field(default=None, description="申请人姓名")
    department: str | None = Field(default=None, description="申请人所属部门")
    permission_level: str | None = Field(default=None, description="用户明确申请的权限级别，如只读、读写或管理员")
    approver: str | None = Field(default=None, description="用户明确指定的审批人姓名或角色")
    additional_info: str | None = Field(default=None, description="原因、用途、时间、金额等用户明确提供的补充信息")

    @field_validator("*", mode="after")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class ProcessExtractionResult(BaseModel):
    fields: ProcessFields
    missing_fields: list[str]
    status: Literal["extracted", "failed"] = "extracted"
    error: str | None = None


class ProcessExtractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    input_text: str = Field(min_length=1, max_length=20000)

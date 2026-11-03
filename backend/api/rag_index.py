"""同步索引管理 HTTP 边界；不运行 SQL 或自行编排索引步骤。"""

import sqlite3
from uuid import UUID
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from backend.db.index_repository import IndexOperationConflict
from backend.schema.rag_index_schema import IndexActionRequest, IndexErrorResponse, IndexOperationResponse
from backend.services.rag_documents import IndexManagementError, IndexManagementService


router = APIRouter()
ERROR_RESPONSES = {code: {"model": IndexErrorResponse} for code in (400, 403, 404, 409, 502, 503)}


def get_index_management_service():
    return IndexManagementService()


def index_http_response(call, schema, *, query=False):
    """统一筛选公开 Schema、状态码及错误回执；原始 SDK/SQLite 异常不外传。"""
    try:
        result = call()
        if query:
            return schema.model_validate(result).model_dump(mode="json")
        reason = result.reason
        if reason in ("ready", "not_requested"):
            code = 200
        elif reason in ("already_claimed", "capacity_unavailable"):
            code = 202
        elif reason == "state_unconfirmed":
            code = 503
        elif reason == "failed":
            error = (result.receipt.get("vector_index") or {}).get("error_code")
            code = 409 if error in ("index_input_changed", "index_configuration_changed") else (
                503 if error in ("interrupted", "attempt_budget_exceeded") else 502)
        else:
            code = 409
        receipt = schema.model_validate(result.receipt).model_dump(mode="json")
        headers = {"Location": receipt["status_url"]} if receipt.get("status_url") else {}
        if code >= 400:
            detail = "文本已保存，向量索引失败。" if reason == "failed" else (
                "文本已保存，索引状态暂未确认，请查询任务。" if code == 503 else "索引状态、版本或配置不允许本次操作。")
            return JSONResponse(status_code=code, content={"detail": detail, "receipt": receipt}, headers=headers)
        return JSONResponse(status_code=code, content=receipt, headers=headers)
    except IndexManagementError as exc:
        code, detail = {"not_found": (404, "未找到当前范围内的索引任务。"),
                        "forbidden": (403, "当前角色没有知识库管理权限。")}[exc.reason]
    except IndexOperationConflict:
        code, detail = 409, "文档输入或索引目标与已有任务冲突，请查询原任务。"
    except ValueError:
        code, detail = 400, "文档内容、分类或索引配置不合法。"
    except sqlite3.Error:
        code, detail = 503, "索引存储暂不可用，保存或状态结果尚未确认。"
    except Exception:
        code, detail = 503, "索引管理暂不可用，请查询已知任务后再操作。"
    return JSONResponse(status_code=code, content={"detail": detail, "receipt": None})


@router.get("/rag_index_operations/{operation_id}", response_model=IndexOperationResponse, responses=ERROR_RESPONSES)
def get_operation(operation_id: UUID, session_id: str = Query(min_length=1, pattern=r"\S"),
                  knowledge_base_id: str | None = None, user_role: str | None = None,
                  service: IndexManagementService = Depends(get_index_management_service)):
    return index_http_response(lambda: service.query(operation_id, session_id=session_id,
        knowledge_base_id=knowledge_base_id, user_role=user_role), IndexOperationResponse, query=True)


@router.post("/rag_index_operations/{operation_id}/resume", response_model=IndexOperationResponse,
             responses={**ERROR_RESPONSES, 202: {"model": IndexOperationResponse}})
def resume_operation(operation_id: UUID, request: IndexActionRequest,
                     session_id: str = Query(min_length=1, pattern=r"\S"), knowledge_base_id: str | None = None,
                     user_role: str | None = None, service: IndexManagementService = Depends(get_index_management_service)):
    return index_http_response(lambda: service.action(operation_id, request.expected_state_version, action="resume",
        session_id=session_id, knowledge_base_id=knowledge_base_id, user_role=user_role), IndexOperationResponse)


@router.post("/rag_index_operations/{operation_id}/retry", response_model=IndexOperationResponse,
             responses={**ERROR_RESPONSES, 202: {"model": IndexOperationResponse}})
def retry_operation(operation_id: UUID, request: IndexActionRequest,
                    session_id: str = Query(min_length=1, pattern=r"\S"), knowledge_base_id: str | None = None,
                    user_role: str | None = None, service: IndexManagementService = Depends(get_index_management_service)):
    return index_http_response(lambda: service.action(operation_id, request.expected_state_version, action="retry",
        session_id=session_id, knowledge_base_id=knowledge_base_id, user_role=user_role), IndexOperationResponse)

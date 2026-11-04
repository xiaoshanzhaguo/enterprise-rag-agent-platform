"""Public version DTOs never include index execution tokens or configuration secrets."""
import sqlite3
from uuid import UUID
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ConfigDict
from backend.db.version_repository import VersionConflict
from backend.services.document_versions import DocumentVersionService, VersionForbidden

router = APIRouter()


class CreateLogicalDocument(BaseModel):
    model_config = ConfigDict(extra='forbid')
    display_name: str = Field(min_length=1, max_length=255, pattern=r'\S')


class VersionAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_state_version: int = Field(gt=0, strict=True)


class PublishVersion(VersionAction):
    document_id: int = Field(gt=0, strict=True)


def get_version_service():
    return DocumentVersionService()


def response(call):
    try:
        return call()
    except VersionForbidden:
        code, message = 403, '当前角色没有知识库管理权限。'
    except LookupError:
        code, message = 404, '当前知识库中未找到文档或版本。'
    except VersionConflict as exc:
        code, message = 409, str(exc)
    except ValueError:
        code, message = 400, '版本请求不合法。'
    except sqlite3.Error:
        code, message = 503, '版本状态暂未确认，请查询后重试。'
    return JSONResponse(status_code=code, content={'detail': message})


@router.post('/logical_documents')
def create(request: CreateLogicalDocument, knowledge_base_id: str = Query(min_length=1), user_role: str | None = None,
           service: DocumentVersionService = Depends(get_version_service)):
    return response(lambda: service.create(knowledge_base_id, user_role, request.display_name))


@router.get('/logical_documents')
def list_documents(knowledge_base_id: str = Query(min_length=1),
                   service: DocumentVersionService = Depends(get_version_service)):
    return response(lambda: service.list(knowledge_base_id))


@router.get('/logical_documents/{identity}')
def get(identity: UUID, knowledge_base_id: str = Query(min_length=1),
        service: DocumentVersionService = Depends(get_version_service)):
    return response(lambda: service.get(identity, knowledge_base_id))


@router.post('/logical_documents/{identity}/publish')
def publish(identity: UUID, request: PublishVersion, knowledge_base_id: str = Query(min_length=1),
            user_role: str | None = None, service: DocumentVersionService = Depends(get_version_service)):
    return response(lambda: service.publish(identity, knowledge_base_id, user_role, request.document_id,
                                            request.expected_state_version))


@router.post('/logical_documents/{identity}/disable')
def disable(identity: UUID, request: VersionAction, knowledge_base_id: str = Query(min_length=1),
            user_role: str | None = None, service: DocumentVersionService = Depends(get_version_service)):
    return response(lambda: service.disable(identity, knowledge_base_id, user_role, request.expected_state_version))

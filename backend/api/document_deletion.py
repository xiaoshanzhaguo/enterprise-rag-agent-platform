from uuid import UUID
from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import JSONResponse

from backend.db.deletion_repository import DeletionConflict
from backend.schema.document_deletion_schema import DeleteDocumentRequest, RetryDeletionRequest, DocumentDeletionResponse
from backend.services.document_deletion import DocumentDeletionService, DeletionForbidden


router = APIRouter()


def get_deletion_service():
    return DocumentDeletionService()


def deletion_response(call, *, query=False):
    try:
        receipt = DocumentDeletionResponse.model_validate(call()).model_dump(mode='json')
        code = 200 if query or receipt['status'] == 'completed' else 202
        if not receipt['state_confirmed']:
            code = 503
        elif not query and receipt['status'] == 'failed':
            code = 502
        return JSONResponse(status_code=code, content=receipt,
                            headers={'Location': receipt['status_url']})
    except DeletionForbidden:
        code, detail = 403, '当前角色没有文档删除权限。'
    except LookupError:
        code, detail = 404, '当前知识库中未找到文档或删除任务。'
    except DeletionConflict as exc:
        code, detail = 409, str(exc)
    except ValueError:
        code, detail = 400, '删除请求参数或目标配置不合法。'
    except Exception:
        code, detail = 503, '删除结果暂未确认，请刷新文档列表或查询已知任务，不要自动重发。'
    return JSONResponse(status_code=code, content={'detail': detail})


@router.delete('/documents/{document_id}', response_model=DocumentDeletionResponse)
def delete_document(request: DeleteDocumentRequest, document_id: int = Path(gt=0),
                    knowledge_base_id: str = Query(min_length=1, pattern=r'\S'), user_role: str | None = None,
                    service: DocumentDeletionService = Depends(get_deletion_service)):
    return deletion_response(lambda: service.create(document_id, knowledge_base_id, user_role, request.expected_document_version))


@router.get('/document_deletions/{operation_id}', response_model=DocumentDeletionResponse)
def query_deletion(operation_id: UUID, knowledge_base_id: str = Query(min_length=1, pattern=r'\S'),
                   user_role: str | None = None, service: DocumentDeletionService = Depends(get_deletion_service)):
    return deletion_response(lambda: service.query(operation_id, knowledge_base_id, user_role), query=True)


@router.post('/document_deletions/{operation_id}/retry', response_model=DocumentDeletionResponse)
def retry_deletion(operation_id: UUID, request: RetryDeletionRequest,
                   knowledge_base_id: str = Query(min_length=1, pattern=r'\S'), user_role: str | None = None,
                   service: DocumentDeletionService = Depends(get_deletion_service)):
    return deletion_response(lambda: service.retry(operation_id, knowledge_base_id, user_role, request.expected_state_version))

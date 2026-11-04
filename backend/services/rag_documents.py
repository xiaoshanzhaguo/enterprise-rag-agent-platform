"""上传与索引管理用例；不依赖 Router，不执行 SQL。"""

from dataclasses import dataclass
from uuid import uuid4
from urllib.parse import urlencode

from backend.config import settings
from backend.db import index_repository as repository
from backend.db import version_repository
from backend.db.repository import get_document_management_snapshot
from backend.rag.categories import normalize_knowledge_base_type, get_department_for_knowledge_base_type
from backend.rag.chunker import split_text_into_chunks
from backend.rag.index_adapters import build_index_target_profile
from backend.rag.knowledge_bases import (
    SESSION_SCOPED_KNOWLEDGE_BASE_ID, normalize_knowledge_base_id, can_manage_knowledge_base,
    build_knowledge_base_storage_session_id,
)
from backend.schema.rag_index_schema import VectorIndexStatus
from backend.services.rag_indexing import create_rag_indexing_service


_OWNER = str(uuid4())


class IndexManagementError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass
class IndexManagementResult:
    receipt: dict
    reason: str = "ready"


class IndexManagementService:
    def __init__(self, *, indexing=None, mode=None, profile_factory=build_index_target_profile):
        self.mode = mode if mode is not None else settings.rag_retrieval_mode
        self.indexing = indexing
        self.profile_factory = profile_factory

    def _engine(self):
        if self.indexing is None:
            self.indexing = create_rag_indexing_service(owner_instance_id=_OWNER, retrieval_mode=self.mode)
        return self.indexing

    def _compatible(self, operation):
        target = operation["target_profile"]
        if self.mode != "vector" or target is None:
            return False
        try:
            current = self.profile_factory()
            return all(current[key] == value for key, value in target.items()
                       if key not in ("embedding_dimension", "distance_metric")) and all(
                current[key] is None or target[key] is None or current[key] == target[key]
                for key in ("embedding_dimension", "distance_metric")
            )
        except Exception:
            return False

    def vector_status(self, operation):
        if operation is None:
            return VectorIndexStatus(status="unknown").model_dump()
        compatible = self._compatible(operation) and operation.get('lifecycle_state', 'active') == 'active'
        active = operation["execution_claimed"]
        url = f"/rag_index_operations/{operation['operation_id']}"
        query = "?" + urlencode(dict(session_id=operation["storage_session_id"], **(
            {"knowledge_base_id": operation["knowledge_base_id"]}
            if operation["knowledge_base_id"] != SESSION_SCOPED_KNOWLEDGE_BASE_ID else {})))
        return VectorIndexStatus(
            **{key: operation[key] for key in ("operation_id", "status", "state_version", "attempt_count",
               "retry_count", "execution_claimed", "error_code", "error_message", "updated_at")},
            current_config_compatible=compatible,
            runtime_validation_required=compatible and operation["status"] in ("pending", "failed"),
            resumable=compatible and operation["status"] == "pending" and not active,
            retryable=compatible and operation["status"] == "failed" and not active,
            status_url=url + query, resume_url=url + "/resume" + query, retry_url=url + "/retry" + query,
        ).model_dump()

    def _operation_receipt(self, operation):
        vector = self.vector_status(operation)
        return dict(operation_id=operation["operation_id"], document_id=operation["document_id"],
                    file_name=operation["file_name"], knowledge_base_id=operation["knowledge_base_id"],
                    text_status="persisted", state_confirmed=True, vector_index=vector,
                    status_url=vector["status_url"])

    @staticmethod
    def _authorize(operation, session_id, knowledge_base_id, user_role, *, write=False):
        knowledge_base_id = normalize_knowledge_base_id(knowledge_base_id) if knowledge_base_id else None
        scope = (build_knowledge_base_storage_session_id(knowledge_base_id) if knowledge_base_id else session_id)
        if (not session_id or not session_id.strip() or operation["storage_session_id"] != scope
                or (knowledge_base_id is None and operation["knowledge_base_id"] != SESSION_SCOPED_KNOWLEDGE_BASE_ID)):
            raise IndexManagementError("not_found")
        if write and knowledge_base_id and not can_manage_knowledge_base(user_role):
            raise IndexManagementError("forbidden")

    def query(self, operation_id, *, session_id, knowledge_base_id=None, user_role=None):
        operation = repository.get_index_operation(str(operation_id))
        if operation is None:
            raise IndexManagementError("not_found")
        self._authorize(operation, session_id, knowledge_base_id, user_role)
        return self._operation_receipt(operation)

    def action(self, operation_id, expected_state_version, *, action, session_id, knowledge_base_id=None, user_role=None):
        operation = repository.get_index_operation(str(operation_id))
        if operation is None:
            raise IndexManagementError("not_found")
        self._authorize(operation, session_id, knowledge_base_id, user_role, write=True)
        if operation.get('lifecycle_state', 'active') != 'active':
            raise repository.IndexOperationConflict('文档正在删除，禁止执行索引。')
        receipt = self._operation_receipt(operation)
        if self.mode != "vector":
            return IndexManagementResult(receipt, "vector_index_disabled")
        if operation["status"] != "ready" and not operation["execution_claimed"] and not self._compatible(operation):
            return IndexManagementResult(receipt, "configuration_conflict")
        try:
            result = getattr(self._engine(), action)(str(operation_id), expected_state_version)
        except Exception:
            receipt.update(state_confirmed=False, vector_index=None)
            return IndexManagementResult(receipt, "state_unconfirmed")
        receipt.update(reason=result.reason, state_confirmed=result.state_confirmed,
                       vector_index=self.vector_status(result.operation) if result.state_confirmed else None,
                       resume_applied=action == "resume" and result.execution_started,
                       retry_applied=action == "retry" and result.execution_started)
        return IndexManagementResult(receipt, result.reason)

    def upload(self, request):
        if request.knowledge_base_id and not can_manage_knowledge_base(request.user_role):
            raise IndexManagementError("forbidden")
        kb = normalize_knowledge_base_id(request.knowledge_base_id) if request.knowledge_base_id else None
        text = request.document_text.strip()
        if not text:
            raise ValueError("文档内容不能为空。")
        chunks = split_text_into_chunks(text)
        if not chunks:
            raise ValueError("文档切块后为空。")
        inferred = infer_document_business_fields(request.file_name, text)
        category = normalize_knowledge_base_type(request.knowledge_base_type, allow_none=True) or inferred["knowledge_base_type"]
        metadata = dict(knowledge_base_type=category,
                        department=request.department or get_department_for_knowledge_base_type(category) or inferred["department"],
                        process_type=request.process_type or inferred["process_type"],
                        process_status=request.process_status or inferred["process_status"] or "active")
        identity = getattr(request, 'logical_document_id', None)
        if identity:
            parent = version_repository.get_logical_document(identity)
            scope = build_knowledge_base_storage_session_id(kb) if kb else request.session_id
            if not parent or parent['storage_session_id'] != scope:
                raise IndexManagementError('not_found')
            if parent['versions']:
                previous = next((v for v in parent['versions'] if v['document_id'] == parent['current_document_id']), parent['versions'][0])
                fields = request.model_fields_set
                changed_category = request.knowledge_base_type is not None and category != previous['knowledge_base_type']
                metadata = dict(
                    knowledge_base_type=category if request.knowledge_base_type is not None else previous['knowledge_base_type'],
                    department=request.department if 'department' in fields else (
                        get_department_for_knowledge_base_type(category) if changed_category else previous['department']),
                    process_type=request.process_type if 'process_type' in fields else previous['process_type'],
                    process_status=request.process_status or previous['process_status'],
                )
        profile = self.profile_factory() if self.mode == "vector" else None
        prepared = repository.prepare_document_index(
            request.session_id, request.file_name, chunks, retrieval_mode=self.mode,
            target_profile=profile, knowledge_base_id=kb, logical_document_id=identity, **metadata,
        )
        operation = prepared.operation
        vector = self.vector_status(operation)
        receipt = dict(session_id=request.session_id, knowledge_base_id=kb or SESSION_SCOPED_KNOWLEDGE_BASE_ID,
                       file_name=request.file_name, chunk_count=len(prepared.chunks), **metadata,
                       document_id=prepared.document_id, document_reused=prepared.document_reused,
                       operation_id=operation["operation_id"] if operation else None, text_status="persisted",
                       effective_retrieval_mode=self.mode, state_confirmed=True,
                       vector_index=vector, status_url=vector["status_url"])
        if self.mode == "keyword":
            version_repository.auto_publish(prepared.document_id)
            receipt.update(version_repository.document_version(prepared.document_id))
            return IndexManagementResult(receipt, "not_requested" if not operation or operation["status"] == "not_requested"
                                         else "vector_index_disabled")
        # 重复上传 failed 不隐式 retry；只对未领取 pending 继续执行。
        try:
            result = self._engine().resume(operation["operation_id"], operation["state_version"])
        except Exception:
            receipt.update(state_confirmed=False, vector_index=None)
            return IndexManagementResult(receipt, "state_unconfirmed")
        receipt.update(state_confirmed=result.state_confirmed,
                       vector_index=self.vector_status(result.operation) if result.state_confirmed else None)
        receipt.update(version_repository.document_version(prepared.document_id))
        return IndexManagementResult(receipt, result.reason)

    def document_status(self, session_id, knowledge_base_id=None):
        result, operations, deletions = get_document_management_snapshot(session_id, knowledge_base_id)
        summary = dict.fromkeys(("ready", "pending", "failed", "not_requested", "unknown"), 0)
        for doc in result["documents"]:
            deletion = deletions.get(doc['document_id'])
            if deletion:
                from backend.services.document_deletion import DocumentDeletionService
                doc['deletion'] = DocumentDeletionService.receipt(deletion)
            vector = self.vector_status(operations.get(doc["document_id"]))
            doc.update(text_status="persisted", vector_index=vector,
                       tracking_source="managed" if vector["status"] != "unknown" else "legacy")
            summary[vector["status"]] += 1
        result["has_document"] = any(d.get("is_current_version") for d in result["documents"])
        result["vector_index_summary"] = summary
        return result


def infer_document_business_fields(file_name: str | None, document_text: str) -> dict[str, str | None]:
    """
    根据文件名和文档内容推断企业知识库业务标签。

    为什么要做自动推断：
    1. 当前前端上传入口还没有部门、流程类型下拉框。
    2. 目前需要先把“知识库类型、部门、流程类型、流程状态”字段落到接口和数据库。
    3. 自动推断可以让旧前端请求不改 UI 也能写入基本业务标签，后续再做筛选或下拉选择会更平滑。

    :param file_name: 上传文件名
    :param document_text: 文档正文
    :return: 包含 knowledge_base_type、department、process_type、process_status 的字典
    """
    # 统一转小写，方便同时匹配英文文件名和中文正文关键词
    searchable_text = f"{file_name or ''}\n{document_text[:500]}".lower()

    # 先按部门/知识库类型做粗分类
    if any(keyword in searchable_text for keyword in ["hr", "请假", "考勤", "入职", "试用期"]):
        knowledge_base_type = "hr"
        department = "HR"
    elif any(keyword in searchable_text for keyword in ["finance", "财务", "报销", "差旅", "付款", "发票"]):
        knowledge_base_type = "finance"
        department = "财务"
    elif any(keyword in searchable_text for keyword in ["it", "vpn", "gitlab", "设备", "数据安全"]):
        knowledge_base_type = "it"
        department = "IT"
    elif any(keyword in searchable_text for keyword in ["product", "产品", "发布", "客户反馈", "faq"]):
        knowledge_base_type = "product"
        department = "产品"
    else:
        knowledge_base_type = "general"
        department = None

    # 再按具体流程关键词细分，便于后续按流程类型筛选或路由
    process_type = None
    if any(keyword in searchable_text for keyword in ["请假", "休假", "leave"]):
        process_type = "leave"
    elif any(keyword in searchable_text for keyword in ["考勤", "远程办公", "attendance"]):
        process_type = "attendance"
    elif any(keyword in searchable_text for keyword in ["入职", "onboarding"]):
        process_type = "onboarding"
    elif any(keyword in searchable_text for keyword in ["报销", "reimbursement"]):
        process_type = "reimbursement"
    elif any(keyword in searchable_text for keyword in ["差旅", "travel"]):
        process_type = "travel"
    elif any(keyword in searchable_text for keyword in ["付款", "供应商", "payment"]):
        process_type = "payment"
    elif "vpn" in searchable_text:
        process_type = "vpn"
    elif "gitlab" in searchable_text:
        process_type = "gitlab_access"
    elif any(keyword in searchable_text for keyword in ["设备", "数据安全", "security"]):
        process_type = "device_security"
    elif any(keyword in searchable_text for keyword in ["产品 faq", "faq"]):
        process_type = "product_faq"
    elif any(keyword in searchable_text for keyword in ["发布", "release"]):
        process_type = "release"
    elif any(keyword in searchable_text for keyword in ["客户反馈", "feedback"]):
        process_type = "customer_feedback"

    # 样例文档默认都是已生效制度/流程；以后草稿文档可由前端显式传 draft
    return {
        "knowledge_base_type": knowledge_base_type,
        "department": department,
        "process_type": process_type,
        "process_status": "active",
    }

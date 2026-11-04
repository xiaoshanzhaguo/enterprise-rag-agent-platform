"""Version management use cases; scope and demo-role authorization match knowledge administration."""
from backend.db import version_repository as repository
from backend.db.repository import _prepare_document_storage_scope
from backend.config import settings
from backend.rag.knowledge_bases import (
    can_manage_knowledge_base, normalize_knowledge_base_id, SESSION_SCOPED_KNOWLEDGE_BASE_ID,
    build_knowledge_base_storage_session_id,
)


class VersionForbidden(Exception):
    pass


class DocumentVersionService:
    @staticmethod
    def _public_scope(kb):
        kb = normalize_knowledge_base_id(kb)
        if kb == SESSION_SCOPED_KNOWLEDGE_BASE_ID:
            raise VersionForbidden('公共知识库版本接口不支持普通会话文档。')
        return kb

    def list(self, kb):
        kb = self._public_scope(kb)
        return {'logical_documents': repository.list_logical_documents(
            kb, storage_session_id=build_knowledge_base_storage_session_id(kb))}

    def get(self, identity, kb, role=None, *, write=False):
        kb = self._public_scope(kb)
        if write and not can_manage_knowledge_base(role):
            raise VersionForbidden()
        parent = repository.get_logical_document(str(identity))
        if (not parent or parent['knowledge_base_id'] != kb
                or parent['storage_session_id'] != build_knowledge_base_storage_session_id(kb)):
            raise LookupError('当前知识库中未找到逻辑文档。')
        return parent

    def create(self, kb, role, display_name):
        kb = self._public_scope(kb)
        if not can_manage_knowledge_base(role):
            raise VersionForbidden()
        kb, storage, _ = _prepare_document_storage_scope('knowledge-version-admin', mode='rag', knowledge_base_id=kb)
        return repository.create_logical_document(storage, kb, display_name)

    def publish(self, identity, kb, role, document_id, expected_state_version):
        parent = self.get(identity, kb, role, write=True)
        if not any(v['document_id'] == document_id for v in parent['versions']):
            raise LookupError('版本不属于此逻辑文档。')
        repository.publish_document(document_id, expected_state_version, retrieval_mode=settings.rag_retrieval_mode)
        return self.get(identity, kb)

    def disable(self, identity, kb, role, expected_state_version):
        self.get(identity, kb, role, write=True)
        repository.disable_document(str(identity), expected_state_version)
        return self.get(identity, kb)

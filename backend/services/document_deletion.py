"""删除用例编排；SQL、事务归 Repository，向量操作由独立 Adapter 注入。"""

from urllib.parse import urlencode
from backend.db import deletion_repository as repository
from backend.rag.deletion_adapter import ChromaDeletionAdapter, build_deletion_target
from backend.rag.knowledge_bases import can_manage_knowledge_base, SESSION_SCOPED_KNOWLEDGE_BASE_ID


class DeletionForbidden(Exception):
    pass


class DocumentDeletionService:
    def __init__(self, adapter=None, target_factory=build_deletion_target):
        self.adapter = adapter if adapter is not None else ChromaDeletionAdapter()
        self.target_factory = target_factory

    @staticmethod
    def authorize(knowledge_base_id, user_role):
        if not can_manage_knowledge_base(user_role):
            raise DeletionForbidden('当前角色没有文档删除权限。')
        if not knowledge_base_id or knowledge_base_id == SESSION_SCOPED_KNOWLEDGE_BASE_ID:
            raise LookupError('请选择公共知识库文档。')

    @staticmethod
    def receipt(task, *, state_confirmed=True, user_role=None):
        scope = {'knowledge_base_id': task['knowledge_base_id']}
        if user_role is not None:
            scope['user_role'] = user_role
        return {**{key: task[key] for key in ('operation_id', 'document_id', 'knowledge_base_id', 'file_name',
                 'status', 'state_version', 'attempt_count', 'execution_claimed', 'error_message',
                 'created_at', 'updated_at', 'started_at', 'completed_at')},
                'state_confirmed': state_confirmed,
                'retryable': state_confirmed and not task['execution_claimed'] and task['status'] in ('failed', 'pending'),
                'status_url': f"/document_deletions/{task['operation_id']}?{urlencode(scope)}"}

    def create(self, document_id, knowledge_base_id, user_role, expected_document_version):
        self.authorize(knowledge_base_id, user_role)
        task = repository.prepare_deletion(document_id, knowledge_base_id, expected_document_version, self.target_factory())
        if task['status'] != 'pending' or task['execution_claimed']:
            return self.receipt(task, user_role=user_role)
        return self._execute(task, user_role)

    def query(self, operation_id, knowledge_base_id, user_role):
        self.authorize(knowledge_base_id, user_role)
        task = repository.get_deletion(operation_id)
        if task is None or task['knowledge_base_id'] != knowledge_base_id:
            raise LookupError('删除任务不存在。')
        return self.receipt(task, user_role=user_role)

    def retry(self, operation_id, knowledge_base_id, user_role, expected_state_version):
        self.query(operation_id, knowledge_base_id, user_role)
        task = repository.get_deletion(operation_id)
        if task['status'] == 'completed':
            return self.receipt(task, user_role=user_role)
        task = repository.retry_deletion(operation_id, expected_state_version)
        return self._execute(task, user_role)

    def _execute(self, task, user_role):
        try:
            task = repository.claim_deletion(task['operation_id'], task['state_version'])
        except repository.DeletionConflict:
            current = repository.get_deletion(task['operation_id'])
            if current and (current['execution_claimed'] or current['status'] == 'completed'):
                return self.receipt(current, user_role=user_role)
            raise
        except Exception:
            return self.receipt(task, state_confirmed=False, user_role=user_role)
        try:
            self.adapter.delete(task)
        except Exception:
            return self._fail(task, user_role)
        try:
            finished = repository.finish_deletion(task['operation_id'], task['attempt_token'],
                                                   task['state_version'], completed=True)
            return self.receipt(finished, user_role=user_role)
        except Exception:
            # Chroma 已删但 SQLite 失败：保留目标并标记可重试；提交回执丢失则只认可再次查询的事实。
            return self._fail(task, user_role)

    def _fail(self, task, user_role):
        try:
            failed = repository.finish_deletion(task['operation_id'], task['attempt_token'],
                                                 task['state_version'], completed=False)
            return self.receipt(failed, user_role=user_role)
        except Exception:
            try:
                current = repository.get_deletion(task['operation_id'])
                if current and current['status'] == 'completed':
                    return self.receipt(current, user_role=user_role)
            except Exception:
                pass
            return self.receipt(task, state_confirmed=False, user_role=user_role)

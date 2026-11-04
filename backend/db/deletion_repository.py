"""单文档删除短事务；独立任务保存完整目标，不执行 Chroma 或模型调用。"""

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from uuid import UUID, uuid4

from backend.db.connection import get_connection


class DeletionConflict(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat()


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _version(value):
    if type(value) is not int or value <= 0:
        raise ValueError('版本必须是正整数。')


def _decode(row):
    if row is None:
        return None
    result = dict(row)
    result['vector_ids'] = json.loads(result.pop('vector_ids_json'))
    result['target_store'] = json.loads(result.pop('target_store_json'))
    result['execution_claimed'] = result['attempt_token'] is not None
    return result


def _get(connection, operation_id):
    return _decode(connection.execute('SELECT * FROM document_deletion_tasks WHERE operation_id = ?',
                                      (str(operation_id),)).fetchone())


def _require(connection, operation_id):
    task = _get(connection, operation_id)
    if task is None:
        raise LookupError('删除任务不存在。')
    return task


def _target(task):
    return {key: task[key] for key in ('document_id', 'knowledge_base_id', 'storage_session_id',
                                      'vector_ids', 'target_store')}


def get_deletion(operation_id):
    operation_id = str(UUID(str(operation_id)))
    with closing(get_connection()) as connection:
        return _get(connection, operation_id)


def get_document_deletion(document_id, knowledge_base_id):
    with closing(get_connection()) as connection:
        row = connection.execute('SELECT * FROM document_deletion_tasks WHERE document_id = ? AND knowledge_base_id = ?',
                                 (document_id, knowledge_base_id)).fetchone()
        return _decode(row)


def prepare_deletion(document_id, knowledge_base_id, expected_document_version, target_store):
    """在同一写事务检查索引领取、标记 deleting、保存稳定 ID 并使旧索引版本失效。"""
    _version(document_id)
    _version(expected_document_version)
    if set(target_store) != {'collection_name', 'vector_store_fingerprint'}:
        raise ValueError('删除目标存储配置不合法。')
    with closing(get_connection()) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        old = connection.execute('SELECT * FROM document_deletion_tasks WHERE document_id = ?', (document_id,)).fetchone()
        if old:
            task = _decode(old)
            if task['knowledge_base_id'] != knowledge_base_id:
                raise LookupError('文档不存在。')
            return task
        doc = connection.execute('SELECT * FROM documents WHERE id = ? AND knowledge_base_id = ?',
                                 (document_id, knowledge_base_id)).fetchone()
        if doc is None:
            raise LookupError('文档不存在。')
        parent = connection.execute('SELECT * FROM logical_documents WHERE logical_document_id = ?',
                                    (doc['logical_document_id'],)).fetchone()
        if parent and parent['enabled'] and parent['current_document_id'] == document_id:
            raise DeletionConflict('当前生效版本不能删除，请先发布其他版本或停用逻辑文档。')
        index = connection.execute('SELECT * FROM rag_index_tasks WHERE document_id = ?', (document_id,)).fetchone()
        if index and index['attempt_token'] is not None:
            raise DeletionConflict('文档正在索引，请待执行结束后再删除。')
        if doc['lifecycle_state'] != 'active' or doc['lifecycle_version'] != expected_document_version:
            raise DeletionConflict('文档状态或版本已变化，请刷新后核对。')
        # 采用原索引记录的存储身份，不因当前配置改变就删除另一个 Collection。
        if index and index['target_profile_json']:
            profile = json.loads(index['target_profile_json'])
            target_store = {key: profile[key] for key in target_store}
        ids = [f"{doc['session_id']}:{row['id']}" for row in connection.execute(
            'SELECT id FROM document_chunks WHERE document_id = ? ORDER BY chunk_index, id', (document_id,))]
        target = dict(document_id=document_id, knowledge_base_id=knowledge_base_id,
                      storage_session_id=doc['session_id'], vector_ids=ids, target_store=target_store)
        now, operation_id = _now(), str(uuid4())
        cursor = connection.execute("UPDATE documents SET lifecycle_state = 'deleting', lifecycle_version = lifecycle_version + 1 "
                                    "WHERE id = ? AND lifecycle_state = 'active' AND lifecycle_version = ?",
                                    (document_id, expected_document_version))
        if cursor.rowcount != 1:
            raise DeletionConflict('文档状态已变化。')
        if parent:
            connection.execute('UPDATE logical_documents SET state_version = state_version + 1, updated_at = ? WHERE logical_document_id = ?',
                               (now, parent['logical_document_id']))
        if index:
            connection.execute('UPDATE rag_index_tasks SET state_version = state_version + 1, updated_at = ? WHERE document_id = ?',
                               (now, document_id))
        connection.execute('''INSERT INTO document_deletion_tasks
            (operation_id, document_id, knowledge_base_id, storage_session_id, file_name, vector_ids_json,
             target_store_json, target_sha256, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)''',
            (operation_id, document_id, knowledge_base_id, doc['session_id'], doc['file_name'], _json(ids), _json(target_store),
             _hash(target), now, now))
        connection.execute('UPDATE document_deletion_tasks SET logical_document_id = ?, version_number = ? WHERE operation_id = ?',
                           (doc['logical_document_id'], doc['version_number'], operation_id))
        return _require(connection, operation_id)


def claim_deletion(operation_id, expected_state_version):
    _version(expected_state_version)
    with closing(get_connection()) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        task = _require(connection, operation_id)
        if _hash(_target(task)) != task['target_sha256']:
            raise DeletionConflict('删除目标快照校验失败。')
        cursor = connection.execute('''UPDATE document_deletion_tasks SET attempt_token = ?,
            attempt_count = attempt_count + 1, state_version = state_version + 1,
            started_at = ?, updated_at = ?
            WHERE operation_id = ? AND status = 'pending' AND attempt_token IS NULL AND state_version = ?''',
            (str(uuid4()), _now(), _now(), str(operation_id), expected_state_version))
        if cursor.rowcount != 1:
            raise DeletionConflict('删除任务已领取或状态版本已变化。')
        return _require(connection, operation_id)


def retry_deletion(operation_id, expected_state_version):
    _version(expected_state_version)
    with closing(get_connection()) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        task = _require(connection, operation_id)
        # 领取前存储故障可能留下未执行 pending，允许显式继续，绝不自动重新发送删除。
        if (task['status'] == 'pending' and not task['execution_claimed']
                and task['state_version'] == expected_state_version):
            return task
        cursor = connection.execute("UPDATE document_deletion_tasks SET status = 'pending', error_message = NULL, "
                                    "state_version = state_version + 1, updated_at = ? WHERE operation_id = ? "
                                    "AND status = 'failed' AND attempt_token IS NULL AND state_version = ?",
                                    (_now(), str(operation_id), expected_state_version))
        if cursor.rowcount != 1:
            raise DeletionConflict('只有匹配版本的失败或未领取任务可以继续。')
        return _require(connection, operation_id)


def finish_deletion(operation_id, attempt_token, expected_state_version, *, completed):
    _version(expected_state_version)
    if not attempt_token:
        raise DeletionConflict('缺少当前执行 token。')
    with closing(get_connection()) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        task = _require(connection, operation_id)
        if (task['status'] != 'pending' or task['attempt_token'] != attempt_token
                or task['state_version'] != expected_state_version):
            raise DeletionConflict('删除任务执行权或版本已变化。')
        if _hash(_target(task)) != task['target_sha256']:
            raise DeletionConflict('删除目标快照校验失败。')
        if completed:
            doc = connection.execute('SELECT lifecycle_state, session_id, knowledge_base_id FROM documents WHERE id = ?',
                                     (task['document_id'],)).fetchone()
            if doc and (doc['lifecycle_state'] != 'deleting' or doc['session_id'] != task['storage_session_id']
                        or doc['knowledge_base_id'] != task['knowledge_base_id']):
                raise DeletionConflict('文档删除范围发生变化。')
            connection.execute('DELETE FROM documents WHERE id = ?', (task['document_id'],))
        now = _now()
        connection.execute('''UPDATE document_deletion_tasks SET status = ?, attempt_token = NULL,
            state_version = state_version + 1, error_message = ?, updated_at = ?, completed_at = ?
            WHERE operation_id = ? AND attempt_token = ? AND state_version = ?''',
            ('completed' if completed else 'failed', None if completed else '向量删除或结果核验失败，请查询后显式重试。',
             now, now if completed else None, str(operation_id), attempt_token, expected_state_version))
        return _require(connection, operation_id)


def recover_deletion(operation_id, expected_state_version, *, execution_stopped_confirmed=False):
    """仅供受控 CLI：确认旧删除执行者停止后，释放 claimed 任务供显式重试。"""
    if execution_stopped_confirmed is not True:
        raise ValueError('必须确认原删除执行进程已停止。')
    _version(expected_state_version)
    with closing(get_connection()) as connection, connection:
        cursor = connection.execute("UPDATE document_deletion_tasks SET status = 'failed', attempt_token = NULL, "
            "state_version = state_version + 1, error_message = ?, updated_at = ? WHERE operation_id = ? "
            "AND status = 'pending' AND attempt_token IS NOT NULL AND state_version = ?",
            ('原删除执行已停止，请核对后显式重试。', _now(), str(operation_id), expected_state_version))
        if cursor.rowcount != 1:
            raise DeletionConflict('任务版本或执行状态不允许恢复。')
        return _require(connection, operation_id)

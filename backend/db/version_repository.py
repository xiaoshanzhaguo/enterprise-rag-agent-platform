"""Logical document identity and publication. All changes use short SQLite transactions."""
from contextlib import closing
from datetime import datetime, timezone
from uuid import uuid4
import hashlib
import json

from backend.db.connection import get_connection


class VersionConflict(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def metadata_hash(category, department, process_type, process_status):
    return hashlib.sha256(json.dumps([category or 'general', department, process_type,
                                     process_status or 'active'], ensure_ascii=False).encode()).hexdigest()


def find_duplicate(connection, session, kb, filename, content_hash, category, department,
                   process_type, process_status, identity=None):
    if identity:
        parent = connection.execute('SELECT * FROM logical_documents WHERE logical_document_id = ?', (identity,)).fetchone()
        if not parent or parent['storage_session_id'] != session or parent['knowledge_base_id'] != kb:
            raise VersionConflict('逻辑文档不属于当前知识库。')
    # Explicit identity ignores filenames; implicit uploads never join a version chain.
    scope = 'logical_document_id = ?' if identity else 'session_id = ? AND knowledge_base_id = ? AND file_name IS ?'
    params = [identity] if identity else [session, kb, filename]
    if identity:
        scope += ' AND knowledge_base_type = ? AND department IS ? AND process_type IS ? AND process_status = ?'
        params.extend([category or 'general', department, process_type, process_status or 'active'])
    else:
        scope += ' AND logical_document_id IN (SELECT logical_document_id FROM logical_documents WHERE version_counter = 1)'
    hash_column = 'content_sha256' if identity else 'content_hash'
    return connection.execute(f'SELECT * FROM documents WHERE {scope} AND {hash_column} = ? ORDER BY id DESC LIMIT 1',
                              (*params, content_hash)).fetchone()


def migrate_documents(connection):
    """One independent V1 per legacy row; never infer identity from a filename."""
    for row in connection.execute('SELECT * FROM documents WHERE logical_document_id IS NULL').fetchall():
        attach_document(connection, row['id'], legacy=True)


def create_in_connection(connection, storage_session_id, knowledge_base_id, display_name):
    identity = str(uuid4())
    connection.execute('''INSERT INTO logical_documents
        (logical_document_id, storage_session_id, knowledge_base_id, display_name, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)''', (identity, storage_session_id, knowledge_base_id,
                                       display_name or '未命名文档', now(), now()))
    return identity


def attach_document(connection, document_id, *, logical_document_id=None, legacy=False):
    doc = connection.execute('SELECT * FROM documents WHERE id = ?', (document_id,)).fetchone()
    if doc['logical_document_id']:
        return
    identity = logical_document_id or create_in_connection(connection, doc['session_id'],
                                                          doc['knowledge_base_id'], doc['file_name'])
    parent = connection.execute('SELECT * FROM logical_documents WHERE logical_document_id = ?', (identity,)).fetchone()
    if not parent or parent['storage_session_id'] != doc['session_id'] or parent['knowledge_base_id'] != doc['knowledge_base_id']:
        raise VersionConflict('逻辑文档不属于当前知识库。')
    task = connection.execute('SELECT status FROM rag_index_tasks WHERE document_id = ?', (document_id,)).fetchone()
    legacy = legacy and doc['lifecycle_state'] == 'active' and (not task or task['status'] in ('ready', 'not_requested'))
    content = '\n\n'.join(row[0] for row in connection.execute(
        'SELECT chunk_text FROM document_chunks WHERE document_id = ? ORDER BY chunk_index, id', (document_id,)))
    content_sha256 = hashlib.sha256(content.encode()).hexdigest()
    number = parent['version_counter'] + 1
    connection.execute('UPDATE logical_documents SET version_counter = ?, updated_at = ? WHERE logical_document_id = ?',
                       (number, now(), identity))
    connection.execute('''UPDATE documents SET logical_document_id = ?, version_number = ?, content_sha256 = ?,
        metadata_sha256 = ?, auto_publish_version = ?, published_at = ? WHERE id = ?''',
        (identity, number, content_sha256, metadata_hash(doc['knowledge_base_type'], doc['department'],
         doc['process_type'], doc['process_status']), parent['state_version'], now() if legacy else None, document_id))
    if legacy and doc['lifecycle_state'] == 'active':
        connection.execute('UPDATE logical_documents SET current_document_id = ? WHERE logical_document_id = ?',
                           (document_id, identity))


def create_logical_document(storage_session_id, knowledge_base_id, display_name):
    with closing(get_connection()) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        identity = create_in_connection(connection, storage_session_id, knowledge_base_id, display_name)
    return get_logical_document(identity)


def get_logical_document(identity):
    with closing(get_connection()) as connection:
        connection.execute('BEGIN')
        row = connection.execute('SELECT * FROM logical_documents WHERE logical_document_id = ?', (identity,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result['versions'] = [dict(item) for item in connection.execute('''SELECT d.id AS document_id,
            d.file_name, d.version_number, d.published_at, d.lifecycle_state, d.lifecycle_version,
            d.knowledge_base_type, d.department, d.process_type, d.process_status,
            COALESCE(t.status, 'legacy') AS index_status FROM documents d LEFT JOIN rag_index_tasks t
            ON t.document_id = d.id WHERE d.logical_document_id = ? ORDER BY d.version_number DESC''', (identity,))]
        return result


def list_logical_documents(knowledge_base_id, *, storage_session_id=None):
    with closing(get_connection()) as connection:
        scope = 'knowledge_base_id = ?'
        params = [knowledge_base_id]
        if storage_session_id is not None:
            scope += ' AND storage_session_id = ?'
            params.append(storage_session_id)
        return [dict(row) for row in connection.execute(f'''SELECT * FROM logical_documents
            WHERE {scope} ORDER BY created_at DESC, logical_document_id''', params)]


def document_version(document_id):
    with closing(get_connection()) as connection:
        return _document_version_in_connection(connection, document_id)


def _document_version_in_connection(connection, document_id):
    row = connection.execute('''SELECT d.logical_document_id, d.version_number, d.published_at,
        l.state_version AS logical_state_version, l.enabled AS logical_enabled,
        l.current_document_id, l.display_name FROM documents d JOIN logical_documents l
        ON l.logical_document_id = d.logical_document_id WHERE d.id = ?''', (document_id,)).fetchone()
    if not row:
        return {}
    result = dict(row)
    result['is_current_version'] = bool(row['logical_enabled'] and row['current_document_id'] == document_id)
    return result


def publish_document(document_id, expected_state_version, *, automatic=False, retrieval_mode='keyword'):
    if type(expected_state_version) is not int or expected_state_version < 1:
        raise ValueError('需要有效的逻辑文档状态版本。')
    with closing(get_connection()) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        doc = connection.execute('SELECT * FROM documents WHERE id = ?', (document_id,)).fetchone()
        if not doc:
            raise LookupError('版本不存在。')
        parent = connection.execute('SELECT * FROM logical_documents WHERE logical_document_id = ?',
                                    (doc['logical_document_id'],)).fetchone()
        task = connection.execute('SELECT status FROM rag_index_tasks WHERE document_id = ?', (document_id,)).fetchone()
        if (doc['lifecycle_state'] != 'active' or (task and task['status'] not in ('ready', 'not_requested'))
                or (not task and not doc['published_at'])):
            raise VersionConflict('只能发布已完成索引的有效版本。')
        if retrieval_mode not in ('keyword', 'vector') or (retrieval_mode == 'vector' and task and task['status'] != 'ready'):
            raise VersionConflict('向量检索模式需要 ready 的索引版本。')
        if automatic and (doc['published_at'] or not parent['enabled']):
            return False
        cursor = connection.execute('''UPDATE logical_documents SET current_document_id = ?, enabled = 1,
            state_version = state_version + 1, updated_at = ?
            WHERE logical_document_id = ? AND state_version = ?''',
            (document_id, now(), doc['logical_document_id'], expected_state_version))
        if cursor.rowcount != 1:
            raise VersionConflict('当前版本已变化，请刷新后重新确认发布。')
        connection.execute('UPDATE documents SET published_at = ? WHERE id = ?', (now(), document_id))
        return True


def auto_publish(document_id, *, retrieval_mode='keyword'):
    with closing(get_connection()) as connection:
        row = connection.execute('SELECT auto_publish_version FROM documents WHERE id = ?', (document_id,)).fetchone()
    if row:
        try:
            return publish_document(document_id, row[0], automatic=True, retrieval_mode=retrieval_mode)
        except VersionConflict:
            return False  # Ready candidate remains available for an explicit CAS publication.
    return False


def disable_document(identity, expected_state_version):
    with closing(get_connection()) as connection, connection:
        cursor = connection.execute('''UPDATE logical_documents SET enabled = 0, state_version = state_version + 1,
            updated_at = ? WHERE logical_document_id = ? AND state_version = ?''',
            (now(), identity, expected_state_version))
        if cursor.rowcount != 1:
            raise VersionConflict('逻辑文档状态已变化。')


# SQLite is authoritative even when Chroma still contains historical or orphaned vectors.
def visible_sql(alias='documents'):
    return (f"{alias}.lifecycle_state = 'active' AND EXISTS (SELECT 1 FROM logical_documents lv "
            f"WHERE lv.logical_document_id = {alias}.logical_document_id AND lv.enabled = 1 "
            f"AND lv.current_document_id = {alias}.id)")

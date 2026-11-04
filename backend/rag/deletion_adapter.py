"""只删稳定 ID 并读回核验；不生成 Embedding，不创建 Collection。"""

from backend.rag import vector_store
from backend.rag.index_adapters import _store_fingerprint


def build_deletion_target():
    return dict(collection_name=vector_store.COLLECTION_NAME, vector_store_fingerprint=_store_fingerprint())


class ChromaDeletionAdapter:
    def __init__(self, collection_factory=None):
        self.collection_factory = collection_factory

    def delete(self, task):
        if task['target_store'] != build_deletion_target():
            raise ValueError('删除目标存储配置已变化。')
        ids = task['vector_ids']
        if not ids:
            return
        if len(set(ids)) != len(ids) or any(not value.startswith(task['storage_session_id'] + ':') for value in ids):
            raise ValueError('删除向量范围不合法。')
        if self.collection_factory is not None:
            collection = self.collection_factory()
        else:
            # 空的新库不为删除而创建存储，也不把任意连接错误当作 Collection 不存在。
            from pathlib import Path
            location = Path(vector_store.settings.vector_store_dir)
            if not location.is_absolute():
                location = vector_store.PROJECT_ROOT / location
            if not (location / 'chroma.sqlite3').exists():
                return
            from chromadb.errors import NotFoundError
            try:
                collection = vector_store.get_chroma_collection(create_if_missing=False)
            except NotFoundError:
                return
        if collection.name != task['target_store']['collection_name']:
            raise ValueError('删除 Collection 不匹配。')
        for start in range(0, len(ids), 100):
            batch = ids[start:start + 100]
            collection.delete(ids=batch)
            if collection.get(ids=batch, include=[])['ids']:
                raise RuntimeError('向量删除结果未确认。')
        # 再核对整组目标，避免只确认最后一批。
        for start in range(0, len(ids), 100):
            if collection.get(ids=ids[start:start + 100], include=[])['ids']:
                raise RuntimeError('向量仍然存在。')

"""Persist optional document categories with stable IDs."""

from contextlib import closing
from uuid import uuid4

from backend.db.connection import get_connection

CUSTOM_CATEGORY_PREFIX = "cat_"


class CategoryConflict(ValueError):
    """The category name or its document references prevent the operation."""


def seed_builtin_categories(connection, categories):
    connection.executemany(
        """INSERT INTO document_categories (id, label, label_key, description)
           SELECT ?, ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM document_categories WHERE id = ?)""",
        [(row["category_id"], row["label"], row["label"].casefold(), row["description"], row["category_id"])
         for row in categories],
    )


def list_categories():
    with closing(get_connection()) as connection:
        return [dict(row) for row in connection.execute(
            """SELECT id AS category_id, id AS knowledge_base_type, label, description
               FROM document_categories WHERE enabled = 1 ORDER BY label_key, id"""
        )]


def category_exists(connection, category_id):
    return connection.execute("SELECT 1 FROM document_categories WHERE id = ? AND enabled = 1", (category_id,)).fetchone() is not None


def active_category_exists(category_id):
    with closing(get_connection()) as connection:
        return category_exists(connection, category_id)


def save_category(label, description, category_id=None):
    with closing(get_connection()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        label_key = label.casefold()
        duplicate = connection.execute("SELECT id FROM document_categories WHERE label_key = ?", (label_key,)).fetchone()
        if duplicate and duplicate["id"] != category_id:
            raise CategoryConflict("已有同名分类。")
        if category_id is None:
            category_id = CUSTOM_CATEGORY_PREFIX + uuid4().hex
            connection.execute("INSERT INTO document_categories (id, label, label_key, description) VALUES (?, ?, ?, ?)",
                               (category_id, label, label_key, description))
        else:
            updated = connection.execute(
                "UPDATE document_categories SET label = ?, label_key = ?, description = ? WHERE id = ? AND enabled = 1",
                (label, label_key, description, category_id),
            )
            if not updated.rowcount:
                raise LookupError("分类不存在或已删除。")
    return dict(category_id=category_id, knowledge_base_type=category_id, label=label, description=description)


def delete_category(category_id, *, keep_record: bool):
    with closing(get_connection()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT 1 FROM documents WHERE knowledge_base_type = ?", (category_id,)).fetchone():
            raise CategoryConflict("仍有文档或历史版本使用此分类。请先删除相关版本，再删除分类。")
        statement = ("UPDATE document_categories SET enabled = 0 WHERE id = ? AND enabled = 1"
                     if keep_record else "DELETE FROM document_categories WHERE id = ?")
        if not connection.execute(statement, (category_id,)).rowcount:
            raise LookupError("分类不存在或已删除。")

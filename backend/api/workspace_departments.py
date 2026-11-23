"""Administrator-managed department catalog."""
import sqlite3
from contextlib import closing

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from backend.api.workspace_auth import require_admin, verify_admin
from backend.db.connection import get_connection
from backend.db.departments import get_department, routing_key

router = APIRouter(prefix='/workspace/departments', tags=['部门管理'])


class DepartmentName(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)


def department_row(connection, department_id):
    row = get_department(connection, department_id)
    if row is None:
        raise HTTPException(404, '部门不存在或已删除。')
    return row


@router.get('', dependencies=[Depends(require_admin)])
def list_departments():
    with closing(get_connection()) as connection:
        return [dict(row) for row in connection.execute('''SELECT d.id, d.name,
            (SELECT COUNT(*) FROM workspace_users u WHERE u.department = d.name) AS user_count,
            (SELECT COUNT(*) FROM process_approval_rules r WHERE r.department_key = d.name_key) AS rule_count
            FROM workspace_departments d ORDER BY d.name_key, d.id''')]


@router.post('', status_code=201)
def create_department(data: DepartmentName, admin=Depends(require_admin)):
    with closing(get_connection()) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        verify_admin(connection, admin['id'])
        cursor = connection.execute('INSERT INTO workspace_departments (name, name_key) VALUES (?, ?) ON CONFLICT(name_key) DO NOTHING',
                                    (data.name, routing_key(data.name)))
        if not cursor.rowcount:
            raise HTTPException(409, '已存在同名部门。')
        return {'id': cursor.lastrowid, 'name': data.name}


@router.patch('/{department_id}')
def rename_department(department_id: int, data: DepartmentName, admin=Depends(require_admin)):
    key = routing_key(data.name)
    with closing(get_connection()) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        verify_admin(connection, admin['id'])
        old = department_row(connection, department_id)
        try:
            connection.execute('UPDATE workspace_departments SET name = ?, name_key = ? WHERE id = ?', (data.name, key, department_id))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, '已存在同名部门。') from exc
        connection.execute('UPDATE workspace_users SET department = ? WHERE department = ?', (data.name, old['name']))
        connection.execute('UPDATE process_approval_rules SET department = ?, department_key = ? WHERE department_key = ?',
                           (data.name, key, old['name_key']))
    return {'id': department_id, 'name': data.name}


@router.delete('/{department_id}')
def delete_department(department_id: int, admin=Depends(require_admin)):
    with closing(get_connection()) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        verify_admin(connection, admin['id'])
        row = department_row(connection, department_id)
        if connection.execute('''SELECT 1 FROM workspace_users WHERE department = ?
            UNION ALL SELECT 1 FROM process_approval_rules WHERE department_key = ? LIMIT 1''',
            (row['name'], row['name_key'])).fetchone():
            raise HTTPException(409, '此部门关联用户或审批规则，无法删除。')
        connection.execute('DELETE FROM workspace_departments WHERE id = ?', (department_id,))
    return {'deleted': True}

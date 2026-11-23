"""Administrator-managed routing by request type and account department."""
from contextlib import closing

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from backend.api.workspace_auth import require_admin, require_module, verify_admin
from backend.db.connection import get_connection
from backend.db.departments import get_department, routing_key

router = APIRouter(tags=['审批规则'])
ELIGIBLE_APPROVER = """u.enabled = 1 AND (u.module_permissions_json IS NULL OR EXISTS (
    SELECT 1 FROM json_each(u.module_permissions_json) WHERE value = 'tickets'))"""


class ApprovalRule(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    application_type: str = Field(min_length=1, max_length=100)
    department_id: int = Field(gt=0, strict=True)
    approver_id: int = Field(gt=0)


class ApprovalAssignee(BaseModel):
    model_config = ConfigDict(extra='forbid')
    approver_id: int = Field(gt=0)


def _applicant(connection, user_id: int):
    user = connection.execute('SELECT * FROM workspace_users WHERE id = ? AND enabled = 1', (user_id,)).fetchone()
    if not user:
        raise HTTPException(401, '账号已停用。')
    if not user['department']:
        raise HTTPException(422, '当前账号尚未设置所属部门，请联系管理员。')
    return user


def resolve_approver(connection, user_id: int, application_type: str):
    user = _applicant(connection, user_id)
    approver = connection.execute(f'''SELECT u.id, u.display_name FROM process_approval_rules r
        JOIN workspace_users u ON u.id = r.approver_id
        WHERE r.application_key = ? AND r.department_key = ? AND {ELIGIBLE_APPROVER} AND u.id <> ?''',
        (routing_key(application_type), routing_key(user['department']), user_id)).fetchone()
    if not approver:
        raise HTTPException(422, '此申请类型和部门没有可用的审批人，请联系管理员检查审批规则。')
    return user, approver


@router.get('/process_ticket_routes')
def available_routes(user=Depends(require_module('workflow'))):
    with closing(get_connection()) as connection:
        applicant = _applicant(connection, user['id'])
        routes = connection.execute(f'''SELECT r.application_type, u.id AS approver_id, u.display_name AS approver_name
            FROM process_approval_rules r JOIN workspace_users u ON u.id = r.approver_id
            WHERE r.department_key = ? AND {ELIGIBLE_APPROVER} AND u.id <> ? ORDER BY r.application_key''',
            (routing_key(applicant['department']), user['id'])).fetchall()
        return {'department': applicant['department'], 'routes': [dict(route) for route in routes]}


@router.get('/process_approval_rules', dependencies=[Depends(require_admin)])
def list_rules():
    with closing(get_connection()) as connection:
        return [dict(row) for row in connection.execute(f'''SELECT r.id, r.department, r.application_type,
            r.approver_id, u.display_name AS approver_name, ({ELIGIBLE_APPROVER}) AS approver_enabled
            FROM process_approval_rules r JOIN workspace_users u ON u.id = r.approver_id
            ORDER BY r.department_key, r.application_key''')]


@router.post('/process_approval_rules')
def save_rule(data: ApprovalRule, admin=Depends(require_admin)):
    with closing(get_connection()) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        verify_admin(connection, admin['id'])
        department = get_department(connection, data.department_id)
        if department is None:
            raise HTTPException(422, '所选部门不存在或已删除。')
        if not connection.execute(f'SELECT 1 FROM workspace_users u WHERE u.id = ? AND {ELIGIBLE_APPROVER}', (data.approver_id,)).fetchone():
            raise HTTPException(422, '请选择已启用且开通工单中心的审批账号。')
        if not connection.execute('''INSERT INTO process_approval_rules
            (application_type, department, application_key, department_key, approver_id) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(application_key, department_key) DO NOTHING''',
            (data.application_type, department['name'], routing_key(data.application_type), department['name_key'], data.approver_id)).rowcount:
            raise HTTPException(409, '此部门和申请类型已有审批规则，请编辑现有规则。')
    return {'saved': True}


@router.patch('/process_approval_rules/{rule_id}')
def update_rule(rule_id: int, data: ApprovalAssignee, admin=Depends(require_admin)):
    with closing(get_connection()) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        verify_admin(connection, admin['id'])
        if not connection.execute(f'SELECT 1 FROM workspace_users u WHERE u.id = ? AND {ELIGIBLE_APPROVER}', (data.approver_id,)).fetchone():
            raise HTTPException(422, '请选择已启用且开通工单中心的审批账号。')
        if not connection.execute('UPDATE process_approval_rules SET approver_id = ? WHERE id = ?', (data.approver_id, rule_id)).rowcount:
            raise HTTPException(404, '审批规则不存在。')
    return {'saved': True}


@router.delete('/process_approval_rules/{rule_id}')
def delete_rule(rule_id: int, admin=Depends(require_admin)):
    with closing(get_connection()) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        verify_admin(connection, admin['id'])
        if not connection.execute('DELETE FROM process_approval_rules WHERE id = ?', (rule_id,)).rowcount:
            raise HTTPException(404, '审批规则不存在。')
    return {'deleted': True}

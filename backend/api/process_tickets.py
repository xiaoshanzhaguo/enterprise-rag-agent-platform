"""工单创建、查询和审批。"""
from contextlib import closing
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.api.process_approval_rules import resolve_approver
from backend.api.workspace_auth import check_module_access, require_module
from backend.db.connection import get_connection
from backend.schema.process_schema import ProcessFields
from backend.services.process_ticket_events import deliver_ticket_event, enqueue_ticket_event

router = APIRouter(tags=["工单审批"])


class TicketCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    request_id: UUID
    fields: ProcessFields
    expected_approver_id: int = Field(gt=0, strict=True)
    expected_department: str = Field(min_length=1, max_length=80)

    @model_validator(mode='after')
    def require_application_type(self):
        if not self.fields.application_type or len(self.fields.application_type) > 100:
            raise ValueError('申请类型必须为 1 至 100 个字符。')
        return self


class TicketDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approved", "rejected"]
    note: str = Field(default="", max_length=500)


def ticket_row(connection, request_id):
    row = connection.execute("""SELECT t.*, u.display_name AS approver_name FROM process_tickets t
        JOIN workspace_users u ON u.id = t.approver_id WHERE request_id = ?""", (str(request_id),)).fetchone()
    if row is None:
        raise HTTPException(404, "工单不存在。")
    return row


def public_ticket(row, user_id):
    return dict(request_id=row["request_id"], ticket_number=f"WO-{row['number']:06d}",
                fields=ProcessFields.model_validate_json(row["fields_json"]), status=row["status"],
                approver_name=row["approver_name"], decision_note=row["decision_note"],
                created_at=row["created_at"], decided_at=row["decided_at"],
                can_decide=row["approver_id"] == user_id and row["status"] == "pending")


@router.post("/process_tickets", status_code=201)
def create_ticket(request: TicketCreate, background: BackgroundTasks, user=Depends(require_module('workflow'))):
    with closing(get_connection()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        if not connection.execute("SELECT 1 FROM workspace_users WHERE id = ? AND enabled = 1", (user["id"],)).fetchone():
            raise HTTPException(401, "账号已停用。")
        existing = connection.execute("SELECT 1 FROM process_tickets WHERE request_id = ?", (str(request.request_id),)).fetchone()
        if existing:
            row = ticket_row(connection, request.request_id)
            if row["applicant_id"] != user["id"]:
                raise HTTPException(404, "工单不存在。")
            existing_fields = ProcessFields.model_validate_json(row["fields_json"])
            if row['approver_id'] != request.expected_approver_id or existing_fields.department != request.expected_department or any(
                getattr(existing_fields, key) != getattr(request.fields, key)
                for key in ('application_type', 'permission_level', 'additional_info')
            ):
                raise HTTPException(409, "该申请编号已用于其他工单内容。")
            return public_ticket(row, user["id"])
        applicant, approver = resolve_approver(connection, user['id'], request.fields.application_type)
        if approver['id'] != request.expected_approver_id or applicant['department'] != request.expected_department:
            raise HTTPException(409, {'code': 'approval_route_changed', 'message': '审批分配已更新，请重新核对后提交。'})
        fields = request.fields.model_copy(update={'applicant': applicant['display_name'], 'department': applicant['department'],
                                                   'approver': approver['display_name']})
        connection.execute(
            """INSERT INTO process_tickets (request_id, fields_json, applicant_id, approver_id, created_at)
               VALUES (?, ?, ?, ?, datetime('now'))""",
            (str(request.request_id), fields.model_dump_json(), user['id'], approver['id']),
        )
        row = ticket_row(connection, request.request_id)
        event_id = enqueue_ticket_event(connection, row, 'process_ticket.submitted')
    background.add_task(deliver_ticket_event, event_id)
    return public_ticket(row, user['id'])


@router.get("/process_tickets")
def list_tickets(view: Literal["mine", "assigned"] = "mine", user=Depends(require_module('tickets', 'workflow'))):
    if view == 'assigned':
        check_module_access(user, 'tickets')
    column = "applicant_id" if view == "mine" else "approver_id"
    with closing(get_connection()) as connection:
        rows = connection.execute(f"""SELECT t.*, u.display_name AS approver_name FROM process_tickets t
            JOIN workspace_users u ON u.id = t.approver_id WHERE t.{column} = ?
            ORDER BY (t.status = 'pending') DESC, t.number DESC LIMIT 100""", (user["id"],))
        return [public_ticket(row, user["id"]) for row in rows]


@router.get("/process_tickets/pending-count")
def count_pending_approvals(user=Depends(require_module('tickets'))):
    with closing(get_connection()) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM process_tickets WHERE approver_id = ? AND status = 'pending'", (user['id'],)
        ).fetchone()[0]
    return {'pending_count': count}


@router.get("/process_tickets/{request_id}")
def read_ticket(request_id: UUID, user=Depends(require_module('tickets', 'workflow'))):
    with closing(get_connection()) as connection:
        row = ticket_row(connection, request_id)
        if user["id"] != row["applicant_id"] and (user["id"] != row["approver_id"] or 'tickets' not in user['module_permissions']):
            raise HTTPException(404, "工单不存在。")
        return public_ticket(row, user["id"])


@router.post("/process_tickets/{request_id}/decision")
def decide_ticket(request_id: UUID, request: TicketDecision, background: BackgroundTasks, user=Depends(require_module('tickets'))):
    with closing(get_connection()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        if not connection.execute("SELECT 1 FROM workspace_users WHERE id = ? AND enabled = 1", (user["id"],)).fetchone():
            raise HTTPException(401, "账号已停用。")
        row = ticket_row(connection, request_id)
        if row["approver_id"] != user["id"]:
            raise HTTPException(403, "只有指定的审批人可以审批。")
        note = request.note.strip()
        if request.decision == "rejected" and not note:
            raise HTTPException(422, "请填写驳回原因。")
        changed = connection.execute(
            """UPDATE process_tickets SET status = ?, decision_note = ?, decided_at = datetime('now')
               WHERE request_id = ? AND status = 'pending'""",
            (request.decision, note or None, str(request_id)),
        )
        if changed.rowcount != 1:
            raise HTTPException(409, "工单已审批，请刷新结果。")
        row = ticket_row(connection, request_id)
        event_id = enqueue_ticket_event(connection, row, f'process_ticket.{request.decision}')
    if event_id:
        background.add_task(deliver_ticket_event, event_id)
    return public_ticket(row, user['id'])

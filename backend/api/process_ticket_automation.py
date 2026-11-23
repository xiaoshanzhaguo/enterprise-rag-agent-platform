"""Administrator view and explicit retry for durable ticket automation deliveries."""
from contextlib import closing

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from backend.api.workspace_auth import require_admin
from backend.db.connection import get_connection
from backend.services.process_ticket_events import deliver_ticket_event

router = APIRouter(tags=['工单自动化'], dependencies=[Depends(require_admin)])


class RetryEvent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_attempts: int = Field(ge=0, strict=True)


@router.get('/process_ticket_events')
def list_ticket_events():
    with closing(get_connection()) as connection:
        return [dict(row) for row in connection.execute('''SELECT e.event_id, e.event_type, e.status, e.attempts,
            e.error_message, e.updated_at, printf('WO-%06d', t.number) AS ticket_number
            FROM process_ticket_events e JOIN process_tickets t ON t.request_id = e.request_id
            ORDER BY (e.status IN ('failed', 'pending')) DESC, e.updated_at DESC, e.rowid DESC LIMIT 100''')]


@router.post('/process_ticket_events/{event_id}/retry', status_code=202)
def retry_ticket_event(event_id: str, data: RetryEvent, background: BackgroundTasks):
    with closing(get_connection()) as connection:
        row = connection.execute('SELECT status, attempts FROM process_ticket_events WHERE event_id = ?', (event_id,)).fetchone()
    if not row:
        raise HTTPException(404, '自动化事件不存在。')
    if row['status'] not in ('pending', 'failed') or row['attempts'] != data.expected_attempts:
        raise HTTPException(409, '事件状态已更新，请刷新执行记录。')
    background.add_task(deliver_ticket_event, event_id, retry=row['status'] == 'failed', expected_attempts=row['attempts'])
    return {'queued': True}

"""Persist workflow events with the ticket and deliver them after the commit."""
import json
import logging
import math
import os
from contextlib import closing
from urllib.parse import urlsplit

import httpx

from backend.config import settings
from backend.db.connection import get_connection

logger = logging.getLogger(__name__)

_DIFY_INPUT_FIELDS = ('request_id', 'event', 'event_id', 'ticket_number')
_SUBMITTED_EVENT = 'process_ticket.submitted'
_APPROVED_EVENT = 'process_ticket.approved'
_REJECTED_EVENT = 'process_ticket.rejected'


def _dify_applicant_email_enabled() -> bool:
    return settings.ticket_automation_provider == 'dify' and settings.dify_ticket_applicant_email_enabled


def _dify_email_target(event: str) -> tuple[str, str] | None:
    if event == _SUBMITTED_EVENT and settings.dify_ticket_approver_email_enabled:
        return 'approver', '审批人'
    if event in {_APPROVED_EVENT, _REJECTED_EVENT} and _dify_applicant_email_enabled():
        return 'applicant', '发起人'
    return None


def enqueue_ticket_event(connection, ticket, event_type):
    if event_type == _REJECTED_EVENT and not _dify_applicant_email_enabled():
        return None
    event_id = f"{ticket['request_id']}:{event_type}"
    fields = json.loads(ticket['fields_json'])
    payload = {
        'event_id': event_id,
        'event': event_type,
        'request_id': ticket['request_id'],
        'ticket_number': f"WO-{ticket['number']:06d}",
        'status': ticket['status'],
        'application_type': fields['application_type'],
        'department': fields['department'],
        'applicant_id': ticket['applicant_id'],
        'approver_id': ticket['approver_id'],
    }
    connection.execute('INSERT INTO process_ticket_events (event_id, request_id, event_type, payload_json) VALUES (?, ?, ?, ?)',
                       (event_id, ticket['request_id'], event_type, json.dumps(payload, ensure_ascii=False)))
    return event_id


def _deliver(payload):
    provider = settings.ticket_automation_provider
    if provider == 'dify':
        name = 'Dify'
        base_url = settings.dify_ticket_api_base_url.rstrip('/')
        url = f'{base_url}/workflows/run'
        token = settings.dify_ticket_api_key
        timeout = settings.dify_ticket_timeout_seconds
        headers = {'Authorization': f'Bearer {token}'}
        inputs = {field: payload[field] for field in _DIFY_INPUT_FIELDS}
        target = _dify_email_target(payload['event'])
        if target:
            email_field = f'{target[0]}_email'
            inputs[email_field] = payload[email_field]
        body = {
            'inputs': inputs,
            'response_mode': 'blocking',
            'user': 'zhixing-ticket-automation',
        }
        config_error = '请配置 Dify 工作流 API 基础地址、应用 API Key 和有效超时。'
    elif provider == 'n8n':
        name = 'n8n'
        url = os.getenv('N8N_TICKET_WEBHOOK_URL', '').strip()
        token = settings.n8n_webhook_token
        timeout = settings.n8n_webhook_timeout_seconds
        headers = {'X-Webhook-Token': token}
        body = {key: value for key, value in payload.items() if key not in {'approver_email', 'applicant_email'}}
        config_error = '请配置工单 Webhook 地址和令牌。'
    else:
        return '工单自动化服务配置无效，请选择 dify 或 n8n。'

    try:
        parsed = urlsplit(url)
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.fragment or not token
                or not math.isfinite(timeout) or timeout <= 0):
            return config_error
        if provider == 'dify' and (parsed.query or not parsed.path.endswith('/v1/workflows/run')):
            return config_error
    except ValueError:
        return config_error

    try:
        with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
            response = client.post(url, json=body, headers=headers)
            response.raise_for_status()
            receipt = response.json()
        if provider == 'dify':
            run = receipt.get('data') if isinstance(receipt, dict) else None
            if not isinstance(run, dict) or run.get('status') != 'succeeded':
                return 'Dify 工作流未确认成功，请在 Dify 日志中核对工单事件。'
            receipt = run.get('outputs')
        if not isinstance(receipt, dict) or receipt.get('accepted') is not True or receipt.get('event_id') != payload['event_id']:
            return f'回执无效，请先按事件编号核对 {name} 执行记录。'
    except httpx.TimeoutException:
        return f'{name} 请求超时，执行结果待确认，请先按事件编号核对执行记录。'
    except httpx.HTTPStatusError as exc:
        return f'{name} 返回 HTTP {exc.response.status_code}，请先按事件编号核对执行记录。'
    except (httpx.RequestError, httpx.InvalidURL, ValueError):
        return f'未收到有效回执，请先按事件编号核对 {name} 执行记录。'
    return None


def _prepare_dify_email(connection, event_id, payload):
    if payload['event'] == _REJECTED_EVENT and not _dify_applicant_email_enabled():
        return '请先启用 Dify 发起人邮件通知，再重试驳回通知。'
    if settings.ticket_automation_provider != 'dify':
        return None
    target = _dify_email_target(payload['event'])
    if target is None:
        return None
    role, label = target
    email_field = f'{role}_email'
    recipient = connection.execute('SELECT work_email FROM workspace_users WHERE id = ? AND enabled = 1',
                                   (payload[f'{role}_id'],)).fetchone()
    if not recipient:
        return f'工单{label}已停用，未发送邮件提醒。'
    previous_email = payload.get(email_field)
    current_email = recipient['work_email']
    if previous_email and previous_email != current_email:
        return f'{label}工作邮箱已变更，请先核对原邮件发送结果。'
    if not current_email:
        return f'{label}未配置工作邮箱，请在「用户管理」补充后重试。'
    if not previous_email:
        payload[email_field] = current_email
        connection.execute('UPDATE process_ticket_events SET payload_json = ? WHERE event_id = ?',
                           (json.dumps(payload, ensure_ascii=False), event_id))
    return None


def deliver_ticket_event(event_id, *, retry=False, expected_attempts=0):
    """Claim once, send outside the transaction, and retain the same event ID on retry."""
    try:
        allowed_status = 'failed' if retry else 'pending'
        with closing(get_connection()) as connection, connection:
            row = connection.execute('''UPDATE process_ticket_events SET status = 'sending', attempts = attempts + 1,
                error_message = NULL, updated_at = CURRENT_TIMESTAMP WHERE event_id = ? AND status = ? AND attempts = ?
                RETURNING payload_json''', (event_id, allowed_status, expected_attempts)).fetchone()
            if not row:
                return
            payload = json.loads(row['payload_json'])
            error = _prepare_dify_email(connection, event_id, payload)
        try:
            if error is None:
                error = _deliver(payload)
        except Exception:
            logger.error('Unexpected ticket event failure: %s', event_id)
            error = '自动化结果待确认，请先按事件编号核对自动化服务的执行记录。'
        with closing(get_connection()) as connection, connection:
            connection.execute('''UPDATE process_ticket_events SET status = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP
                WHERE event_id = ? AND status = 'sending' ''', ('failed' if error else 'sent', error, event_id))
    except Exception:
        logger.error('Could not update ticket event state: %s', event_id)

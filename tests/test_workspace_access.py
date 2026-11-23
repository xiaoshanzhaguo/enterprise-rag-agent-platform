from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4
from contextlib import closing
from dataclasses import replace
import json

import httpx

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from backend.api import chat, feedback, process_approval_rules, process_ticket_automation, process_tickets, rag_index, workspace_auth, workspace_departments
from backend.config import settings
from backend.db.connection import get_connection
from backend.db.init_db import init_database
from backend.db.repository import ensure_chat_session, save_chat_message
from backend.db.schema import CREATE_PROCESS_TICKET_EVENTS_SQL
from backend.services.process_ticket_events import _deliver


class WorkspaceAccessTests(TestCase):
    def setUp(self):
        self.enterContext(patch('backend.services.process_ticket_events.settings',
                                replace(settings, ticket_automation_provider='n8n')))
        directory = self.enterContext(TemporaryDirectory())
        config = SimpleNamespace(database_url="sqlite:///" + str(Path(directory) / "workspace.db"))
        self.enterContext(patch("backend.db.connection.settings", config))
        init_database(config.database_url)
        app = FastAPI()
        app.include_router(workspace_auth.router)
        app.include_router(workspace_departments.router)
        app.include_router(process_tickets.router)
        app.include_router(process_approval_rules.router)
        app.include_router(process_ticket_automation.router)
        app.include_router(chat.router)
        app.include_router(feedback.router)
        app.include_router(rag_index.router)
        self.client = self.enterContext(TestClient(app))
        self.approver = self.enterContext(TestClient(app))
        self.anonymous = self.enterContext(TestClient(app))
        self.deliver_event = self.enterContext(patch('backend.services.process_ticket_events._deliver', return_value=None))
        with closing(get_connection()) as connection, connection:
            workspace_auth.insert_user(connection, workspace_auth.NewUser(**self.account("admin", "admin")), department='IT')
        self.sign_in(self.client, "admin")
        department = self.client.post('/workspace/departments', json={'name': 'IT'})
        self.assertEqual(department.status_code, 201, department.text)
        self.department_id = department.json()['id']
        for name in ("employee", "reviewer"):
            response = self.client.post("/workspace/users", json={**self.account(name), 'department_id': self.department_id})
            self.assertEqual(response.status_code, 201, response.text)
            self.assertNotIn("password_hash", response.json())
            if name == "reviewer":
                self.reviewer_id = response.json()["id"]
        result = self.client.post('/process_approval_rules', json={
            'application_type': 'VPN', 'department_id': self.department_id, 'approver_id': self.reviewer_id,
        })
        self.assertEqual(result.status_code, 200, result.text)

    def account(self, name, role="member"):
        return {"username": name, "display_name": name.title(), "role": role, "password": "testing-password"}

    def ticket_payload(self, ticket_id):
        return {'request_id': ticket_id, 'expected_approver_id': self.reviewer_id, 'expected_department': 'IT',
                'fields': {'application_type': 'VPN', 'applicant': '其他姓名', 'department': '伪造部门', 'approver': '伪造审批人'}}

    def sign_in(self, client, name):
        response = client.post("/workspace/login", json={"username": name, "password": "testing-password"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("httponly", response.headers["set-cookie"].lower())

    def test_user_management_and_disabled_session(self):
        self.assertEqual(self.anonymous.get("/workspace/users").status_code, 401)
        self.sign_in(self.approver, "reviewer")
        self.assertEqual(self.approver.get("/workspace/users").status_code, 403)
        self.assertEqual(self.approver.post("/workspace/users", json={**self.account("intruder", "admin"),
            'department_id': self.department_id}).status_code, 403)
        self.assertEqual(self.client.post(f"/workspace/users/{self.reviewer_id}/status", json={"enabled": False}).status_code, 200)
        self.assertEqual(self.approver.get("/workspace/me").status_code, 401)

    def test_work_email_is_editable_only_in_user_management(self):
        payload = {**self.account('email-user'), 'department_id': self.department_id}
        invalid = self.client.post('/workspace/users', json={**payload, 'work_email': 'a@company.test,b@company.test'})
        self.assertEqual(invalid.status_code, 422)
        created = self.client.post('/workspace/users', json={**payload, 'work_email': ' Staff+vpn@COMPANY.TEST '})
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()['work_email'], 'Staff+vpn@company.test')
        self.sign_in(self.approver, 'email-user')
        self.assertNotIn('work_email', self.approver.get('/workspace/me').json())

        path = f"/workspace/users/{created.json()['id']}"
        profile = {'username': 'email-user', 'display_name': 'Staff', 'department_id': self.department_id,
                   'module_permissions': ['chat']}
        self.assertEqual(self.approver.patch(path, json={**profile, 'work_email': None}).status_code, 403)
        self.assertEqual(self.client.patch(path, json=profile).json()['work_email'], 'Staff+vpn@company.test')
        self.assertIsNone(self.client.patch(path, json={**profile, 'work_email': None}).json()['work_email'])

    def test_chat_drafts_history_and_pagination_are_account_scoped(self):
        self.sign_in(self.client, 'employee')
        self.sign_in(self.approver, 'reviewer')
        mode = '企业知识库问答'
        ensure_chat_session('legacy', mode=mode)
        save_chat_message('legacy', 'user', '历史共用问题')
        init_database()
        for client, session in [(self.client, 'employee-chat'), (self.approver, 'reviewer-chat')]:
            def draft(identity):
                response = client.post('/chat_session_draft', json={'session_id': identity, 'mode': mode})
                self.assertEqual(response.status_code, 200, response.text)
                return response.json()['session_id']

            self.assertEqual(draft(session), session)
            self.assertEqual(draft('another-' + session), session)
            save_chat_message(session, 'user', f'问题 {session}')
            self.assertEqual(draft('draft-' + session), 'draft-' + session)
            listed = client.get('/chat_sessions?include_empty=true').json()['sessions']
            self.assertEqual({item['session_id'] for item in listed}, {session, 'draft-' + session})
            history = client.post('/chat_history', json={'mode_names': [mode]}).json()['mode_sessions'][mode]
            self.assertIn(history['session_id'], {session, 'draft-' + session})
            self.assertEqual(client.get(f'/chat_session/{session}').json()['messages'][0]['content'], f'问题 {session}')
            self.assertEqual(client.get('/chat_session/legacy').status_code, 404)

        cursor = self.client.get('/chat_session_pages?limit=1&include_empty=true').json()['next_cursor']
        self.assertTrue(cursor)
        self.assertEqual(self.approver.get('/chat_session_pages', params={'include_empty': 'true','cursor': cursor}).status_code, 400)
        self.assertEqual(self.anonymous.get('/chat_sessions').status_code, 401)

    def test_feedback_management_is_admin_only_and_keeps_review_notes_private(self):
        admin = self.client.get('/workspace/me').json()
        response = self.client.patch(f"/workspace/users/{admin['id']}", json={
            'username': admin['username'], 'display_name': admin['display_name'], 'role': 'admin',
            'department_id': self.department_id, 'module_permissions': ['chat'],
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.sign_in(self.approver, 'employee')
        self.assertEqual(self.approver.post('/chat_session', json={'session_id': 'feedback', 'mode': 'chat'}).status_code, 200)
        message_id = save_chat_message('feedback', 'assistant', '专属回答')
        feedback_path = f'/chat_session/feedback/messages/{message_id}/feedback'
        feedback = {'rating': 'unhelpful', 'reason': 'incomplete', 'comment': '建议补充示例'}
        self.assertEqual(self.approver.post(feedback_path, json=feedback).status_code, 200)
        review_path = f'/answer_feedback/{message_id}/review'
        review = {'status': 'resolved', 'expected_revision': 1, 'note': '内部处理说明'}
        for client, expected in ((self.anonymous, 401), (self.approver, 403)):
            self.assertEqual(client.get('/answer_feedback').status_code, expected)
            self.assertEqual(client.post(review_path, json=review).status_code, expected)
        item = self.client.get('/answer_feedback').json()['items'][0]
        self.assertEqual(item['comment'], feedback['comment'])
        self.assertEqual(self.client.post(review_path, json=review).json()['review_note'], review['note'])
        repeated = self.approver.post(feedback_path, json=feedback)
        self.assertEqual(repeated.status_code, 200)
        self.assertTrue({'review_note', 'review_status', 'revision'}.isdisjoint(repeated.json()))
        self.assertNotIn(review['note'], self.approver.get('/chat_session/feedback').text)

    def test_foreign_chat_cannot_be_read_modified_or_used_for_streaming(self):
        self.sign_in(self.client, 'employee')
        # Even an administrator can only open their own sessions in the chat workspace.
        self.sign_in(self.approver, 'admin')
        self.assertEqual(self.client.post('/chat_session', json={'session_id': 'private', 'mode': 'chat'}).status_code, 200)
        message_id = save_chat_message('private', 'assistant', '专属回答')

        with patch('backend.api.chat.get_client') as get_model, patch('backend.api.chat.clear_document_chunks') as clear:
            body = {'session_id': 'private', 'input_text': '查询'}
            for endpoint in ('/chat_stream', '/workflow_stream', '/agent_stream'):
                self.assertEqual(self.approver.post(endpoint, json=body).status_code, 404)
            self.assertEqual(self.approver.get('/chat_session/private').status_code, 404)
            self.assertEqual(self.approver.patch('/chat_session/private', json={'title': '改名'}).status_code, 404)
            self.assertEqual(self.approver.post('/chat_session/private/auto_title').status_code, 404)
            self.assertEqual(self.approver.delete('/chat_session/private').status_code, 404)
            self.assertEqual(self.approver.delete('/clear_document/private').status_code, 404)
            self.assertEqual(self.approver.get('/rag_status/private').status_code, 404)
            self.assertEqual(self.approver.post('/rag_preview', json={'session_id': 'private', 'query': '查询'}).status_code, 404)
            self.assertEqual(self.approver.post('/index_document', json={
                'session_id': 'private', 'document_text': '越权写入',
            }).status_code, 404)
            operation = f'/rag_index_operations/{uuid4()}'
            self.assertEqual(self.approver.get(operation, params={'session_id': 'private'}).status_code, 404)
            for action in ('resume', 'retry'):
                self.assertEqual(self.approver.post(f'{operation}/{action}', params={'session_id': 'private'},
                    json={'expected_state_version': 1}).status_code, 404)
            self.assertEqual(self.approver.get('/rag_status/private?knowledge_base_id=session_scoped').status_code, 400)
            self.assertEqual(self.client.post('/agent_stream', json={**body, 'user_options': {
                'knowledge_base_id': ' session_scoped ',
            }}).status_code, 400)
            feedback_path = f'/chat_session/private/messages/{message_id}/feedback'
            self.assertEqual(self.approver.post(feedback_path, json={'rating': 'helpful'}).status_code, 404)
            self.assertEqual(self.client.post(feedback_path, json={'rating': 'helpful'}).status_code, 200)
            self.assertEqual(self.client.get('/answer_feedback').status_code, 403)
            self.assertEqual(self.approver.get('/answer_feedback').status_code, 200)
            self.assertEqual(self.approver.post('/chat_session', json={'session_id': 'private', 'mode': 'chat'}).status_code, 409)
            get_model.assert_not_called()
            clear.assert_not_called()

        with patch('backend.api.chat.get_client', return_value=object()), patch('backend.api.chat.run_agent_stream',
                return_value=StreamingResponse(iter(['data: ok\n\n']), media_type='text/event-stream')) as stream:
            self.assertEqual(self.client.post('/agent_stream', json=body).text, 'data: ok\n\n')
            self.assertEqual(stream.call_args.args[0].session_id, 'private')

    def test_legacy_ticket_schema_is_archived_without_losing_records(self):
        with closing(get_connection()) as connection, connection:
            connection.execute("DROP TABLE process_ticket_events")
            connection.execute("DROP TABLE process_tickets")
            connection.execute("""CREATE TABLE process_tickets (
                request_id TEXT PRIMARY KEY, applicant_key_hash TEXT, approval_key_hash TEXT
            )""")
            connection.execute("INSERT INTO process_tickets VALUES ('old-request', 'read-hash', 'approve-hash')")

        init_database()
        init_database()

        with closing(get_connection()) as connection:
            archived = connection.execute("SELECT request_id FROM process_tickets_legacy_codes").fetchall()
            self.assertEqual([row[0] for row in archived], ["old-request"])
            self.assertEqual(connection.execute("SELECT COUNT(approver_id) FROM process_tickets").fetchone()[0], 0)
            indexes = {row[1] for row in connection.execute("PRAGMA index_list(process_tickets)")}
            self.assertIn("idx_ticket_approver_status", indexes)

    def test_workflow_only_user_can_list_only_their_own_tickets(self):
        employee = next(user for user in self.client.get('/workspace/users').json() if user['username'] == 'employee')
        updated = self.client.patch(f"/workspace/users/{employee['id']}", json={
            'username': 'employee', 'display_name': employee['display_name'],
            'department_id': self.department_id, 'module_permissions': ['workflow'],
        })
        self.assertEqual(updated.status_code, 200, updated.text)
        other = self.client.post('/process_tickets', json=self.ticket_payload(str(uuid4())))
        self.assertEqual(other.status_code, 201, other.text)
        self.sign_in(self.anonymous, 'employee')
        own = self.anonymous.post('/process_tickets', json=self.ticket_payload(str(uuid4())))
        self.assertEqual(own.status_code, 201, own.text)
        result = self.anonymous.get('/process_tickets?view=mine')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual([ticket['request_id'] for ticket in result.json()], [own.json()['request_id']])
        self.assertEqual(self.anonymous.get('/process_tickets?view=assigned').status_code, 403)
        self.assertEqual(self.anonymous.get('/process_tickets/pending-count').status_code, 403)
        self.assertEqual(self.anonymous.post(f"/process_tickets/{own.json()['request_id']}/decision",
                                             json={'decision': 'approved'}).status_code, 403)

    def test_ticket_creation_idempotency_and_assignment(self):
        self.sign_in(self.client, "employee")
        self.sign_in(self.approver, "reviewer")
        ticket_id = str(uuid4())
        payload = self.ticket_payload(ticket_id)
        created = self.client.post("/process_tickets", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["fields"]["applicant"], "Employee")
        self.assertEqual(created.json()['fields']['department'], 'IT')
        self.assertEqual(created.json()['fields']['approver'], 'Reviewer')
        repeated = self.client.post("/process_tickets", json=payload)
        self.assertEqual(repeated.json()["ticket_number"], created.json()["ticket_number"])
        self.deliver_event.assert_called_once()
        self.assertEqual(len(self.client.get("/process_tickets").json()), 1)
        self.assertEqual(self.anonymous.get('/process_tickets/pending-count').status_code, 401)
        self.assertEqual(self.client.get('/process_tickets/pending-count').json(), {'pending_count': 0})
        self.assertEqual(self.approver.get('/process_tickets/pending-count').json(), {'pending_count': 1})
        path = f"/process_tickets/{ticket_id}"
        decision = {"decision": "rejected", "note": "请补充用途"}
        self.assertEqual(self.anonymous.get(path).status_code, 401)
        self.assertEqual(self.client.post(path + "/decision", json=decision).status_code, 403)
        self.assertTrue(self.approver.get("/process_tickets?view=assigned").json()[0]["can_decide"])
        self.assertEqual(self.approver.post(path + "/decision", json={"decision": "rejected"}).status_code, 422)
        self.assertEqual(self.approver.post(path + "/decision", json=decision).json()["status"], "rejected")
        self.assertEqual(self.approver.get('/process_tickets/pending-count').json(), {'pending_count': 0})
        self.assertEqual(self.approver.post(path + "/decision", json=decision).status_code, 409)
        self.assertEqual(self.client.get(path).json()["decision_note"], decision["note"])
        self.approver.post("/workspace/logout")
        self.assertEqual(self.approver.get(path).status_code, 401)

    def test_pending_approver_cannot_be_disabled_until_decision(self):
        self.sign_in(self.approver, "reviewer")
        ticket_id = str(uuid4())
        payload = self.ticket_payload(ticket_id)
        self.assertEqual(self.client.post("/process_tickets", json=payload).status_code, 201)
        disable = f"/workspace/users/{self.reviewer_id}/status"
        self.assertEqual(self.client.post(disable, json={"enabled": False}).status_code, 409)
        decision = self.approver.post(f"/process_tickets/{ticket_id}/decision", json={"decision": "approved"})
        self.assertEqual(decision.json()["status"], "approved")
        self.assertIsNotNone(decision.json()["decided_at"])
        self.assertEqual(self.client.post(disable, json={"enabled": False}).status_code, 200)

    def test_approval_rule_is_admin_only_and_stale_confirmation_cannot_create_a_ticket(self):
        self.sign_in(self.approver, 'employee')
        route = self.approver.get('/process_ticket_routes')
        self.assertEqual(route.json(), {'department': 'IT', 'routes': [
            {'application_type': 'VPN', 'approver_id': self.reviewer_id, 'approver_name': 'Reviewer'},
        ]})
        self.assertEqual(self.approver.get('/process_approval_rules').status_code, 403)
        self.assertEqual(self.approver.get('/process_ticket_events').status_code, 403)
        self.assertEqual(self.approver.post('/process_approval_rules', json={'department_id': self.department_id, 'application_type': 'VPN', 'approver_id': 1}).status_code, 403)

        admin_id = self.client.get('/workspace/me').json()['id']
        payload = self.ticket_payload(str(uuid4()))
        payload['fields']['application_type'] = ' ｖｐｎ '
        self.assertEqual(self.approver.post('/process_tickets', json={**payload, 'approver_id': admin_id}).status_code, 422)
        rule = self.client.get('/process_approval_rules').json()[0]
        changed = self.client.patch(f"/process_approval_rules/{rule['id']}", json={'approver_id': admin_id})
        self.assertEqual(changed.status_code, 200, changed.text)
        response = self.approver.post('/process_tickets', json=payload)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['detail']['code'], 'approval_route_changed')
        self.assertEqual(self.approver.get('/process_tickets').json(), [])
        self.deliver_event.assert_not_called()

    def test_missing_disabled_and_self_approval_require_administrator_configuration(self):
        self.sign_in(self.approver, 'reviewer')
        self.assertEqual(self.approver.get('/process_ticket_routes').json(), {'department': 'IT', 'routes': []})
        missing = self.ticket_payload(str(uuid4()))
        missing['fields']['application_type'] = 'Other'
        self.assertEqual(self.client.post('/process_tickets', json=missing).status_code, 422)
        self.assertEqual(self.client.post(f'/workspace/users/{self.reviewer_id}/status', json={'enabled': False}).status_code, 200)
        self.assertEqual(self.client.post('/process_tickets', json=self.ticket_payload(str(uuid4()))).status_code, 422)
        with closing(get_connection()) as connection, connection:
            workspace_auth.insert_user(connection, workspace_auth.NewUser(**self.account('unassigned')))
        self.sign_in(self.approver, 'unassigned')
        self.assertIn('尚未设置所属部门', self.approver.get('/process_ticket_routes').json()['detail'])

    def test_department_catalog_requires_admin_and_protects_referenced_departments(self):
        path = '/workspace/departments'
        self.sign_in(self.approver, 'employee')
        self.assertEqual(self.approver.get(path).status_code, 403)
        self.assertEqual(self.approver.post(path, json={'name': '其他部门'}).status_code, 403)
        self.assertEqual(self.client.post(path, json={'name': ' ｉｔ '}).status_code, 409)
        self.assertEqual(self.client.delete(f'{path}/{self.department_id}').status_code, 409)
        empty = self.client.post(path, json={'name': '临时部门'}).json()
        self.assertEqual(self.client.delete(f"{path}/{empty['id']}").status_code, 200)
        self.assertEqual(self.client.post('/workspace/users', json={**self.account('late'), 'department_id': empty['id']}).status_code, 422)
        self.assertEqual(self.client.post('/workspace/users', json=self.account('missing')).status_code, 422)
        self.assertEqual(self.client.patch(f'/workspace/users/{self.reviewer_id}/department', json={'department': '其他'}).status_code, 404)

    def test_user_profile_permissions_and_eight_character_initial_password(self):
        from backend.main import app as production_app

        payload = {**self.account('limited'), 'department_id': self.department_id, 'password': '8letters',
                   'module_permissions': ['chat']}
        self.assertEqual(self.client.post('/workspace/users', json={**payload, 'password': 'short12'}).status_code, 422)
        created = self.client.post('/workspace/users', json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        profile = {'username': 'renamed', 'display_name': '改名后', 'role': 'member',
                   'department_id': self.department_id, 'module_permissions': ['tickets']}
        path = f"/workspace/users/{created.json()['id']}"
        with TestClient(production_app) as member:
            # Use the fixture database for the full production router, without starting a model.
            self.assertEqual(member.post('/workspace/login', json={'username': 'limited', 'password': '8letters'}).status_code, 200)
            self.assertEqual(member.patch(path, json=profile).status_code, 403)
            self.assertEqual(member.get('/chat_sessions').status_code, 200)
            self.assertEqual(member.get('/process_tickets').status_code, 403)
            self.assertEqual(member.post('/process_fields/extract', json={'input_text': '测试'}).status_code, 403)
            self.assertEqual(member.get('/process_applications').status_code, 403)
            self.assertEqual(member.get('/logical_documents?knowledge_base_id=enterprise_default').status_code, 403)

            updated = self.client.patch(path, json=profile)
            self.assertEqual(updated.status_code, 200, updated.text)
            self.assertEqual(updated.json()['username'], 'renamed')
            self.assertEqual(member.get('/chat_sessions').status_code, 403)
            self.assertEqual(member.get('/process_tickets').status_code, 200)
            self.assertEqual(self.client.patch(path, json={**profile, 'username': 'admin'}).status_code, 409)

    def test_pending_approval_prevents_removing_ticket_permission(self):
        payload = self.ticket_payload(str(uuid4()))
        self.assertEqual(self.client.post('/process_tickets', json=payload).status_code, 201)
        response = self.client.patch(f'/workspace/users/{self.reviewer_id}', json={
            'username': 'reviewer', 'display_name': 'Reviewer', 'department_id': self.department_id,
            'role': 'member', 'module_permissions': ['chat'],
        })
        self.assertEqual(response.status_code, 409, response.text)

    def test_chinese_username_can_be_created_renamed_and_used_for_login(self):
        payload = {**self.account('张三'), 'department_id': self.department_id}
        created = self.client.post('/workspace/users', json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(self.client.post('/workspace/users', json=payload).status_code, 409)
        self.assertEqual(self.client.post('/workspace/users', json={**payload, 'username': '张 三'}).status_code, 422)
        self.sign_in(self.approver, '张三')

        path = f"/workspace/users/{created.json()['id']}"
        updated = self.client.patch(path, json={
            'username': '李四', 'display_name': '测试员工', 'role': 'member',
            'department_id': self.department_id, 'module_permissions': ['chat'],
        })
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(self.approver.get('/workspace/me').json()['username'], '李四')
        self.assertEqual(self.anonymous.post('/workspace/login', json={
            'username': '张三', 'password': payload['password'],
        }).status_code, 401)
        self.sign_in(self.approver, '李四')

    def test_department_migration_and_rename_preserve_approval_and_ticket_history(self):
        ticket_id = str(uuid4())
        self.assertEqual(self.client.post('/process_tickets', json=self.ticket_payload(ticket_id)).status_code, 201)
        with closing(get_connection()) as connection, connection:
            workspace_auth.insert_user(connection, workspace_auth.NewUser(**self.account('legacy')), department='平台部')
        init_database()
        init_database()
        departments = self.client.get('/workspace/departments').json()
        self.assertEqual({item['name'] for item in departments}, {'平台部', 'IT'})
        renamed = self.client.patch(f'/workspace/departments/{self.department_id}', json={'name': '信息技术部'})
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.sign_in(self.approver, 'employee')
        route = self.approver.get('/process_ticket_routes').json()
        self.assertEqual(route['department'], '信息技术部')
        self.assertEqual(route['routes'][0]['approver_id'], self.reviewer_id)
        self.assertEqual(self.client.get(f'/process_tickets/{ticket_id}').json()['fields']['department'], 'IT')

    def test_n8n_failure_and_retry_do_not_change_ticket_or_duplicate_events(self):
        self.sign_in(self.approver, 'reviewer')
        request_id = str(uuid4())
        payload = self.ticket_payload(request_id)
        def fail_after_commit(event):
            with closing(get_connection()) as connection:
                ticket = connection.execute('SELECT status FROM process_tickets WHERE request_id = ?', (request_id,)).fetchone()
                self.assertEqual(ticket['status'], event['status'])
            return '模拟超时'

        self.deliver_event.side_effect = fail_after_commit
        self.assertEqual(self.client.post('/process_tickets', json=payload).status_code, 201)
        self.assertEqual(self.client.post('/process_tickets', json=payload).status_code, 201)
        path = f'/process_tickets/{request_id}'
        self.assertEqual(self.approver.post(path + '/decision', json={'decision': 'approved'}).json()['status'], 'approved')
        events = self.client.get('/process_ticket_events').json()
        self.assertEqual({e['event_type'] for e in events}, {'process_ticket.submitted', 'process_ticket.approved'})
        self.assertTrue(all(e['status'] == 'failed' and e['attempts'] == 1 for e in events))
        self.assertEqual(self.deliver_event.call_count, 2)
        event = next(item for item in events if item['event_type'] == 'process_ticket.approved')
        retry_url = f"/process_ticket_events/{event['event_id']}/retry"
        self.assertEqual(self.approver.post(retry_url, json={'expected_attempts': 1}).status_code, 403)
        self.deliver_event.side_effect = None
        self.assertEqual(self.client.post(retry_url, json={'expected_attempts': 1}).status_code, 202)
        self.assertEqual(self.client.post(retry_url, json={'expected_attempts': 1}).status_code, 409)
        final_events = self.client.get('/process_ticket_events').json()
        self.assertEqual(next(e for e in final_events if e['event_id'] == event['event_id'])['status'], 'sent')
        self.assertEqual(self.client.get(path).json()['status'], 'approved')
        self.assertEqual(self.deliver_event.call_args.args[0]['event_id'], event['event_id'])

    def test_ticket_webhook_requires_the_matching_event_receipt(self):
        payload = {'event_id': 'event-1', 'event': 'process_ticket.approved'}
        url = 'http://test-n8n.invalid/webhook/process-ticket-events'
        with patch.dict('os.environ', {'N8N_TICKET_WEBHOOK_URL': url}), patch('backend.services.process_ticket_events.settings',
                SimpleNamespace(ticket_automation_provider='n8n', n8n_webhook_token='test-token', n8n_webhook_timeout_seconds=2.0)):
            def mock_response(request):
                self.assertEqual(str(request.url), url)
                self.assertEqual(request.headers['X-Webhook-Token'], 'test-token')
                self.assertEqual(json.loads(request.content), payload)
                return httpx.Response(200, json={'event_id': 'event-1', 'accepted': True})

            with patch('httpx.Client', return_value=httpx.Client(transport=httpx.MockTransport(mock_response))):
                self.assertIsNone(_deliver(payload))
            with patch('httpx.Client', return_value=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(
                    200, json={'event_id': 'another-event', 'accepted': True})))):
                self.assertIn('回执无效', _deliver(payload))

    def test_dify_ticket_events_use_workflow_api_and_record_sent_after_approval(self):
        self.sign_in(self.approver, 'reviewer')
        request_id = str(uuid4())
        requests = []
        self.deliver_event.side_effect = _deliver

        def run_workflow(request):
            self.assertEqual(str(request.url), 'https://dify.invalid/v1/workflows/run')
            self.assertEqual(request.headers['Authorization'], 'Bearer test-app-key')
            self.assertNotIn('X-Webhook-Token', request.headers)
            payload = json.loads(request.content)
            self.assertEqual(payload['response_mode'], 'blocking')
            self.assertEqual(payload['user'], 'zhixing-ticket-automation')
            inputs = payload['inputs']
            self.assertEqual(set(inputs), {'request_id', 'event', 'event_id', 'ticket_number'})
            self.assertEqual(inputs['request_id'], request_id)
            self.assertEqual(inputs['event_id'], f"{request_id}:{inputs['event']}")
            self.assertRegex(inputs['ticket_number'], r'^WO-\d{6,}$')
            requests.append(inputs['event'])
            return httpx.Response(200, json={'data': {'status': 'succeeded', 'outputs': {
                'event_id': inputs['event_id'], 'accepted': True,
            }}})

        config = SimpleNamespace(ticket_automation_provider='dify', dify_ticket_api_base_url='https://dify.invalid/v1/',
                                 dify_ticket_api_key='test-app-key', dify_ticket_timeout_seconds=2.0,
                                 dify_ticket_approver_email_enabled=False, dify_ticket_applicant_email_enabled=False)
        httpx_client = httpx.Client
        with patch('backend.services.process_ticket_events.settings', config), patch(
                'backend.services.process_ticket_events.httpx.Client',
                side_effect=lambda **kwargs: httpx_client(transport=httpx.MockTransport(run_workflow), **kwargs)):
            response = self.client.post('/process_tickets', json=self.ticket_payload(request_id))
            self.assertEqual(response.status_code, 201, response.text)
            response = self.approver.post(f'/process_tickets/{request_id}/decision', json={'decision': 'approved'})
            self.assertEqual(response.json()['status'], 'approved')

        self.assertEqual(requests, ['process_ticket.submitted', 'process_ticket.approved'])
        events = self.client.get('/process_ticket_events').json()
        self.assertEqual(len(events), 2)
        self.assertTrue(all(event['status'] == 'sent' and event['attempts'] == 1 for event in events))

    def test_dify_email_uses_assigned_approver_and_retries_after_missing_address(self):
        self.sign_in(self.approver, 'reviewer')
        self.deliver_event.side_effect = _deliver
        requests = []
        request_id = str(uuid4())
        event_id = f'{request_id}:process_ticket.submitted'
        retry_url = f'/process_ticket_events/{event_id}/retry'
        profile = {'username': 'reviewer', 'display_name': 'Reviewer', 'department_id': self.department_id,
                   'module_permissions': ['tickets']}

        def run_workflow(request):
            inputs = json.loads(request.content)['inputs']
            requests.append(inputs)
            if len(requests) == 1:
                raise httpx.ReadTimeout('simulated timeout', request=request)
            return httpx.Response(200, json={'data': {'status': 'succeeded', 'outputs': {
                'event_id': inputs['event_id'], 'accepted': True,
            }}})

        config = SimpleNamespace(ticket_automation_provider='dify', dify_ticket_api_base_url='https://dify.invalid/v1',
                                 dify_ticket_api_key='test-app-key', dify_ticket_timeout_seconds=2.0,
                                 dify_ticket_approver_email_enabled=True, dify_ticket_applicant_email_enabled=False)
        httpx_client = httpx.Client
        transport = httpx.MockTransport(run_workflow)
        with patch('backend.services.process_ticket_events.settings', config), patch(
                'httpx.Client', side_effect=lambda **kwargs: httpx_client(transport=transport, **kwargs)):
            submitted = self.client.post('/process_tickets', json=self.ticket_payload(request_id))
            self.assertEqual(submitted.status_code, 201)
            self.assertEqual(submitted.json()['status'], 'pending')
            self.assertNotIn('approver_email', submitted.json())
            failed = self.client.get('/process_ticket_events').json()[0]
            self.assertEqual(failed['status'], 'failed')
            self.assertIn('未配置工作邮箱', failed['error_message'])
            self.assertEqual(requests, [])

            updated = self.client.patch(f'/workspace/users/{self.reviewer_id}',
                                        json={**profile, 'work_email': 'reviewer@company.test'})
            self.assertEqual(updated.status_code, 200, updated.text)
            self.assertEqual(self.client.post(retry_url, json={'expected_attempts': 1}).status_code, 202)
            failed = self.client.get('/process_ticket_events').json()[0]
            self.assertEqual(failed['status'], 'failed')
            self.assertIn('超时', failed['error_message'])

            changed_email = self.client.patch(f'/workspace/users/{self.reviewer_id}',
                                             json={**profile, 'work_email': 'new@company.test'})
            self.assertEqual(changed_email.status_code, 200)
            self.assertEqual(self.client.post(retry_url, json={'expected_attempts': 2}).status_code, 202)
            self.assertEqual(len(requests), 1)
            failed = self.client.get('/process_ticket_events').json()[0]
            self.assertIn('邮箱已变更', failed['error_message'])

            self.client.patch(f'/workspace/users/{self.reviewer_id}', json={**profile, 'work_email': 'reviewer@company.test'})
            self.assertEqual(self.client.post(retry_url, json={'expected_attempts': 3}).status_code, 202)
            self.assertEqual(self.client.get('/process_ticket_events').json()[0]['status'], 'sent')
            approved = self.approver.post(f'/process_tickets/{request_id}/decision', json={'decision': 'approved'})
            self.assertEqual(approved.json()['status'], 'approved')

            second = self.client.post('/workspace/users', json={**self.account('second-reviewer'),
                                      'department_id': self.department_id, 'work_email': 'second@company.test'})
            self.assertEqual(second.status_code, 201)
            second_id = second.json()['id']
            rule = self.client.get('/process_approval_rules').json()[0]
            self.assertEqual(self.client.patch(f"/process_approval_rules/{rule['id']}",
                                               json={'approver_id': second_id}).status_code, 200)
            second_ticket = self.client.post('/process_tickets', json={
                **self.ticket_payload(str(uuid4())), 'expected_approver_id': second_id,
            })
            self.assertEqual(second_ticket.status_code, 201, second_ticket.text)

        self.assertEqual(len(requests), 4)
        self.assertEqual(requests[0]['event_id'], event_id)
        self.assertEqual(requests[0]['approver_email'], 'reviewer@company.test')
        self.assertEqual(requests[0], requests[1])
        self.assertEqual(requests[2]['event'], 'process_ticket.approved')
        self.assertNotIn('approver_email', requests[2])
        self.assertEqual(requests[3]['approver_email'], 'second@company.test')

    def test_dify_decision_emails_only_the_applicant_and_supports_retry(self):
        self.sign_in(self.approver, 'reviewer')
        self.sign_in(self.anonymous, 'employee')
        self.deliver_event.side_effect = _deliver
        users = {user['username']: user for user in self.client.get('/workspace/users').json()}
        recipients = {'reviewer': 'reviewer@company.test', 'employee': 'employee@company.test'}

        def set_email(name, address):
            user = users[name]
            response = self.client.patch(f"/workspace/users/{user['id']}", json={
                'username': name, 'display_name': user['display_name'], 'department_id': self.department_id,
                'module_permissions': user['module_permissions'], 'work_email': address,
            })
            self.assertEqual(response.status_code, 200, response.text)

        set_email('reviewer', recipients['reviewer'])
        config = SimpleNamespace(ticket_automation_provider='dify', dify_ticket_api_base_url='https://dify.invalid/v1',
                                 dify_ticket_api_key='test-app-key', dify_ticket_timeout_seconds=2.0,
                                 dify_ticket_approver_email_enabled=True, dify_ticket_applicant_email_enabled=True)
        requests = []

        def run_workflow(request):
            inputs = json.loads(request.content)['inputs']
            requests.append(inputs)
            return httpx.Response(200, json={'data': {'status': 'succeeded', 'outputs': {
                'event_id': inputs['event_id'], 'accepted': True,
            }}})

        httpx_client = httpx.Client
        transport = httpx.MockTransport(run_workflow)
        with patch('backend.services.process_ticket_events.settings', config), patch(
                'httpx.Client', side_effect=lambda **kwargs: httpx_client(transport=transport, **kwargs)):
            for decision in ('approved', 'rejected'):
                with self.subTest(decision=decision):
                    set_email('employee', recipients['employee'] if decision == 'approved' else None)
                    request_id = str(uuid4())
                    self.assertEqual(self.anonymous.post('/process_tickets', json=self.ticket_payload(request_id)).status_code, 201)
                    self.assertEqual(requests[-1]['approver_email'], recipients['reviewer'])
                    self.assertNotIn('applicant_email', requests[-1])
                    path = f'/process_tickets/{request_id}/decision'
                    before = len(requests)
                    response = self.approver.post(path, json={'decision': decision, 'note': '测试审批'})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()['status'], decision)
                    event_id = f'{request_id}:process_ticket.{decision}'
                    event = next(event for event in self.client.get('/process_ticket_events').json()
                                 if event['event_id'] == event_id)
                    if decision == 'rejected':
                        self.assertEqual(len(requests), before)
                        self.assertEqual(event['status'], 'failed')
                        self.assertIn('发起人未配置工作邮箱', event['error_message'])
                        set_email('employee', recipients['employee'])
                        retry = self.client.post(f'/process_ticket_events/{event_id}/retry', json={'expected_attempts': 1})
                        self.assertEqual(retry.status_code, 202)
                        event = next(event for event in self.client.get('/process_ticket_events').json()
                                     if event['event_id'] == event_id)
                    self.assertEqual(event['status'], 'sent')
                    self.assertEqual(len(requests), before + 1)
                    self.assertEqual(requests[-1]['event_id'], event_id)
                    self.assertEqual(requests[-1]['applicant_email'], recipients['employee'])
                    self.assertNotIn('approver_email', requests[-1])
                    self.assertEqual(self.approver.post(path, json={'decision': decision, 'note': '测试审批'}).status_code, 409)
                    self.assertEqual(len(requests), before + 1)

    def test_ticket_event_migration_preserves_existing_rows(self):
        request_id = str(uuid4())
        self.assertEqual(self.client.post('/process_tickets', json=self.ticket_payload(request_id)).status_code, 201)
        with closing(get_connection()) as connection, connection:
            previous = [tuple(row) for row in connection.execute("SELECT * FROM process_ticket_events")]
            connection.execute("ALTER TABLE process_ticket_events RENAME TO test_ticket_events")
            connection.execute(CREATE_PROCESS_TICKET_EVENTS_SQL.replace(", 'process_ticket.rejected'", ""))
            connection.execute("INSERT INTO process_ticket_events SELECT * FROM test_ticket_events")
            connection.execute("DROP TABLE test_ticket_events")

        init_database()
        init_database()
        with closing(get_connection()) as connection, connection:
            self.assertEqual([tuple(row) for row in connection.execute("SELECT * FROM process_ticket_events")], previous)
            connection.execute("""INSERT INTO process_ticket_events (event_id, request_id, event_type, payload_json)
                                  VALUES (?, ?, 'process_ticket.rejected', '{}')""",
                               (f'{request_id}:process_ticket.rejected', request_id))
            self.assertEqual(connection.execute("PRAGMA foreign_key_check('process_ticket_events')").fetchall(), [])

    def test_dify_requires_a_successful_run_and_matching_boolean_receipt(self):
        payload = {'request_id': 'req', 'event': 'process_ticket.approved',
                   'event_id': 'req:process_ticket.approved', 'ticket_number': 'WO-000001'}
        config = SimpleNamespace(ticket_automation_provider='dify', dify_ticket_api_base_url='https://dify.invalid/v1',
                                 dify_ticket_api_key='test-app-key', dify_ticket_timeout_seconds=2.0,
                                 dify_ticket_approver_email_enabled=False, dify_ticket_applicant_email_enabled=False)
        accepted = {'accepted': True, 'event_id': payload['event_id']}
        cases = [
            (200, {'data': {'status': 'failed', 'outputs': accepted}}, '未确认成功'),
            (200, {'data': {'status': 'succeeded', 'outputs': {**accepted, 'event_id': 'other'}}}, '回执无效'),
            (200, {'data': {'status': 'succeeded', 'outputs': {**accepted, 'accepted': 'true'}}}, '回执无效'),
            (401, {'message': 'test-app-key'}, 'HTTP 401'),
        ]
        with patch('backend.services.process_ticket_events.settings', config):
            for status, body, expected_error in cases:
                with self.subTest(status=status, body=body), patch('httpx.Client', return_value=httpx.Client(
                        transport=httpx.MockTransport(lambda _: httpx.Response(status, json=body)))):
                    error = _deliver(payload)
                    self.assertIn(expected_error, error)
                    self.assertNotIn(config.dify_ticket_api_key, error)

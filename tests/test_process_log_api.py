"""执行日志必须来自 SQLite；筛选、分页和异常不触发流程副作用。"""

from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.api.processes import router
from backend.db.connection import get_connection
from backend.db.init_db import init_database
from backend.db.process_repository import start_process_execution, finish_process_execution


class ProcessLogApiTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        config = SimpleNamespace(database_url='sqlite:///' + str(Path(self.folder.name) / 'logs.db'))
        self.config_patch = patch('backend.db.connection.settings', config)
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)
        init_database(config.database_url)
        self.webhook = patch('backend.services.process_submission._call_n8n_webhook').start()
        self.addCleanup(patch.stopall)
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def seed(self, status):
        request_id = str(uuid4())
        record_id = start_process_execution({'request_id': request_id, 'fields': {
            'application_type': 'VPN权限', 'applicant': '张三', 'department': '研发部'}})
        if status != 'running':
            receipt = dict(request_id=request_id, status='simulated', ticket_id='SIM-42', actions=['ticket_simulated'])
            finish_process_execution(record_id, status=status, response_status_code=200 if status == 'simulated' else 504,
                                     output=receipt if status == 'simulated' else None, error_message=None if status == 'simulated' else '执行结果待核对')
        return request_id

    def test_empty_is_not_sample_data(self):
        body = self.client.get('/process_applications').json()
        self.assertEqual(body['items'], [])
        self.assertEqual(body['total'], 0)
        self.assertEqual(sum(body['status_counts'].values()), 0)
        self.webhook.assert_not_called()

    def test_pagination_filter_counts_and_detail_agree(self):
        ids = [self.seed(s) for s in ('simulated', 'failed', 'timeout', 'running')]
        with closing(get_connection()) as connection, connection:
            connection.execute("INSERT INTO n8n_execution_records (workflow_name) VALUES ('legacy')")
        first = self.client.get('/process_applications?page_size=2').json()
        second = self.client.get('/process_applications?page_size=2&page=2').json()
        self.assertEqual(first['total'], 4)
        self.assertEqual([x['request_id'] for x in first['items']+second['items']], list(reversed(ids)))
        filtered = self.client.get('/process_applications?status=simulated').json()
        self.assertEqual(filtered['total'], 1)
        self.assertEqual(filtered['status_counts'], dict.fromkeys(('running', 'simulated', 'failed', 'timeout'), 1))
        item = filtered['items'][0]
        detail = self.client.get(f'/process_applications/{ids[0]}').json()
        self.assertEqual(item['ticket_id'], detail['output']['ticket_id'])
        self.assertEqual(item['applicant'], detail['input']['fields']['applicant'])
        self.assertNotIn('input', item)
        self.assertEqual(self.client.get(f'/process_applications?request_id={ids[1]}').json()['total'], 1)
        self.assertEqual(self.client.get(f'/process_applications?request_id={ids[1]}&status=simulated').json()['items'], [])
        self.assertEqual(self.client.get('/process_applications?page=10').json()['items'], [])
        self.webhook.assert_not_called()

    def test_invalid_filters_and_storage_error(self):
        for query in ('page=0', 'page_size=101', 'status=success', "request_id=' OR 1=1"):
            with self.subTest(query=query):
                self.assertEqual(self.client.get('/process_applications?'+query).status_code, 422)
        with patch('backend.api.processes.list_process_executions', side_effect=sqlite3.OperationalError('private path')):
            response = self.client.get('/process_applications')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private', response.text)
        self.webhook.assert_not_called()


if __name__ == '__main__':
    unittest.main()

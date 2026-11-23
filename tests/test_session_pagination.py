"""Session pagination uses temporary SQLite and never calls model/vector services."""
from contextlib import closing
from pathlib import Path
import tempfile
import gc
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.api.chat import router
from backend.db.connection import get_connection
from backend.db.init_db import init_database
from tests.workspace_session_fixture import authenticate_fixture

MODE='企业知识库问答'


class SessionPaginationTests(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        config=SimpleNamespace(database_url='sqlite:///'+str(Path(directory.name)/'sessions.db'))
        change=patch('backend.db.connection.settings',config);change.start();self.addCleanup(change.stop)
        init_database(config.database_url)
        # Legacy repository methods use SQLite context managers without closing;
        # collect their released connections before removing the temporary DB on Windows.
        self.addCleanup(gc.collect)
        app=FastAPI();app.include_router(router)
        self.client=TestClient(app);self.addCleanup(self.client.close)
        self.owner_id = authenticate_fixture(self.client)
        with closing(get_connection()) as connection:
            connection.executemany('INSERT INTO chat_sessions(id,mode,title,created_at,updated_at) VALUES(?,?,?,?,?)',
                [(f'chat-{n:03}',MODE,f'会话 {n}','2026-11-08 10:00:00','2026-11-08 10:00:00') for n in range(125)]
                + [('other','普通聊天','其他模式','2026-11-08 10:00:00','2026-11-08 10:00:00'),
                   ('knowledge_base:test',MODE,'内部知识库','2026-11-08 10:00:00','2026-11-08 10:00:00')])
            connection.execute("UPDATE chat_sessions SET owner_user_id = ? WHERE id NOT LIKE 'knowledge_base:%'", (self.owner_id,))
            connection.commit()

    def page(self,**extra):
        return self.client.get('/chat_session_pages',params={'mode':MODE,'include_empty':True,**extra})

    def test_all_125_sessions_are_reachable_without_duplicates(self):
        ids=[];cursor=None;sizes=[]
        while True:
            response=self.page(**({'cursor':cursor} if cursor else {}));self.assertEqual(response.status_code,200)
            data=response.json();sizes.append(len(data['sessions']));ids.extend(row['session_id'] for row in data['sessions'])
            cursor=data['next_cursor']
            if not cursor:break
        self.assertEqual(sizes,[50,50,25])
        self.assertEqual(len(set(ids)),125)
        self.assertEqual(ids,[f'chat-{n:03}' for n in reversed(range(125))])

    def test_first_page_matches_legacy_sort_and_limits(self):
        old=self.client.get('/chat_sessions',params={'mode':MODE,'include_empty':True,'limit':50}).json()['sessions']
        self.assertEqual(self.page().json()['sessions'],old)
        self.assertEqual(len(self.client.get('/chat_sessions?include_empty=true').json()['sessions']),10)

    def test_empty_sessions_and_internal_scope_remain_protected(self):
        result=self.client.get('/chat_session_pages',params={'mode':MODE}).json()
        self.assertEqual(result,{'sessions':[],'next_cursor':None})
        self.assertNotIn('knowledge_base:test',{r['session_id'] for r in self.page().json()['sessions']})

    def test_invalid_and_wrong_scope_cursors_rejected(self):
        self.assertEqual(self.page(cursor='not-a-valid-cursor').status_code,400)
        cursor=self.page().json()['next_cursor']
        self.assertEqual(self.client.get('/chat_session_pages',params={'mode':'普通聊天','include_empty':True,'cursor':cursor}).status_code,400)
        self.assertEqual(self.client.get('/chat_session_pages',params={'mode':MODE,'cursor':cursor}).status_code,400)
        self.assertEqual(self.page(limit=51).status_code,422)

    def test_deleting_before_cursor_does_not_skip_remaining_rows(self):
        first=self.page().json()
        with closing(get_connection()) as connection:
            connection.execute("DELETE FROM chat_sessions WHERE id='chat-124'");connection.commit()
        second=self.page(cursor=first['next_cursor']).json()
        self.assertEqual(second['sessions'][0]['session_id'],'chat-074')


if __name__=='__main__':unittest.main()

"""Draft reuse, concurrent creation and legacy API compatibility, temporary SQLite only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import unittest

from backend.db.connection import get_connection
from backend.db.repository import save_chat_message
from backend.db.session_drafts import get_or_create_draft
from tests import test_session_pagination as fixture

MODE = fixture.MODE


class SessionDraftTests(unittest.TestCase):
    setUp = fixture.SessionPaginationTests.setUp

    def create(self, session_id):
        return self.client.post('/chat_session_draft', json={'session_id': session_id, 'mode': MODE, 'title': '新会话'})

    def test_repeated_clicks_return_same_empty_draft(self):
        self.assertEqual(self.create('draft-a').json()['session_id'], 'draft-a')
        self.assertEqual(self.create('draft-b').json()['session_id'], 'draft-a')
        with closing(get_connection()) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM chat_sessions WHERE title='新会话'").fetchone()[0], 1)

    def test_used_or_renamed_draft_is_not_reused(self):
        self.create('draft-a')
        save_chat_message('draft-a', 'user', '问题')
        self.assertEqual(self.create('draft-b').json()['session_id'], 'draft-b')
        self.client.patch('/chat_session/draft-b', json={'title': '已命名的工作'})
        self.assertEqual(self.create('draft-c').json()['session_id'], 'draft-c')

    def test_concurrent_creation_returns_one_draft(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda n: get_or_create_draft(f'draft-{n}', MODE, owner_user_id=self.owner_id), range(12)))
        self.assertEqual(len(set(results)), 1)

    def test_modes_remain_separate_and_legacy_creation_unchanged(self):
        first = get_or_create_draft('draft-a', MODE, owner_user_id=self.owner_id)
        self.assertEqual(get_or_create_draft('draft-other', '普通聊天', owner_user_id=self.owner_id), 'draft-other')
        self.assertEqual(self.create('draft-b').json()['session_id'], first)
        result = self.client.post('/chat_session', json={'session_id': 'legacy', 'mode': MODE})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['session_id'], 'legacy')

    def test_reserved_session_rejected_without_write(self):
        self.assertEqual(self.create('knowledge_base:secret').status_code, 409)
        with closing(get_connection()) as connection:
            self.assertIsNone(connection.execute("SELECT id FROM chat_sessions WHERE id='knowledge_base:secret'").fetchone())


if __name__ == '__main__':
    unittest.main()

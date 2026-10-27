"""反馈处理状态：迁移、重新打开、并发版本保护与筛选。"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import sqlite3
import unittest
from unittest.mock import patch
from tests import test_answer_feedback as fixture
from backend.db.connection import get_connection
from backend.db.feedback_repository import FeedbackConflict, review_feedback
from backend.db.init_db import init_database
from backend.db.repository import save_chat_message


class FeedbackReviewTests(unittest.TestCase):
    setUp = fixture.AnswerFeedbackTests.setUp
    post = fixture.AnswerFeedbackTests.post

    def seed(self):
        return self.post({'rating': 'unhelpful', 'reason': 'incomplete', 'comment': '缺少时限'}).json()

    def review(self, status, revision=1, note='', message=None):
        return self.client.post(f'/answer_feedback/{message or self.answer}/review',
                                json={'status': status, 'expected_revision': revision, 'note': note})

    def test_transitions_notes_and_restart_persistence(self):
        self.assertEqual(self.seed()['review_status'], 'pending')
        working = self.review('in_progress', note='核对制度').json()
        self.assertEqual((working['review_status'], working['revision']), ('in_progress', 2))
        resolved = self.review('resolved', 2, '  已补充资料并复测原问题  ').json()
        self.assertEqual(resolved['review_note'], '已补充资料并复测原问题')
        self.assertEqual(resolved['revision'], 3)
        same = self.review('resolved', 3, '已补充资料并复测原问题').json()
        self.assertEqual(same['revision'], 3)
        init_database(self.database_url)
        item = self.client.get('/answer_feedback').json()['items'][0]
        self.assertEqual(item['review_status'], 'resolved')
        self.assertEqual(self.review('pending', 3, '需要补充复核').json()['revision'], 4)

    def test_identical_feedback_preserves_resolution_changed_content_reopens(self):
        self.seed(); self.review('resolved', note='人工复测通过')
        unchanged = self.seed()
        self.assertEqual((unchanged['review_status'], unchanged['revision']), ('resolved', 2))
        changed = self.post({'rating': 'unhelpful', 'reason': 'citation_mismatch', 'comment': '新问题'}).json()
        self.assertEqual((changed['review_status'], changed['review_note'], changed['revision']), ('pending', '', 3))
        conflict = self.review('resolved', 2, '旧反馈的处理结果')
        self.assertEqual(conflict.status_code, 409)
        item = self.client.get('/answer_feedback').json()['items'][0]
        self.assertEqual(item['review_note'], '')

    def test_positive_missing_and_invalid_input(self):
        self.assertEqual(self.review('in_progress').status_code, 404)
        self.post({'rating': 'helpful'})
        self.assertEqual(self.review('in_progress').status_code, 409)
        self.seed()
        for body in ({'status': 'resolved', 'note': ' ', 'expected_revision': 2},
                     {'status': 'done', 'expected_revision': 2}, {'status': 'pending'},
                     {'status': 'pending', 'expected_revision': 0},
                     {'status': 'pending', 'expected_revision': 2, 'note': '字'*1001}):
            self.assertEqual(self.client.post(f'/answer_feedback/{self.answer}/review', json=body).status_code, 422)

    def test_filters_and_global_review_counts(self):
        self.seed()
        for status in ('in_progress', 'resolved'):
            save_chat_message('a', 'user', '其他问题')
            message = save_chat_message('a', 'assistant', '其他回答')
            self.post({'rating': 'unhelpful', 'reason': 'other'}, message=message)
            self.review(status, note='复测完成', message=message)
        message = save_chat_message('a', 'assistant', '有帮助回答')
        self.post({'rating': 'helpful'}, message=message)
        for status in ('pending', 'in_progress', 'resolved'):
            result = self.client.get('/answer_feedback', params={'review_status': status}).json()
            self.assertEqual(result['total'], 1)
            self.assertEqual(result['items'][0]['review_status'], status)
            self.assertEqual(result['review_counts'], dict(pending=1, in_progress=1, resolved=1))
            self.assertEqual(result['counts'], dict(helpful=1, unhelpful=3))
        self.assertEqual(self.client.get('/answer_feedback?rating=helpful&review_status=pending').json()['total'], 0)
        self.assertEqual(self.client.get('/answer_feedback?review_status=unknown').status_code, 422)

    def test_old_feedback_table_migrates_without_losing_data(self):
        with closing(get_connection()) as conn, conn:
            conn.execute('DROP TABLE answer_feedback')
            conn.execute('''CREATE TABLE answer_feedback (message_id INTEGER PRIMARY KEY, rating TEXT NOT NULL,
                          reason TEXT NOT NULL DEFAULT '', comment TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
                          FOREIGN KEY(message_id) REFERENCES chat_messages(id) ON DELETE CASCADE)''')
            conn.execute("INSERT INTO answer_feedback VALUES (?, 'unhelpful', 'incomplete', '旧反馈', '2026-10-26')", (self.answer,))
        init_database(self.database_url); init_database(self.database_url)
        item = self.client.get('/answer_feedback').json()['items'][0]
        self.assertEqual((item['comment'], item['review_status'], item['revision']), ('旧反馈', 'pending', 1))
        self.assertEqual(self.review('in_progress').status_code, 200)

    def test_two_reviewers_cannot_overwrite_same_revision(self):
        self.seed()
        def save(note):
            try:
                return review_feedback(self.answer, status='in_progress', note=note, expected_revision=1)
            except FeedbackConflict:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(save, ['复核甲', '复核乙']))
        self.assertEqual(results.count('conflict'), 1)
        self.assertEqual(self.client.get('/answer_feedback').json()['items'][0]['revision'], 2)

    def test_storage_failure_is_clear(self):
        self.seed()
        with patch('backend.api.feedback.review_feedback', side_effect=sqlite3.OperationalError('private path')):
            result = self.review('in_progress')
        self.assertEqual(result.status_code, 503)
        self.assertNotIn('private', result.text)
        self.assertEqual(self.client.get('/answer_feedback').json()['items'][0]['review_status'], 'pending')


if __name__ == '__main__':
    unittest.main()

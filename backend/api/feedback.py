"""反馈只针对已经保存的回答；不接受前端上传答案副本。"""
import sqlite3
from typing import Literal
from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, Field, model_validator
from backend.db.feedback_repository import FeedbackConflict, list_feedback, review_feedback, save_feedback

router = APIRouter()
ReviewStatus = Literal['pending', 'in_progress', 'resolved']


class FeedbackReviewRequest(BaseModel):
    model_config = {'str_strip_whitespace': True, 'extra': 'forbid'}
    status: ReviewStatus
    note: str = Field(default='', max_length=1000)
    expected_revision: int = Field(ge=1, le=9223372036854775807)

    @model_validator(mode='after')
    def require_resolution_note(self):
        if self.status == 'resolved' and not self.note:
            raise ValueError('标记已解决时必须填写处理说明和复核结果。')
        return self


class FeedbackRequest(BaseModel):
    model_config = {'str_strip_whitespace': True, 'extra': 'forbid'}
    rating: Literal['helpful', 'unhelpful']
    reason: Literal['', 'answer_inaccurate', 'citation_mismatch', 'incomplete', 'other'] = ''
    comment: str = Field(default='', max_length=500)

    @model_validator(mode='after')
    def check_reason(self):
        if self.rating == 'helpful' and self.reason:
            raise ValueError('有帮助的回答无需选择问题原因。')
        if self.rating == 'unhelpful' and not self.reason:
            raise ValueError('请为无帮助的回答选择原因。')
        return self


@router.post('/chat_session/{session_id}/messages/{message_id}/feedback')
def submit_feedback(session_id: str, request: FeedbackRequest, message_id: int = Path(gt=0, le=9223372036854775807)):
    if session_id.startswith('knowledge_base:'):
        raise HTTPException(403, '公共知识库不是聊天会话。')
    try:
        result = save_feedback(session_id, message_id, **request.model_dump())
    except sqlite3.Error as exc:
        raise HTTPException(503, '反馈暂未确认保存，请刷新历史核对后重试。') from exc
    if result is None:
        raise HTTPException(404, '回答不存在、已删除或不属于当前会话。')
    return result


@router.get('/answer_feedback')
def feedback_list(rating: Literal['helpful', 'unhelpful'] | None = None,
                  page: int = Query(default=1, ge=1), page_size: int = Query(default=10, ge=1, le=50),
                  review_status: ReviewStatus | None = None):
    try:
        return list_feedback(rating=rating, page=page, page_size=page_size, review_status=review_status)
    except sqlite3.Error as exc:
        raise HTTPException(503, '反馈列表暂不可用，请稍后刷新。') from exc


@router.post('/answer_feedback/{message_id}/review')
def submit_review(request: FeedbackReviewRequest, message_id: int = Path(gt=0, le=9223372036854775807)):
    try:
        result = review_feedback(message_id, **request.model_dump())
    except FeedbackConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except sqlite3.Error as exc:
        raise HTTPException(503, '处理结果尚未确认保存，请刷新列表核对后重试。') from exc
    if result is None:
        raise HTTPException(404, '反馈不存在或已删除。')
    return result

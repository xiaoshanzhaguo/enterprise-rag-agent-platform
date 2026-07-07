"""
企业知识库配置模块。

这个模块只放“知识库级别”的轻量配置，不放 HR / 财务 / IT / 产品这种文档分类。
当前项目还没有登录系统，所以先用一个默认企业知识库和演示级角色判断，
让业务边界从“当前 session 的临时文档”过渡到“企业公共知识库”。
"""

# 未来版本兼容特性, 让类型注解延迟解析。简单理解：让类型标注更灵活，减少某些类型循环引用或版本兼容问题。
# 可以记成：让类型提示更安全、更适合未来扩展。
from __future__ import annotations

from typing import Any


# 默认企业知识库 ID。前端和后端都会围绕这个知识库完成演示闭环。
DEFAULT_KNOWLEDGE_BASE_ID = "enterprise_default"

# 兼容旧入口的占位 ID。
# 旧的内容分析/临时 RAG 文档仍然按 session_id 检索，不应该混入企业公共知识库。
SESSION_SCOPED_KNOWLEDGE_BASE_ID = "session_scoped"

# 默认企业知识库展示信息。后续如果接入管理后台，可以迁移成数据库可编辑字段。
DEFAULT_KNOWLEDGE_BASE = {
    "knowledge_base_id": DEFAULT_KNOWLEDGE_BASE_ID,
    "name": "企业默认知识库",
    "description": "用于保存 HR、财务、IT、产品等企业内部制度和流程资料。",
    "owner_role": "kb_admin",
}

# 演示级角色：这些角色可以上传和维护知识库文档。
KNOWLEDGE_BASE_ADMIN_ROLES = {"kb_admin", "admin"}


def normalize_knowledge_base_id(value: str | None) -> str:
    """
    归一化前端传入的知识库 ID。

    当前第一版只内置一个企业默认知识库，所以为空时回到 enterprise_default。
    以后如果新增多知识库表，这个函数可以继续作为入口做白名单校验。

    :param value: 前端传入的 knowledge_base_id
    :return: 可用于数据库查询的知识库 ID
    """
    # 把传入的知识库 ID 变成干净字符串；如果没传，就先变成空字符串。
    normalized_value = (value or "").strip()
    return normalized_value or DEFAULT_KNOWLEDGE_BASE_ID


def build_knowledge_base_storage_session_id(knowledge_base_id: str | None) -> str:
    """
    构造知识库文档专用的内部 session_id。

    documents 表历史上依赖 session_id 外键。为了不大改旧表结构，
    公共知识库文档会挂到一个内部 session 上，而不是普通用户聊天 session。
    这样用户删除自己的聊天记录时，不会误删企业公共知识库。

    :param knowledge_base_id: 知识库 ID
    :return: 内部存储会话 ID
    """
    return f"knowledge_base:{normalize_knowledge_base_id(knowledge_base_id)}"


def can_manage_knowledge_base(user_role: str | None) -> bool:
    """
    判断当前演示角色是否允许维护知识库。

    这不是完整 RBAC，只是为了把“管理员上传、员工查询”的业务边界先立住。
    RBAC 是 Role-Based Access Control，基于角色的访问控制。

    :param user_role: 前端选择的演示角色
    :return: True 表示可以上传知识库文档
    """
    return (user_role or "").strip() in KNOWLEDGE_BASE_ADMIN_ROLES


def list_default_knowledge_bases() -> list[dict[str, Any]]:
    """
    返回内置知识库列表。

    :return: 知识库配置列表
    """
    return [DEFAULT_KNOWLEDGE_BASE.copy()]

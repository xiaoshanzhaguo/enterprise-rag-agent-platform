"""
知识库分类定义与校验工具。

内置分类供问答路由使用，自定义分类使用独立的持久化 ID。
前端下拉框、文档入库与检索过滤使用相同的分类值。

模块作用：把知识库分类统一放在后端管理，并提供“列表返回、分类校验、分类归一化、部门名补齐”这些工具函数。
避免前端写 "财务"，后端写 "finance"，数据库又写 "Finance"，最后查不到数据。
"""

from typing import Any
from backend.db.category_repository import active_category_exists, list_categories


DEFAULT_KNOWLEDGE_BASE_TYPE = "general"

# category_id 也是当前检索过滤使用的 knowledge_base_type。
# 后续如果升级成真正的分类表，可以先保持这个值不变，再增加数据库主键。
KNOWLEDGE_BASE_CATEGORIES: list[dict[str, Any]] = [
    {
        "category_id": "hr",
        "label": "HR",
        "knowledge_base_type": "hr",
        "department": "HR",
        "description": "人力资源制度、考勤、请假、入职等文档",
    },
    {
        "category_id": "finance",
        "label": "财务",
        "knowledge_base_type": "finance",
        "department": "财务",
        "description": "报销、发票、差旅、付款等财务流程文档",
    },
    {
        "category_id": "it",
        "label": "IT",
        "knowledge_base_type": "it",
        "department": "IT",
        "description": "VPN、GitLab、账号权限、设备和网络支持文档",
    },
    {
        "category_id": "product",
        "label": "产品",
        "knowledge_base_type": "product",
        "department": "产品",
        "description": "产品 FAQ、版本发布、功能说明和客户反馈文档",
    },
    {
        "category_id": "general",
        "label": "通用",
        "knowledge_base_type": "general",
        "department": None,
        "description": "适用于跨部门或尚无专用分类的文档",
    },
]

# 构造按type查询的字典。作用：根据 knowledge_base_type 快速找到对应分类配置。
CATEGORY_BY_TYPE = {
    category["knowledge_base_type"]: category
    for category in KNOWLEDGE_BASE_CATEGORIES
}


def list_knowledge_base_categories() -> list[dict[str, Any]]:
    """
    返回前端可展示的知识库分类列表。

    :return: 分类字典列表，调用方可以安全修改返回值，不会影响模块常量
    """
    # copy() 可以防止调用方误改 KNOWLEDGE_BASE_CATEGORIES 里的全局配置。
    # 为什么用copy()？
    # 因为这样返回的是每个字典的浅拷贝。调用方修改返回结果时，不会改到模块里的原始配置。
    categories = {row["category_id"]: row for row in list_categories()}
    builtin = [dict(categories.pop(category_id), builtin=True, department=defaults["department"])
               for category_id, defaults in CATEGORY_BY_TYPE.items() if category_id in categories]
    return builtin + list(categories.values())


def normalize_knowledge_base_type(value: str | None, *, allow_none: bool = False) -> str | None:
    """
    归一化并校验知识库分类值。把前端传来的知识库分类值清洗、归一化、校验后返回。

    函数说明：
    1. 前端传来的分类值可能带空格或大小写不一致，这里统一转成小写。
    2. 上传文档时不传分类可以回退到 general。
    3. 检索过滤时不传分类表示检索全部分类，所以 allow_none=True 时返回 None。
    4. 传入未知分类时抛出 ValueError，由 API 层转换成 400，避免静默检索不到结果。
    5. * 星号的意思是：星号后面的参数必须用关键字传参。
    6. “归一化”就是把各种不统一的输入，变成统一格式。

    :param value: 原始分类值，例如 hr、finance、it、product、general
    :param allow_none: 是否允许空值直接返回 None
    :return: 归一化后的分类值，或 None
    """
    # 处理None
    if value is None:
        return None if allow_none else DEFAULT_KNOWLEDGE_BASE_TYPE

    # 清理字符串
    normalized = str(value).strip().lower()
    # 判断归一化后的字符串是否为空
    if not normalized:
        return None if allow_none else DEFAULT_KNOWLEDGE_BASE_TYPE

    # 校验分类是否合法
    if normalized != DEFAULT_KNOWLEDGE_BASE_TYPE and not active_category_exists(normalized):
        raise ValueError(f"知识库分类不存在或已删除：{value}。")

    # 若前面没有返回，也没有抛错，说明分类值是合法的
    return normalized


def get_department_for_knowledge_base_type(value: str | None) -> str | None:
    """
    根据知识库分类补齐部门展示名。

    :param value: 知识库分类值
    :return: 部门展示名；general 或空值没有对应部门时返回 None
    """
    # 调用前面的归一化函数
    normalized = normalize_knowledge_base_type(value, allow_none=True)
    # 如果分类为空，或者分类是 general，就没有对应部门
    if not normalized or normalized == DEFAULT_KNOWLEDGE_BASE_TYPE:
        return None

    return CATEGORY_BY_TYPE.get(normalized, {}).get("department")

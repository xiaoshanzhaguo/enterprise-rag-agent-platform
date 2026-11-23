"""Workspace accounts and opaque, server-side login sessions."""

import json
import re
import sqlite3
from contextlib import closing
from hashlib import scrypt, sha256
from hmac import compare_digest
from secrets import token_bytes, token_urlsafe
from time import time
from typing import Literal, get_args

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from backend.db.connection import get_connection
from backend.db.departments import get_department

router = APIRouter(prefix="/workspace", tags=["工作台账号"])
COOKIE = "workspace_session"
SESSION_SECONDS = 8 * 60 * 60
WorkspaceModule = Literal['chat', 'workflow', 'tickets', 'knowledge', 'logs', 'evaluation']
WORKSPACE_MODULES = get_args(WorkspaceModule)


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64)
    password: SecretStr = Field(min_length=1, max_length=128)


class UserProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(pattern=r"^[\p{Han}a-zA-Z0-9_.-]{2,64}$")
    display_name: str = Field(min_length=1, max_length=80, pattern=r"\S")
    role: Literal["admin", "member"] = "member"
    module_permissions: set[WorkspaceModule] = Field(default_factory=lambda: set(WORKSPACE_MODULES))
    work_email: str | None = Field(default=None, max_length=254)

    @field_validator('work_email')
    @classmethod
    def validate_work_email(cls, value: str | None) -> str | None:
        value = value.strip() if value else ''
        if not value:
            return None
        local, _, domain = value.rpartition('@')
        atom = r"[a-zA-Z0-9!#$%&'*+/=?^_`{|}~-]+"
        label = r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?'
        if (len(local) > 64 or not re.fullmatch(rf'{atom}(?:\.{atom})*', local)
                or not re.fullmatch(rf'{label}(?:\.{label})+', domain)):
            raise ValueError('请输入有效的工作邮箱地址。')
        return f'{local}@{domain.lower()}'


class NewUser(UserProfile):
    password: SecretStr = Field(min_length=8, max_length=128)


class CreateUser(NewUser):
    department_id: int = Field(gt=0, strict=True)


class UpdateUser(UserProfile):
    department_id: int | None = Field(gt=0, strict=True)
    module_permissions: set[WorkspaceModule]


class UserStatus(BaseModel):
    enabled: bool


def password_digest(password: SecretStr, salt: bytes) -> str:
    return scrypt(password.get_secret_value().encode(), salt=salt, n=2**14, r=8, p=1).hex()


def token_digest(token: str) -> str:
    return sha256(token.encode()).hexdigest()


def public_user(row):
    stored = row['module_permissions_json']
    permissions = WORKSPACE_MODULES if stored is None else json.loads(stored)
    return dict(id=row["id"], username=row["username"], display_name=row["display_name"],
                role=row["role"], enabled=bool(row["enabled"]), department=row["department"],
                module_permissions=[module for module in WORKSPACE_MODULES if module in permissions])


def managed_user(row):
    return {**public_user(row), 'work_email': row['work_email']}


def current_user(request: Request):
    token = request.cookies.get(COOKIE)
    if token and len(token) == 43:
        with closing(get_connection()) as connection:
            row = connection.execute(
                """SELECT u.* FROM workspace_users u JOIN workspace_sessions s ON s.user_id = u.id
                   WHERE s.token_hash = ? AND s.expires_at > ? AND u.enabled = 1""",
                (token_digest(token), int(time())),
            ).fetchone()
            if row:
                return public_user(row)
    raise HTTPException(401, "请先登录。")


def require_admin(user=Depends(current_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "只有管理员可以管理用户。")
    return user


def check_module_access(user, *modules: WorkspaceModule):
    if not any(module in user['module_permissions'] for module in modules):
        raise HTTPException(403, '当前账号未开通此模块。')
    return user


def require_module(*modules: WorkspaceModule):
    def dependency(user=Depends(current_user)):
        return check_module_access(user, *modules)
    return dependency


def insert_user(connection, user: NewUser, *, department: str | None = None):
    salt = token_bytes(16)
    hashed = salt.hex() + ":" + password_digest(user.password, salt)
    cursor = connection.execute(
        """INSERT INTO workspace_users (username, display_name, password_hash, role, department, module_permissions_json, work_email)
           VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(username) DO NOTHING""",
        (user.username.lower(), user.display_name.strip(), hashed, user.role, department,
         json.dumps(sorted(user.module_permissions)), user.work_email),
    )
    if cursor.rowcount != 1:
        raise HTTPException(409, "用户名已存在。")
    return managed_user(connection.execute("SELECT * FROM workspace_users WHERE id = ?", (cursor.lastrowid,)).fetchone())


@router.post("/login")
def login(data: Login, request: Request, response: Response):
    with closing(get_connection()) as connection, connection:
        row = connection.execute("SELECT * FROM workspace_users WHERE username = ?", (data.username.strip().lower(),)).fetchone()
        salt, expected = row["password_hash"].split(":") if row else ("00" * 16, "00" * 64)
        valid = compare_digest(password_digest(data.password, bytes.fromhex(salt)), expected)
        if not valid or not row or not row["enabled"]:
            raise HTTPException(401, "用户名或密码不正确，或账号已停用。")
        connection.execute("BEGIN IMMEDIATE")
        if not connection.execute("SELECT 1 FROM workspace_users WHERE id = ? AND enabled = 1", (row["id"],)).fetchone():
            raise HTTPException(401, "账号已停用。")
        token = token_urlsafe(32)
        now = int(time())
        connection.execute("DELETE FROM workspace_sessions WHERE expires_at <= ?", (now,))
        previous = request.cookies.get(COOKIE)
        if previous:
            connection.execute("DELETE FROM workspace_sessions WHERE token_hash = ?", (token_digest(previous),))
        connection.execute("INSERT INTO workspace_sessions VALUES (?, ?, ?)", (token_digest(token), row["id"], now + SESSION_SECONDS))
    response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True, samesite="strict",
                        secure=request.url.scheme == "https")
    return public_user(row)


@router.post("/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        with closing(get_connection()) as connection, connection:
            connection.execute("DELETE FROM workspace_sessions WHERE token_hash = ?", (token_digest(token),))
    response.delete_cookie(COOKIE)
    return {"ok": True}


@router.get("/me")
def me(user=Depends(current_user)):
    return user


@router.get("/users")
def list_users(admin=Depends(require_admin)):
    with closing(get_connection()) as connection:
        return [managed_user(row) for row in connection.execute("SELECT * FROM workspace_users ORDER BY username")]


@router.post("/users", status_code=201)
def create_user(user: CreateUser, admin=Depends(require_admin)):
    with closing(get_connection()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        verify_admin(connection, admin["id"])
        department = get_department(connection, user.department_id)
        if department is None:
            raise HTTPException(422, '所选部门不存在或已删除，请刷新部门列表。')
        return insert_user(connection, user, department=department['name'])


def verify_admin(connection, user_id):
    if not connection.execute(
        "SELECT 1 FROM workspace_users WHERE id = ? AND enabled = 1 AND role = 'admin'", (user_id,)
    ).fetchone():
        raise HTTPException(403, "当前账号已失去管理权限。")


@router.patch('/users/{user_id}')
def update_user_profile(user_id: int, data: UpdateUser, admin=Depends(require_admin)):
    if user_id == admin['id'] and data.role != 'admin':
        raise HTTPException(409, '不能修改当前登录账号的管理员角色。')
    with closing(get_connection()) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        verify_admin(connection, admin['id'])
        department = get_department(connection, data.department_id) if data.department_id is not None else None
        if data.department_id is not None and department is None:
            raise HTTPException(422, '所选部门不存在或已删除，请刷新部门列表。')
        if 'tickets' not in data.module_permissions and connection.execute(
            "SELECT 1 FROM process_tickets WHERE approver_id = ? AND status = 'pending' LIMIT 1", (user_id,)
        ).fetchone():
            raise HTTPException(409, '该用户还有待审批工单，处理完毕后才能关闭工单中心。')
        try:
            changed = connection.execute('''UPDATE workspace_users SET username = ?, display_name = ?, role = ?,
                department = ?, module_permissions_json = ?, work_email = CASE WHEN ? THEN ? ELSE work_email END
                WHERE id = ?''',
                (data.username.lower(), data.display_name.strip(), data.role, department['name'] if department else None,
                 json.dumps(sorted(data.module_permissions)), 'work_email' in data.model_fields_set, data.work_email, user_id))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, '用户名已存在。') from exc
        if not changed.rowcount:
            raise HTTPException(404, '用户不存在。')
        return managed_user(connection.execute('SELECT * FROM workspace_users WHERE id = ?', (user_id,)).fetchone())


@router.post("/users/{user_id}/status")
def update_user(user_id: int, data: UserStatus, admin=Depends(require_admin)):
    if user_id == admin["id"] and not data.enabled:
        raise HTTPException(409, "不能停用当前登录账号。")
    with closing(get_connection()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        verify_admin(connection, admin["id"])
        if not data.enabled and connection.execute(
            "SELECT 1 FROM process_tickets WHERE approver_id = ? AND status = 'pending' LIMIT 1", (user_id,)
        ).fetchone():
            raise HTTPException(409, "该用户还有待审批工单，请处理完毕后再停用。")
        changed = connection.execute("UPDATE workspace_users SET enabled = ? WHERE id = ?", (data.enabled, user_id))
        if not changed.rowcount:
            raise HTTPException(404, "用户不存在。")
        if not data.enabled:
            connection.execute("DELETE FROM workspace_sessions WHERE user_id = ?", (user_id,))
        return managed_user(connection.execute("SELECT * FROM workspace_users WHERE id = ?", (user_id,)).fetchone())

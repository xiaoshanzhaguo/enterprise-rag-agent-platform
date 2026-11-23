"""Create the first administrator from an interactive local terminal."""

from contextlib import closing
from getpass import getpass

from backend.api.workspace_auth import NewUser, insert_user
from backend.db.connection import get_connection
from backend.db.init_db import init_database


def main():
    username = input("管理员用户名: ").strip()
    password = getpass("管理员密码（至少 8 个字符）: ")
    if password != getpass("再次输入密码: "):
        raise SystemExit("两次密码不一致。")
    try:
        data = NewUser(username=username, display_name="管理员", password=password, role="admin")
    except ValueError:
        raise SystemExit("用户名或密码不符合要求。") from None
    init_database()
    with closing(get_connection()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT 1 FROM workspace_users WHERE role = 'admin' AND enabled = 1").fetchone():
            raise SystemExit("已有启用的管理员，请从设置页管理用户。")
        insert_user(connection, data)
    print("管理员已创建。")


if __name__ == "__main__":
    main()

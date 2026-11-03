"""受控人工恢复：python -m backend.cli.rag_index_recovery --help。"""

import argparse
import json
import sqlite3
import sys
from uuid import UUID

from backend.db import index_repository as repository


def _operation_id(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("operation_id 必须是 UUID。") from exc


def _version(value: str) -> int:
    try:
        version = int(value)
        if version > 0:
            return version
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("state_version 必须是正整数。")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(
        "检查或人工恢复索引任务。恢复前必须停止原执行进程及所有可能继续写入的应用实例；"
        "此工具不会检测或停止进程，不按超时自动抢占，也不会重新执行索引。"
    ))
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="只读检查任务，不执行恢复")
    inspect.add_argument("operation_id", type=_operation_id)
    recover = commands.add_parser("recover", help="将指定版本的已领取 pending 标记为 failed/interrupted")
    recover.add_argument("operation_id", type=_operation_id)
    recover.add_argument("--expected-state-version", type=_version, required=True)
    recover.add_argument("--confirm-execution-stopped", action="store_true", required=True,
                         help="操作人员明确确认原执行进程和其他可能写入的实例均已停止")
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            operation = repository.get_index_operation(args.operation_id)
            if operation is None:
                raise LookupError("任务不存在。")
        else:
            operation = repository.recover_index_operation(
                args.operation_id, args.expected_state_version,
                execution_stopped_confirmed=args.confirm_execution_stopped,
            )
    except LookupError:
        print("未找到索引任务。", file=sys.stderr)
        return 3
    except repository.IndexOperationConflict:
        print("恢复被拒绝：任务状态或版本已变化，请重新检查。", file=sys.stderr)
        return 4
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except sqlite3.Error:
        print("数据库操作失败，未确认操作结果；请重新查询任务。", file=sys.stderr)
        return 5
    # 不输出执行 token、输入正文、配置或连接信息。
    fields = ("operation_id", "document_id", "status", "state_version", "execution_claimed",
              "owner_instance_id", "attempt_count", "error_code", "updated_at")
    print(json.dumps({key: operation[key] for key in fields}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""停写确认后的删除任务人工恢复；不提供 HTTP recover，也不执行删除。"""

import argparse
from uuid import UUID
from backend.db import deletion_repository as repository
from backend.services.document_deletion import DocumentDeletionService
import json


def main(argv=None):
    parser = argparse.ArgumentParser(description='确认原删除执行者已全部停止后，释放 claimed 任务供显式 retry。')
    parser.add_argument('operation_id', type=UUID)
    parser.add_argument('--expected-state-version', type=int, required=True)
    parser.add_argument('--confirm-execution-stopped', action='store_true', required=True)
    args = parser.parse_args(argv)
    try:
        result = repository.recover_deletion(args.operation_id, args.expected_state_version,
            execution_stopped_confirmed=args.confirm_execution_stopped)
    except Exception:
        print('恢复未确认，请查询任务；仅匹配版本的 claimed pending 允许恢复。')
        return 1
    print(json.dumps(DocumentDeletionService.receipt(result), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

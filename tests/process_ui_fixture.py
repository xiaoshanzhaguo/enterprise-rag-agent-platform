"""仅供浏览器离线验收：真实 API/SQLite，模拟模型和 n8n；不用于部署。"""

from contextlib import ExitStack
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import uvicorn
from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.api.processes import router
from backend.db.init_db import init_database
from backend.schema.process_submission_schema import ProcessSubmissionResponse
from backend.services.process_submission import ProcessSubmissionError


def model_reply(*, client, messages, temperature):
    text = messages[1]["content"]
    if "提取失败" in text:
        return "invalid json"
    fields = {"application_type": "VPN权限", "permission_level": "只读", "additional_info": text}
    if "缺失" not in text:
        fields.update(applicant="张三", department="研发部", approver="李经理")
    return json.dumps({"fields": fields}, ensure_ascii=False)


def webhook_reply(request):
    if "超时" in (request.fields.additional_info or ""):
        raise ProcessSubmissionError(504, "流程服务响应超时，执行状态未知，请先在 n8n 核对申请编号。")
    return ProcessSubmissionResponse(
        request_id=request.request_id, status="simulated",
        ticket_id=f"SIM-{request.request_id}", actions=["ticket_simulated"],
    )


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
        stack.enter_context(patch("backend.db.connection.settings", SimpleNamespace(database_url="sqlite:///" + str(Path(directory) / "ui.db"))))
        stack.enter_context(patch("backend.api.processes.get_client", return_value=object()))
        stack.enter_context(patch("backend.services.process_extraction.invoke_chat_model", side_effect=model_reply))
        stack.enter_context(patch("backend.services.process_submission._call_n8n_webhook", side_effect=webhook_reply))
        init_database()
        app = FastAPI()
        app.include_router(router)
        uvicorn.run(app, host="127.0.0.1", port=18001)

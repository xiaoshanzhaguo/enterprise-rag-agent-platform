"""显式运行的 Docker 部署冒烟检查，不被 unittest discover 自动执行。

使用独立项目、临时数据卷和空模型/Webhook 配置，不触发真实模型或流程。
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import tempfile
import time
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend-image', default='enterprise-rag-agent-backend:day27')
    parser.add_argument('--web-image', default='enterprise-rag-agent-web:day27')
    parser.add_argument('--backend-port', type=int, default=18020)
    parser.add_argument('--web-port', type=int, default=18021)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/day27_deployment_result.json')
    args = parser.parse_args()
    project = 'enterprise-day27-smoke-' + uuid4().hex[:8]
    report = {'executed_at_utc': datetime.now(timezone.utc).isoformat(), 'passed': False,
              'project': project, 'backend_image': args.backend_image, 'web_image': args.web_image,
              'scope': 'isolated_docker_no_external_model_or_webhook', 'checks': {}}
    with tempfile.TemporaryDirectory(prefix=project) as directory:
        folder = Path(directory)
        application = folder / 'smoke_app.py'
        application.write_text('''import asyncio
from fastapi.responses import StreamingResponse
from backend.main import app

@app.post('/__smoke__/stream')
async def stream_probe():
    async def frames():
        for index in range(3):
            yield f'data: {{"probe": {index}}}\\n\\n'
            await asyncio.sleep(0.4)
    return StreamingResponse(frames(), media_type='text/event-stream')
''', encoding='utf-8')
        override = folder / 'compose.smoke.yaml'
        override.write_text(f'''services:
  backend:
    image: {json.dumps(args.backend_image)}
    container_name: {project}-backend
    command: [uvicorn, smoke_app:app, --host, 0.0.0.0, --port, "8000"]
    ports: !override ["127.0.0.1:{args.backend_port}:8000"]
    environment:
      DEEPSEEK_API_KEY: ""
      BASE_URL: ""
      RAG_RETRIEVAL_MODE: keyword
      N8N_WEBHOOK_URL: ""
      N8N_WEBHOOK_TOKEN: ""
    volumes: !override
      - smoke_data:/app/data
      - {json.dumps(application.as_posix() + ':/app/smoke_app.py:ro')}
  web:
    image: {json.dumps(args.web_image)}
    ports: !override ["127.0.0.1:{args.web_port}:80"]
volumes:
  smoke_data:
''', encoding='utf-8')
        command = ['docker', 'compose', '-p', project, '--project-directory', str(ROOT),
                   '-f', str(ROOT / 'docker-compose.yml'), '-f', str(override)]

        def run(*parts):
            return subprocess.run([*command, *parts], check=True, timeout=240, capture_output=True, text=True)

        try:
            run('config', '--quiet')
            run('up', '-d', '--no-build', '--wait', '--wait-timeout', '180', 'backend', 'web')
            run('exec', '-T', 'web', 'nginx', '-t')
            report['checks']['nginx_config'] = True
            base = f'http://127.0.0.1:{args.web_port}'
            with httpx.Client(trust_env=False, timeout=20) as client:
                for route in ('/', '/logs', '/workflow', '/eval'):
                    response = client.get(base + route)
                    assert response.status_code == 200 and '<div id="root">' in response.text, route
                report['checks']['spa_routes'] = True
                health = client.get(base + '/api/health')
                assert health.status_code == 200 and health.json()['status'] == 'ok'
                report['checks']['api_proxy_health'] = True
                records = client.get(base + '/api/process_applications').json()
                assert records['items'] == [] and records['total'] == 0
                report['checks']['isolated_empty_sqlite'] = True
                missing = client.get(base + '/api/process_applications/11111111-1111-4111-8111-111111111111')
                assert missing.status_code == 404 and 'application/json' in missing.headers['content-type']
                report['checks']['api_errors_not_spa_html'] = True
                started = time.perf_counter()
                arrivals = []
                with client.stream('POST', base + '/api/__smoke__/stream', json={}) as stream:
                    assert stream.status_code == 200 and 'text/event-stream' in stream.headers['content-type']
                    for line in stream.iter_lines():
                        if line.startswith('data:'):
                            arrivals.append(round(time.perf_counter() - started, 3))
                assert len(arrivals) == 3 and arrivals[-1] - arrivals[0] > 0.3, arrivals
                report['checks']['post_sse_unbuffered'] = True
                report['sse_arrival_seconds'] = arrivals
            run('exec', '-T', 'backend', 'python', '-c',
                "from pathlib import Path; assert not any(Path(p).exists() for p in "
                "['/app/.env','/app/integrations/dify/.env.local','/app/integrations/dify/.runtime','/app/frontend-react/node_modules'])")
            report['checks']['local_secrets_and_dependencies_excluded'] = True
            report['passed'] = True
        finally:
            # project 是本脚本新生成的唯一测试命名空间；仅清理它自己的临时卷和容器。
            cleanup = subprocess.run([*command, 'down', '--volumes'], timeout=120, capture_output=True, text=True)
            report['cleaned_up'] = cleanup.returncode == 0
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        if not report['cleaned_up']:
            raise SystemExit('测试资源清理失败，请按报告中的唯一 project 核对。')
        print('PASS: isolated Docker, SPA, API, SQLite, POST SSE, build-context isolation; test containers removed')


if __name__ == '__main__':
    main()

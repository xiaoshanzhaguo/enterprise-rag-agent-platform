"""准备独立的本机 Dify 环境；官方源码、数据与随机秘密仅保存在忽略目录。"""

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import secrets
import subprocess
from urllib.request import Request, urlopen


VERSION = "1.18.0"
ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / ".runtime"
DOCKER = RUNTIME / "docker"
PROJECT = "enterprise-dify-day22"


def fetch(url: str) -> bytes:
    with urlopen(Request(url, headers={"User-Agent": "enterprise-rag-day22-setup"}), timeout=30) as response:
        return response.read()


def blob_hash(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def download_official_files() -> None:
    manifest = RUNTIME / "source-manifest.json"
    if manifest.exists():
        tree = json.loads(manifest.read_text(encoding="utf-8"))
        if tree.get("version") != VERSION:
            raise ValueError("本机源码版本不匹配，请使用独立目录升级，不覆盖现有部署。")
    else:
        payload = json.loads(fetch(f"https://api.github.com/repos/langgenius/dify/git/trees/{VERSION}?recursive=1"))
        if payload.get("truncated"):
            raise ValueError("官方文件清单不完整。")
        tree = {
            "version": VERSION, "tree_sha": payload["sha"],
            "files": [item for item in payload["tree"] if item["path"].startswith("docker/") and item["type"] == "blob"],
        }

    def download(item):
        relative = PurePosixPath(item["path"]).relative_to("docker")
        target = DOCKER.joinpath(*relative.parts).resolve()
        if not target.is_relative_to(DOCKER.resolve()):
            raise ValueError("无效的官方文件路径。")
        if target.exists() and blob_hash(target.read_bytes()) == item["sha"]:
            return
        data = fetch(f"https://raw.githubusercontent.com/langgenius/dify/{VERSION}/{item['path']}")
        if blob_hash(data) != item["sha"]:
            # 某些原始文件下载有编码转换，使用 Git blob API 保留原始字节。
            blob = json.loads(fetch(item["url"]))
            data = base64.b64decode(blob["content"])
        if blob_hash(data) != item["sha"]:
            raise ValueError(f"官方文件校验失败：{item['path']}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    DOCKER.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(download, tree["files"]))
    manifest.write_text(json.dumps(tree, indent=2) + "\n", encoding="utf-8")
    print(f"已校验 Dify {VERSION} 官方部署文件：{len(tree['files'])} 个。", flush=True)


def prepare_config() -> None:
    env_path = DOCKER / ".env"
    if env_path.exists():
        print("保留现有本机 .env，不重置秘密或数据库配置。", flush=True)
        return
    env = (DOCKER / ".env.example").read_text(encoding="utf-8")
    sandbox_key = secrets.token_urlsafe(32)
    redis_password = secrets.token_urlsafe(24)
    updates = {
        "SECRET_KEY": secrets.token_urlsafe(48),
        "DB_PASSWORD": secrets.token_urlsafe(24),
        "REDIS_PASSWORD": redis_password,
        "CELERY_BROKER_URL": f"redis://:{redis_password}@redis:6379/1",
        "SANDBOX_API_KEY": sandbox_key,
        "CODE_EXECUTION_API_KEY": sandbox_key,
        "PLUGIN_DAEMON_KEY": secrets.token_urlsafe(48),
        "PLUGIN_DIFY_INNER_API_KEY": secrets.token_urlsafe(48),
        "DIFY_AGENT_API_TOKEN": secrets.token_urlsafe(32),
        "DIFY_AGENT_LOCAL_SANDBOX_AUTH_TOKEN": secrets.token_urlsafe(32),
        "DIFY_AGENT_SERVER_SECRET_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        "EXPOSE_NGINX_PORT": "18080",
        "EXPOSE_NGINX_SSL_PORT": "18443",
        "TRIGGER_URL": "http://localhost:18080",
        "ENDPOINT_URL_TEMPLATE": "http://localhost:18080/e/{hook_id}",
        "NEXT_PUBLIC_SOCKET_URL": "ws://localhost:18080",
    }
    for key, value in updates.items():
        env, count = re.subn(rf"(?m)^{re.escape(key)}=.*$", lambda _: f"{key}={value}", env)
        if count != 1:
            raise ValueError(f"官方配置格式发生变化：{key}")
    env_path.write_text(env, encoding="utf-8")
    print("已创建本机随机秘密配置；不打印或提交秘密。", flush=True)


def compose_command(*args: str) -> list[str]:
    return [
        "docker", "compose", "--progress", "plain", "--project-name", PROJECT,
        "--project-directory", str(DOCKER), "--env-file", str(DOCKER / ".env"),
        "-f", str(DOCKER / "docker-compose.yaml"), "-f", str(ROOT / "compose.local.yaml"), *args,
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", action="store_true", help="校验 Docker 后启动独立的 Dify Compose 项目")
    args = parser.parse_args()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    download_official_files()
    prepare_config()
    subprocess.run(compose_command("config", "--quiet"), check=True, timeout=30)
    print("Compose 配置校验通过。访问地址：http://localhost:18080", flush=True)
    if args.start:
        subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], check=True, timeout=25)
        subprocess.run(compose_command("up", "-d"), check=True)


if __name__ == "__main__":
    main()

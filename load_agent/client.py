"""Small JSON client with private durable Agent credentials."""

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


logger = logging.getLogger(__name__)
AGENT_PATH = "/api/api-testing/load-agent/v1"


class AgentClientError(RuntimeError):
    pass


@dataclass(frozen=True, repr=False)
class AgentCredential:
    agent_id: str
    secret: str

    def __repr__(self):
        return f"AgentCredential(agent_id={self.agent_id!r}, secret='***')"


class UrlTransport:
    def request(self, method, url, body, headers, timeout):
        payload = None if body is None else json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        request = Request(url, data=payload, method=method, headers=headers)
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL is strict config
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            try:
                detail = json.loads(error.read().decode("utf-8"))
                message = detail.get("error", {}).get("message") or f"HTTP {error.code}"
            except Exception:
                message = f"HTTP {error.code}"
            raise AgentClientError(message) from error
        except URLError as error:
            raise OSError("平台连接失败") from error


class AgentClient:
    def __init__(self, config, *, transport=None, retry_attempts=3, sleeper=None):
        self.config = config
        self.transport = transport or UrlTransport()
        self.retry_attempts = max(1, int(retry_attempts))
        self.sleeper = sleeper or time.sleep
        self._credential = None

    def ensure_registered(self, capabilities):
        if self._credential is not None:
            return self._credential
        if self.config.credential_file.is_file():
            self._credential = self._read_credential()
            return self._credential
        response = self._request(
            "POST",
            "/register",
            {"enrollment_token": self.config.enroll_token, "capabilities": capabilities},
            authenticate=False,
        )
        data = response.get("data", {})
        agent = data.get("agent") if isinstance(data.get("agent"), dict) else {}
        credential = AgentCredential(str(agent.get("id") or ""), str(data.get("secret") or ""))
        if not credential.agent_id or not credential.secret:
            raise AgentClientError("平台注册响应缺少节点凭据")
        self._store_credential(credential)
        self._credential = credential
        logger.info("压测节点注册成功 agent_id=%s", credential.agent_id)
        return credential

    def heartbeat(self, payload):
        return self._request("POST", "/heartbeat", payload)

    def claim(self):
        return self._request("POST", "/claim", {}).get("data", {}).get("shard")

    def commands(self, shard_id):
        return self._request("GET", f"/shards/{shard_id}/commands", None).get("data", {}).get("commands", [])

    def mark_started(self, shard_id, process_info):
        return self._request("POST", f"/shards/{shard_id}/started", {"process_info": process_info})

    def post_metrics(self, shard_id, payload, *, batch_id):
        return self._request("POST", f"/shards/{shard_id}/metrics", {**payload, "batch_id": batch_id})

    def post_samples(self, shard_id, payload, *, batch_id):
        return self._request("POST", f"/shards/{shard_id}/samples", {**payload, "batch_id": batch_id})

    def post_events(self, shard_id, payload, *, batch_id):
        return self._request("POST", f"/shards/{shard_id}/events", {**payload, "batch_id": batch_id})

    def finish(self, shard_id, state, summary, error):
        return self._request("POST", f"/shards/{shard_id}/finish", {"state": state, "summary": summary, "error": error})

    def _request(self, method, path, body, *, authenticate=True):
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if authenticate:
            if self._credential is None:
                raise AgentClientError("节点尚未注册")
            headers["Authorization"] = "Agent " + self._credential.secret
        for attempt in range(self.retry_attempts):
            try:
                return self.transport.request(
                    method,
                    self.config.platform_url + AGENT_PATH + path,
                    body,
                    headers,
                    self.config.request_timeout_seconds,
                )
            except OSError as error:
                if attempt + 1 >= self.retry_attempts:
                    raise AgentClientError(f"平台请求失败：{type(error).__name__}") from error
                self.sleeper(min(2 ** attempt, 5))
        raise AgentClientError("平台请求失败")

    def _read_credential(self):
        directory_mode = self.config.data_dir.stat().st_mode & 0o777
        if directory_mode & 0o077:
            raise AgentClientError("节点数据目录权限必须是0700")
        mode = self.config.credential_file.stat().st_mode & 0o777
        if mode & 0o077:
            raise AgentClientError("节点凭据文件权限必须是0600")
        try:
            data = json.loads(self.config.credential_file.read_text(encoding="utf-8"))
            credential = AgentCredential(str(data["agent_id"]), str(data["secret"]))
        except (OSError, KeyError, TypeError, ValueError) as error:
            raise AgentClientError("节点凭据文件无效") from error
        if not credential.agent_id or not credential.secret:
            raise AgentClientError("节点凭据文件无效")
        return credential

    def _store_credential(self, credential):
        self.config.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.config.data_dir, 0o700)
        temporary = self.config.data_dir / ".credential.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump({"agent_id": credential.agent_id, "secret": credential.secret}, stream, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.config.credential_file)
        finally:
            if temporary.exists():
                temporary.unlink()

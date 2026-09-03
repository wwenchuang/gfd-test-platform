"""Bounded DNS, TCP and TLS connectivity checks for target environments."""

import socket
import ssl
import time


STAGE_MESSAGES = {
    "dns": "DNS解析失败，请检查节点DNS、目标域名和网络策略",
    "connect": "TCP连接失败，请检查目标端口、防火墙和安全组",
    "tls": "TLS握手失败，请检查证书、域名和系统时间",
}


def probe_target(target, *, timeout=5.0):
    host = str(target.get("host") or "")
    port = int(target.get("port") or (443 if target.get("tls") else 80))
    if not host:
        return {"reachable": False, "stage": "dns", "message": STAGE_MESSAGES["dns"]}
    started = time.monotonic()
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return {"reachable": False, "stage": "dns", "message": STAGE_MESSAGES["dns"]}
    dns_ms = (time.monotonic() - started) * 1000
    connected = time.monotonic()
    raw_socket = None
    try:
        raw_socket = socket.create_connection(addresses[0][4], timeout=timeout)
        connect_ms = (time.monotonic() - connected) * 1000
        tls_ms = 0.0
        if target.get("tls"):
            tls_started = time.monotonic()
            context = ssl.create_default_context()
            with context.wrap_socket(raw_socket, server_hostname=host, do_handshake_on_connect=False) as wrapped:
                raw_socket = None
                wrapped.do_handshake()
            tls_ms = (time.monotonic() - tls_started) * 1000
        return {"reachable": True, "stage": "complete", "dns_ms": dns_ms, "connect_ms": connect_ms, "tls_ms": tls_ms}
    except ssl.SSLError:
        return {"reachable": False, "stage": "tls", "message": STAGE_MESSAGES["tls"], "dns_ms": dns_ms}
    except OSError:
        return {"reachable": False, "stage": "connect", "message": STAGE_MESSAGES["connect"], "dns_ms": dns_ms}
    finally:
        if raw_socket is not None:
            raw_socket.close()


def run_connectivity_command(command, *, probe=probe_target):
    targets = command.get("targets") if isinstance(command, dict) else None
    if not isinstance(targets, list) or not targets:
        return {
            "command_id": str(command.get("id") or ""), "reachable": False, "stage": "configuration",
            "message": "目标环境没有可检查的服务地址，请先完善环境配置",
        }
    totals = {"dns_ms": 0.0, "connect_ms": 0.0, "tls_ms": 0.0}
    for target in targets:
        result = probe(target)
        for key in totals:
            totals[key] += max(0.0, float(result.get(key) or 0))
        if result.get("reachable") is not True:
            stage = str(result.get("stage") or "connect")
            return {
                "command_id": str(command.get("id") or ""), "reachable": False,
                "stage": stage, "failed_target": str(target.get("name") or "未命名服务"),
                "message": STAGE_MESSAGES.get(stage, "目标环境不可达，请检查节点网络和环境地址"),
                **totals,
            }
    return {
        "command_id": str(command.get("id") or ""), "reachable": True,
        "stage": "complete", "message": "目标环境DNS、连接和TLS检查通过", **totals,
    }

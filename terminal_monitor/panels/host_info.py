from __future__ import annotations

import os
import platform
import socket
from dataclasses import dataclass


@dataclass
class HostInfo:
    hostname: str
    primary_ip: str
    kernel: str
    os: str
    user: str


def get_host_info() -> HostInfo:
    hostname = socket.gethostname()
    primary_ip = _get_primary_ip()
    kernel = platform.release()
    os_name = platform.system()
    user = os.getenv("USER", "unknown")
    return HostInfo(hostname=hostname, primary_ip=primary_ip, kernel=kernel, os=os_name, user=user)


def _get_primary_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
    except OSError:
        ip = "unknown"
    finally:
        try:
            sock.close()
        except Exception:
            pass
    return ip

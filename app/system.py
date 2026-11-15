"""Systeminfo till startsidan: CPU, minne, disk, drifttid."""
from __future__ import annotations

import os
import shutil
import socket
import time

import psutil

from . import config

psutil.cpu_percent(None)  # första anropet ger alltid 0


def _host_file(path: str) -> str:
    for p in (config.HOST_ROOT / path.lstrip("/"), path):
        try:
            return open(p, encoding="utf-8").read()
        except OSError:
            continue
    return ""


def hostname() -> str:
    return _host_file("/etc/hostname").strip() or socket.gethostname()


def os_name() -> str:
    for line in _host_file("/etc/os-release").splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def stats() -> dict:
    vm = psutil.virtual_memory()
    try:
        du = shutil.disk_usage(config.DATA_ROOT if config.DATA_ROOT.exists() else "/")
        disk = {"total": du.total, "used": du.used, "percent": round(du.used / du.total * 100, 1)}
    except OSError:
        disk = {"total": 0, "used": 0, "percent": 0}
    try:
        uptime = float(open("/proc/uptime").read().split()[0])
    except OSError:
        uptime = time.time() - psutil.boot_time()
    try:
        load = os.getloadavg()
    except (OSError, AttributeError):
        load = (0, 0, 0)
    temp = None
    try:
        temps = psutil.sensors_temperatures()
        for key in ("coretemp", "k10temp", "cpu_thermal", "zenpower", "acpitz"):
            if temps.get(key):
                temp = round(temps[key][0].current)
                break
    except (AttributeError, OSError):
        pass
    net = psutil.net_io_counters()
    return {
        "hostname": config.load().get("server_name") or hostname(),
        "os": os_name(),
        "cpu": psutil.cpu_percent(None),
        "cores": psutil.cpu_count() or 1,
        "load": [round(x, 2) for x in load],
        "temp": temp,
        "mem": {"total": vm.total, "used": vm.total - vm.available, "percent": vm.percent},
        "disk": disk,
        "uptime": int(uptime),
        "net": {"sent": net.bytes_sent, "recv": net.bytes_recv},
        "time": time.time(),
    }

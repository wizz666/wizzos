"""Bakgrundsjobb (installera, uppdatera ...) med logg som gränssnittet kan läsa."""
from __future__ import annotations

import subprocess
import threading
import time
import uuid

_jobs: dict[str, dict] = {}


def start(title: str, fn) -> str:
    """Kör fn(log) i en tråd. log(text) lägger till en rad i jobbets logg."""
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "title": title, "state": "running", "log": [],
           "started": time.time(), "error": "", "result": None}
    _jobs[job_id] = job

    def log(line: str) -> None:
        job["log"].append(line.rstrip())
        del job["log"][:-400]

    def run():
        try:
            job["result"] = fn(log)
            job["state"] = "done"
        except Exception as e:  # noqa: BLE001 - visas för användaren
            job["state"] = "failed"
            job["error"] = str(e)
            log(f"FEL: {e}")

    threading.Thread(target=run, daemon=True).start()
    _prune()
    return job_id


def get(job_id: str) -> dict | None:
    return _jobs.get(job_id)


def running() -> list[dict]:
    return [{k: j[k] for k in ("id", "title", "state")} for j in _jobs.values()
            if j["state"] == "running"]


def _prune() -> None:
    old = [k for k, j in _jobs.items()
           if j["state"] != "running" and time.time() - j["started"] > 3600]
    for k in old:
        _jobs.pop(k, None)


def run_cmd(cmd: list[str], log, cwd: str | None = None) -> None:
    """Kör ett kommando och skicka varje utdatarad till log. Fel = exception."""
    log("$ " + " ".join(cmd))
    p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace")
    assert p.stdout
    for line in p.stdout:
        line = line.rstrip()
        if line:
            log(line)
    if p.wait() != 0:
        raise RuntimeError(f"Kommandot misslyckades (kod {p.returncode})")

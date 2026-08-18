# -*- coding: utf-8 -*-
"""Long-running IronPython host for repository-local InoProShop MCP calls.

InoProShop executes this file through ``--runscript``. Individual MCP scripts
are delivered through a private queue directory and executed in this process,
so ``projects.primary`` stays open until PLC-Sim explicitly stops the session.
"""

import os
import re
import traceback

import scriptengine


SESSION_DIR = os.environ.get("OPCUASIM_INO_SESSION_DIR", "")
QUEUE_DIR = os.path.join(SESSION_DIR, "queue")
HEARTBEAT_FILE = os.path.join(SESSION_DIR, "heartbeat")
READY_FILE = os.path.join(SESSION_DIR, "ready")
STOP_FILE = os.path.join(SESSION_DIR, "stop")
STOPPED_FILE = os.path.join(SESSION_DIR, "stopped")
RESULT_RE = re.compile(r"_RESULT_FILE\s*=\s*r?['\"]([^'\"]+)['\"]")


def _write_file(path, text):
    handle = open(path, "wb")
    try:
        handle.write(text.encode("utf-8"))
    finally:
        handle.close()


def _append_result(path, text):
    if not path:
        return
    try:
        handle = open(path, "ab")
        try:
            handle.write((text + "\n").encode("utf-8"))
        finally:
            handle.close()
    except Exception:
        pass


def _opcuasim_keep_project_open(project):
    """Replacement target for bundle scripts that normally close every call."""
    return project


def _stop_requested():
    return os.path.exists(STOP_FILE)


def _parent_is_gone():
    try:
        import time
        return time.time() - os.path.getmtime(HEARTBEAT_FILE) > 12.0
    except Exception:
        return False


def _next_job():
    try:
        names = sorted(
            name for name in os.listdir(QUEUE_DIR)
            if name.endswith(".job.py")
        )
    except Exception:
        return None
    return os.path.join(QUEUE_DIR, names[0]) if names else None


def _run_job(job_path):
    result_path = ""
    try:
        handle = open(job_path, "rb")
        try:
            code = handle.read().decode("utf-8-sig")
        finally:
            handle.close()
        match = RESULT_RE.search(code)
        if match:
            result_path = match.group(1)
        compiled = compile(code, job_path, "exec")
        try:
            exec compiled in globals(), globals()
        except SystemExit:
            # MCP tool scripts use sys.exit() to finish one request. In the
            # persistent host it must end only that request, not InoProShop.
            pass
    except Exception:
        _append_result(
            result_path or globals().get("_RESULT_FILE", ""),
            "SCRIPT_ERROR: Persistent host job failed:\n" + traceback.format_exc(),
        )
    finally:
        try:
            os.unlink(job_path)
        except Exception:
            pass


def _close_project():
    try:
        primary = scriptengine.projects.primary
        if primary:
            primary.close()
    except Exception:
        pass


def main():
    if not SESSION_DIR:
        raise RuntimeError("OPCUASIM_INO_SESSION_DIR is required")
    if not os.path.isdir(QUEUE_DIR):
        os.makedirs(QUEUE_DIR)

    try:
        scriptengine.system.prompt_handling = scriptengine.PromptHandling.ProcessScriptPrompts
    except Exception:
        pass

    _write_file(READY_FILE, str(os.getpid()))
    try:
        while True:
            if _stop_requested() or _parent_is_gone():
                break
            job = _next_job()
            if job:
                _run_job(job)
            else:
                # Unlike time.sleep(), system.delay() pumps the InoProShop
                # message loop while this long-running script is active.
                scriptengine.system.delay(100)
    finally:
        _close_project()
        _write_file(STOPPED_FILE, "stopped")
        try:
            scriptengine.system.exit(0)
        except Exception:
            pass


main()

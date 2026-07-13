"""Subprocess helpers — unified hidden-window execution.

All external process spawns go through this module so that window
visibility, timeout, and encoding are controlled in one place.

Usage::

    from core.proc import run_ps, popen_hidden

    # 同步执行 PowerShell 脚本，返回 stdout
    out = run_ps("Get-Process | Out-Null", timeout=10)

    # 启动隐藏窗口的子进程（长驻读 stdout 等场景）
    proc = popen_hidden([python, worker_py], stdout=PIPE)
"""

from __future__ import annotations

import os
import subprocess
import tempfile

# ── 预构建隐藏窗口参数（所有子进程共享） ──


def _make_startupinfo() -> subprocess.STARTUPINFO:
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    return si


_HIDDEN_SI = _make_startupinfo()
_HIDDEN_FLAGS = subprocess.CREATE_NO_WINDOW


def popen_hidden(
    args: list[str],
    *,
    stdout=None,
    stderr=None,
    stdin=None,
    bufsize: int = -1,
    encoding: str | None = None,
    timeout: int | None = None,
    **kwargs,
) -> subprocess.Popen:
    """启动一个无窗口子进程，返回 Popen 对象。

    所有额外关键字参数会透传给 ``subprocess.Popen``。
    """
    return subprocess.Popen(
        args,
        stdout=stdout,
        stderr=stderr,
        stdin=stdin,
        bufsize=bufsize,
        encoding=encoding,
        startupinfo=_HIDDEN_SI,
        creationflags=_HIDDEN_FLAGS,
        **kwargs,
    )


def run_ps(
    script: str,
    *,
    timeout: float = 8,
    encoding: str = "utf-8",
) -> str:
    """执行 PowerShell 脚本并返回 stdout（去尾空白）。

    脚本通过临时 .ps1 文件传递，避免转义问题。
    出错或超时返回空字符串。
    """
    ps_path = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".ps1", delete=False, encoding=encoding,
    )
    try:
        tmp.write(script)
        tmp.close()
        r = subprocess.run(
            [ps_path, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", tmp.name],
            capture_output=True,
            text=True,
            encoding=encoding,
            errors="replace",
            timeout=timeout,
            startupinfo=_HIDDEN_SI,
            creationflags=_HIDDEN_FLAGS,
        )
        return r.stdout.strip()
    except Exception:
        return ""
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def run_command(
    args: list[str],
    *,
    timeout: float = 10,
    encoding: str = "utf-8",
) -> str:
    """同步执行任意命令并返回 stdout（去尾空白）。"""
    try:
        r = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding=encoding,
            errors="replace",
            timeout=timeout,
            startupinfo=_HIDDEN_SI,
            creationflags=_HIDDEN_FLAGS,
        )
        return r.stdout.strip()
    except Exception:
        return ""

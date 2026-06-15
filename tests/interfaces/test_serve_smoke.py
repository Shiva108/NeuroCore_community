from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def _base_env(tmp_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = (
        f"{SRC_ROOT}{os.pathsep}{env['PYTHONPATH']}"
        if env.get("PYTHONPATH")
        else str(SRC_ROOT)
    )
    env["PYTHONUNBUFFERED"] = "1"
    env["NEUROCORE_OPERATOR_HOME"] = str(tmp_path / "operator-home")
    env["NEUROCORE_DEFAULT_NAMESPACE"] = "project-alpha"
    env["NEUROCORE_ALLOWED_BUCKETS"] = "research,ops"
    env["NEUROCORE_DEFAULT_SENSITIVITY"] = "standard"
    env["NEUROCORE_STORAGE_BACKEND"] = "sqlite"
    env["NEUROCORE_PRIMARY_STORE_PATH"] = str(tmp_path / "primary.db")
    env["NEUROCORE_SEALED_STORE_PATH"] = str(tmp_path / "sealed.db")
    return env


@contextmanager
def _serve_process(tmp_path: Path, *args: str, env: dict[str, str]):
    proc = subprocess.Popen(
        [sys.executable, "-m", "neurocore", *args],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        yield proc
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


def _wait_for_http_ready(
    proc: subprocess.Popen[str],
    url: str,
    *,
    timeout: float = 10.0,
    ready_statuses: tuple[int, ...] = (200,),
):
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        if proc.poll() is not None:
            stdout, stderr = proc.communicate()
            raise AssertionError(
                f"process exited early with code {proc.returncode}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
            )
        try:
            with urlopen(url, timeout=1.0) as response:
                if response.status in ready_statuses:
                    return response.read().decode("utf-8")
        except HTTPError as exc:
            if exc.code in ready_statuses:
                return exc.read().decode("utf-8")
            last_error = f"HTTP {exc.code}"
        except Exception as exc:
            last_error = str(exc)
            time.sleep(0.1)
    stdout, stderr = proc.communicate(timeout=1)
    raise AssertionError(
        f"timed out waiting for readiness at {url}: {last_error}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
    )


def test_serve_http_subprocess_smoke(tmp_path: Path):
    port = _free_port()
    env = _base_env(tmp_path)
    env["NEUROCORE_ENABLE_HTTP_ADAPTER"] = "true"

    with _serve_process(
        tmp_path,
        "serve",
        "http",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        env=env,
    ) as proc:
        payload = _wait_for_http_ready(proc, f"http://127.0.0.1:{port}/openapi.json")

    assert '"title":"FastAPI"' in payload or '"openapi"' in payload


@pytest.mark.asyncio
async def test_serve_mcp_streamable_http_subprocess_smoke(tmp_path: Path):
    port = _free_port()
    env = _base_env(tmp_path)
    env["NEUROCORE_ENABLE_MCP_ADAPTER"] = "true"

    with _serve_process(
        tmp_path,
        "serve",
        "mcp",
        "--transport",
        "streamable-http",
        "--mount-path",
        "/mcp",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        env=env,
    ) as proc:
        _wait_for_http_ready(
            proc,
            f"http://127.0.0.1:{port}/mcp",
            ready_statuses=(200, 400, 404, 405, 406),
        )
        async with streamable_http_client(f"http://127.0.0.1:{port}/mcp") as (
            read_stream,
            write_stream,
            _get_session_id,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()

    tool_names = {tool.name for tool in tools.tools}
    assert "capture_memory" in tool_names
    assert "query_memory" in tool_names

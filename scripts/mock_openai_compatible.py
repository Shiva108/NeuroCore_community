"""Tiny local OpenAI-compatible mock for NeuroCore consensus development."""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/mock_openai_compatible.py",
        description="Run a local OpenAI-compatible chat completions mock.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    return parser


class _Handler(BaseHTTPRequestHandler):
    server_version = "NeuroCoreMock/0.1"

    def log_message(self, format: str, *args) -> None:  # pragma: no cover
        return

    def do_GET(self) -> None:  # pragma: no cover - trivial
        if self.path.rstrip("/") == "/health":
            self._send_json(200, {"status": "ok"})
            return
        self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._send_json(404, {"error": "not_found"})
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        payload = json.loads(raw.decode("utf-8") or "{}")
        model = str(payload.get("model") or "mock-model")
        messages = payload.get("messages") or []
        prompt = ""
        if messages and isinstance(messages, list):
            prompt = str((messages[-1] or {}).get("content") or "")

        response = {
            "id": "mock-chatcmpl-1",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": _render_response(model=model, prompt=prompt),
                    },
                    "finish_reason": "stop",
                }
            ],
        }
        self._send_json(200, response)

    def _send_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _render_response(*, model: str, prompt: str) -> str:
    first_line = prompt.strip().splitlines()[0] if prompt.strip() else "No prompt"
    return (
        "## Overview\n"
        f"Local mock response from {model}.\n\n"
        "## Findings\n"
        f"Prompt seed: {first_line[:120]}\n\n"
        "## Risks\n"
        "Local development mock only.\n\n"
        "## Actions\n"
        "Replace with a real OpenAI-compatible endpoint for production use."
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    server = ThreadingHTTPServer((args.host, args.port), _Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - manual usage
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

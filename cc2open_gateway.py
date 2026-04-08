#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urljoin, urlparse


"""
Minimal Anthropic-to-OpenAI gateway for Claude Code style clients.

Key behavior:
- Accepts Anthropic-style requests on /v1/messages
- Forwards them to OpenAI /v1/chat/completions
- Ignores any incoming effort field and always injects a configurable
  OpenAI reasoning_effort value (default: xhigh)
"""


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com"
DEFAULT_OPENAI_CHAT_PATH = "/v1/chat/completions"
DEFAULT_REASONING_EFFORT = "xhigh"
DEFAULT_TIMEOUT_SECONDS = 600
DEFAULT_STREAM_PING_INTERVAL = 5
DEFAULT_STREAM_IDLE_TIMEOUT = 15
SERVER_NAME = "cc2open-gateway"


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    openai_base_url: str
    openai_chat_path: str
    openai_api_key: str
    openai_model: str | None
    reasoning_effort: str
    timeout_seconds: int
    stream_ping_interval: int
    stream_idle_timeout: int
    debug: bool

    @property
    def upstream_url(self) -> str:
        base_url = self.openai_base_url.rstrip("/")
        chat_path = self.openai_chat_path
        if base_url.endswith("/v1") and chat_path.startswith("/v1/"):
            base_url = base_url[: -len("/v1")]
        base = base_url + "/"
        path = chat_path.lstrip("/")
        return urljoin(base, path)


def env_or_default(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def parse_args() -> Config:
    parser = argparse.ArgumentParser(
        description="Anthropic-style gateway that forwards to OpenAI chat completions."
    )
    parser.add_argument("--host", default=env_or_default("CC2OPEN_HOST", DEFAULT_HOST))
    parser.add_argument(
        "--port",
        type=int,
        default=int(env_or_default("CC2OPEN_PORT", str(DEFAULT_PORT))),
    )
    parser.add_argument(
        "--openai-base-url",
        default=env_or_default("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL),
    )
    parser.add_argument(
        "--openai-chat-path",
        default=env_or_default("OPENAI_CHAT_PATH", DEFAULT_OPENAI_CHAT_PATH),
    )
    parser.add_argument(
        "--openai-api-key",
        default=env_or_default("OPENAI_API_KEY"),
    )
    parser.add_argument(
        "--openai-model",
        default=env_or_default("OPENAI_MODEL"),
        help="Override the upstream OpenAI model. Defaults to the incoming model when unset.",
    )
    parser.add_argument(
        "--reasoning-effort",
        default=env_or_default(
            "OPENAI_REASONING_EFFORT", DEFAULT_REASONING_EFFORT
        ),
        help="Always inject this OpenAI reasoning_effort value. Default: xhigh",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=int(
            env_or_default("CC2OPEN_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
        ),
    )
    parser.add_argument(
        "--stream-ping-interval",
        type=int,
        default=int(
            env_or_default(
                "CC2OPEN_STREAM_PING_INTERVAL", str(DEFAULT_STREAM_PING_INTERVAL)
            )
        ),
        help="Send SSE ping events every N seconds while streaming. Default: 5",
    )
    parser.add_argument(
        "--stream-idle-timeout",
        type=int,
        default=int(
            env_or_default(
                "CC2OPEN_STREAM_IDLE_TIMEOUT", str(DEFAULT_STREAM_IDLE_TIMEOUT)
            )
        ),
        help="Finalize stream if upstream stays idle for N seconds after output starts. Default: 15",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=(env_or_default("CC2OPEN_DEBUG", "0") == "1"),
        help="Enable verbose debug logs for streaming and upstream requests.",
    )

    args = parser.parse_args()

    if not args.openai_api_key:
        parser.error("OPENAI_API_KEY or --openai-api-key is required")

    return Config(
        host=args.host,
        port=args.port,
        openai_base_url=args.openai_base_url,
        openai_chat_path=args.openai_chat_path,
        openai_api_key=args.openai_api_key,
        openai_model=args.openai_model,
        reasoning_effort=args.reasoning_effort,
        timeout_seconds=args.timeout_seconds,
        stream_ping_interval=args.stream_ping_interval,
        stream_idle_timeout=args.stream_idle_timeout,
        debug=args.debug,
    )


def json_dumps(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def safe_json_loads(raw: bytes) -> Any:
    return json.loads(raw.decode("utf-8"))


def now_ts() -> int:
    return int(time.time())


def gen_message_id() -> str:
    return f"msg_{uuid.uuid4().hex}"


def gen_tool_id() -> str:
    return f"toolu_{uuid.uuid4().hex}"


def anthropic_error(message: str, error_type: str = "api_error") -> dict[str, Any]:
    return {
        "type": "error",
        "error": {
            "type": error_type,
            "message": message,
        },
    }


def anthropic_text_from_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                item_type = item.get("type")
                if item_type == "text":
                    parts.append(str(item.get("text", "")))
                elif item_type == "tool_result":
                    inner = item.get("content")
                    if isinstance(inner, str):
                        parts.append(inner)
                    elif isinstance(inner, list):
                        parts.append(anthropic_text_from_content(inner))
                elif "text" in item:
                    parts.append(str(item.get("text", "")))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(part for part in parts if part)
    return str(content)


def flatten_system_prompt(system_value: Any) -> str | None:
    text = anthropic_text_from_content(system_value).strip()
    return text or None


def build_openai_tools(anthropic_tools: Any) -> list[dict[str, Any]] | None:
    if not isinstance(anthropic_tools, list) or not anthropic_tools:
        return None

    tools: list[dict[str, Any]] = []
    for tool in anthropic_tools:
        if not isinstance(tool, dict) or not tool.get("name"):
            continue
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {"type": "object"}),
                },
            }
        )
    return tools or None


def map_tool_choice(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        if value in {"auto", "none", "required"}:
            return value
        return None
    if isinstance(value, dict):
        tool_type = value.get("type")
        if tool_type == "auto":
            return "auto"
        if tool_type == "none":
            return "none"
        if tool_type == "any":
            return "required"
        if tool_type == "tool" and value.get("name"):
            return {
                "type": "function",
                "function": {
                    "name": value["name"],
                },
            }
    return None


def normalize_tool_result_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text = anthropic_text_from_content(content)
        if text:
            return text
        return json.dumps(content, ensure_ascii=False)
    if content is None:
        return ""
    return json.dumps(content, ensure_ascii=False)


def anthropic_messages_to_openai(
    system_value: Any, messages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    system_prompt = flatten_system_prompt(system_value)
    if system_prompt:
        out.append({"role": "system", "content": system_prompt})

    for message in messages:
        role = message.get("role")
        content = message.get("content")

        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        if not isinstance(content, list):
            out.append({"role": role, "content": anthropic_text_from_content(content)})
            continue

        if role == "assistant":
            assistant_text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []

            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type == "text":
                    text = str(block.get("text", ""))
                    if text:
                        assistant_text_parts.append(text)
                elif block_type == "tool_use":
                    tool_calls.append(
                        {
                            "id": block.get("id") or gen_tool_id(),
                            "type": "function",
                            "function": {
                                "name": block.get("name", "unknown_tool"),
                                "arguments": json.dumps(
                                    block.get("input", {}),
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                ),
                            },
                        }
                    )

            content_text = "\n".join(part for part in assistant_text_parts if part)
            assistant_message: dict[str, Any] = {"role": "assistant"}
            assistant_message["content"] = content_text or None
            if tool_calls:
                assistant_message["tool_calls"] = tool_calls
            out.append(assistant_message)
            continue

        pending_text_parts: list[str] = []

        for block in content:
            if not isinstance(block, dict):
                if block:
                    pending_text_parts.append(str(block))
                continue

            block_type = block.get("type")
            if block_type == "text":
                text = str(block.get("text", ""))
                if text:
                    pending_text_parts.append(text)
            elif block_type == "tool_result":
                if pending_text_parts:
                    out.append(
                        {
                            "role": role,
                            "content": "\n".join(pending_text_parts),
                        }
                    )
                    pending_text_parts.clear()

                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id") or gen_tool_id(),
                        "content": normalize_tool_result_content(block.get("content")),
                    }
                )

        if pending_text_parts:
            out.append(
                {
                    "role": role,
                    "content": "\n".join(pending_text_parts),
                }
            )

    return out


def build_openai_request(config: Config, body: dict[str, Any]) -> dict[str, Any]:
    if "messages" not in body or not isinstance(body["messages"], list):
        raise ValueError("Anthropic request must include a messages array")

    openai_body: dict[str, Any] = {
        "model": config.openai_model or body.get("model"),
        "messages": anthropic_messages_to_openai(
            body.get("system"),
            body["messages"],
        ),
        "reasoning_effort": config.reasoning_effort,
    }

    if not openai_body["model"]:
        raise ValueError("No model supplied. Set OPENAI_MODEL or pass model in the request.")

    max_tokens = body.get("max_tokens")
    if isinstance(max_tokens, int):
        openai_body["max_completion_tokens"] = max_tokens

    tools = build_openai_tools(body.get("tools"))
    if tools:
        openai_body["tools"] = tools

    tool_choice = map_tool_choice(body.get("tool_choice"))
    if tool_choice is not None:
        openai_body["tool_choice"] = tool_choice

    if bool(body.get("stream")):
        openai_body["stream"] = True
        openai_body["stream_options"] = {"include_usage": True}

    output_config = body.get("output_config")
    if isinstance(output_config, dict):
        output_format = output_config.get("format")
        if (
            isinstance(output_format, dict)
            and output_format.get("type") == "json_schema"
            and isinstance(output_format.get("schema"), dict)
        ):
            openai_body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "cc2open_output",
                    "schema": output_format["schema"],
                },
            }

    # GPT-5.4 with reasoning_effort may reject temperature/top_p/logprobs.
    # We intentionally do not forward those fields here.
    return openai_body


def parse_tool_arguments(raw_arguments: Any) -> dict[str, Any]:
    if raw_arguments in (None, ""):
        return {}
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if isinstance(raw_arguments, str):
        try:
            parsed = json.loads(raw_arguments)
            if isinstance(parsed, dict):
                return parsed
            return {"_value": parsed}
        except json.JSONDecodeError:
            return {"_raw": raw_arguments}
    return {"_raw": raw_arguments}


def openai_content_to_anthropic_blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []

    content = message.get("content")
    if isinstance(content, str) and content:
        blocks.append({"type": "text", "text": content})
    elif isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text" and item.get("text"):
                    text_parts.append(str(item["text"]))
                elif item.get("type") == "output_text" and item.get("text"):
                    text_parts.append(str(item["text"]))
        if text_parts:
            blocks.append({"type": "text", "text": "\n".join(text_parts)})

    for tool_call in message.get("tool_calls", []) or []:
        function = tool_call.get("function", {})
        blocks.append(
            {
                "type": "tool_use",
                "id": tool_call.get("id") or gen_tool_id(),
                "name": function.get("name", "unknown_tool"),
                "input": parse_tool_arguments(function.get("arguments")),
            }
        )

    if not blocks:
        blocks.append({"type": "text", "text": ""})

    return blocks


def map_finish_reason(reason: str | None) -> str | None:
    mapping = {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "content_filter": "stop_sequence",
    }
    return mapping.get(reason, "end_turn" if reason else None)


def convert_openai_response_to_anthropic(
    upstream_data: dict[str, Any], request_model: str | None
) -> dict[str, Any]:
    choices = upstream_data.get("choices") or []
    if not choices:
        raise ValueError("Upstream response did not contain any choices")

    choice = choices[0]
    message = choice.get("message") or {}
    usage = upstream_data.get("usage") or {}

    return {
        "id": upstream_data.get("id") or gen_message_id(),
        "type": "message",
        "role": "assistant",
        "content": openai_content_to_anthropic_blocks(message),
        "model": upstream_data.get("model") or request_model,
        "stop_reason": map_finish_reason(choice.get("finish_reason")),
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
        },
    }


def estimate_tokens(payload: dict[str, Any]) -> int:
    text = anthropic_text_from_content(payload.get("system", ""))
    for message in payload.get("messages", []) or []:
        text += "\n" + anthropic_text_from_content(message.get("content"))
    if not text:
        return 0
    return max(1, len(text) // 4)


def sse_encode(event: str, data: dict[str, Any] | str) -> bytes:
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


def extract_sse_data_lines(raw_line: bytes) -> str | None:
    if not raw_line:
        return None
    line = raw_line.decode("utf-8", errors="replace").strip()
    if not line or not line.startswith("data:"):
        return None
    return line[len("data:") :].strip()


def maybe_set_stream_timeout(response: Any, timeout_seconds: int) -> None:
    candidates = [
        getattr(response, "fp", None),
        getattr(getattr(response, "fp", None), "raw", None),
        getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None),
    ]
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            candidate.settimeout(timeout_seconds)
            return
        except Exception:
            continue


def debug_log(config: Config, message: str) -> None:
    if config.debug:
        print(f"[cc2open-debug] {message}", flush=True)


class ClaudeToOpenAIHandler(BaseHTTPRequestHandler):
    server_version = SERVER_NAME
    protocol_version = "HTTP/1.1"

    @property
    def config(self) -> Config:
        return self.server.config  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write(
            "%s - - [%s] %s\n"
            % (self.address_string(), self.log_date_time_string(), fmt % args)
        )

    def setup(self) -> None:
        super().setup()
        try:
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass

    def route_path(self) -> str:
        return urlparse(self.path).path

    def do_GET(self) -> None:
        path = self.route_path()

        if path in {"", "/"}:
            payload = {
                "ok": True,
                "gateway": SERVER_NAME,
                "message": "Use /healthz or /v1/messages",
            }
            self.send_json_response(200, payload)
            return

        if path == "/healthz":
            payload = {
                "ok": True,
                "gateway": SERVER_NAME,
                "upstream_url": self.config.upstream_url,
                "openai_model_override": self.config.openai_model,
                "reasoning_effort": self.config.reasoning_effort,
            }
            self.send_json_response(200, payload)
            return

        if path == "/v1/models":
            model_name = self.config.openai_model or "upstream-request-model"
            payload = {
                "data": [
                    {
                        "id": model_name,
                        "type": "model",
                        "display_name": model_name,
                        "created_at": now_ts(),
                    }
                ],
                "has_more": False,
                "first_id": model_name,
                "last_id": model_name,
            }
            self.send_json_response(200, payload)
            return

        self.send_anthropic_error_response(404, f"Unknown path: {self.path}")

    def do_HEAD(self) -> None:
        path = self.route_path()
        if path in {"", "/", "/healthz", "/v1/models"}:
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:
        try:
            path = self.route_path()

            if path == "/v1/messages":
                self.handle_messages()
                return

            if path == "/v1/messages/count_tokens":
                self.handle_count_tokens()
                return

            self.send_anthropic_error_response(404, f"Unknown path: {self.path}")
        except Exception as exc:  # pragma: no cover - final safety net
            traceback.print_exc()
            self.send_anthropic_error_response(500, f"Internal server error: {exc}")

    def handle_count_tokens(self) -> None:
        body = self.read_json_body()
        estimated = estimate_tokens(body)
        self.send_json_response(
            200,
            {
                "input_tokens": estimated,
            },
        )

    def handle_messages(self) -> None:
        body = self.read_json_body()
        openai_body = build_openai_request(self.config, body)

        if openai_body.get("stream"):
            self.handle_streaming_request(body, openai_body)
            return

        upstream_status, upstream_headers, upstream_bytes = self.call_openai(openai_body)
        if upstream_status >= 400:
            self.proxy_upstream_error(upstream_status, upstream_bytes)
            return

        _ = upstream_headers
        upstream_json = safe_json_loads(upstream_bytes)
        anthropic_json = convert_openai_response_to_anthropic(
            upstream_json,
            body.get("model"),
        )
        self.send_json_response(200, anthropic_json)

    def handle_streaming_request(
        self, original_body: dict[str, Any], openai_body: dict[str, Any]
    ) -> None:
        request = urllib.request.Request(
            self.config.upstream_url,
            data=json_dumps(openai_body),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.config.openai_api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
        )

        try:
            with urllib.request.urlopen(
                request, timeout=self.config.timeout_seconds
            ) as response:
                maybe_set_stream_timeout(response, self.config.stream_idle_timeout)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()

                write_lock = threading.Lock()
                stop_pings = threading.Event()

                def send_sse(event: str, data: dict[str, Any], flush: bool = True) -> None:
                    payload = sse_encode(event, data)
                    with write_lock:
                        self.wfile.write(payload)
                        if flush:
                            self.wfile.flush()

                def ping_loop() -> None:
                    interval = max(1, self.config.stream_ping_interval)
                    while not stop_pings.wait(interval):
                        try:
                            send_sse("ping", {"type": "ping"})
                        except Exception:
                            stop_pings.set()
                            return

                ping_thread = threading.Thread(
                    target=ping_loop,
                    name="cc2open-sse-ping",
                    daemon=True,
                )
                ping_thread.start()

                try:
                    message_id = gen_message_id()
                    model_name = self.config.openai_model or original_body.get("model")
                    input_tokens = 0
                    output_tokens = 0
                    finish_reason: str | None = None
                    should_finalize = False
                    saw_any_output = False
                    timed_out_waiting_for_upstream = False

                    text_block_started = False
                    text_block_index: int | None = None
                    next_content_index = 0
                    tool_blocks: dict[int, dict[str, Any]] = {}

                    send_sse(
                        "message_start",
                        {
                            "type": "message_start",
                            "message": {
                                "id": message_id,
                                "type": "message",
                                "role": "assistant",
                                "content": [],
                                "model": model_name,
                                "stop_reason": None,
                                "stop_sequence": None,
                                "usage": {
                                    "input_tokens": 0,
                                    "output_tokens": 0,
                                },
                            },
                        },
                    )

                    try:
                        for raw_line in response:
                            debug_log(
                                self.config,
                                f"upstream raw line: {raw_line[:200]!r}",
                            )
                            sse_data = extract_sse_data_lines(raw_line)
                            if sse_data is None:
                                continue
                            if sse_data == "[DONE]":
                                debug_log(self.config, "received upstream [DONE]")
                                should_finalize = True
                                break

                            chunk = json.loads(sse_data)
                            if not isinstance(chunk, dict):
                                continue

                            if chunk.get("id"):
                                message_id = chunk["id"]
                            if chunk.get("model"):
                                model_name = chunk["model"]
                            if chunk.get("usage"):
                                usage = chunk["usage"]
                                input_tokens = int(usage.get("prompt_tokens") or input_tokens)
                                output_tokens = int(
                                    usage.get("completion_tokens") or output_tokens
                                )

                            for choice in chunk.get("choices", []) or []:
                                delta = choice.get("delta") or {}
                                if choice.get("finish_reason"):
                                    finish_reason = choice.get("finish_reason")
                                    should_finalize = True
                                    debug_log(
                                        self.config,
                                        f"finish_reason detected: {finish_reason}",
                                    )

                                text_delta = delta.get("content")
                                if isinstance(text_delta, str) and text_delta:
                                    saw_any_output = True
                                    if not text_block_started:
                                        text_block_index = next_content_index
                                        send_sse(
                                            "content_block_start",
                                            {
                                                "type": "content_block_start",
                                                "index": text_block_index,
                                                "content_block": {
                                                    "type": "text",
                                                    "text": "",
                                                },
                                            },
                                        )
                                        text_block_started = True
                                        next_content_index += 1

                                    send_sse(
                                        "content_block_delta",
                                        {
                                            "type": "content_block_delta",
                                            "index": text_block_index,
                                            "delta": {
                                                "type": "text_delta",
                                                "text": text_delta,
                                            },
                                        },
                                    )

                                for tool_delta in delta.get("tool_calls", []) or []:
                                    if not isinstance(tool_delta, dict):
                                        continue

                                    saw_any_output = True
                                    tool_index = int(tool_delta.get("index") or 0)
                                    block_state = tool_blocks.get(tool_index)
                                    if block_state is None:
                                        block_state = {
                                            "anthropic_index": next_content_index,
                                            "tool_id": tool_delta.get("id") or gen_tool_id(),
                                            "name": "",
                                        }
                                        tool_blocks[tool_index] = block_state
                                        next_content_index += 1

                                    function = tool_delta.get("function") or {}
                                    if function.get("name"):
                                        block_state["name"] = function["name"]

                                    if not block_state.get("started"):
                                        send_sse(
                                            "content_block_start",
                                            {
                                                "type": "content_block_start",
                                                "index": block_state["anthropic_index"],
                                                "content_block": {
                                                    "type": "tool_use",
                                                    "id": block_state["tool_id"],
                                                    "name": block_state["name"]
                                                    or "unknown_tool",
                                                    "input": {},
                                                },
                                            },
                                        )
                                        block_state["started"] = True

                                    arguments_delta = function.get("arguments")
                                    if isinstance(arguments_delta, str) and arguments_delta:
                                        send_sse(
                                            "content_block_delta",
                                            {
                                                "type": "content_block_delta",
                                                "index": block_state["anthropic_index"],
                                                "delta": {
                                                    "type": "input_json_delta",
                                                    "partial_json": arguments_delta,
                                                },
                                            },
                                        )

                            if should_finalize:
                                debug_log(self.config, "finalizing stream because finish_reason arrived")
                                break
                    except socket.timeout:
                        timed_out_waiting_for_upstream = True
                        should_finalize = True
                        if not finish_reason:
                            finish_reason = "stop"
                        debug_log(
                            self.config,
                            f"upstream stream idle for {self.config.stream_idle_timeout}s; forcing finalize",
                        )

                    if text_block_started:
                        send_sse(
                            "content_block_stop",
                            {
                                "type": "content_block_stop",
                                "index": text_block_index,
                            },
                        )

                    for block_state in tool_blocks.values():
                        if block_state.get("started"):
                            send_sse(
                                "content_block_stop",
                                {
                                    "type": "content_block_stop",
                                    "index": block_state["anthropic_index"],
                                },
                            )

                    send_sse(
                        "message_delta",
                        {
                            "type": "message_delta",
                            "delta": {
                                "stop_reason": map_finish_reason(finish_reason),
                                "stop_sequence": None,
                            },
                            "usage": {
                                "output_tokens": output_tokens,
                            },
                        },
                    )
                    send_sse(
                        "message_stop",
                        {
                            "type": "message_stop",
                        },
                    )
                    debug_log(self.config, "sent message_stop to client")
                    if timed_out_waiting_for_upstream:
                        debug_log(self.config, "stream ended via idle-timeout fallback")
                    self.close_connection = True
                    _ = input_tokens
                finally:
                    stop_pings.set()
                    ping_thread.join(timeout=1)
        except urllib.error.HTTPError as exc:
            body_bytes = exc.read()
            self.proxy_upstream_error(exc.code, body_bytes)
        except urllib.error.URLError as exc:
            self.send_anthropic_error_response(
                502,
                f"Failed to reach upstream OpenAI endpoint: {exc}",
            )

    def proxy_upstream_error(self, status_code: int, body_bytes: bytes) -> None:
        try:
            upstream_json = safe_json_loads(body_bytes)
        except Exception:
            upstream_json = {"message": body_bytes.decode("utf-8", errors="replace")}

        message = (
            upstream_json.get("error", {}).get("message")
            if isinstance(upstream_json, dict)
            else None
        ) or (
            upstream_json.get("message")
            if isinstance(upstream_json, dict)
            else None
        ) or "Upstream request failed"

        self.send_anthropic_error_response(status_code, message)

    def call_openai(self, openai_body: dict[str, Any]) -> tuple[int, dict[str, str], bytes]:
        request = urllib.request.Request(
            self.config.upstream_url,
            data=json_dumps(openai_body),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.config.openai_api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(
                request, timeout=self.config.timeout_seconds
            ) as response:
                return response.getcode(), dict(response.info()), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Failed to reach upstream OpenAI endpoint: {exc}") from exc

    def read_json_body(self) -> dict[str, Any]:
        content_length = self.headers.get("Content-Length")
        if not content_length:
            raise ValueError("Missing Content-Length header")

        raw = self.rfile.read(int(content_length))
        payload = safe_json_loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object")
        return payload

    def send_json_response(self, status_code: int, payload: dict[str, Any]) -> None:
        body = json_dumps(payload)
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_anthropic_error_response(self, status_code: int, message: str) -> None:
        self.send_json_response(status_code, anthropic_error(message))


def main() -> int:
    config = parse_args()
    server = ThreadingHTTPServer((config.host, config.port), ClaudeToOpenAIHandler)
    server.config = config  # type: ignore[attr-defined]

    print(
        f"{SERVER_NAME} listening on http://{config.host}:{config.port} -> {config.upstream_url}"
    )
    print(
        f"OpenAI model override: {config.openai_model or '<use incoming model>'}; "
        f"forced reasoning_effort={config.reasoning_effort}"
    )
    print("Press Ctrl+C to stop.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        server.server_close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

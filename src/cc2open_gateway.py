#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import errno
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
DEFAULT_POST_FINISH_GRACE_TIMEOUT = 5
SERVER_NAME = "cc2open-gateway"
CLIENT_DISCONNECT_WINERRORS = {10053, 10054}


class ClientDisconnectedError(Exception):
    pass


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
    post_finish_grace_timeout: int
    debug: bool
    debug_pet: str

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
        "--post-finish-grace-timeout",
        type=int,
        default=int(
            env_or_default(
                "CC2OPEN_POST_FINISH_GRACE_TIMEOUT",
                str(DEFAULT_POST_FINISH_GRACE_TIMEOUT),
            )
        ),
        help="After finish_reason arrives, wait up to N more seconds for trailing chunks/[DONE]. Default: 5",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=(env_or_default("CC2OPEN_DEBUG", "0") == "1"),
        help="Enable verbose debug logs for streaming and upstream requests.",
    )
    parser.add_argument(
        "--debug-pet",
        choices=("auto", "0", "1"),
        default=env_or_default("CC2OPEN_DEBUG_PET", "auto"),
        help="Enable terminal pet UI: auto, 0, or 1. Default: auto",
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
        post_finish_grace_timeout=args.post_finish_grace_timeout,
        debug=args.debug,
        debug_pet=args.debug_pet,
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
                if item_type == "thinking":
                    continue
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


def _extract_reasoning_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("thinking", "reasoning", "reasoning_content", "text", "content"):
            text = _extract_reasoning_text(value.get(key))
            if text:
                return text
        return ""
    if isinstance(value, list):
        parts = [_extract_reasoning_text(item) for item in value]
        return "\n".join(part for part in parts if part)
    return ""


def extract_openai_reasoning_block(message: dict[str, Any]) -> dict[str, str] | None:
    thinking_parts: list[str] = []
    signature = ""

    for key in ("reasoning_content", "reasoning"):
        text = _extract_reasoning_text(message.get(key))
        if text:
            thinking_parts.append(text)
        value = message.get(key)
        if isinstance(value, dict) and value.get("signature") and not signature:
            signature = str(value["signature"])

    content = message.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "")
            if item_type in {"reasoning", "reasoning_content", "thinking"}:
                text = _extract_reasoning_text(item)
                if text:
                    thinking_parts.append(text)
                if item.get("signature") and not signature:
                    signature = str(item["signature"])

    thinking_text = "\n".join(part for part in thinking_parts if part).strip()
    if not thinking_text and not signature:
        return None

    block = {"thinking": thinking_text}
    if signature:
        block["signature"] = signature
    return block


def iter_openai_reasoning_deltas(delta: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []

    for key in ("reasoning_content", "reasoning"):
        value = delta.get(key)
        text = _extract_reasoning_text(value)
        if text:
            out.append({"thinking": text})
        if isinstance(value, dict) and value.get("signature"):
            out.append({"signature": str(value["signature"])})

    content = delta.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "")
            if item_type not in {"reasoning", "reasoning_content", "thinking"}:
                continue
            text = _extract_reasoning_text(item)
            if text:
                out.append({"thinking": text})
            if item.get("signature"):
                out.append({"signature": str(item["signature"])})

    if delta.get("reasoning_signature"):
        out.append({"signature": str(delta["reasoning_signature"])})

    return out


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
                if block_type == "thinking":
                    continue
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

    reasoning_block = extract_openai_reasoning_block(message)
    if reasoning_block is not None:
        thinking_block: dict[str, Any] = {
            "type": "thinking",
            "thinking": reasoning_block.get("thinking", ""),
        }
        if reasoning_block.get("signature"):
            thinking_block["signature"] = reasoning_block["signature"]
        blocks.append(thinking_block)

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


DEBUG_OUTPUT_LOCK = threading.Lock()


def debug_log(config: Config, message: str) -> None:
    if config.debug:
        with DEBUG_OUTPUT_LOCK:
            print(f"[cc2open-debug] {message}", file=sys.stderr, flush=True)


def is_client_disconnect_error(exc: BaseException) -> bool:
    if isinstance(exc, ClientDisconnectedError):
        return True
    if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
        return True
    if isinstance(exc, OSError):
        if getattr(exc, "winerror", None) in CLIENT_DISCONNECT_WINERRORS:
            return True
        if getattr(exc, "errno", None) in {errno.EPIPE, errno.ECONNRESET, errno.ECONNABORTED}:
            return True
    return False


def stream_debug_log(config: Config, stream_pet: StreamingDebugPet | None, message: str) -> None:
    if stream_pet is not None:
        stream_pet.pause_for_log()
    debug_log(config, message)
    if stream_pet is not None:
        stream_pet.resume_after_log()


@dataclass
class StreamingDebugStatus:
    state: str = "daydream"
    line_count: int = 0
    byte_count: int = 0
    bubble: str = "等你发消息"
    last_event: str = ""
    last_activity_at: float = 0.0


class StreamingDebugPet:
    BOX_WIDTH = 38
    BOX_HEIGHT = 8
    BOX_TOP = 1
    ACTIVE_SECONDS = 0.7
    SLEEP_AFTER_SECONDS = 6.0
    RENDER_INTERVAL_SECONDS = 0.25
    SUMMARY_INTERVAL_SECONDS = 5.0
    BOX_FRAMES: dict[str, tuple[tuple[str, ...], ...]] = {
        "daydream": (
            (
                "   .-o-OO-o-.   ",
                "  (__________)  ",
                "     |·  ·|     ",
                "     |____|     ",
            ),
            (
                "   .-o-OO-o-.   ",
                "  (__________)  ",
                "     |·  ·|     ",
                "     |__﹏|     ",
            ),
        ),
        "sleeping": (
            (
                "      ,>        ",
                "   .-o-OO-o-.   ",
                "  (__________)  ",
                "     |-  -|  zZ ",
            ),
            (
                "      ,>        ",
                "   .-o-OO-o-.   ",
                "  (__________)  ",
                "     |-  -|  Zz ",
            ),
        ),
        "excited": (
            (
                "    \\ .-. /    ",
                "   .-o-OO-o-.   ",
                "  (__________)  ",
                "    \\|^  ^|/    ",
            ),
            (
                "    / .-. \\    ",
                "   .-o-OO-o-.   ",
                "  (__________)  ",
                "    /|^  ^|\\    ",
            ),
        ),
        "dancing": (
            (
                "    \\ .-. /    ",
                "   .-o-OO-o-.   ",
                "  (__________)  ",
                "   \\_|^  ^|_/   ",
            ),
            (
                "    / .-. \\    ",
                "   .-o-OO-o-.   ",
                "  (__________)  ",
                "   /_|^  ^|_\\   ",
            ),
            (
                "     .-.-.      ",
                "   .-o-OO-o-.   ",
                "  (__________)  ",
                "    \\|^  ^|/    ",
            ),
        ),
    }

    def __init__(self, config: Config) -> None:
        self.config = config
        self.status = StreamingDebugStatus(last_activity_at=time.monotonic())
        self.stream = sys.stderr
        self.enabled = bool(config.debug) and config.debug_pet != "0"
        self.mode = config.debug_pet
        self.interactive = self.enabled and self.stream.isatty()
        self.multiline = self.interactive and self._supports_fixed_box()
        self._frame_index = 0
        self._last_render = 0.0
        self._last_summary = 0.0
        self._rendered = False
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._finished = False

    def start(self) -> None:
        if not self.enabled:
            return
        if self.multiline:
            self._render(time.monotonic(), force=True)
            self._thread = threading.Thread(
                target=self._render_loop,
                name="cc2open-debug-pet",
                daemon=True,
            )
            self._thread.start()
        elif self.interactive:
            self._render_single_line(time.monotonic(), force=True)
            self._thread = threading.Thread(
                target=self._render_loop,
                name="cc2open-debug-pet",
                daemon=True,
            )
            self._thread.start()
        else:
            debug_log(self.config, "pet ui disabled in non-interactive terminal")

    def record_waiting(self) -> None:
        self._set_state("daydream", "等你发消息")

    def record_activity(self, raw_line: bytes) -> None:
        now = time.monotonic()
        with self._lock:
            self.status.line_count += 1
            self.status.byte_count += len(raw_line)
            self.status.last_activity_at = now
            if self.status.line_count == 1:
                self.status.state = "excited"
                self.status.bubble = "我在收流啦"
            else:
                self.status.state = "dancing"
                self.status.bubble = "继续收流中"
        self._trigger_render(now)

    def record_done(self) -> None:
        self._set_state("daydream", "这次收完啦")

    def record_finish_reason(self, finish_reason: str) -> None:
        self._set_state("excited", f"收尾中：{finish_reason}")

    def record_timeout(self) -> None:
        self._set_state("sleeping", "上游有点慢，我守着")

    def record_message_stop(self) -> None:
        self._set_state("daydream", "等你下一条消息")

    def finish(self) -> None:
        if not self.enabled or self._finished:
            return
        self._finished = True
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        if self.multiline:
            self.record_message_stop()
            self._render(time.monotonic(), force=True)
        elif self.interactive:
            self._clear_rendered_block()
        elif self.status.line_count:
            debug_log(
                self.config,
                f"upstream stream pet summary: lines={self.status.line_count} bytes={self.status.byte_count}",
            )

    def pause_for_log(self) -> None:
        if self._finished:
            return
        if not self.multiline:
            self._clear_rendered_block()

    def resume_after_log(self) -> None:
        if self._finished or not self.enabled:
            return
        if not self.multiline:
            self._trigger_render(time.monotonic(), force=True)

    def _set_state(self, state: str, bubble: str) -> None:
        with self._lock:
            self.status.state = state
            self.status.bubble = bubble
            self.status.last_activity_at = time.monotonic()
        self._trigger_render(time.monotonic(), force=True)

    def _supports_fixed_box(self) -> bool:
        if not self.interactive:
            return False
        size = self._get_terminal_size()
        if size is None or size.columns < self.BOX_WIDTH + 2 or size.lines < self.BOX_HEIGHT:
            return False
        term = (os.environ.get("TERM") or "").lower()
        term_program = (os.environ.get("TERM_PROGRAM") or "").lower()
        if self.mode == "1":
            return True
        if os.name != "nt":
            return term != "dumb"
        return bool(
            os.environ.get("WT_SESSION")
            or os.environ.get("ANSICON")
            or os.environ.get("ConEmuANSI") == "ON"
            or term_program == "vscode"
            or any(token in term for token in ("xterm", "vt100", "cygwin", "msys"))
        )

    def _get_terminal_size(self) -> os.terminal_size | None:
        try:
            return os.get_terminal_size(self.stream.fileno())
        except OSError:
            return None

    def _render_loop(self) -> None:
        while not self._stop_event.wait(self.RENDER_INTERVAL_SECONDS):
            self._trigger_render(time.monotonic(), force=True)

    def _trigger_render(self, now: float, force: bool = False) -> None:
        if not self.enabled:
            return
        if self.multiline:
            self._render(now, force=force)
        elif self.interactive:
            self._render_single_line(now, force=force)
        elif now - self._last_summary >= self.SUMMARY_INTERVAL_SECONDS:
            self._last_summary = now
            debug_log(
                self.config,
                f"pet state={self._effective_state(now)} lines={self.status.line_count} bytes={self.status.byte_count}",
            )

    def _effective_state(self, now: float) -> str:
        with self._lock:
            state = self.status.state
            last_activity_at = self.status.last_activity_at
        if state in {"excited", "dancing"} and now - last_activity_at > self.ACTIVE_SECONDS:
            return "sleeping" if now - last_activity_at > self.SLEEP_AFTER_SECONDS else "daydream"
        if state == "daydream" and now - last_activity_at > self.SLEEP_AFTER_SECONDS:
            return "sleeping"
        return state

    def _top_right_origin(self) -> tuple[int, int] | None:
        size = self._get_terminal_size()
        if size is None or size.columns < self.BOX_WIDTH or size.lines < self.BOX_HEIGHT:
            return None
        col = max(1, size.columns - self.BOX_WIDTH + 1)
        return self.BOX_TOP, col

    def _render(self, now: float, force: bool = False) -> None:
        if not self.multiline:
            return
        if not force and now - self._last_render < self.RENDER_INTERVAL_SECONDS:
            return
        origin = self._top_right_origin()
        if origin is None:
            self.multiline = False
            self._rendered = False
            return
        self._last_render = now
        state = self._effective_state(now)
        with self._lock:
            bubble = self.status.bubble
            lines = self.status.line_count
            bytes_count = self.status.byte_count
        frame = self.BOX_FRAMES[state][self._frame_index % len(self.BOX_FRAMES[state])]
        self._frame_index += 1
        bubble_line = f"气泡：{bubble}"[: self.BOX_WIDTH - 4]
        info_line = f"状态：{state}  lines={lines}  bytes={bytes_count}"[: self.BOX_WIDTH - 4]
        box_lines = [
            "┌" + ("─" * (self.BOX_WIDTH - 2)) + "┐",
            "│ " + bubble_line.ljust(self.BOX_WIDTH - 4) + " │",
            *[
                "│ " + part.ljust(self.BOX_WIDTH - 4) + " │"
                for part in frame
            ],
            "│ " + info_line.ljust(self.BOX_WIDTH - 4) + " │",
            "└" + ("─" * (self.BOX_WIDTH - 2)) + "┘",
        ]
        row, col = origin
        with DEBUG_OUTPUT_LOCK:
            self.stream.write("\x1b[s")
            for index, line in enumerate(box_lines):
                self.stream.write(f"\x1b[{row + index};{col}H{line[: self.BOX_WIDTH].ljust(self.BOX_WIDTH)}")
            self.stream.write("\x1b[u")
            self.stream.flush()
            self._rendered = True

    def _render_single_line(self, now: float, force: bool = False) -> None:
        if not self.interactive:
            return
        if not force and now - self._last_render < self.RENDER_INTERVAL_SECONDS:
            return
        self._last_render = now
        state = self._effective_state(now)
        with self._lock:
            bubble = self.status.bubble
            lines = self.status.line_count
            bytes_count = self.status.byte_count
        line = f"[pet] {state} | {bubble} | lines={lines} bytes={bytes_count}"
        with DEBUG_OUTPUT_LOCK:
            if self._rendered:
                self.stream.write("\r" + (" " * len(line)) + "\r")
            self.stream.write("\r" + line)
            self.stream.flush()
            self._rendered = True

    def _clear_rendered_block(self) -> None:
        if not self.interactive or not self._rendered:
            return
        with DEBUG_OUTPUT_LOCK:
            if self.multiline:
                origin = self._top_right_origin()
                if origin is not None:
                    row, col = origin
                    blank = " " * self.BOX_WIDTH
                    self.stream.write("\x1b[s")
                    for index in range(self.BOX_HEIGHT):
                        self.stream.write(f"\x1b[{row + index};{col}H{blank}")
                    self.stream.write("\x1b[u")
            else:
                self.stream.write("\r" + (" " * 120) + "\r")
            self.stream.flush()
            self._rendered = False


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

    def handle(self) -> None:
        try:
            super().handle()
        except Exception as exc:
            if is_client_disconnect_error(exc):
                debug_log(self.config, f"client disconnected during request handling: {exc}")
                self.close_connection = True
                return
            raise

    def finish(self) -> None:
        try:
            super().finish()
        except Exception as exc:
            if is_client_disconnect_error(exc):
                debug_log(self.config, f"client disconnected during response flush: {exc}")
                return
            raise

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
            if is_client_disconnect_error(exc):
                debug_log(self.config, f"client disconnected during POST handling: {exc}")
                self.close_connection = True
                return
            traceback.print_exc()
            try:
                self.send_anthropic_error_response(500, f"Internal server error: {exc}")
            except Exception as send_exc:
                if is_client_disconnect_error(send_exc):
                    debug_log(
                        self.config,
                        f"client disconnected before 500 error could be sent: {send_exc}",
                    )
                    self.close_connection = True
                    return
                raise

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
                    try:
                        with write_lock:
                            self.wfile.write(payload)
                            if flush:
                                self.wfile.flush()
                    except Exception as exc:
                        if is_client_disconnect_error(exc):
                            raise ClientDisconnectedError(str(exc)) from exc
                        raise

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
                stream_pet = StreamingDebugPet(self.config)
                stream_pet.start()
                stream_pet.record_waiting()

                try:
                    message_id = gen_message_id()
                    model_name = self.config.openai_model or original_body.get("model")
                    input_tokens = 0
                    output_tokens = 0
                    finish_reason: str | None = None
                    should_finalize = False
                    finish_reason_seen = False
                    saw_any_output = False
                    timed_out_waiting_for_upstream = False

                    text_block_started = False
                    text_block_index: int | None = None
                    thinking_block_started = False
                    thinking_block_index: int | None = None
                    thinking_signature = ""
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
                            stream_pet.record_activity(raw_line)
                            sse_data = extract_sse_data_lines(raw_line)
                            if sse_data is None:
                                continue
                            if sse_data == "[DONE]":
                                stream_pet.record_done()
                                stream_debug_log(
                                    self.config,
                                    stream_pet,
                                    "received upstream [DONE]",
                                )
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
                                    if not finish_reason_seen:
                                        finish_reason_seen = True
                                        maybe_set_stream_timeout(
                                            response,
                                            max(1, self.config.post_finish_grace_timeout),
                                        )
                                    stream_pet.record_finish_reason(finish_reason)
                                    stream_debug_log(
                                        self.config,
                                        stream_pet,
                                        f"finish_reason detected: {finish_reason}",
                                    )

                                for reasoning_delta in iter_openai_reasoning_deltas(delta):
                                    thinking_text = reasoning_delta.get("thinking", "")
                                    signature = reasoning_delta.get("signature", "")
                                    if not thinking_text and not signature:
                                        continue

                                    saw_any_output = saw_any_output or bool(thinking_text)
                                    if not thinking_block_started:
                                        thinking_block_index = next_content_index
                                        send_sse(
                                            "content_block_start",
                                            {
                                                "type": "content_block_start",
                                                "index": thinking_block_index,
                                                "content_block": {
                                                    "type": "thinking",
                                                    "thinking": "",
                                                },
                                            },
                                        )
                                        thinking_block_started = True
                                        next_content_index += 1

                                    if thinking_text:
                                        send_sse(
                                            "content_block_delta",
                                            {
                                                "type": "content_block_delta",
                                                "index": thinking_block_index,
                                                "delta": {
                                                    "type": "thinking_delta",
                                                    "thinking": thinking_text,
                                                },
                                            },
                                        )

                                    if signature:
                                        thinking_signature = signature

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

                    except socket.timeout:
                        timed_out_waiting_for_upstream = True
                        should_finalize = True
                        if not finish_reason:
                            finish_reason = "stop"
                        stream_pet.record_timeout()
                        stream_debug_log(
                            self.config,
                            stream_pet,
                            (
                                f"upstream post-finish grace timeout reached "
                                f"({self.config.post_finish_grace_timeout}s); forcing finalize"
                                if finish_reason_seen
                                else f"upstream stream idle for {self.config.stream_idle_timeout}s; forcing finalize"
                            ),
                        )

                    if thinking_block_started:
                        if thinking_signature:
                            send_sse(
                                "content_block_delta",
                                {
                                    "type": "content_block_delta",
                                    "index": thinking_block_index,
                                    "delta": {
                                        "type": "signature_delta",
                                        "signature": thinking_signature,
                                    },
                                },
                            )
                        send_sse(
                            "content_block_stop",
                            {
                                "type": "content_block_stop",
                                "index": thinking_block_index,
                            },
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
                    stream_pet.record_message_stop()
                    stream_debug_log(self.config, stream_pet, "sent message_stop to client")
                    if timed_out_waiting_for_upstream:
                        stream_debug_log(
                            self.config,
                            stream_pet,
                            "stream ended via idle-timeout fallback",
                        )
                    self.close_connection = True
                    _ = input_tokens
                finally:
                    stop_pings.set()
                    stream_pet.finish()
                    ping_thread.join(timeout=1)
        except urllib.error.HTTPError as exc:
            body_bytes = exc.read()
            self.proxy_upstream_error(exc.code, body_bytes)
        except ClientDisconnectedError as exc:
            debug_log(self.config, f"stream client disconnected: {exc}")
            self.close_connection = True
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
        try:
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            if is_client_disconnect_error(exc):
                raise ClientDisconnectedError(str(exc)) from exc
            raise

    def send_anthropic_error_response(self, status_code: int, message: str) -> None:
        self.send_json_response(status_code, anthropic_error(message))


class GracefulThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> int:
    config = parse_args()
    server = GracefulThreadingHTTPServer((config.host, config.port), ClaudeToOpenAIHandler)
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

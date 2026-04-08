# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository purpose

This repo is a minimal local gateway that accepts Anthropic/Claude-style `POST /v1/messages` requests and forwards them to an OpenAI-compatible `POST /v1/chat/completions` endpoint. The main behavior is to ignore any upstream effort setting and always inject a locally configured `reasoning_effort`.

## Common commands

### Run the gateway directly

Set the required environment variables, then start the server:

```powershell
$env:OPENAI_API_KEY="<YOUR_OPENAI_API_KEY>"
$env:OPENAI_MODEL="gpt-5.4"
$env:OPENAI_REASONING_EFFORT="xhigh"
python D:\Ai\cc2open\cc2open_gateway.py
```

### Run via preset launcher scripts

These wrappers all start `start_gateway.ps1`, set the model/base URL defaults, and only vary `reasoning_effort` plus stream ping interval:

```powershell
D:\Ai\cc2open\start_gateway_fast.cmd
D:\Ai\cc2open\start_gateway_balanced.cmd
D:\Ai\cc2open\start_gateway_max.cmd
```

### Show CLI options

```powershell
python D:\Ai\cc2open\cc2open_gateway.py --help
```

### Syntax check

There is no formal build/lint/test setup in this repo. The closest lightweight verification is Python bytecode compilation:

```powershell
python -m py_compile D:\Ai\cc2open\cc2open_gateway.py
```

### Health check

```powershell
Invoke-WebRequest http://127.0.0.1:8787/healthz | Select-Object -ExpandProperty Content
```

### Manual request smoke test

```powershell
$body = @{
  model = "gpt-5.4"
  max_tokens = 512
  messages = @(
    @{
      role = "user"
      content = "请用一句话介绍你自己"
    }
  )
} | ConvertTo-Json -Depth 20

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8787/v1/messages" `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

## High-level architecture

### Main executable

- `cc2open_gateway.py` is the whole application: CLI parsing, config, Anthropic↔OpenAI payload translation, HTTP routing, upstream proxying, and SSE streaming.
- `main()` builds a `ThreadingHTTPServer` and serves a single `BaseHTTPRequestHandler` subclass.

### Configuration model

- `Config` is the central immutable runtime config object.
- Configuration can come from CLI flags or environment variables.
- `Config.upstream_url` normalizes `OPENAI_BASE_URL` + `OPENAI_CHAT_PATH`, including the special case where the base URL already ends in `/v1`.

Relevant env vars:

- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `OPENAI_REASONING_EFFORT`
- `OPENAI_BASE_URL`
- `OPENAI_CHAT_PATH`
- `CC2OPEN_HOST`
- `CC2OPEN_PORT`
- `CC2OPEN_TIMEOUT_SECONDS`
- `CC2OPEN_STREAM_PING_INTERVAL`
- `CC2OPEN_STREAM_IDLE_TIMEOUT`
- `CC2OPEN_DEBUG`

### Request translation pipeline

The core data flow is:

1. `ClaudeToOpenAIHandler.handle_messages()` reads an Anthropic-style request.
2. `build_openai_request()` converts it into an OpenAI chat-completions payload.
3. The gateway forcibly writes `reasoning_effort=config.reasoning_effort`.
4. The request is sent upstream with `urllib.request`.
5. The response is converted back into Anthropic-style message JSON or Anthropic-style SSE events.

Important translation helpers:

- `anthropic_messages_to_openai()` flattens Anthropic message blocks into OpenAI `messages`.
- `build_openai_tools()` and `map_tool_choice()` adapt Anthropic tool declarations/tool choice into OpenAI function-calling format.
- `openai_content_to_anthropic_blocks()` and `convert_openai_response_to_anthropic()` map OpenAI responses back into Anthropic content blocks.

### Streaming behavior

Streaming is not a raw pass-through. The gateway consumes upstream OpenAI SSE chunks and re-emits Anthropic-style events such as:

- `message_start`
- `content_block_start`
- `content_block_delta`
- `content_block_stop`
- `message_delta`
- `message_stop`
- `ping`

A background ping thread keeps the client stream alive. There is also an idle-timeout fallback that finalizes the Anthropic stream if upstream stalls after output starts.

### Exposed endpoints

- `GET /` returns a minimal info payload.
- `GET /healthz` returns gateway status plus resolved upstream URL/model/reasoning effort.
- `GET /v1/models` returns a synthetic single-model list based on the configured model override.
- `POST /v1/messages` is the main proxy endpoint.
- `POST /v1/messages/count_tokens` is only a local estimator based on text length; it does not call upstream tokenization.

## Non-obvious implementation details

- Incoming effort is intentionally ignored. The gateway always overwrites it with local `OPENAI_REASONING_EFFORT` / `--reasoning-effort`.
- The gateway intentionally does **not** forward some Anthropic/OpenAI-adjacent fields like `temperature`, `top_p`, and `logprobs`, because the target GPT-5.4 + `reasoning_effort` combination may reject them.
- `max_tokens` from Anthropic requests is translated to OpenAI `max_completion_tokens`.
- Tool calls/tool results are supported in both directions.
- `output_config.format.type=json_schema` is mapped into OpenAI `response_format.json_schema`.
- The repo currently has no package manager metadata, no lint config, and no automated test suite; development is centered on editing a single Python file and validating behavior through `py_compile`, `/healthz`, and manual request tests.

## Launcher scripts

- `start_gateway.ps1` is the canonical launcher. It prompts for `OPENAI_API_KEY` if needed, exports env vars, prints the chosen config, and runs `python -S cc2open_gateway.py`.
- `start_gateway_fast.ps1`, `start_gateway_balanced.ps1`, and `start_gateway_max.ps1` are thin presets over `start_gateway.ps1`.
- The `.cmd` files are only Windows shims that invoke the matching PowerShell scripts.

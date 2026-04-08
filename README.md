# cc2open

一个最小可运行的本地网关：接收 Anthropic / Claude 风格的 `POST /v1/messages` 请求，转发到 OpenAI 兼容的 `POST /v1/chat/completions`，并始终强制写入本地配置的 `reasoning_effort`。

## 30 秒快速启动

推荐直接用现成脚本。

### 1) 进入仓库目录

```powershell
cd <this-repo>
```

### 2) 设置 API Key

```powershell
$env:OPENAI_API_KEY="<YOUR_OPENAI_API_KEY>"
```

### 3) 直接启动

最强推理：

```powershell
.\scripts\windows\start_gateway_max.cmd
```

如果你想更快一些，也可以用：

```powershell
.\scripts\windows\start_gateway_fast.cmd
```

### 4) 确认服务起来了

```powershell
Invoke-WebRequest http://127.0.0.1:8787/healthz | Select-Object -ExpandProperty Content
```

### 5) 把 Claude Code 指到本地网关

把 Anthropic provider 地址改成：

```text
http://127.0.0.1:8787
```

然后继续按 Anthropic 风格调用 `/v1/messages` 即可。

## 常用启动方式

### 预设脚本

- `start_gateway_fast.cmd` → `reasoning_effort=medium`
- `start_gateway_balanced.cmd` → `reasoning_effort=high`
- `start_gateway_max.cmd` → `reasoning_effort=xhigh`

对应文件都在：

```text
scripts/windows/
```

这些脚本默认使用：

- `Base URL = https://airouter.service.itstudio.club/v1`
- `Model = gpt-5.4`
- `Port = 8787`
- `stream_idle_timeout = 300`
- `debug = 1`

如果当前会话里没有 `OPENAI_API_KEY`，`scripts/windows/start_gateway.ps1` 会提示输入。

### 手动直接启动

```powershell
$env:OPENAI_API_KEY="<YOUR_OPENAI_API_KEY>"
$env:OPENAI_MODEL="gpt-5.4"
$env:OPENAI_REASONING_EFFORT="xhigh"
python .\src\cc2open_gateway.py
```

## 支持的接口

- `GET /healthz`
- `GET /v1/models`
- `POST /v1/messages`
- `POST /v1/messages/count_tokens`

## 运行要求

- Windows
- Python 3.10+
- 一个可用的 OpenAI 兼容 API Key

先确认 Python 可用：

```powershell
python --version
```

## 目录结构

```text
.
├─ src/
│  └─ cc2open_gateway.py
├─ scripts/
│  └─ windows/
│     ├─ start_gateway.ps1
│     ├─ start_gateway.cmd
│     ├─ start_gateway_fast.ps1
│     ├─ start_gateway_fast.cmd
│     ├─ start_gateway_balanced.ps1
│     ├─ start_gateway_balanced.cmd
│     ├─ start_gateway_max.ps1
│     └─ start_gateway_max.cmd
├─ CLAUDE.md
└─ README.md
```

## 命令行参数

查看帮助：

```powershell
python .\src\cc2open_gateway.py --help
```

直接传参启动：

```powershell
python .\src\cc2open_gateway.py `
  --openai-api-key "<YOUR_OPENAI_API_KEY>" `
  --openai-model "gpt-5.4" `
  --reasoning-effort "xhigh" `
  --port 8787
```

常用参数：

- `--host`
- `--port`
- `--openai-base-url`
- `--openai-chat-path`
- `--openai-api-key`
- `--openai-model`
- `--reasoning-effort`
- `--timeout-seconds`
- `--stream-ping-interval`
- `--stream-idle-timeout`
- `--debug`

## 环境变量

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

## 验证命令

语法检查：

```powershell
python -m py_compile .\src\cc2open_gateway.py
```

健康检查：

```powershell
Invoke-WebRequest http://127.0.0.1:8787/healthz | Select-Object -ExpandProperty Content
```

手工 smoke test：

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

## 关键行为

- 无视上游传入的 effort，始终覆盖为本地配置值
- `max_tokens` 会转换为 OpenAI 的 `max_completion_tokens`
- 支持工具调用 / 工具结果双向转换
- 支持流式 SSE，但不是原样透传；会重发成 Anthropic 风格事件
- `POST /v1/messages/count_tokens` 只是本地文本长度估算，不会调用上游 tokenization
- 某些字段如 `temperature`、`top_p`、`logprobs` 不会透传，以避免和 `gpt-5.4 + reasoning_effort` 组合冲突

## Claude Code 接入方式

把 Claude Code 的 Anthropic provider 地址指向：

```text
http://127.0.0.1:8787
```

然后继续按 Anthropic 风格调用 `/v1/messages` 即可。

## 停止服务

在启动窗口按：

```text
Ctrl + C
```

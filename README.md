# cc2open

一个最小可运行的本地网关：接收 Anthropic / Claude 风格的 `POST /v1/messages` 请求，转发到 OpenAI 兼容的 `POST /v1/chat/completions`，并始终强制写入本地配置的 `reasoning_effort`。

## 30 秒快速启动

推荐直接用现成脚本。

### 1) 进入仓库目录

```powershell
cd <this-repo>
```

### 2) 首次启动时设置 API Key

```powershell
$env:OPENAI_API_KEY="<YOUR_OPENAI_API_KEY>"
```

说明：

- 如果你用启动脚本启动，第一次没有保存过配置时会提示输入 `OPENAI_API_KEY`
- 首次输入后，脚本会自动把 `API Key` 和 `Base URL` 保存到本地
- 后续再次启动时，默认使用上次保存的值，不需要每次重新输入
- 如果你当前 PowerShell 会话里显式设置了 `$env:OPENAI_API_KEY`，它会优先覆盖已保存的值

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

启动脚本现在支持本地持久化配置：

- 首次启动时，如果当前会话里没有 `OPENAI_API_KEY`，`scripts/windows/start_gateway.ps1` 会提示输入
- 输入后的 `API Key` 和当前 `Base URL` 会保存到 `scripts/windows/.cc2open_state.json`
- 后续直接运行 `start_gateway.cmd`、`start_gateway_fast.cmd`、`start_gateway_balanced.cmd` 或 `start_gateway_max.cmd` 时，会默认读取上次保存的值
- 启动参数优先级为：命令行参数 > 当前环境变量 > 已保存的本地配置 > 脚本默认值

### Base URL 持久化与切换

默认 `Base URL` 仍然是：

```text
https://airouter.service.itstudio.club/v1
```

如果你有新的供应商，可以在首次启动时直接指定新的 `Base URL`：

```powershell
.\scripts\windows\start_gateway_max.ps1 -BaseUrl "https://geek.tm2.xin/v1"
```

或者：

```powershell
$env:OPENAI_BASE_URL="https://geek.tm2.xin/v1"
.\scripts\windows\start_gateway_max.cmd
```

说明：

- 第一次用新地址启动后，这个地址会被保存，后续默认继续使用
- 传入完整接口地址也可以，例如 `https://airouter.service.itstudio.club/v1/chat/completions`
- 程序会自动规整并保存为 `https://airouter.service.itstudio.club/v1` 这种 `Base URL` 形式

### 运行中的热切换命令

网关启动后，在当前 `cmd` / PowerShell 窗口里可以直接输入命令热切换配置，不需要重启进程。

支持的命令：

```text
url <base_url>
apikey <api_key>
show
help
```

示例：

```text
url https://geek.tm2.xin/v1
apikey sk-xxxxxxxx
show
```

行为说明：

- `url <base_url>`：立即切换后续请求使用的上游地址
- `apikey <api_key>`：立即切换后续请求使用的 API Key
- `show`：显示当前生效的 `Base URL`、完整上游请求地址和脱敏后的 API Key
- `help`：显示命令帮助
- 热切换只影响后续新请求，已经在处理中的请求不会被中途打断
- 热切换后的 `Base URL` 和 `API Key` 会立即写回本地配置文件，后续重启仍然生效

### 手动直接启动

```powershell
$env:OPENAI_API_KEY="<YOUR_OPENAI_API_KEY>"
$env:OPENAI_BASE_URL="https://airouter.service.itstudio.club/v1"
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
- `CC2OPEN_STATE_PATH`

`CC2OPEN_STATE_PATH` 用于自定义本地持久化配置文件位置；如果不设置，默认使用：

```text
scripts/windows/.cc2open_state.json
```

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

## 新供应商接入说明

你可以把新的供应商地址作为 `Base URL` 使用，例如：

```text
https://geek.tm2.xin/v1
```

如果使用启动脚本：

```powershell
.\scripts\windows\start_gateway_max.ps1 -BaseUrl "https://geek.tm2.xin/v1"
```

或者在已启动窗口里热切换：

```text
url https://geek.tm2.xin/v1
```

注意：

- 当前网关内部转发的目标协议仍然是 `POST /v1/chat/completions`
- 如果某个供应商声明的是 `wire_api = "responses"`，那表示它原生接口是 Responses API，不是 Chat Completions API
- 这类供应商即使 `Base URL` 可以切过去，也不一定能直接兼容当前网关
- 如果要正式支持 `responses` 供应商，需要再增加一层 `messages -> responses` 的协议适配逻辑

## Claude Code 接入方式

把 Claude Code 的 Anthropic provider 地址指向：

```text
http://127.0.0.1:8787
```

然后继续按 Anthropic 风格调用 `/v1/messages` 即可。

## 停止服务

在启动窗口按：

```
Ctrl + C
```

## 宠物功能

支持宠物模式，可以在启动时开启。宠物功能提供更友好的交互体验。

### 启用宠物功能

在启动脚本中添加 `--pet` 参数：

```powershell
python .\src\cc2open_gateway.py --pet
```

或设置环境变量：

```powershell
$env:CC2OPEN_PET="1"
```


# cc2open

一个本地可运行的轻量网关，用来把 `Claude Code / Anthropic-style` 的 `/v1/messages` 请求转发到 OpenAI 的 `/v1/chat/completions`，并且无视上游传来的 effort，统一强制写成你指定的 `reasoning_effort`。

默认值是 `xhigh`，你也可以在启动时改成 `high`、`medium` 等别的值。

## 目录结构

```text
D:\Ai\cc2open
├─ cc2open_gateway.py
├─ start_gateway.ps1
├─ start_gateway.cmd
├─ start_gateway_fast.ps1
├─ start_gateway_fast.cmd
├─ start_gateway_balanced.ps1
├─ start_gateway_balanced.cmd
├─ start_gateway_max.ps1
├─ start_gateway_max.cmd
└─ README.md
```

## 这个脚本做了什么

- 接收 Anthropic 风格的 `POST /v1/messages`
- 转成 OpenAI `POST /v1/chat/completions`
- 强制注入 `reasoning_effort`
- 支持基础非流式响应
- 支持基础流式 SSE 转发
- 提供简单健康检查和模型列表接口

## 支持的接口

- `GET /healthz`
- `GET /v1/models`
- `POST /v1/messages`
- `POST /v1/messages/count_tokens`

## 运行前准备

需要：

- Windows
- Python 3.10 及以上
- 一个可用的 OpenAI 兼容 API Key

先确认 Python 可用：

```powershell
python --version
```

## 安全提醒

不要把真实 API Key 写进 README、代码文件、Git 仓库或截图里。

如果你之前已经在聊天记录、配置文件、日志或其他地方暴露过 Key，建议立刻去服务端旋转或重新生成新的 Key。

本文档所有示例都使用占位符：

```text
<YOUR_OPENAI_API_KEY>
```

## 最简单的启动方式

进入 PowerShell，执行：

```powershell
$env:OPENAI_API_KEY="<sk-mH6XI234JuYtCpizT4zXRyLE3U8z9yahHnyx0FzhN4PebrId>"
$env:OPENAI_MODEL="gpt-5.4"
$env:OPENAI_REASONING_EFFORT="xhigh"
python D:\Ai\cc2open\cc2open_gateway.py
```

如果你想一键启动，现在也可以直接执行：

```powershell
D:\Ai\cc2open\start_gateway.cmd
```

或者：

```powershell
powershell -ExecutionPolicy Bypass -File D:\Ai\cc2open\start_gateway.ps1
```

`start_gateway.ps1` 默认会使用：

- `https://airouter.service.itstudio.club/v1`
- `gpt-5.4`
- `xhigh`
- `8787`

如果当前会话里没有 `OPENAI_API_KEY`，脚本会提示你输入。

## 三档启动脚本

现在已经额外提供三档启动脚本：

- `快速`：`start_gateway_fast.cmd`
- `平衡`：`start_gateway_balanced.cmd`
- `最强`：`start_gateway_max.cmd`

对应关系：

- `快速` = `reasoning_effort=medium`
- `平衡` = `reasoning_effort=high`
- `最强` = `reasoning_effort=xhigh`

直接运行：

```powershell
D:\Ai\cc2open\start_gateway_fast.cmd
```

```powershell
D:\Ai\cc2open\start_gateway_balanced.cmd
```

```powershell
D:\Ai\cc2open\start_gateway_max.cmd
```

这三档默认都使用：

- `Base URL = https://airouter.service.itstudio.club/v1`
- `Model = gpt-5.4`

它们的区别只有：

- 推理强度不同
- 流式 ping 间隔略有不同

默认监听地址：

```text
http://127.0.0.1:8787
```

启动成功后，终端会看到类似输出：

```text
cc2open-gateway listening on http://127.0.0.1:8787 -> https://api.openai.com/v1/chat/completions
OpenAI model override: gpt-5.4; forced reasoning_effort=xhigh
Press Ctrl+C to stop.
```

## 用命令行参数启动

如果你不想用环境变量，也可以直接传参数：

```powershell
python D:\Ai\cc2open\cc2open_gateway.py `
  --openai-api-key "<sk-mH6XI234JuYtCpizT4zXRyLE3U8z9yahHnyx0FzhN4PebrId>" `
  --openai-model "gpt-5.4" `
  --reasoning-effort "xhigh" `
  --port 8787
```

## 所有可用启动参数

```powershell
python D:\Ai\cc2open\cc2open_gateway.py --help
```

可用参数：

- `--host`
- `--port`
- `--openai-base-url`
- `--openai-chat-path`
- `--openai-api-key`
- `--openai-model`
- `--reasoning-effort`
- `--timeout-seconds`

## 环境变量说明

脚本支持以下环境变量：

- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `OPENAI_REASONING_EFFORT`
- `OPENAI_BASE_URL`
- `OPENAI_CHAT_PATH`
- `CC2OPEN_HOST`
- `CC2OPEN_PORT`
- `CC2OPEN_TIMEOUT_SECONDS`
- `CC2OPEN_STREAM_PING_INTERVAL`

最常用的是这三个：

```powershell
$env:OPENAI_API_KEY="<YOUR_OPENAI_API_KEY>"
$env:OPENAI_MODEL="gpt-5.4"
$env:OPENAI_REASONING_EFFORT="xhigh"
```

## 如何修改强制 effort

脚本不会读取 Claude Code 发来的 `low / medium / high`，而是统一用你本地配置的值覆盖。

比如改成 `high`：

```powershell
$env:OPENAI_REASONING_EFFORT="high"
python D:\Ai\cc2open\cc2open_gateway.py
```

或者：

```powershell
python D:\Ai\cc2open\cc2open_gateway.py --reasoning-effort high
```

## 如何验证服务是否启动成功

浏览器或 PowerShell 访问：

```powershell
Invoke-WebRequest http://127.0.0.1:8787/healthz | Select-Object -ExpandProperty Content
```

正常会返回类似：

```json
{
  "ok": true,
  "gateway": "cc2open-gateway",
  "upstream_url": "https://api.openai.com/v1/chat/completions",
  "openai_model_override": "gpt-5.4",
  "reasoning_effort": "xhigh"
}
```

## 手工测试一次消息请求

可以用下面这个 PowerShell 示例发送 Anthropic 风格请求：

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

## Claude Code 如何接这个网关

你的目标是让 Claude Code 把 Anthropic 风格请求发给本地网关，再由本地网关转到 OpenAI。

思路是：

1. Claude Code 请求本地 `http://127.0.0.1:8787`
2. 本地脚本收到 `POST /v1/messages`
3. 脚本转发到 OpenAI
4. 脚本统一写入 `reasoning_effort=xhigh`

你需要在 Claude Code 的提供方地址中指向本地网关，而不是直接指向 OpenAI。

如果你已经有自己的路由层或自定义 provider，请把 Anthropic 入口改为：

```text
http://127.0.0.1:8787
```

然后让客户端继续按 Anthropic 风格访问 `/v1/messages` 即可。

## 推荐的本地启动顺序

1. 打开 PowerShell
2. 设置环境变量
3. 启动 `cc2open_gateway.py`
4. 先访问 `/healthz`
5. 再让 Claude Code 连到 `http://127.0.0.1:8787`

## 如果你想固定成 bat 启动

可以自己建一个 `start_gateway.bat`，内容示例：

```bat
@echo off
set OPENAI_API_KEY=<YOUR_OPENAI_API_KEY>
set OPENAI_MODEL=gpt-5.4
set OPENAI_REASONING_EFFORT=xhigh
python D:\Ai\cc2open\cc2open_gateway.py
pause
```

注意：

- 这个方式会把 Key 明文写进文件
- 只建议在你完全信任的本机临时使用
- 更推荐环境变量方式，不要把 Key 落盘

## 如果你想固定成 PowerShell 启动脚本

示例 `start_gateway.ps1`：

```powershell
$env:OPENAI_API_KEY="<YOUR_OPENAI_API_KEY>"
$env:OPENAI_MODEL="gpt-5.4"
$env:OPENAI_REASONING_EFFORT="xhigh"
python D:\Ai\cc2open\cc2open_gateway.py
```

## 常见问题

### 1. 为什么不把我的真实 Key 直接写进 README

因为 README 很容易被同步、提交、截图、分享或索引。\
把真实 Key 落进文档是高风险操作，所以这里故意保留为占位符。

### 2. 为什么我设置了 Claude Code 的 effort，但这里还是统一成 xhigh

因为这个脚本就是按你的要求实现的：

- 不做 `low / medium / high` 分别映射
- 无视上游传来的 effort
- 统一写成本地配置值

### 3. 默认一定是 xhigh 吗

默认是 `xhigh`，但你可以自己改：

```powershell
$env:OPENAI_REASONING_EFFORT="high"
```

或者：

```powershell
python D:\Ai\cc2open\cc2open_gateway.py --reasoning-effort high
```

### 4. 如果 OpenAI 兼容服务不是官方地址怎么办

你可以改 `OPENAI_BASE_URL`：

```powershell
$env:OPENAI_BASE_URL="https://your-openai-compatible-endpoint"
```

也可以改完整路径：

```powershell
$env:OPENAI_CHAT_PATH="/v1/chat/completions"
```

### 5. 为什么有些参数没有从上游透传

脚本是“够用优先”的轻量实现，不是全量协议镜像。\
而且像 `gpt-5.4 + reasoning_effort` 这类组合下，某些参数可能本来就不兼容，所以脚本没有盲目全透传。

### 6. 如何停止服务

在启动窗口按：

```text
Ctrl + C
```

## 排错建议

### 看看脚本能不能正常解析参数

```powershell
python D:\Ai\cc2open\cc2open_gateway.py --help
```

### 看看 Python 语法是否正常

```powershell
python -m py_compile D:\Ai\cc2open\cc2open_gateway.py
```

### 看看健康检查是否正常

```powershell
Invoke-WebRequest http://127.0.0.1:8787/healthz | Select-Object -ExpandProperty Content
```

### 如果端口被占用

换个端口：

```powershell
python D:\Ai\cc2open\cc2open_gateway.py --port 8788
```

## 一条最短使用路径

```powershell
$env:OPENAI_API_KEY="<YOUR_OPENAI_API_KEY>"
$env:OPENAI_MODEL="gpt-5.4"
$env:OPENAI_REASONING_EFFORT="xhigh"
python D:\Ai\cc2open\cc2open_gateway.py
```

然后把 Claude Code 的 Anthropic 接口地址改成：

```text
http://127.0.0.1:8787
```

## 当前脚本文件

- [cc2open\_gateway.py](D:\Ai\cc2open\cc2open_gateway.py)
- [README.md](D:\Ai\cc2open\README.md)

# Claude Code + CubeSandbox 集成

[English](README.md)

通过 MCP（Model Context Protocol）将 Claude Code 的代码执行路由至 CubeSandbox
MicroVM，为 Claude Code 提供安全隔离的执行环境。

## 这是什么

一个 **MCP Server**，将 CubeSandbox 的沙箱操作暴露为 Claude Code 可调用的工具。
当 Claude Code 需要执行 Python、运行 Shell 命令或操作文件时，这些操作会被代理到
硬件隔离的 MicroVM 中，而非直接在宿主机上执行。

```
Claude Code          MCP (stdio)        MCP Server       CubeSandbox
──────────          ──────────         ──────────       ───────────
"帮我分析数据"  →  sandbox_create  →  Sandbox.create() →  KVM MicroVM
                   sandbox_run_code   sandbox.run_code()   (隔离环境)
                   sandbox_read_file  sandbox.files.read() ↑ 安全
```

> **验证状态**：此 MCP Server 已对照 CubeSandbox v0.4.0 / v0.5.0 Python SDK 源码
> 进行了审查，API 调用逻辑上正确，但**尚未在真实 CubeSandbox 部署中进行端到端测试**。
> 欢迎反馈、Bug 报告和验证报告。

## 为什么需要它

| 不用时                                  | 用了之后                                    |
|----------------------------------------|---------------------------------------------|
| Claude Code 的 `bash` 直接在宿主机执行  | 代码在硬件隔离的 MicroVM 中运行              |
| `rm -rf /` 会破坏你的机器               | `rm -rf /` 只破坏沙箱 VM                    |
| `pip install` 污染系统环境               | 所有包安装在即用即毁的 VM 中                 |
| 无快照/回滚能力                          | 随时保存、克隆、回滚沙箱状态                  |
| 无网络访问控制                           | 按沙箱粒度设置网络策略（allow/deny）          |

## 前提条件

- **已部署 CubeSandbox**（参考[快速开始](https://cube-sandbox.pages.dev/zh/guide/quickstart)）。
- **已有沙箱模板**：预装 Python 及所需工具。标准 `sandbox-code` 镜像能满足大部分场景。
- **Claude Code** 支持 MCP（Claude Code v1.0.0+）。
- **Python 3.10+** 用于运行此 MCP Server。

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`，填入你的 CubeSandbox 连接信息：

| 变量                  | 说明                                                           |
|-----------------------|---------------------------------------------------------------|
| `E2B_API_URL`         | CubeAPI 地址，如 `http://<cube-host>:3000`                    |
| `E2B_API_KEY`         | CubeAPI 认证密钥（本地/测试部署可用任意占位符，如 `e2b_000000`） |
| `CUBE_TEMPLATE_ID`    | 沙箱模板 ID                                                    |
| `CUBE_SSL_CERT_FILE`  | （可选）CubeSandbox CA 证书路径，用于 HTTPS 连接                  |

### 3. 创建沙箱模板

如果还没有模板：

```bash
cubemastercli tpl create-from-image \
  --image cube-sandbox-int.tencentcloudcr.com/cube-sandbox/sandbox-code:latest \
  --writable-layer-size 1G \
  --expose-port 49999 \
  --expose-port 49983 \
  --probe 49999
```

将输出的 `template_id` 填入 `.env`。

> **镜像源**：国内用户请使用 `cube-sandbox-cn.tencentcloudcr.com/cube-sandbox/sandbox-code:latest`。

### 4. 在 Claude Code 中注册 MCP Server

在 Claude Code 配置中添加：

```json
{
  "mcpServers": {
    "cube-sandbox": {
      "command": "python",
      "args": ["/path/to/cube-sandbox/examples/claude-code-sandbox/mcp_server.py"],
      "env": {
        "CUBE_TEMPLATE_ID": "<your-template-id>",
        "E2B_API_URL": "http://<cube-host>:3000",
        "E2B_API_KEY": "e2b_000000"
      }
    }
  }
}
```

配置文件位置：
- 项目级：`.claude/settings.json`
- 用户级：`~/.claude/settings.json`

### 5. 开始使用

重启 Claude Code（或重新加载 MCP Server）。Claude Code 现在拥有以下工具：

| 工具                   | 功能                     |
|------------------------|--------------------------|
| `sandbox_create`       | 创建新的隔离沙箱           |
| `sandbox_run_code`     | 执行 Python（Jupyter 内核）|
| `sandbox_run_command`  | 执行 Shell 命令            |
| `sandbox_read_file`    | 从沙箱读取文件              |
| `sandbox_write_file`   | 向沙箱写入文件              |
| `sandbox_get_info`     | 查看沙箱元数据              |
| `sandbox_snapshot`     | 保存时间点快照              |
| `sandbox_pause`        | 暂停沙箱（保留状态）         |
| `sandbox_destroy`      | 销毁沙箱                    |

开始对话：

```
> 帮我创建一个沙箱，然后写一个 Python 脚本计算前 100 个斐波那契数，
  保存到 fib.csv，读取文件并验证结果。
```

Claude Code 会按步骤调用沙箱工具并报告结果。

## 工具参考

### sandbox_create

创建硬件隔离的 MicroVM。必须首先调用。

```
参数:
  template (字符串, 可选) — 模板 ID
  timeout  (整数, 可选)   — 沙箱生命周期秒数（默认 600）

返回: 沙箱 ID、模板 ID、状态、超时时间
```

### sandbox_run_code

在持久化 Jupyter 内核中执行 Python 代码。变量、导入、DataFrame 在多次调用间保持。

```
参数:
  code    (字符串, 必填) — Python 源码
  timeout (整数, 可选)   — 执行超时秒数（默认 120）

返回: stdout、stderr、文本结果、错误回溯（如有）
```

### sandbox_run_command

执行 Shell 命令。

```
参数:
  command (字符串, 必填) — Shell 命令（传递给 sh -lc）
  timeout (整数, 可选)   — 命令超时秒数（默认 60）

返回: 退出码、stdout、stderr
```

### sandbox_read_file / sandbox_write_file

沙箱内文件读写。用于提供输入数据和获取输出。

### sandbox_snapshot

捕获完整沙箱状态（文件系统 + 内存）。快照独立于沙箱生命周期，可用于后续克隆或回滚。

### sandbox_pause

暂停沙箱并保留内存快照。后续可恢复。适合跨会话保存长任务进度。

### sandbox_destroy

立即销毁沙箱。所有未保存状态将丢失（快照不受影响）。完成后请及时调用来释放资源。

## 注意事项

### 沙箱启动延迟

首次 `sandbox_create` 可能需要几秒（冷启动）。同一模板的后续创建因 VM 池化会更快。

### 模板端口

- MCP Server 使用端口 **49999** 进行 `run_code`（Jupyter 内核网关）。模板必须暴露此端口。
- 端口 **49983**（envd）用于 `run_command` 和文件操作。大部分模板默认暴露此端口。

### 网络与出口

- 默认情况下沙箱可访问公网。如需限制，请在 `Sandbox.create()` 中使用 `network` 选项。
- 如果 Claude Code 从沙箱内访问 LLM API（不常见，LLM 调用通常发生在宿主机的 MCP Server 进程中），需要配置 CubeEgress 域名白名单。

### 文件大小限制

`sandbox_read_file` 将输出截断至 50,000 字符以免淹没 Claude Code 上下文窗口。对于大文件，请使用 `sandbox_run_command` 配合 `head`/`tail`/`wc` 逐步查看。

### MCP vs 直接 SDK

此 MCP Server 设计用于**与 Claude Code 交互使用**。如果你在构建自动化 Agent 流水线，建议直接调用 `cubesandbox` Python SDK——它能提供对沙箱生命周期、并发、错误处理的完全控制。

## 架构

```
┌──────────────────┐
│   Claude Code     │  Anthropic 的终端编码 Agent
│   (你的宿主机上)   │
└────────┬─────────┘
         │ MCP 协议 (stdio)
         │
┌────────▼─────────┐
│   mcp_server.py   │  本文件 — MCP ↔ CubeSDK 桥接
│   (本项目)        │
└────────┬─────────┘
         │ cubesandbox Python SDK
         │ (pip install cubesandbox)
         │
┌────────▼─────────┐
│    CubeAPI        │  CubeSandbox REST API (:3000)
└────────┬─────────┘
         │ gRPC
┌────────▼─────────┐
│  CubeMaster /     │  调度器、节点代理、Hypervisor
│  Cubelet /        │
│  CubeHypervisor   │
└────────┬─────────┘
         │ KVM
┌────────▼─────────┐
│   MicroVM         │  硬件隔离沙箱
│   (独立 Linux 内核)│  ─ Python、Node.js、CLI 工具
└──────────────────┘
```

## 常见问题

| 现象                                          | 可能原因             | 解决方案                                                                         |
|-----------------------------------------------|---------------------|---------------------------------------------------------------------------------|
| `sandbox_create` 返回 "Connection refused"      | CubeAPI 不可达       | 检查 `E2B_API_URL`；确认 CubeAPI 正在运行（`curl http://host:3000/health`）       |
| `sandbox_run_code` 卡住                         | Jupyter 网关未就绪    | 创建沙箱后等待几秒；模板需暴露 49999 端口                                           |
| `sandbox_run_command` 返回 "not found"          | 工具未安装在模板中    | 用 `sandbox_run_command` 执行 `apt install` 或 `pip install`                      |
| "Template not found"                            | `CUBE_TEMPLATE_ID` 错误或缺失 | 执行 `cubemastercli tpl list` 核对 ID                                          |
| SSL 错误                                        | 自定义 CA 未被信任    | 设置 `CUBE_SSL_CERT_FILE` 指向 CubeSandbox CA 证书路径                              |

## 相关文档

- [CubeSandbox Claude Code 集成指南](../../docs/zh/guide/integrations/claude-code.md) — 完整集成指南与最佳实践
- [CubeSandbox 快速开始](https://cube-sandbox.pages.dev/zh/guide/quickstart)
- [CubeSandbox Python SDK](../sdk/python/README.md)
- [Claude Code MCP 文档](https://docs.anthropic.com/en/docs/claude-code/mcp)

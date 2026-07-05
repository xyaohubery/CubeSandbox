---
title: Claude Code 集成指南
author: community
date: 2026-07-01
tags:
  - integration
  - claude-code
  - mcp
lang: zh-CN
---

# Claude Code 集成指南

通过 MCP（Model Context Protocol）将 Claude Code 的代码执行安全地卸载到
CubeSandbox MicroVM。本指南覆盖从模板创建到生产环境最佳实践的端到端流程。

## 集成对象与版本

| 组件                | 已测试版本        |
|---------------------|------------------|
| Claude Code         | ≥ 1.0.0           |
| CubeSandbox         | v0.4.0            |
| `cubesandbox` SDK   | ≥ 0.3.0           |
| `mcp` (Python)      | ≥ 1.0.0           |

## 概述

Claude Code 是 Anthropic 的交互式终端编码 Agent。默认情况下，Claude Code 的
`Bash` 工具直接在宿主机上执行命令。本集成通过添加一组 MCP 工具，将代码执行
路由到 CubeSandbox——一个基于硬件隔离 MicroVM 的平台——从而实现：

- AI 生成的代码在隔离 VM 中而非宿主机上运行。
- 恶意或有 bug 的脚本无法影响你的系统。
- 每个编码会话拥有独立的即用即毁环境。
- 长任务可通过快照跨会话保存和恢复。

本集成采用 **"沙箱即工具"** 模式：Claude Code 在本地运行（处理对话和编排），
按需将代码执行委托给 CubeSandbox。

## 前提条件

- **一台 Linux x86_64 主机**，需启用 KVM。CubeSandbox 需要裸金属 Linux 或支持
  嵌套虚拟化的云服务器。**WSL2 不受支持**（CubeSandbox v0.5.0 的 network-agent
  和 cubelet 组件在 Microsoft WSL2 内核上因内核接口不兼容而崩溃）。
- Cube Sandbox 已部署且 CubeAPI 可访问（参见[快速开始](../quickstart.md)）。
- 已有预装 Python 和开发工具链的沙箱模板。推荐使用 `sandbox-code` 镜像作为起点。
- 本地已安装 Claude Code。
- 本地 Python 3.10+（用于运行 MCP Server 进程）。

## 集成步骤

### 第一步 — 创建沙箱模板

```bash
cubemastercli tpl create-from-image \
  --image cube-sandbox-int.tencentcloudcr.com/cube-sandbox/sandbox-code:latest \
  --writable-layer-size 2G \
  --expose-port 49999 \
  --expose-port 49983 \
  --probe 49999
```

等待构建完成（用 `cubemastercli tpl watch --job-id <job_id>` 查看进度）。
记下输出的 `template_id`。

> **镜像源说明**：国内用户使用 `cube-sandbox-cn.tencentcloudcr.com`，
> 国际用户使用 `cube-sandbox-int.tencentcloudcr.com`。

**Claude Code 集成所需的模板要求：**

| 要求                 | 原因                                      |
|----------------------|------------------------------------------|
| 暴露端口 49999        | Jupyter 内核网关，用于 `run_code`          |
| 暴露端口 49983        | envd 端点，用于 `run_command` 和文件操作   |
| Python 3.10+         | `run_code` 后端                           |
| 常用 CLI 工具         | `run_command`（git、curl、make、gcc 等）   |
| ≥ 2GB 可写层          | 为 `pip install`/`npm install` 留空间      |

按需构建自定义 Docker 镜像并创建模板：

```dockerfile
FROM cube-sandbox-int.tencentcloudcr.com/cube-sandbox/sandbox-code:latest
RUN pip install torch numpy pandas matplotlib scikit-learn
RUN apt-get update && apt-get install -y ffmpeg
```

### 第二步 — 安装 MCP Server 依赖

```bash
git clone https://github.com/tencentcloud/CubeSandbox.git
cd CubeSandbox/examples/claude-code-sandbox
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env 填入你的 CubeSandbox 连接信息
```

### 第三步 — 配置 Claude Code

在 Claude Code 设置中添加 MCP Server 条目：

**项目级**（项目根目录下的 `.claude/settings.json`）：

```json
{
  "mcpServers": {
    "cube-sandbox": {
      "command": "python",
      "args": ["/绝对路径/CubeSandbox/examples/claude-code-sandbox/mcp_server.py"],
      "env": {
        "CUBE_TEMPLATE_ID": "<your-template-id>",
        "E2B_API_URL": "http://<cube-host>:3000",
        "E2B_API_KEY": "e2b_000000"
      }
    }
  }
}
```

**用户级**（`~/.claude/settings.json`）— 对所有项目生效。

### 第四步 — 验证集成

重启 Claude Code，确认沙箱工具已注册：

```
/claude mcp list
```

应能看到 `cube-sandbox` 及 9 个工具。用简单对话测试：

```
> 创建一个沙箱，运行 `print(1 + 1)`，然后销毁沙箱。
```

预期：Claude Code 依次调用 `sandbox_create` → `sandbox_run_code` →
`sandbox_destroy`，并报告结果 `2`。

## 关键代码片段

### 最小 MCP Server

MCP Server 封装了 `cubesandbox` Python SDK。核心连接方式：

```python
from cubesandbox import Sandbox
from mcp.server import Server
from mcp.server.stdio import stdio_server

server = Server("cube-sandbox")

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "sandbox_create":
        sb = Sandbox.create(
            template=os.environ["CUBE_TEMPLATE_ID"],
            timeout=arguments.get("timeout", 600),
        )
        return [TextContent(type="text", text=f"Created: {sb.sandbox_id}")]
    # ... 其他工具

async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())
```

完整实现见 [`examples/claude-code-sandbox/mcp_server.py`](../../examples/claude-code-sandbox/mcp_server.py)。

### 自定义模板（含额外工具）

如果你的工作流需要额外的软件包或工具，创建自定义 Docker 镜像和模板：

```bash
# 1. 构建自定义镜像
docker build -t my-sandbox:latest -f- . <<'EOF'
FROM cube-sandbox-int.tencentcloudcr.com/cube-sandbox/sandbox-code:latest
RUN pip install torch transformers
EOF

# 2. 推送到 CubeSandbox 可访问的镜像仓库
docker tag my-sandbox:latest your-registry/my-sandbox:latest
docker push your-registry/my-sandbox:latest

# 3. 从自定义镜像创建模板
cubemastercli tpl create-from-image \
  --image your-registry/my-sandbox:latest \
  --writable-layer-size 5G \
  --expose-port 49999 \
  --expose-port 49983 \
  --probe 49999
```

## 最佳实践

### 沙箱生命周期管理

- **每个会话创建一个沙箱**：在 Claude Code 对话开始时创建一个沙箱，在多次
  `run_code`/`run_command` 调用中复用。Jupyter 内核在多次调用间保持状态。
- **合理设置超时时间**：根据预期会话时长设置 `timeout`。默认 600 秒（10 分钟）
  对复杂任务可能不够。
- **务必销毁**：会话结束时调用 `sandbox_destroy`。沙箱会消耗节点资源（CPU、
  内存、磁盘）。

### 长任务的快照保存

对于跨会话的任务（如持续数天的调试）：

```text
会话 1：
  sandbox_create → work → sandbox_snapshot(name="checkpoint-1")
  sandbox_pause

会话 2：
  sandbox_create → (快照可用) sandbox_run_code → 继续工作
```

快照独立于沙箱生命周期。你可以创建多个检查点并回滚到任意一个。

### 凭证安全

**不要**在传给 `sandbox_run_code` 或 `sandbox_write_file` 的代码中硬编码 API
密钥或其他机密信息。使用以下方式之一：

1. **CubeEgress 凭证注入**（推荐）：配置沙箱的网络策略，在出口代理处注入
   `Authorization` 头。机密信息存储在 CubeEgress 配置中，不会进入沙箱。
   参见[安全代理指南](../security-proxy.md)。

2. **环境变量**：通过 `Sandbox.create()` 的 `env_vars` 传入机密信息。
   这些变量设置在沙箱进程环境中，在 Claude Code 上下文中不可见。

```python
# 在 mcp_server.py（或自定义变体）中：
sb = Sandbox.create(
    template=template,
    env_vars={"GITHUB_TOKEN": os.environ["GITHUB_TOKEN"]},
)
```

### 网络策略

默认情况下沙箱可以不受限访问公网。如需更严格的控制，按沙箱配置网络策略：

```python
sb = Sandbox.create(
    template=template,
    allow_internet_access=False,  # 默认拦截所有出口
    network={
        "allow_out": [
            "pypi.org",
            "files.pythonhosted.org",
        ],
        "deny_out": [
            "169.254.0.0/16",   # 链路本地
            "10.0.0.0/8",        # 私有 A 类
            "172.16.0.0/12",     # 私有 B 类
            "192.168.0.0/16",    # 私有 C 类
        ],
    },
)
```

详见[网络策略指南](../network-policy.md)。

## 注意事项

### 本地文件访问

与 Claude Code 原生的 `Bash` 工具不同，沙箱工具**无法**直接访问宿主机上的文件。
使用 `sandbox_write_file` 上传文件，`sandbox_read_file` 下载结果。对于大规模
数据集，使用 `sandbox_run_command("git clone ...")` 或配置 host mount
（参见[Host Mount 示例](../../examples/host-mount/README.md)）。

### 工具发现

当沙箱工具和内置 `Bash` 工具同时可用时，Claude Code 不一定总是选择沙箱工具。
为引导 Claude Code 优先使用沙箱，在项目中创建 CLAUDE.md 文件：

```markdown
# CLAUDE.md

执行代码时，始终使用 sandbox_run_code 或 sandbox_run_command 工具。
不要使用 Bash 工具执行代码。使用 sandbox_write_file 提供输入，
使用 sandbox_read_file 获取输出。
```

### 性能开销

- **冷启动**：从模板创建首个沙箱通常需要 2–5 秒。包含模板 clone（CoW）、VM
  启动和服务就绪。
- **热启动**：使用最近用过的模板创建沙箱更快（< 1 秒），得益于 VM 池化。
- **执行延迟**：`run_code` 比本地执行增加约 50–200ms 往返延迟，取决于你与
  CubeSandbox 节点之间的网络距离。

### 兼容性

- 此 MCP Server 使用 **Jupyter 内核模式**（`run_code`）。需要模板中包含 e2b
  code-interpreter 服务（端口 49999）。仅含 envd 的模板可用于 `run_command`
  但不能用于 `run_code`。
- `cubesandbox` Python SDK 兼容 CubeSandbox v0.3.0+。
- Claude Code MCP 支持需要 Claude Code v1.0.0+。

## 常见问题

| 现象                                              | 可能原因              | 解决方案                                                                                 |
|---------------------------------------------------|----------------------|-----------------------------------------------------------------------------------------|
| MCP Server 无法启动                                | 缺少依赖              | 执行 `pip install -r requirements.txt`；检查 Python 3.10+                                |
| `sandbox_create` 挂起                             | CubeAPI 不可达        | 检查 `E2B_API_URL`；`curl http://<host>:3000/health`                                     |
| `sandbox_create` 返回 404                         | 模板 ID 错误          | 核对 `CUBE_TEMPLATE_ID`；执行 `cubemastercli tpl list`                                   |
| `sandbox_run_code` 返回 502                       | 沙箱被驱逐（超时/被删） | 增加 `timeout`；检查沙箱是否被空闲超时机制终止                                              |
| `sandbox_run_code` 返回 "connection refused"      | Jupyter 网关未就绪     | 模板必须暴露端口 49999；首次 `run_code` 调用前等待 2–3 秒                                   |
| SSL 证书错误                                       | 自定义 CA 未被信任     | 设置 `CUBE_SSL_CERT_FILE` 环境变量；或测试环境传 `verify=False`（生产环境不要这样做）        |
| Claude Code 不显示沙箱工具                         | MCP 配置错误           | 检查 `settings.json` 语法；用 `--debug` 运行 Claude Code 查看 MCP 启动日志                  |

## 参考

- 示例代码：[`examples/claude-code-sandbox/`](../../examples/claude-code-sandbox/)
- CubeSandbox Python SDK：[`sdk/python/`](../../sdk/python/)
- Claude Code MCP 文档：[docs.anthropic.com](https://docs.anthropic.com/en/docs/claude-code/mcp)
- MCP 规范：[modelcontextprotocol.io](https://modelcontextprotocol.io)

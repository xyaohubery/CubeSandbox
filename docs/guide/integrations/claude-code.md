---
title: Claude Code Integration Guide
author: community
date: 2026-07-01
tags:
  - integration
  - claude-code
  - mcp
lang: en-US
---

# Claude Code Integration Guide

Run Claude Code with code execution safely offloaded to CubeSandbox
MicroVMs via MCP (Model Context Protocol). This guide covers the
end-to-end setup: from template creation to production best practices.

## Integration Target and Version

| Component       | Tested Version     |
|-----------------|--------------------|
| Claude Code     | ≥ 1.0.0            |
| CubeSandbox     | v0.4.0             |
| `cubesandbox` SDK | ≥ 0.3.0          |
| `mcp` (Python)  | ≥ 1.0.0            |

## Overview

Claude Code is Anthropic's interactive terminal coding agent. By default,
Claude Code's `Bash` tool executes commands directly on the host machine.
This integration adds a set of MCP tools that route code execution to
CubeSandbox — a hardware-isolated MicroVM platform — so that:

- AI-generated code runs in an isolated VM, not on your host.
- Malicious or buggy scripts can't affect your system.
- Each coding session can have its own disposable environment.
- Long-running tasks can be snapshot and resumed across sessions.

This integration implements the **"Sandbox as Tool"** pattern: Claude Code
runs locally (handling conversation and orchestration), but delegates code
execution to CubeSandbox on demand.

## Prerequisites

- **A Linux x86_64 host** with KVM enabled. CubeSandbox requires bare-metal
  Linux or a cloud VM with nested virtualization. **WSL2 is not supported**
  (CubeSandbox v0.5.0 components including network-agent and cubelet crash on
  the Microsoft WSL2 kernel due to incompatible kernel interfaces).
- Cube Sandbox deployed and CubeAPI reachable (see
  [Quick Start](../quickstart.md)).
- A sandbox template with Python and development toolchain preinstalled.
  The `sandbox-code` image is recommended as a starting point.
- Claude Code installed on your local machine.
- Python 3.10+ on your local machine (for the MCP server process).

## Integration Steps

### Step 1 — Create a Sandbox Template

```bash
cubemastercli tpl create-from-image \
  --image cube-sandbox-int.tencentcloudcr.com/cube-sandbox/sandbox-code:latest \
  --writable-layer-size 2G \
  --expose-port 49999 \
  --expose-port 49983 \
  --probe 49999
```

Wait for the build to finish (check with `cubemastercli tpl watch --job-id <job_id>`).
Note the `template_id` from the output.

> **Registry note:** Use `cube-sandbox-cn.tencentcloudcr.com` for mainland
> China access or `cube-sandbox-int.tencentcloudcr.com` for international.

**Template requirements for Claude Code integration:**

| Requirement           | Why                                              |
|-----------------------|--------------------------------------------------|
| Port 49999 exposed    | Jupyter kernel gateway for `run_code`            |
| Port 49983 exposed    | envd endpoint for `run_command` and file ops     |
| Python 3.10+          | `run_code` backend                               |
| Common CLI tools      | `run_command` (git, curl, make, gcc, etc.)       |
| ≥ 2GB writable layer  | Room for `pip install` / `npm install` packages  |

For custom needs, build your own Docker image and create a template from it:

```dockerfile
FROM cube-sandbox-int.tencentcloudcr.com/cube-sandbox/sandbox-code:latest
RUN pip install torch numpy pandas matplotlib scikit-learn
RUN apt-get update && apt-get install -y ffmpeg
```

### Step 2 — Install the MCP Server Dependencies

```bash
git clone https://github.com/tencentcloud/CubeSandbox.git
cd CubeSandbox/examples/claude-code-sandbox
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your CubeSandbox connection details
```

### Step 3 — Configure Claude Code

Add the MCP server entry to your Claude Code settings:

**Project-level** (`.claude/settings.json` in your project root):

```json
{
  "mcpServers": {
    "cube-sandbox": {
      "command": "python",
      "args": ["/absolute/path/to/CubeSandbox/examples/claude-code-sandbox/mcp_server.py"],
      "env": {
        "CUBE_TEMPLATE_ID": "<your-template-id>",
        "E2B_API_URL": "http://<cube-host>:3000",
        "E2B_API_KEY": "e2b_000000"
      }
    }
  }
}
```

**User-level** (`~/.claude/settings.json`) — applies to all projects.

### Step 4 — Verify the Integration

Restart Claude Code and check that the sandbox tools are registered:

```
/claude mcp list
```

You should see `cube-sandbox` with 9 tools listed. Test with a simple
conversation:

```
> Create a sandbox, run `print(1 + 1)`, then destroy the sandbox.
```

Expected: Claude Code calls `sandbox_create` → `sandbox_run_code` →
`sandbox_destroy` and reports the result `2`.

## Key Code Snippets

### Minimal MCP Server

The MCP server wraps the `cubesandbox` Python SDK. Core connection:

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
    # ... other tools

async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())
```

See [`examples/claude-code-sandbox/mcp_server.py`](../../examples/claude-code-sandbox/mcp_server.py)
for the full implementation.

### Custom Template with Extra Tools

If your workflow needs additional packages or tools, create a custom
Docker image and template:

```bash
# 1. Build a custom image
docker build -t my-sandbox:latest -f- . <<'EOF'
FROM cube-sandbox-int.tencentcloudcr.com/cube-sandbox/sandbox-code:latest
RUN pip install torch transformers
EOF

# 2. Push to a registry accessible by CubeSandbox
docker tag my-sandbox:latest your-registry/my-sandbox:latest
docker push your-registry/my-sandbox:latest

# 3. Create a template from the custom image
cubemastercli tpl create-from-image \
  --image your-registry/my-sandbox:latest \
  --writable-layer-size 5G \
  --expose-port 49999 \
  --expose-port 49983 \
  --probe 49999
```

## Best Practices

### Sandbox Lifecycle Management

- **Create once per session**: Create one sandbox at the start of a
  Claude Code conversation and reuse it across multiple `run_code` /
  `run_command` calls. The Jupyter kernel preserves state across calls.
- **Timeout appropriately**: Set `timeout` to match your expected session
  length. Default 600s (10 min) may be too short for complex tasks.
- **Always destroy**: Call `sandbox_destroy` at the end of a session.
  Sandboxes consume node resources (CPU, memory, disk).

### Snapshots for Long-Running Work

For multi-session tasks (e.g., debugging that spans days):

```text
Session 1:
  sandbox_create → work → sandbox_snapshot(name="checkpoint-1")
  sandbox_pause

Session 2:
  sandbox_create → (snapshot is available) sandbox_run_code → continue work
```

Snapshots survive sandbox destruction. You can create multiple checkpoints
and roll back to any of them.

### Credential Security

Do **not** hardcode API keys or secrets in code passed to `sandbox_run_code`
or `sandbox_write_file`. Use one of these approaches instead:

1. **CubeEgress credential injection** (recommended): Configure the
   sandbox's network policy to inject `Authorization` headers at the
   egress proxy. Secrets are stored in CubeEgress configuration and
   never enter the sandbox. See
   [Security Proxy Guide](../security-proxy.md).

2. **Environment variables**: Pass secrets via `env_vars` in
   `Sandbox.create()`. These are set in the sandbox process environment
   and are not visible in Claude Code's context.

```python
# In mcp_server.py (or a custom variant):
sb = Sandbox.create(
    template=template,
    env_vars={"GITHUB_TOKEN": os.environ["GITHUB_TOKEN"]},
)
```

### Network Policy

By default, sandboxes have unrestricted internet access. For stricter
control, configure per-sandbox network policies:

```python
sb = Sandbox.create(
    template=template,
    allow_internet_access=False,  # Block all by default
    network={
        "allow_out": [
            "pypi.org",
            "files.pythonhosted.org",
        ],
        "deny_out": [
            "169.254.0.0/16",
            "10.0.0.0/8",
            "172.16.0.0/12",
            "192.168.0.0/16",
        ],
    },
)
```

See [Network Policy Guide](../network-policy.md) for details.

## Caveats

### Local File Access

Unlike Claude Code's native `Bash` tool, the sandbox tools **cannot**
directly access files on your host machine. Use `sandbox_write_file` to
upload files and `sandbox_read_file` to download results. For large
datasets, use `sandbox_run_command("git clone ...")` or configure a
host mount (see [Host Mount Example](../../examples/host-mount/README.md)).

### Tool Discovery

Claude Code may not always choose the sandbox tools over its built-in
`Bash` tool when both are available. To encourage sandbox usage:

1. Use a CLAUDE.md file in your project:

```markdown
# CLAUDE.md

When executing code, ALWAYS use the sandbox_run_code or sandbox_run_command
tools. Do NOT use the Bash tool for code execution. Use sandbox_write_file
to provide input and sandbox_read_file to retrieve output.
```

### Performance Overhead

- **Cold start**: First sandbox creation from a template typically takes
  2–5 seconds. This includes template clone (CoW), VM boot, and service
  startup.
- **Warm start**: Subsequent sandboxes from recently-used templates are
  faster (< 1s) due to VM pooling.
- **Execution latency**: `run_code` adds ~50–200ms round-trip overhead vs
  local execution, depending on network distance to the CubeSandbox node.

### Compatibility

- This MCP server uses the **Jupyter kernel mode** (`run_code`). It
  requires a template with the e2b code-interpreter service (port 49999).
  Plain envd-only templates will work for `run_command` but not `run_code`.
- The `cubesandbox` Python SDK is compatible with CubeSandbox v0.3.0+.
- Claude Code MCP support requires Claude Code v1.0.0+.

## Troubleshooting

| Symptom                                         | Likely Cause                        | Fix                                                                                     |
|-------------------------------------------------|-------------------------------------|-----------------------------------------------------------------------------------------|
| MCP server fails to start                       | Missing dependencies                | Run `pip install -r requirements.txt`; check Python 3.10+                               |
| `sandbox_create` hangs                          | CubeAPI unreachable                 | Verify `E2B_API_URL`; `curl http://<host>:3000/health`                                   |
| `sandbox_create` returns 404                    | Wrong template ID                   | Check `CUBE_TEMPLATE_ID`; run `cubemastercli tpl list`                                  |
| `sandbox_run_code` returns 502                  | Sandbox evicted (timeout/deleted)   | Increase `timeout`; check that sandbox isn't being killed by idle timeout               |
| `sandbox_run_code` returns "connection refused" | Jupyter gateway not ready           | Template must expose port 49999; wait 2-3s after create before first `run_code` call    |
| SSL certificate errors                          | Custom CA not trusted               | Set `CUBE_SSL_CERT_FILE` env var; or pass `verify=False` for testing (not for production)|
| Claude Code doesn't show sandbox tools          | MCP config error                    | Check `settings.json` syntax; run Claude Code with `--debug` to see MCP startup logs     |

## References

- Example code: [`examples/claude-code-sandbox/`](../../examples/claude-code-sandbox/)
- CubeSandbox Python SDK: [`sdk/python/`](../../sdk/python/)
- Claude Code MCP documentation: [docs.anthropic.com](https://docs.anthropic.com/en/docs/claude-code/mcp)
- MCP specification: [modelcontextprotocol.io](https://modelcontextprotocol.io)

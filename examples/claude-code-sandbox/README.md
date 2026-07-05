# Claude Code + CubeSandbox Integration

[中文文档](README_zh.md)

Give Claude Code a safe, isolated execution environment by routing code
execution through CubeSandbox MicroVMs via MCP (Model Context Protocol).

## What This Is

A **MCP Server** that exposes CubeSandbox sandbox operations as Claude Code
tools. When Claude Code needs to execute Python, run shell commands, or
manipulate files, it delegates the work to a hardware-isolated MicroVM
instead of running directly on your host.

```
Claude Code          MCP (stdio)        This Server       CubeSandbox
──────────          ──────────         ───────────       ───────────
"I'll analyze    →  sandbox_create  →  Sandbox.create() →  KVM MicroVM
 the data..."        sandbox_run_code   sandbox.run_code()   (isolated)
                     sandbox_read_file  sandbox.files.read() ↑ safe
```

> **Verification status**: This MCP Server has been reviewed against the
> CubeSandbox v0.4.0 / v0.5.0 Python SDK source and is believed to be API-correct,
> but has **not been tested end-to-end** against a running CubeSandbox deployment.
> Feedback, bug reports, and verification reports are welcome.

## Why Use This

| Without This                         | With This                                    |
|--------------------------------------|----------------------------------------------|
| Claude Code's `bash` tool runs commands directly on your host               | Code runs in hardware-isolated MicroVMs      |
| `rm -rf /` destroys your machine     | `rm -rf /` destroys only the sandbox VM      |
| `pip install` pollutes your system   | All packages installed in a disposable VM    |
| No snapshot / rollback               | Save sandbox state, clone, rollback anytime  |
| Network access to everything         | Per-sandbox network policy (allow/deny)      |

## Prerequisites

- **CubeSandbox deployed**: A running CubeSandbox instance on a Linux x86_64
  host with KVM (bare metal or cloud VM). **WSL2 is not supported** — see
  the [integration guide](../../docs/guide/integrations/claude-code.md) for details.
  See [Quick Start](https://cube-sandbox.pages.dev/guide/quickstart) for deployment.
- **A sandbox template**: Prebuilt with Python and your required tools. The
  standard `sandbox-code` image works for most use cases.
- **Claude Code** with MCP support (Claude Code v1.0.0+).
- **Python 3.10+** to run this MCP server.

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` and fill in your CubeSandbox connection details:

| Variable             | Description                                                    |
|----------------------|----------------------------------------------------------------|
| `E2B_API_URL`        | CubeAPI endpoint, e.g. `http://<cube-host>:3000`              |
| `E2B_API_KEY`        | CubeAPI auth key (`e2b_000000` for local/testing deployments)  |
| `CUBE_TEMPLATE_ID`   | Template ID for your sandbox images                            |
| `CUBE_SSL_CERT_FILE` | (Optional) Path to CubeSandbox CA bundle for HTTPS             |

### 3. Create a Sandbox Template

If you don't have a template yet:

```bash
cubemastercli tpl create-from-image \
  --image cube-sandbox-int.tencentcloudcr.com/cube-sandbox/sandbox-code:latest \
  --writable-layer-size 1G \
  --expose-port 49999 \
  --expose-port 49983 \
  --probe 49999
```

Copy the `template_id` from the output into your `.env`.

### 4. Register the MCP Server with Claude Code

Add this to your Claude Code settings:

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

Settings file location:
- Project-level: `.claude/settings.json`
- User-level: `~/.claude/settings.json`

### 5. Use It

Restart Claude Code (or reload MCP servers). Claude Code now has these tools:

| Tool                   | What It Does                           |
|------------------------|----------------------------------------|
| `sandbox_create`       | Create a new isolated sandbox          |
| `sandbox_run_code`     | Execute Python (Jupyter kernel)        |
| `sandbox_run_command`  | Run shell commands                     |
| `sandbox_read_file`    | Read files from the sandbox            |
| `sandbox_write_file`   | Write files into the sandbox           |
| `sandbox_get_info`     | Inspect sandbox metadata               |
| `sandbox_snapshot`     | Save a point-in-time snapshot          |
| `sandbox_pause`        | Pause the sandbox (preserves state)    |
| `sandbox_destroy`      | Destroy the sandbox                    |

Start a conversation with Claude Code and ask it to do something:

```
> Create a sandbox, then write a Python script that calculates the first
  100 Fibonacci numbers and saves them to fib.csv. Read the file back and
  verify the results.
```

Claude Code will call the sandbox tools step by step and report results.

## Tools Reference

### sandbox_create

Creates a hardware-isolated MicroVM. Must be called first.

```
Arguments:
  template (string, optional) — Template ID
  timeout  (integer, optional) — Sandbox lifetime in seconds (default: 600)

Returns: sandbox ID, template ID, state, timeout
```

### sandbox_run_code

Executes Python code in a persistent Jupyter kernel. Variables, imports,
and DataFrames persist across calls.

```
Arguments:
  code    (string, required) — Python source code
  timeout (integer, optional) — Execution timeout in seconds (default: 120)

Returns: stdout, stderr, text result, error traceback (if any)
```

### sandbox_run_command

Runs a shell command.

```
Arguments:
  command (string, required) — Shell command (passed to sh -lc)
  timeout (integer, optional) — Command timeout in seconds (default: 60)

Returns: exit code, stdout, stderr
```

### sandbox_read_file / sandbox_write_file

File I/O inside the sandbox. Use to provide input data and retrieve outputs.

### sandbox_snapshot

Captures the complete sandbox state (filesystem + memory). Snapshots survive
sandbox destruction and can be used to clone or rollback later.

### sandbox_pause

Pauses the sandbox, preserving its memory snapshot. The sandbox can be
resumed later. Useful for checkpointing long-running work between sessions.

### sandbox_destroy

Kills the sandbox immediately. All unsaved state is lost (snapshots
survive). Always call this when done to free resources.

## Caveats

### Sandbox Startup Latency

The first `sandbox_create` may take a few seconds (cold start). Subsequent
creates from the same template are faster due to VM pooling.

### Template Ports

- The MCP server uses port **49999** for `run_code` (Jupyter kernel gateway).
  Your template must expose this port.
- Port **49983** (envd) is used for `run_command` and file operations. Most
  templates expose this by default.

### Network / Egress

- By default, the sandbox can reach the public internet. Use `network`
  options in `Sandbox.create()` if you need to restrict this.
- If Claude Code accesses an LLM API from inside the sandbox (unusual, since
  LLM calls happen in the MCP server process on the host), you'll need to
  configure CubeEgress domain allowlists.

### File Size Limits

`sandbox_read_file` truncates output at 50,000 characters to avoid flooding
the Claude Code context window. For large files, use `sandbox_run_command`
with `head`/`tail`/`wc` to inspect incrementally.

### MCP vs Direct SDK

This MCP server is designed for **interactive use with Claude Code**. If
you're building an automated agent pipeline, consider calling the
`cubesandbox` Python SDK directly — it gives you full control over
sandbox lifecycle, concurrency, and error handling.

## Architecture

```
┌──────────────────┐
│   Claude Code     │  Anthropic's terminal coding agent
│   (on your host)  │
└────────┬─────────┘
         │ MCP protocol over stdio
         │
┌────────▼─────────┐
│   mcp_server.py   │  This file — MCP ↔ CubeSDK bridge
│   (this project)  │
└────────┬─────────┘
         │ cubesandbox Python SDK
         │ (pip install cubesandbox)
         │
┌────────▼─────────┐
│    CubeAPI        │  CubeSandbox REST API (:3000)
└────────┬─────────┘
         │ gRPC
┌────────▼─────────┐
│  CubeMaster /     │  Scheduler, node agent, hypervisor
│  Cubelet /        │
│  CubeHypervisor   │
└────────┬─────────┘
         │ KVM
┌────────▼─────────┐
│   MicroVM         │  Hardware-isolated sandbox
│   (Linux kernel)  │  ─ Python, Node.js, CLI tools
└──────────────────┘
```

## Troubleshooting

| Symptom                                    | Likely Cause                        | Fix                                                                         |
|--------------------------------------------|-------------------------------------|-----------------------------------------------------------------------------|
| `sandbox_create` returns "Connection refused" | CubeAPI not reachable              | Check `E2B_API_URL`; ensure CubeAPI is running (`curl http://host:3000/health`) |
| `sandbox_run_code` hangs                   | Jupyter gateway not ready           | Wait a few seconds after create; template may need port 49999 exposed        |
| `sandbox_run_command` returns "not found"  | Tool not installed in template      | Use `sandbox_run_command` with `apt install` or `pip install` as needed     |
| "Template not found"                       | Wrong or missing `CUBE_TEMPLATE_ID`  | Run `cubemastercli tpl list` and verify the ID                               |
| SSL errors                                 | Custom CA not trusted               | Set `CUBE_SSL_CERT_FILE` to the path of the CubeSandbox CA bundle            |

## Related Documents

- [CubeSandbox Claude Code Integration Guide](../../docs/guide/integrations/claude-code.md) — Full integration guide with best practices
- [CubeSandbox Quick Start](https://cube-sandbox.pages.dev/guide/quickstart)
- [CubeSandbox Python SDK](../sdk/python/README.md)
- [Claude Code MCP Documentation](https://docs.anthropic.com/en/docs/claude-code/mcp)

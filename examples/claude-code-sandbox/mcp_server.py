"""CubeSandbox MCP Server for Claude Code.

Exposes CubeSandbox sandbox operations as MCP tools so Claude Code can
safely execute code, run shell commands, and manage files inside
hardware-isolated MicroVMs — all through natural language.

Architecture::

    Claude Code  ←→  MCP (stdio)  ←→  this server  ←→  CubeSandbox API
                                                         (CubeAPI :3000)

Usage::

    pip install -r requirements.txt
    # Then configure Claude Code settings.json to point to this script.
    # See README.md for the full setup flow.

Environment variables (required)::

    CUBE_TEMPLATE_ID  — sandbox template ID
    E2B_API_URL       — CubeAPI endpoint, e.g. http://<host>:3000
    E2B_API_KEY       — CubeAPI auth key (any string for local deploys)
    CUBE_SSL_CERT_FILE — optional, path to CA bundle for cube HTTPS

Status:
    This MCP Server has been reviewed against the CubeSandbox v0.4.0 /
    v0.5.0 Python SDK source and is believed to be correct, but has not
    been tested end-to-end against a running CubeSandbox deployment.
    Feedback and bug reports are welcome.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Optional SSL patch for CubeSandbox HTTPS
# ---------------------------------------------------------------------------
_ssl_cert = os.environ.get("CUBE_SSL_CERT_FILE")
if _ssl_cert and Path(_ssl_cert).is_file():
    os.environ["SSL_CERT_FILE"] = _ssl_cert

# ---------------------------------------------------------------------------
# SDK import
# ---------------------------------------------------------------------------
try:
    from cubesandbox import Sandbox
except ImportError:
    raise SystemExit(
        "cubesandbox is not installed. Run: pip install cubesandbox"
    )

try:
    from mcp.server import Server, NotificationOptions
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
except ImportError:
    raise SystemExit("mcp is not installed. Run: pip install mcp")

# ---------------------------------------------------------------------------
# Global sandbox reference (one per MCP session)
# ---------------------------------------------------------------------------
_sandbox: Sandbox | None = None

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_int(name: str, value: object, default: int, *,
                  min_val: int = 1, max_val: int = 86_400) -> int:
    """Coerce *value* to int, falling back to *default* on failure."""
    if value is None:
        return default
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"{name} must be an integer, got {type(value).__name__}: {value!r}"
        )
    if not (min_val <= v <= max_val):
        raise ValueError(
            f"{name} must be between {min_val} and {max_val}, got {v}"
        )
    return v


def _validate_float(name: str, value: object, default: float, *,
                    min_val: float = 1.0, max_val: float = 3_600.0) -> float:
    """Coerce *value* to float, falling back to *default* on failure."""
    if value is None:
        return default
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"{name} must be a number, got {type(value).__name__}: {value!r}"
        )
    if not (min_val <= v <= max_val):
        raise ValueError(
            f"{name} must be between {min_val} and {max_val}, got {v}"
        )
    return v


def _require_str(name: str, value: object) -> str:
    """Require *value* to be a non-empty string."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"{name} must be a non-empty string, got {type(value).__name__}: {value!r}"
        )
    return value.strip()


# ---------------------------------------------------------------------------
# MCP Server setup
# ---------------------------------------------------------------------------
server = Server("cube-sandbox")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="sandbox_create",
            description=(
                "Create a new isolated sandbox environment. "
                "Must be called before any other sandbox_* tools. "
                "The sandbox is a hardware-isolated MicroVM with its own "
                "Linux kernel, preinstalled with Python, Node.js, and common "
                "CLI tools (depending on the template)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "template": {
                        "type": "string",
                        "description": (
                            "Template ID. If omitted, uses CUBE_TEMPLATE_ID "
                            "environment variable."
                        ),
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Sandbox lifetime in seconds (default: 600, min: 30, max: 86400).",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="sandbox_run_code",
            description=(
                "Execute Python code inside the sandbox via a persistent "
                "Jupyter kernel. Variables, imports, and DataFrames persist "
                "across calls within the same sandbox session. Use for data "
                "analysis, computation, plotting, or any Python work. "
                "For plain shell commands, use sandbox_run_command instead."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python source code to execute.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Execution timeout in seconds (default: 120, min: 1, max: 3600).",
                    },
                },
                "required": ["code"],
            },
        ),
        Tool(
            name="sandbox_run_command",
            description=(
                "Run a shell command inside the sandbox. "
                "Returns exit code, stdout, and stderr. "
                "Use for file inspection (ls/cat/head), package management "
                "(pip install / npm install), git operations, or any CLI tool."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute (passed to sh -lc).",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Command timeout in seconds (default: 60, min: 1, max: 3600).",
                    },
                },
                "required": ["command"],
            },
        ),
        Tool(
            name="sandbox_read_file",
            description=(
                "Read the contents of a file from the sandbox. "
                "Use to retrieve generated output, logs, or plot images. "
                "Output is truncated at 50,000 characters."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or relative path inside the sandbox.",
                    },
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="sandbox_write_file",
            description=(
                "Write content to a file inside the sandbox. "
                "Use to provide input data, scripts, or configuration files "
                "that subsequent run_code / run_command calls will use."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Target path inside the sandbox.",
                    },
                    "content": {
                        "type": "string",
                        "description": "File content to write.",
                    },
                },
                "required": ["path", "content"],
            },
        ),
        Tool(
            name="sandbox_get_info",
            description=(
                "Retrieve sandbox metadata: ID, state, CPU/memory, uptime, "
                "template ID. Useful for checking whether a sandbox is still "
                "running or for debugging connectivity issues."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="sandbox_snapshot",
            description=(
                "Create a point-in-time snapshot of the sandbox, preserving "
                "the complete filesystem and memory state. The snapshot "
                "survives sandbox destruction and can be used later to "
                "clone or rollback. Useful for saving long-running "
                "work before a pause or as a checkpoint."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Label for the snapshot (optional).",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="sandbox_pause",
            description=(
                "Pause the sandbox, preserving its memory snapshot. "
                "The sandbox can be resumed later via a new sandbox_create "
                "pointing to the snapshot. Use to save work and release "
                "compute resources between sessions."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="sandbox_destroy",
            description=(
                "Destroy the sandbox immediately, freeing all resources. "
                "All unsaved state is lost. Snapshots created via "
                "sandbox_snapshot survive independently."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
    ]


# ---------------------------------------------------------------------------
# Tool handler
# ---------------------------------------------------------------------------
@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    global _sandbox

    # ---- sandbox_create ------------------------------------------------
    if name == "sandbox_create":
        if _sandbox is not None:
            return [_text("⚠️ A sandbox is already active. Destroy it first.")]

        template = arguments.get("template") or os.environ.get("CUBE_TEMPLATE_ID")
        if not template:
            return [_text("❌ Missing template. Set CUBE_TEMPLATE_ID or pass template=.")]

        try:
            timeout = _validate_int("timeout", arguments.get("timeout"), 600,
                                   min_val=30, max_val=86_400)
        except ValueError as exc:
            return [_text(f"❌ Invalid argument: {exc}")]

        try:
            _sandbox = Sandbox.create(template=template, timeout=timeout)
        except Exception as exc:
            return [_text(f"❌ Failed to create sandbox: {exc}")]

        return [_text(
            f"✅ Sandbox created.\n"
            f"   ID:       {_sandbox.sandbox_id}\n"
            f"   Template: {_sandbox.template_id}\n"
            f"   State:    running\n"
            f"   Timeout:  {timeout}s"
        )]

    # ---- guard: sandbox must exist -------------------------------------
    if _sandbox is None:
        return [_text("❌ No active sandbox. Call sandbox_create first.")]

    # ---- sandbox_run_code ----------------------------------------------
    if name == "sandbox_run_code":
        try:
            code = _require_str("code", arguments.get("code"))
        except ValueError as exc:
            return [_text(f"❌ Invalid argument: {exc}")]

        try:
            timeout_val = _validate_float("timeout", arguments.get("timeout"),
                                          120.0, min_val=1.0, max_val=3_600.0)
        except ValueError as exc:
            return [_text(f"❌ Invalid argument: {exc}")]

        try:
            execution = _sandbox.run_code(code, timeout=timeout_val)
        except Exception as exc:
            return [_text(f"❌ Code execution failed: {exc}")]

        parts: list[str] = []
        # Safely extract structured output — each field is BestEffort.
        # On unexpected types we splice a short warning into the output
        # so callers can see that something went wrong, but the partial
        # result is still returned for debugging.
        try:
            if execution.logs and execution.logs.stdout:
                stdout_text = "".join(execution.logs.stdout).strip()
                if stdout_text:
                    parts.append(stdout_text)
        except Exception as _exc:
            parts.append(f"[mcp: error reading stdout: {_exc}]")
        try:
            if execution.logs and execution.logs.stderr:
                stderr_text = "".join(execution.logs.stderr).strip()
                if stderr_text:
                    parts.append(f"[stderr]\n{stderr_text}")
        except Exception as _exc:
            parts.append(f"[mcp: error reading stderr: {_exc}]")
        try:
            if execution.error:
                parts.append(
                    f"❌ {execution.error.name}: {execution.error.value}\n"
                    f"{execution.error.traceback[:2000] if execution.error.traceback else ''}"
                )
        except Exception as _exc:
            parts.append(f"[mcp: error reading execution.error: {_exc}]")
        try:
            if execution.text and execution.text.strip():
                parts.append(execution.text.strip())
        except Exception as _exc:
            parts.append(f"[mcp: error reading execution.text: {_exc}]")

        if not parts:
            return [_text("(executed, no output)")]
        return [_text("\n\n".join(parts))]

    # ---- sandbox_run_command -------------------------------------------
    if name == "sandbox_run_command":
        try:
            command = _require_str("command", arguments.get("command"))
        except ValueError as exc:
            return [_text(f"❌ Invalid argument: {exc}")]

        try:
            timeout_val = _validate_int("timeout", arguments.get("timeout"),
                                        60, min_val=1, max_val=3_600)
        except ValueError as exc:
            return [_text(f"❌ Invalid argument: {exc}")]

        try:
            result = _sandbox.commands.run(command, timeout=timeout_val)
        except Exception as exc:
            return [_text(f"❌ Command failed: {exc}")]

        output = ""
        if result.stdout:
            output += result.stdout.strip()
        if result.stderr:
            if output:
                output += "\n[stderr]\n"
            output += result.stderr.strip()
        return [_text(
            f"(exit={result.exit_code})\n{output}" if output
            else f"(exit={result.exit_code})"
        )]

    # ---- sandbox_read_file ---------------------------------------------
    if name == "sandbox_read_file":
        try:
            path = _require_str("path", arguments.get("path"))
        except ValueError as exc:
            return [_text(f"❌ Invalid argument: {exc}")]

        try:
            content = _sandbox.files.read(path)
        except Exception as exc:
            return [_text(f"❌ Failed to read {path!r}: {exc}")]

        # Guard against non-string returns (defensive — SDK returns str)
        if not isinstance(content, str):
            content = str(content)

        limit = 50_000
        if len(content) > limit:
            content = (
                content[:limit]
                + f"\n\n... (truncated {len(content) - limit:,d} bytes)"
            )
        return [_text(content)]

    # ---- sandbox_write_file --------------------------------------------
    if name == "sandbox_write_file":
        try:
            path = _require_str("path", arguments.get("path"))
            file_content = _require_str("content", arguments.get("content"))
        except ValueError as exc:
            return [_text(f"❌ Invalid argument: {exc}")]

        try:
            _sandbox.files.write(path, file_content.encode("utf-8"))
            return [_text(f"✅ Written {len(file_content):,d} bytes to {path!r}")]
        except Exception as exc:
            return [_text(f"❌ Failed to write {path!r}: {exc}")]

    # ---- sandbox_get_info ----------------------------------------------
    if name == "sandbox_get_info":
        try:
            info = _sandbox.get_info()
            lines = []
            for k in ("sandboxID", "templateID", "state", "cpuCount",
                       "memoryMB", "startedAt"):
                v = info.get(k)
                if v is not None:
                    lines.append(f"  {k}: {v}")
            if not lines:
                return [_text(f"(info returned, but no recognised keys in: {sorted(info.keys())!r})")]
            return [_text("\n".join(lines))]
        except Exception as exc:
            return [_text(f"❌ Failed to get info: {exc}")]

    # ---- sandbox_snapshot ----------------------------------------------
    if name == "sandbox_snapshot":
        # Explicit None check to avoid falsy string trap:
        # "0", "False", empty string are all valid snapshot names in the API.
        snap_name = arguments.get("name")
        if snap_name is not None and not isinstance(snap_name, str):
            return [_text(f"❌ snapshot 'name' must be a string, got {type(snap_name).__name__}")]
        if snap_name is not None and not snap_name.strip():
            snap_name = None  # Treat empty/whitespace-only as "no name"
        try:
            snap = _sandbox.create_snapshot(name=snap_name)
            # SnapshotInfo.names is list[str]
            names_str = ", ".join(snap.names) if snap.names else "(none)"
            return [_text(
                f"✅ Snapshot created.\n"
                f"   ID:    {snap.snapshot_id}\n"
                f"   Names: {names_str}"
            )]
        except Exception as exc:
            return [_text(f"❌ Snapshot failed: {exc}")]

    # ---- sandbox_pause -------------------------------------------------
    if name == "sandbox_pause":
        try:
            _sandbox.pause()
            return [_text(
                "✅ Sandbox paused. Use sandbox_create with the same "
                "sandbox ID to resume."
            )]
        except Exception as exc:
            return [_text(f"❌ Pause failed: {exc}")]

    # ---- sandbox_destroy -----------------------------------------------
    if name == "sandbox_destroy":
        try:
            sid = _sandbox.sandbox_id
        except Exception:
            sid = "<unknown>"
        try:
            _sandbox.kill()
        except Exception as exc:
            _sandbox = None
            return [_text(f"⚠️ Destroy may have partially failed: {exc}")]
        finally:
            _sandbox = None
        return [_text(f"✅ Sandbox {sid!r} destroyed.")]

    return [_text(f"❌ Unknown tool: {name!r}")]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _text(content: str) -> TextContent:
    return TextContent(type="text", text=content)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(
                notification_options=NotificationOptions(),
                experimental_capabilities={},
            ),
        )


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())

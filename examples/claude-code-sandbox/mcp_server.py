"""CubeSandbox MCP Server for Claude Code.

Exposes CubeSandbox sandbox operations as MCP tools so Claude Code can
safely execute code, run shell commands, and manage files inside
hardware-isolated MicroVMs.

Architecture::

    Claude Code  ←→  MCP (stdio)  ←→  this server  ←→  CubeSandbox API
                                                         (CubeAPI :3000)

Usage::

    pip install -r requirements.txt
    # Then configure Claude Code settings.json to point to this script.

Environment variables::

    CUBE_API_URL       — CubeAPI endpoint (default: http://127.0.0.1:3000)
    CUBE_TEMPLATE_ID   — sandbox template ID
    CUBE_SSL_CERT_FILE — optional, path to CA bundle for cube HTTPS

Status:
    This MCP Server has been reviewed against the CubeSandbox v0.5.0
    Python SDK source and is believed to be API-correct, but has not
    been tested end-to-end against a running CubeSandbox deployment.
"""

from __future__ import annotations

import asyncio
import atexit
import os
import signal
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# SDK import
# ---------------------------------------------------------------------------
try:
    from cubesandbox import Sandbox
except ImportError:
    raise SystemExit("cubesandbox is not installed. Run: pip install cubesandbox")

try:
    from mcp.server import Server, NotificationOptions
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
except ImportError:
    raise SystemExit("mcp is not installed. Run: pip install mcp")

# ---------------------------------------------------------------------------
# Per-session state (guarded by _lock)
# ---------------------------------------------------------------------------
_sandbox: Sandbox | None = None
_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Shutdown — kill sandbox on exit / SIGTERM / SIGINT
# ---------------------------------------------------------------------------
def _cleanup() -> None:
    """Best-effort sandbox kill on process exit."""
    sb = _sandbox
    if sb is not None:
        try:
            sb.kill()
        except Exception:
            pass


atexit.register(_cleanup)

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_int(name: str, value: object, default: int, *,
                  min_val: int = 1, max_val: int = 86_400) -> int:
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
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"{name} must be a non-empty string, "
            f"got {type(value).__name__}: {value!r}"
        )
    return value.strip()


def _sanitize_path(path: str) -> str:
    """Reject path traversal and absolute paths."""
    p = Path(path)
    if p.is_absolute():
        raise ValueError(f"Absolute paths not allowed: {path!r}")
    if ".." in p.parts:
        raise ValueError(f"Path traversal not allowed: {path!r}")
    return str(p)


def _sanitize_error(exc: BaseException) -> str:
    """Truncate error messages to prevent context flooding.

    Long error messages (e.g. HTML error pages from HTTP gateways) are
    truncated at 500 characters. Stack traces, internal URLs, and API
    response bodies are not explicitly stripped — only length-limited.
    Callers see a user-safe prefix of the original error text."""
    msg = str(exc).strip()
    if len(msg) > 500:
        msg = msg[:500] + "..."
    return msg


# ---------------------------------------------------------------------------
# MCP Server
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
                "across calls within the same sandbox session. "
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
                        "type": "number",
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
                "Output is truncated at 50,000 characters."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path inside the sandbox workspace.",
                    },
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="sandbox_write_file",
            description=(
                "Write content to a file inside the sandbox. "
                "Subsequent run_code / run_command calls can use the file."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path inside the sandbox workspace.",
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
                "template ID. Useful for debugging connectivity issues."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="sandbox_snapshot",
            description=(
                "Create a point-in-time snapshot of the sandbox, preserving "
                "the complete filesystem and memory state. The snapshot "
                "survives sandbox destruction and can be used to clone or "
                "rollback later."
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
                "The snapshot survives independently and can be accessed "
                "via snapshot management tools after the sandbox is paused."
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
# Tool handler  (all SDK calls go through asyncio.to_thread)
# ---------------------------------------------------------------------------
@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    global _sandbox

    # ---- sandbox_create ------------------------------------------------
    if name == "sandbox_create":
        async with _lock:
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
                _sandbox = await asyncio.to_thread(
                    Sandbox.create, template=template, timeout=timeout
                )
            except Exception as exc:
                return [_text(f"❌ Failed to create sandbox: {_sanitize_error(exc)}")]

            return [_text(
                f"✅ Sandbox created.\n"
                f"   ID:       {_sandbox.sandbox_id}\n"
                f"   Template: {_sandbox.template_id}\n"
                f"   State:    running\n"
                f"   Timeout:  {timeout}s"
            )]

    # ---- guard: sandbox must exist -------------------------------------
    async with _lock:
        sb = _sandbox
    if sb is None:
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
            execution = await asyncio.to_thread(sb.run_code, code, timeout=timeout_val)
        except Exception as exc:
            return [_text(f"❌ Code execution failed: {_sanitize_error(exc)}")]

        parts: list[str] = []
        stdout_text = "".join(execution.logs.stdout).strip()
        if stdout_text:
            parts.append(stdout_text)
        stderr_text = "".join(execution.logs.stderr).strip()
        if stderr_text:
            parts.append(f"[stderr]\n{stderr_text}")
        if execution.error:
            parts.append(
                f"❌ {execution.error.name}: {execution.error.value}\n"
                f"{execution.error.traceback[:2000] if execution.error.traceback else ''}"
            )
        if execution.text and execution.text.strip():
            parts.append(execution.text.strip())

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
            result = await asyncio.to_thread(
                sb.commands.run, command, timeout=timeout_val
            )
        except Exception as exc:
            return [_text(f"❌ Command failed: {_sanitize_error(exc)}")]

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
            path = _sanitize_path(path)
        except ValueError as exc:
            return [_text(f"❌ Invalid argument: {exc}")]

        try:
            content = await asyncio.to_thread(sb.files.read, path)
        except Exception as exc:
            return [_text(f"❌ Failed to read {path!r}: {_sanitize_error(exc)}")]

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
            path = _sanitize_path(path)
            file_content = _require_str("content", arguments.get("content"))
        except ValueError as exc:
            return [_text(f"❌ Invalid argument: {exc}")]

        # Reject files larger than 500KB to match the read truncation limit
        if len(file_content.encode("utf-8")) > 512_000:
            return [_text(
                f"❌ File too large: {len(file_content):,d} bytes "
                f"(max 512,000 bytes). Split into smaller chunks."
            )]

        try:
            await asyncio.to_thread(
                sb.files.write, path, file_content.encode("utf-8")
            )
            return [_text(f"✅ Written {len(file_content):,d} bytes to {path!r}")]
        except Exception as exc:
            return [_text(f"❌ Failed to write {path!r}: {_sanitize_error(exc)}")]

    # ---- sandbox_get_info ----------------------------------------------
    if name == "sandbox_get_info":
        try:
            info = await asyncio.to_thread(sb.get_info)
            lines = []
            for k in ("sandboxID", "templateID", "state", "cpuCount",
                       "memoryMB", "startedAt"):
                v = info.get(k)
                if v is not None:
                    lines.append(f"  {k}: {v}")
            if not lines:
                return [_text(
                    f"(info returned, no recognised keys in: "
                    f"{sorted(info.keys())!r})"
                )]
            return [_text("\n".join(lines))]
        except Exception as exc:
            return [_text(f"❌ Failed to get info: {_sanitize_error(exc)}")]

    # ---- sandbox_snapshot ----------------------------------------------
    if name == "sandbox_snapshot":
        snap_name = arguments.get("name")
        if snap_name is not None and not isinstance(snap_name, str):
            return [_text(
                f"❌ snapshot 'name' must be a string, "
                f"got {type(snap_name).__name__}"
            )]
        if snap_name is not None and not snap_name.strip():
            snap_name = None

        try:
            snap = await asyncio.to_thread(sb.create_snapshot, name=snap_name)
            names_str = ", ".join(snap.names) if snap.names else "(none)"
            return [_text(
                f"✅ Snapshot created.\n"
                f"   ID:    {snap.snapshot_id}\n"
                f"   Names: {names_str}"
            )]
        except Exception as exc:
            return [_text(f"❌ Snapshot failed: {_sanitize_error(exc)}")]

    # ---- sandbox_pause -------------------------------------------------
    if name == "sandbox_pause":
        try:
            await asyncio.to_thread(sb.pause)
            return [_text(
                "✅ Sandbox paused. The sandbox state has been preserved. "
                "Call sandbox_snapshot before pausing if you need an "
                "explicit snapshot for later cloning or rollback."
            )]
        except Exception as exc:
            return [_text(f"❌ Pause failed: {_sanitize_error(exc)}")]

    # ---- sandbox_destroy -----------------------------------------------
    if name == "sandbox_destroy":
        sid = "<unknown>"
        try:
            sid = sb.sandbox_id
        except Exception:
            pass
        async with _lock:
            try:
                sb.kill()
            except Exception as exc:
                _sandbox = None
                return [_text(f"⚠️ Destroy may have partially failed: {_sanitize_error(exc)}")]
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
async def _main_async() -> None:
    # Apply optional SSL patch inside main() to avoid import-time side effect.
    ssl_cert = os.environ.get("CUBE_SSL_CERT_FILE")
    if ssl_cert and Path(ssl_cert).is_file():
        os.environ["SSL_CERT_FILE"] = ssl_cert

    # Forward SIGTERM/SIGINT to asyncio for clean shutdown + atexit cleanup.
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda: None)
        except NotImplementedError:
            pass  # Windows — signal handlers not supported on ProactorEventLoop

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(
                notification_options=NotificationOptions(),
                experimental_capabilities={},
            ),
        )


def main() -> None:
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()

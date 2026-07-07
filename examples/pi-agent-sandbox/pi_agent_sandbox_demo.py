"""Pi Agent + CubeSandbox demo: run AI-generated code in isolated MicroVMs.

Usage::

    export CUBE_API_URL=http://<cube-host>:3000
    export CUBE_TEMPLATE_ID=<template-id>
    python pi_agent_sandbox_demo.py
"""

import os
from cubesandbox import Sandbox

API_URL = os.environ.get("CUBE_API_URL", "http://127.0.0.1:3000")
TEMPLATE_ID = os.environ["CUBE_TEMPLATE_ID"]


def run_code_in_sandbox(code: str, timeout: int = 120) -> dict:
    """Execute code in a CubeSandbox MicroVM and return results."""
    with Sandbox.create(template=TEMPLATE_ID, timeout=300) as sb:
        execution = sb.run_code(code, timeout=timeout)
        result = {
            "ok": execution.error is None,
            "text": execution.text or "",
            "stdout": "".join(execution.logs.stdout) if execution.logs else "",
            "stderr": "".join(execution.logs.stderr) if execution.logs else "",
        }
        if execution.error:
            result["error"] = str(execution.error.name) + ": " + str(execution.error.value)
        return result


if __name__ == "__main__":
    result = run_code_in_sandbox("print('Hello from CubeSandbox!')")
    print(result["stdout"])

# Pi Agent + CubeSandbox Integration

Run Pi Agent with code execution safely offloaded to CubeSandbox MicroVMs.

## What This Is

A bridge that lets Pi Agent execute code in hardware-isolated CubeSandbox
sandboxes instead of directly on the host. Follows the same "Sandbox as Tool"
pattern as the Claude Code integration.

## Prerequisites

- CubeSandbox deployed (see [Quick Start](https://cube-sandbox.pages.dev/guide/quickstart))
- A sandbox template with Python preinstalled
- Pi Agent installed locally
- Python 3.10+

## Quick Start

```bash
pip install cubesandbox
export CUBE_API_URL=http://<cube-host>:3000
export CUBE_TEMPLATE_ID=<your-template-id>

# Use cubesandbox SDK directly with Pi Agent
python pi_agent_sandbox_demo.py
```

## Architecture

```
Pi Agent (on host) → CubeSandbox SDK → CubeAPI → KVM MicroVM (sandbox)
```

## Related

- [CubeSandbox Claude Code Integration](../../../examples/claude-code-sandbox/)
- [CubeSandbox Pi Agent Issue](https://github.com/TencentCloud/CubeSandbox/issues/698)

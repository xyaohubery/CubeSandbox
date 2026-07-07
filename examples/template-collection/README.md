# CubeSandbox Template Collection

Ready-to-use Dockerfiles for common sandbox scenarios.

## Templates

| Template | Description | Size |
|----------|-------------|------|
| `python-data-science` | Python + pandas, numpy, matplotlib, scikit-learn | ~500MB |
| `nodejs-server` | Node.js 22 + npm, yarn, pnpm | ~300MB |
| `web-automation` | Python + Playwright + Chromium | ~1GB |
| `go-dev` | Go 1.23 + common tools | ~400MB |
| `rust-dev` | Rust toolchain + cargo | ~600MB |

## Usage

```bash
# Build image
cd python-data-science
docker build -t my-registry/sandbox-python:latest .

# Push
docker push my-registry/sandbox-python:latest

# Create template
cubemastercli tpl create-from-image \
  --image my-registry/sandbox-python:latest \
  --writable-layer-size 2G \
  --expose-port 49999 --expose-port 49983 \
  --probe 49999
```

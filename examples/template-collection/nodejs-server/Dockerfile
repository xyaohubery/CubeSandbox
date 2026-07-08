FROM cube-sandbox-int.tencentcloudcr.com/cube-sandbox/sandbox-code:latest

# Install Node.js 22 via NodeSource APT repo with pinned GPG key
# (avoids curl|bash pattern with no integrity check)
RUN apt-get update && apt-get install -y ca-certificates curl gnupg && \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | \
      gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" \
      > /etc/apt/sources.list.d/nodesource.list && \
    apt-get update && apt-get install -y nodejs && \
    npm install -g npm@latest yarn pnpm && \
    rm -rf /var/lib/apt/lists/*

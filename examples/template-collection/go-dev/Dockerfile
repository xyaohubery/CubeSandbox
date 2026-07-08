FROM cube-sandbox-int.tencentcloudcr.com/cube-sandbox/sandbox-code:latest

# Install Go 1.23 via golang-backports PPA
RUN apt-get update && apt-get install -y software-properties-common && \
    add-apt-repository -y ppa:longsleep/golang-backports && \
    apt-get update && apt-get install -y golang-go && \
    go version && \
    rm -rf /var/lib/apt/lists/*

FROM python:3.11-slim

# 安装依赖（包括 rust）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    build-essential \
    curl \
    pkg-config \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# 安装 rust
RUN curl https://sh.rustup.rs -sSf | sh -s -- -y \
    && . "$HOME/.cargo/env"

# 确保 cargo 在 PATH
ENV PATH="/root/.cargo/bin:${PATH}"

# 继续安装 Python 依赖
WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

COPY . .
CMD ["python", "main.py"]

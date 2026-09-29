# Docker

如果不想自己配置 Python，就用 Docker。每次合并到 `main` 以及每次发布版本标签时，镜像都会发布到 GitHub Container Registry。容器运行的是 `python make_book.py`，所以本站介绍的每个参数都原样可用。

## 镜像与标签

镜像有三个，由同一个 Dockerfile 构建。

| 标签 | 包含什么 | 平台 | 用途 |
|---|---|---|---|
| `latest`（也叫 `basic`） | 基于 `python:3.12-slim` 的翻译程序及其 Python 包；amd64 上约 280 MB | amd64、arm64 | EPUB、TXT、Markdown、SRT，以及走旧纯文本路线的 PDF |
| `pdf` | `latest` 加上 Pandoc 3.11 和 PDF 相关的包，PyTorch 用的是 CPU 版本；amd64 上约 2.4 GB | amd64、arm64 | 在处理器上运行 `--to-epub`：macOS、arm64 Linux，以及任何没有 NVIDIA 显卡的机器 |
| `pdf-cuda` | 官方 PyTorch CUDA 运行时镜像，加上 Pandoc 3.11、翻译程序和 PDF 相关的包；光是基础镜像就要下载 3.4 GB | amd64 | 在 NVIDIA GPU 上运行 `--to-epub`，Linux 或 Windows |
| `<version>`、`<version>-pdf`、`<version>-pdf-cuda` | 某个发布版本的这三个镜像 | | 固定到某个发布版本 |
| `sha-<commit>`、`sha-<commit>-pdf`、`sha-<commit>-pdf-cuda` | 某个提交的这三个镜像 | | 固定到某次构建 |

```bash
docker pull ghcr.io/yihong0618/bilingual_book_maker:latest
```

## 挂载

- **把你的书所在文件夹挂载到 `/book`。**以 `/book/<file>` 的形式传入书名。译好的书会写回同一个文件夹。
- **把一个命名卷挂载到 `/root/.cache`**（`pdf` 和 `pdf-cuda` 镜像）。docling 的模型在第一次 `--to-epub` 运行时下载到这里，约 500 MB。没有这个卷的话，每次运行都会重新下载。

容器以 root 身份运行，所以往挂载的文件夹里写入总是可以的。在 Linux 上，它写出的文件归 root 所有：事后 `chown` 一下，或者加上 `--user $(id -u)`。

key 请用环境变量传入，而不要写在命令行上：`-e OPENAI_API_KEY`。

## 按系统翻译 EPUB

=== "Linux / macOS"

    ```bash
    docker run --rm \
      -v "$PWD":/book \
      -e OPENAI_API_KEY \
      ghcr.io/yihong0618/bilingual_book_maker:latest \
      --book_name /book/my_book.epub \
      --language zh-hans \
      --use_context session
    ```

=== "Windows PowerShell"

    ```powershell
    docker run --rm `
      -v "${PWD}:/book" `
      -e OPENAI_API_KEY `
      ghcr.io/yihong0618/bilingual_book_maker:latest `
      --book_name /book/my_book.epub `
      --language zh-hans `
      --use_context session
    ```

一个完全不需要 key 的测试，走免费的 Google 路线：

```bash
docker run --rm \
  -v "$PWD":/book \
  ghcr.io/yihong0618/bilingual_book_maker:latest \
  --book_name /book/animal_farm.epub \
  --api_format google \
  --test \
  --test_num 1 \
  --language zh-hant
```

## 按系统翻译 PDF

`latest` 镜像里没有 Pandoc，也没有 PDF 相关的包，所以不能运行 `--to-epub`。有 NVIDIA 显卡请使用 `pdf-cuda` 标签，其他情况都用 `pdf`。

=== "带 NVIDIA 显卡的 Linux"

    在宿主机上安装 NVIDIA 驱动和 NVIDIA Container Toolkit，别的都不用装：镜像自带 CUDA 运行时。

    ```bash
    docker run --rm --gpus all \
      -v "$PWD":/book \
      -v bbm-models:/root/.cache \
      -e OPENAI_API_KEY \
      ghcr.io/yihong0618/bilingual_book_maker:pdf-cuda \
      --book_name /book/paper.pdf \
      --to-epub \
      --use_context session
    ```

=== "带 NVIDIA 显卡的 Windows"

    使用 **WSL2 后端**的 Docker Desktop。在 Windows 本身（而不是 WSL 里）安装支持 WSL 的 NVIDIA 驱动。Windows 容器模式访问不到 GPU。

    ```powershell
    docker run --rm --gpus all `
      -v "${PWD}:/book" `
      -v bbm-models:/root/.cache `
      -e OPENAI_API_KEY `
      ghcr.io/yihong0618/bilingual_book_maker:pdf-cuda `
      --book_name /book/paper.pdf `
      --to-epub `
      --use_context session
    ```

=== "仅 CPU"

    `pdf` 镜像带的是 CPU 版本的 PyTorch，amd64 和 arm64 都有。`--device cpu` 跳过加速器检测。输出和在 GPU 上完全一样，只是更慢。

    ```bash
    docker run --rm \
      -v "$PWD":/book \
      -v bbm-models:/root/.cache \
      -e OPENAI_API_KEY \
      ghcr.io/yihong0618/bilingual_book_maker:pdf \
      --book_name /book/paper.pdf \
      --to-epub \
      --device cpu \
      --use_context session
    ```

=== "macOS"

    无论传什么参数，容器都只用 CPU：Docker 运行的是一个看不到 Metal 的 Linux 虚拟机。`pdf` 镜像在 Apple 芯片（arm64）上原生运行。在那里，[本机安装](installation-pdf.md)更快（MPS）。

    ```bash
    docker run --rm \
      -v "$PWD":/book \
      -v bbm-models:/root/.cache \
      -e OPENAI_API_KEY \
      ghcr.io/yihong0618/bilingual_book_maker:pdf \
      --book_name /book/paper.pdf \
      --to-epub \
      --device cpu \
      --use_context session
    ```

## arm64 上的 NVIDIA 显卡

`pdf-cuda` 只为 amd64 构建：它所基于的官方 PyTorch CUDA 镜像只发布了这一种架构。在带显卡的 arm64 Linux 主机（GH200、Jetson）上，`pdf` 在处理器上运行。

## 所有镜像里都没有的

Codex 路线（`--api_format codex`）。它驱动的是你机器上已登录的 `codex` 程序；容器里既没有这个程序，也没有登录状态。在 Docker 里请使用 API 路线。

## 自己构建

直接构建得到的是小镜像：

```bash
docker build --tag bilingual_book_maker .
```

PDF 镜像是 `pdf` 和 `pdf-cuda` 阶段：

```bash
docker build --target pdf --tag bilingual_book_maker:pdf .
docker build --platform linux/amd64 --target pdf-cuda --tag bilingual_book_maker:pdf-cuda .
```

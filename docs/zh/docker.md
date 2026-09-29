# Docker

如果不想自己配置 Python，就用 Docker。每次合并到 `main` 以及每次发布版本标签时，镜像都会发布到 GitHub Container Registry。容器运行的是 `python make_book.py`，所以本站介绍的每个参数都原样可用。

## 镜像与标签

镜像有两个，基于 `python:3.12-slim` 由同一个 Dockerfile 构建，支持 `linux/amd64` 和 `linux/arm64`。

| 标签 | 包含什么 | 用途 |
|---|---|---|
| `latest`（也叫 `basic`） | 翻译程序及其 Python 包，几百 MB | EPUB、TXT、Markdown、SRT，以及走旧纯文本路线的 PDF |
| `pdf` | `latest` 加上 Pandoc 3.11 和 PDF 相关的包（docling 和 PyTorch；amd64 上是 CUDA 版本，有好几个 GB） | `--to-epub` |
| `<version>`、`<version>-pdf` | 某个发布版本的这两个镜像 | 固定到某个发布版本 |
| `sha-<commit>`、`sha-<commit>-pdf` | 某个提交的这两个镜像 | 固定到某次构建 |

```bash
docker pull ghcr.io/yihong0618/bilingual_book_maker:latest
```

## 挂载

- **把你的书所在文件夹挂载到 `/book`。**以 `/book/<file>` 的形式传入书名。译好的书会写回同一个文件夹。
- **把一个命名卷挂载到 `/root/.cache`**（`pdf` 镜像）。docling 的模型在第一次 `--to-epub` 运行时下载到这里，约 500 MB。没有这个卷的话，每次运行都会重新下载。

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

`latest` 镜像里没有 Pandoc，也没有 PDF 相关的包，所以不能运行 `--to-epub`。请使用 `pdf` 标签。

=== "带 NVIDIA 显卡的 Linux"

    在宿主机上安装 NVIDIA 驱动和 NVIDIA Container Toolkit，别的都不用装：PyTorch 的 wheel 自带 CUDA 运行时。

    ```bash
    docker run --rm --gpus all \
      -v "$PWD":/book \
      -v bbm-models:/root/.cache \
      -e OPENAI_API_KEY \
      ghcr.io/yihong0618/bilingual_book_maker:pdf \
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
      ghcr.io/yihong0618/bilingual_book_maker:pdf `
      --book_name /book/paper.pdf `
      --to-epub `
      --use_context session
    ```

=== "仅 CPU"

    同一个镜像也能在处理器上运行。`--device cpu` 跳过加速器检测。输出完全一样，只是更慢。

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

    无论传什么参数，容器都只用 CPU：Docker 运行的是一个看不到 Metal 的 Linux 虚拟机。镜像里带的还是 CUDA 版本的 PyTorch。在 Apple 芯片上，[本机安装](installation-pdf.md)既更快（MPS），体积也小得多。

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

## 只有 amd64 能用 GPU

镜像两种架构都有发布，但 PyPI 上的 PyTorch 只有 x86_64 版本是 CUDA 构建：

| torch 2.7.1，Linux | |
|---|---|
| `manylinux_2_28_x86_64` | 821.0 MB——CUDA 构建 |
| `manylinux_2_28_aarch64` | 98.9 MB——不含 CUDA 内核 |

因此，一台*确实*带显卡的 arm64 Linux 主机（GH200、Jetson）默认拉取的是 arm64 镜像，不管你传多少 `--gpus`，都在处理器上运行。在这种机器上请拉取 x86_64 镜像：

```bash
docker run --rm --platform linux/amd64 --gpus all \
  -v "$PWD":/book \
  -v bbm-models:/root/.cache \
  -e OPENAI_API_KEY \
  ghcr.io/yihong0618/bilingual_book_maker:pdf \
  --book_name /book/paper.pdf \
  --to-epub
```

## 两个镜像里都没有的

Codex 路线（`--api_format codex`）。它驱动的是你机器上已登录的 `codex` 程序；容器里既没有这个程序，也没有登录状态。在 Docker 里请使用 API 路线。

## 自己构建

直接构建得到的是小镜像：

```bash
docker build --tag bilingual_book_maker .
```

PDF 镜像是 `pdf` 阶段：

```bash
docker build --target pdf --tag bilingual_book_maker:pdf .
```

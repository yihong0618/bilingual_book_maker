# 安装

bilingual book maker 是一个 Python 程序，需要 Python 3.10 或更新版本。用虚拟环境可以把它和你的其他包隔开。

## 从 PyPI 安装

```bash
pip install -U bbook_maker
```

这样你就有了 `bbook_maker` 命令。它能翻译 EPUB、TXT、Markdown 和 SRT 文件，也能用旧的纯文本路线处理 PDF。

发布的包暂时还不带 PDF 转 EPUB 路线。如果需要它，请从仓库源码安装（见下文），再加上 [PDF 扩展](installation-pdf.md)。

## 从仓库源码安装

想用最新代码或 PDF 转 EPUB 路线，就从源码安装。

```bash
git clone https://github.com/yihong0618/bilingual_book_maker.git
```

```bash
cd bilingual_book_maker
```

```bash
pip install -r requirements.txt
```

`requirements.txt` 是锁定并测试过的一组版本。`pip install .` 也可以，装的是当下能解析到的版本。从源码运行时命令是 `python make_book.py`，参数与 `bbook_maker` 相同。

## PDF 扩展

如果要把 PDF 转成双语 EPUB（`--to-epub`），请在同一份源码里加装 PDF 扩展。它会带来 docling 和 PyTorch；模型（约 500 MB）在首次运行时下载，另外还需要 Pandoc 3.1.12 或更新版本。

```bash
pip install ".[pdf]"
```

这个扩展会复用你已经装好的 PyTorch，而不是另外下载一个锁定版本。上面这行命令适用于 Apple 芯片、带 NVIDIA GPU 的 Linux，以及不带 NVIDIA GPU 的 Windows。不带 NVIDIA GPU 的 Linux 要加上 PyTorch 的 CPU 索引，否则会下载约 3 GB 用不上的 CUDA；带 NVIDIA GPU 的 Windows 要加上 PyTorch 的 CUDA 索引。[安装 PDF 扩展](installation-pdf.md)列出了每种情况的命令。

不要运行 `pip install "bbook_maker[pdf]"`。发布的包还不带这条路线：pip 只会给出警告，然后安装不含它的发布版。

## Docker

如果不想安装 Python 包，就用发布的镜像，见 [Docker](docker.md)。

## 检查安装

```bash
bbook_maker --help
```

从源码运行时：

```bash
python make_book.py --help
```

每个参数都以帮助文本为准。[命令行参数](cmd.md)把它们列在一张表里。

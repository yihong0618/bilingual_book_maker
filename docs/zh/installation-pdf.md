# 安装 PDF 扩展

如果你想把 PDF 变成双语 EPUB（`--to-epub`），请在仓库的克隆里安装 PDF 扩展：

```bash
pip install ".[pdf]"
```

`--to-epub` 用 [docling](https://github.com/docling-project/docling) 的版面模型和表格模型读取 PDF，所以它需要的比基础安装多：PyTorch、模型，以及用来生成 EPUB 的 Pandoc。这些默认都不安装，因为大多数人翻译的是 EPUB，从来不碰 PDF。不需要 Java；提到 JRE 或 Adoptium 的说明都已过时。

## 1. 获取代码

这条路线还没有进入已发布的包，所以要从克隆安装。

```bash
git clone https://github.com/yihong0618/bilingual_book_maker.git
```

```bash
cd bilingual_book_maker
```

建议使用虚拟环境。

## 2. Pandoc

PATH 上需要有 Pandoc **3.1.12 或更新版本**。它负责生成 EPUB 及其导航。更早的版本会让目录指向文件而不是标题，工具会在打开 PDF 之前就拒绝它们。Ubuntu 24.04 和 Debian 13 上的 apt 包都比这个版本旧，所以请从 [pandoc.org/installing.html](https://pandoc.org/installing.html) 获取发布版。

```bash
pandoc --version
```

## 3. PDF 扩展

请安装扩展，而不是锁定版本的 requirements 文件：锁定文件把 PyTorch 和其他所有东西都钉在确切的版本上，所以即使你已经装了 torch，它们也会再下载一遍；扩展则复用已经装好的。

装到哪个 PyTorch 版本，取决于它来自哪个*索引*，而不是版本号。选择你的系统：

=== "macOS（Apple 芯片）"

    ```bash
    pip install ".[pdf]"
    ```

    你会得到 MPS 加速。没有什么要选的。

=== "带 NVIDIA 显卡的 Linux"

    ```bash
    pip install ".[pdf]"
    ```

    PyPI 上的 Linux wheel *就是* CUDA 版，所以这样就够了。不需要 CUDA Toolkit；wheel 自带运行时。

=== "Linux，只有 CPU"

    ```bash
    pip install ".[pdf]" \
        --extra-index-url https://download.pytorch.org/whl/cpu
    ```

    不加 CPU 索引，你会装上约 3.2 GB 用不上的 CUDA。加上它，约 380 MB，而且没有 `nvidia-*` 包。

=== "带 NVIDIA 显卡的 Windows"

    ```bat
    pip install ".[pdf]" ^
        --extra-index-url https://download.pytorch.org/whl/cu126
    ```

    **PyPI 上的 Windows wheel 只支持 CPU。** Windows 的 CUDA 版只发布在 PyTorch 自己的索引上，所以普通的那行命令会让你悄无声息地停留在处理器上。更新的显卡和驱动可以用 `cu128`。CUDA wheel 约 2.7 GB。

    你还需要 NVIDIA 驱动。PyTorch 自带 CUDA 运行时，所以你**不**需要 CUDA Toolkit，但驱动要你自己安装：

    - 驱动下载：<https://www.nvidia.com/en-us/drivers/>（Game Ready 或 Studio 都可以）。
    - PyTorch 的安装矩阵，用来确认适合你显卡的渠道：<https://pytorch.org/get-started/locally/>。

    安装 PyTorch 之前先检查驱动：

    ```bat
    nvidia-smi
    ```

    它会打印驱动版本和它支持的最高 CUDA 版本。如果这个数字低于你选的渠道（`cu126` 对应 12.6），就更新驱动。在有显卡的机器上 `torch.cuda.is_available()` 却返回 `False`，通常就是驱动太旧。

=== "Windows，只有 CPU"

    ```bat
    pip install ".[pdf]"
    ```

    在 Windows 上这会给你 CPU 版，因为 PyPI 在那里发布的就是它。

请用 `--extra-index-url`，不要用 `--index-url`。`--index-url` 会*替换*掉 PyPI，这个工具需要的其他所有包就都解析不到了。

如果你想要与项目测试时完全相同的版本，`requirements-pdf-gpu.txt` 和 `requirements-pdf-cpu.txt` 就是 Docker 镜像安装的那两套锁定版本（`pdf` 用 CPU 那份，`pdf-cuda` 用 GPU 那份、去掉基础镜像里已有的 PyTorch；`pip install -r requirements-pdf-cpu.txt` 的文件里已经写明了 CPU 索引）。它们会替换掉你已有的 PyTorch。

### 使用 uv

```bash
uv pip install ".[pdf]"
```

想要 CPU 版时：

```bash
uv pip install ".[pdf]" --torch-backend=cpu
```

## 4. 运行

先读两页。`--test` 只翻译少数几段。

```bash
python make_book.py \
  --book_name paper.pdf \
  --to-epub \
  --pages 1-2 \
  --test
```

然后完整运行：

```bash
python make_book.py \
  --book_name paper.pdf \
  --to-epub \
  --use_context session
```

模型（约 500 MB）在第一次运行时下载，所以第一份 PDF 会比第二份明显慢一些。下载期间进度行会一直在走。模型来自 Hugging Face，所以可以用 `HF_HOME` 换个缓存位置：

```bash
export HF_HOME=/path/with/room
```

用 `--pdf-ocr` 读取扫描件不会再下载别的东西：pdf 扩展会连同模型一起安装 rapidocr，在 Mac 上还会安装 Apple 的引擎 ocrmac，`auto` 在那里会选它。[选择 OCR 引擎](features/pdf-ocr-engines.md)对它们做了比较。

模型缓存好以后，提取就不需要网络了。[PDF 推荐设置](features/recommended-pdf.md)按文档类型给出了对应的命令。

## 检查装到了什么

```bash
python -c "import torch; print(torch.__version__, torch.version.cuda)"
```

- 版本号以 `+cpu` 结尾，后面是 `None`：CPU 版。走 CPU 路线时这是对的。
- 类似 `12.6` 的 CUDA 版本：CUDA 版。这时 `torch.cuda.is_available()` 会告诉你这台机器能否用上它。`False` 表示这个版本带有 CUDA，但没有可用的显卡，或者驱动太旧。
- 在 Apple 芯片上，`None` 是对的：MPS 不是 CUDA。用 `python -c "import torch; print(torch.backends.mps.is_available())"` 检查它。
- 在带 NVIDIA 显卡的 Windows 上，`None` 表示你装的是 CPU 版，PyPI 在那里发布的就是它。回到第 3 步。

`--device cuda` 能把这两种失败区分开，因为它们的解决办法不同：只支持 CPU 的版本要重新安装；没有显卡的机器则不是重装能解决的。

## 不克隆直接安装：暂时不行

`pip install "bbook_maker[pdf]"` **不会**安装这条路线，也不会报错。pip 把未知的扩展当作警告处理，安装不带 docling 的最新发布版，然后以 0 退出：

```text
WARNING: bbook-maker 1.2.1 does not provide the extra 'pdf'
Successfully installed bbook-maker-1.2.1
```

下一次处理 PDF 时，运行会以缺少扩展的信息拒绝。在某个发布版包含这条路线之前，请克隆仓库（第 1 步），并从克隆安装 `".[pdf]"`。

## 保留 CPU 版

`torch==…+cpu` 满足任何 `torch>=…` 的要求，所以没有什么会强制替换它。但下一次不带索引、又涉及 PyTorch 的 `pip install -U` 会拉取 CUDA wheel，带进约 3 GB 的内容。让这个索引固定在环境里：

```bash
export PIP_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cpu
```

或者写进 `pip.conf` / `pip.ini`：

```ini
[global]
extra-index-url = https://download.pytorch.org/whl/cpu
```

uv 的对应写法是 `UV_TORCH_BACKEND=cpu`。

## 或者全部跳过：Docker

`pdf` 镜像标签带有 Pandoc 和完整的 docling 运行环境，PyTorch 用的是 CPU 版本：`docker pull ghcr.io/yihong0618/bilingual_book_maker:pdf`。有 NVIDIA 显卡的话，用 `pdf-cuda` 标签。见 [Docker 安装](docker.md)，其中也解释了为什么 Mac 应该直接在本机安装。

## 大小

PDF 这一步在 PyTorch 2.7.1 下要下载的内容，按各平台分别解析（102 个包）：

| | 下载量 |
|---|---|
| macOS，Apple 芯片 | **~270 MB** |
| Linux x86_64，CPU 版 | **~380 MB** |
| Linux x86_64，CUDA 版 | **~3.2 GB** |
| Windows x86_64，PyPI（CPU 版） | **~420 MB** |
| Windows x86_64，`cu126` | **~2.9 GB** |

另外第一次运行时还有 **~500 MB 的模型**，所有平台都一样。

差异主要来自 PyTorch。依赖树的其余部分在各平台上都约为 200 MB（opencv 48 MB，scipy 29 MB，rapidocr 27 MB，transformers、numpy、pandas……）。单看 PyTorch wheel：

| | torch 2.7.1 wheel |
|---|---|
| macOS arm64 | 68.6 MB |
| Linux x86_64，`+cpu` | 175.8 MB，其元数据中**没有**声明任何 `nvidia-*` 依赖 |
| Linux x86_64，PyPI 默认 | 821.0 MB，**另加 ~2.16 GB** 的 `nvidia-*` 和 `triton` wheel |
| Windows x86_64，PyPI | 216.0 MB（CPU 版） |
| Windows x86_64，`cu126` | 2.72 GB |

两个平台恰好相反，这正是陷阱所在：在 Linux 上默认是 CUDA，要主动*退出*；在 Windows 上默认是 CPU，要主动*加入*。macOS 没有 CUDA 版本；它那个小小的 PyTorch 带的是 Metal 内核，没有 CUDA，也不缺任何东西。

## 如果不能用

下面是工具会打印的信息，以及该怎么做。

- **`reading a PDF needs the pdf extra, which is not installed.`** 第 3 步没有做。如果你运行的是 `pip install "bbook_maker[pdf]"`，而它说安装成功了，那就是上面说的陷阱。
- **`Pandoc is required for --to-epub. Install it and make sure pandoc is on PATH.`** 做第 2 步。
- **`… is too old for EPUB export; Pandoc 3.1.12 or newer is required`** 你的 Pandoc 来自 apt。从 pandoc.org 安装发布版（第 2 步），并把它放在 PATH 的最前面；这条信息还提到了另一种办法：分步脚本 `tools/pdf_to_book.py --pandoc PATH`。
- **`--device cuda was asked for, but the installed PyTorch is a CPU-only build.`** 用 CUDA 版重新安装扩展：Linux 上用普通的那行命令，Windows 上用 `cu126` 索引（第 3 步）。
- **`--device cuda was asked for, but this machine has no cuda accelerator available.`** 这个版本带有 CUDA，但机器或驱动提供不了。运行 `nvidia-smi`：没有输出说明没有驱动；CUDA 版本低于你的渠道说明驱动太旧。否则就用 `--device cpu`。
- **在 Windows 上，机器明明有显卡，`torch.version.cuda` 却是 `None`。** 用的是普通的那行命令。指定 `cu126` 索引重新安装（第 3 步）。
- **`… selected pages have no text layer …; rerun with --pdf-ocr …`** 这份 PDF 是扫描件。加上 `--pdf-ocr`；如果扫描件不是中文或英文，再加上 `--ocr-lang`。见 [PDF 推荐设置](features/recommended-pdf.md)。
- **在 Docker 里，ARM 机器上的 `--gpus all` 好像被忽略了。** 确实如此：arm64 镜像里的 PyTorch 只支持 CPU。加上 `--platform linux/amd64`。
- **在 Mac 上的 Docker 里，GPU 从来用不上。** 这是对的，也没法解决：Linux 虚拟机看不到 Metal。要用 MPS，请直接在本机安装（第 1 到 4 步）。

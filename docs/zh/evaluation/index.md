# 参数背后的测量

这里每一页测量一个参数：比较了什么、用的是哪个样本、得出了哪些数字。默认值与它所依据的测量一起给出，每页最后是“局限”：样本规模，以及没有测量的内容。每个数字都照抄自该测量自己的记录。

## 翻译

### [每次请求的单元数与 token 数](grouping-batch-size.md)

`--max-batch-units`、`--accumulated_num`。合并请求从哪里开始把译文错放到别的位置（测得的起点：64 个单元），以及每种大小下重试开销的代价。默认每个请求 16 个单元、1200 到 1600 token；没有严格 schema 时为 8 个单元、800 token。

### [会话压缩预算](session-compact-budget.md)

`--context-compact-at`。300 个单元上 4096 与 8192 两档的会话成本，并统计了压缩次数。默认 8192，是为本地模型上的连续性而选；4096 用的 token 更少。

### [用 Jev 做计划分类器](plan-classifier-jev.md)

`--classify-model`、`--classify-min-confidence`。TypeSafe 的 Jev 与 gpt-5.6-luna 在一本书的 31 个签名上的对比（31 个中有 27 个一致），精简请求前后 Jev 的提示 token，以及在 45 本 EPUB 的 662 个签名上测得的置信度闸门（0.95）。默认：翻译模型。

## PDF 转 EPUB

### [扫描页上的 OCR 引擎](pdf-ocr-engines.md)

`--ocr-engine`。rapidocr、ocrmac、easyocr 和 tesseract 在同样 20 张扫描页上的字符错误率和每页耗时，均在 CPU 上。英文用 tesseract 和 ocrmac，简体中文用 rapidocr，繁体用 ocrmac 或 rapidocr。默认 `auto`。

### [用图像模型判定区域角色](pdf-structure-llm-roles.md)

`--img-model`。85 页上的 123 个结构错误，其中哪些能靠视觉模型重新标注区域修正（66 个错误标签中的 40 个），哪些不能（页眉、支离破碎的 OCR 页面）。每页约 3,000 个提示 token。除非指定模型，否则关闭。

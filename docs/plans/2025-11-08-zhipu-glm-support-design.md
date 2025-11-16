# 智谱AI (GLM) 模型支持设计方案

**日期：** 2025-11-08
**状态：** 已批准

## 概述

为bilingual_book_maker添加智谱AI的GLM系列模型支持，使用OpenAI兼容接口实现。

## 设计目标

- 支持智谱AI的GLM系列模型（GLM-4-Flash等）
- 复用现有的CLI参数设计（--model, --api_base）
- 与项目现有架构保持一致
- 提供免费模型选项（GLM-4-Flash）

## 架构设计

### 核心组件

1. **新建翻译器类**：`book_maker/translator/zhipu_translator.py`
   - 继承自 `Base` 翻译器
   - 使用OpenAI Python SDK调用兼容接口
   - API Base: `https://open.bigmodel.cn/api/paas/v4/`

2. **集成方式**：独立文件（与xai、groq模式一致）

### 支持的模型

| 模型标识 | 模型名称 | 说明 |
|---------|---------|------|
| glm | GLM-4-Flash | 默认模型，通用入口 |
| glm-4-flash | GLM-4-Flash | 高性能免费模型 |
| glm-4-air | GLM-4-Air | 轻量级模型 |
| glm-4-airx | GLM-4-AirX | 增强版轻量模型 |
| glm-4-plus | GLM-4-Plus | 最强模型（付费） |
| glm-4-0520 | GLM-4-0520 | 特定版本 |

## 参数设计

### 新增参数

- **命令行参数**：`--glm_key`
  - 用途：传递智谱API密钥
  - 示例：`--glm_key sk-xxx`

- **环境变量**：`BBM_GLM_API_KEY`
  - 优先级：环境变量 < 命令行参数

### 复用参数

- **--model / -m**：选择模型（已存在）
- **--api_base**：自定义API地址（已存在）
- **--temperature**：控制输出随机性（已存在）
- **--prompt**：自定义翻译提示词（已存在）
- **--proxy**：HTTP代理设置（已存在）

## 实现清单

### 1. 新建文件

**文件**：`book_maker/translator/zhipu_translator.py`

**类设计**：
```python
class ZhipuTranslator(Base):
    - API Base默认：https://open.bigmodel.cn/api/paas/v4/
    - 默认模型：GLM-4-Flash
    - 支持密钥轮换
    - 支持自定义prompt
    - 支持温度参数
    - 错误重试机制（最多3次）
```

### 2. 修改文件

**文件**：`book_maker/translator/__init__.py`
- 导入 `ZhipuTranslator`
- 在 `MODEL_DICT` 添加6个模型映射

**文件**：`book_maker/cli.py`
- 添加 `--glm_key` 参数定义（约第193行）
- 在 model choices 添加6个GLM模型（约第214行）
- 添加GLM API密钥处理逻辑（约第479行）
- 添加GLM模型设置逻辑（约第601行）

## 错误处理

1. **API密钥缺失**：抛出异常并提示用户
2. **模型名称无效**：回退到GLM-4-Flash + 警告
3. **API调用失败**：重试3次，失败返回原文
4. **网络超时**：60秒超时，超时后重试
5. **速率限制**：捕获429错误，等待后重试

## 使用示例

```bash
# 使用命令行参数
python make_book.py --book_name book.epub -m glm-4-flash --glm_key sk-xxx

# 使用环境变量
export BBM_GLM_API_KEY=sk-xxx
python make_book.py --book_name book.epub -m glm

# 使用自定义API地址
python make_book.py --book_name book.epub -m glm-4-flash --glm_key sk-xxx --api_base https://custom-url

# 使用代理
python make_book.py --book_name book.epub -m glm --glm_key sk-xxx --proxy http://127.0.0.1:7890

# 测试模式
python make_book.py --book_name book.epub -m glm --glm_key sk-xxx --test --test_num 5
```

## 兼容性

- 支持所有现有功能：--test, --resume, --proxy, --temperature等
- 与现有翻译器接口完全兼容
- 不影响其他翻译器的功能

## 测试计划

1. 基本翻译功能测试
2. 不同模型切换测试
3. 错误处理测试（无效密钥、网络错误等）
4. 参数组合测试（proxy、temperature、prompt等）
5. 多密钥轮换测试

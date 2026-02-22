---
name: meeting-summary-skill
description: 处理会议转写工具导出的 .md 格式转写文件，清洗 ASR 噪声，逐段调用大模型生成会议纪要，输出为 Markdown 文件。当用户提供会议转写文件、要求整理会议内容或生成会议纪要时使用。
---

# 会议纪要生成

## 工作流程

复制以下清单并跟踪进度：

```
- [ ] 第一步：检查文件扩展名
- [ ] 第二步：运行格式校验
- [ ] 第三步：生成会议纪要
- [ ] 第四步：确认输出
```

**第一步：检查文件扩展名**

确认用户提供的文件扩展名为 `.md`，否则告知用户不支持该格式，终止流程。

**第二步：运行格式校验**

```bash
python scripts/validate.py <会议转写.md路径>
```

- 校验通过 → 继续第三步
- 校验失败 → 读取 [assets/会议转写模板.md](assets/会议转写模板.md)，将模板内容连同错误原因一起展示给用户，引导其修正格式或重新上传正确文件，终止流程

**第三步：生成会议纪要**

```bash
python scripts/process.py <会议转写.md路径>
```

实时显示进度（如 `进度：3/7 段`），完成后自动写入纪要文件。

**第四步：确认输出**

告知用户生成文件的完整路径（位于原文件同目录）。

## 脚本说明

**process.py**：清洗 ASR 噪声、逐段调用大模型、写入纪要文件

```bash
python scripts/process.py ./某次会议转写.md
# 生成：./某次会议转写_会议纪要.md
```

**validate.py**：单独校验文件格式，可在运行 process.py 前独立使用

```bash
python scripts/validate.py ./某次会议转写.md
# 输出：校验通过 或 具体错误信息
```

依赖 `openai`、`python-dotenv`，API Key 通过 `~/.../llm-abilities-toolkit/.env` 自动加载。

## 参考资料

- 转写文件模板：[会议转写模板.md](assets/会议转写模板.md)

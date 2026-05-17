---
name: meeting-summary-skill
description: 处理会议转写工具导出的 .md 格式转写文件，清洗 ASR 噪声，逐段生成会议纪要，输出为 Markdown 文件。当用户提供会议转写文件、要求整理会议内容或生成会议纪要时使用。
---

# 会议纪要生成

## 工作流程

复制以下清单并跟踪进度：

```
- [ ] 第一步：检查文件扩展名
- [ ] 第二步：运行格式校验
- [ ] 第三步：运行 prepare.py，初始化输出文件
- [ ] 第四步：并发启动所有子 Agent
- [ ] 第五步：校验临时文件数量，缺失则重试
- [ ] 第六步：运行 finalize.py，合并并清理
- [ ] 第七步：确认输出
```

### 第一步：检查文件扩展名

确认用户提供的文件扩展名为 `.md`，否则告知用户不支持该格式，终止流程。

### 第二步：运行格式校验

```bash
python scripts/validate.py <会议转写.md路径>
```

- 校验通过 → 继续第三步
- 校验失败 → 读取 [assets/会议转写模板.md](assets/会议转写模板.md)，将模板内容连同错误原因一起展示给用户，引导其修正格式或重新上传正确文件，终止流程

### 第三步：运行 prepare.py，初始化输出文件

输出文件默认生成在会议转写文件的同级目录下，无需询问用户路径。直接运行：

```bash
python scripts/prepare.py <会议转写.md路径>
```

脚本输出 JSON，解析后得到三个值，后续步骤会用到：

- `total`：段落总数（即需要启动子 Agent 的次数）
- `source_path`：原始转写文件的完整路径（已被清洗回写）
- `output_path`：已初始化的会议纪要文件路径（已写入基本信息和 `## 会议纪要` 标题）

同时根据 `output_path` 推导出临时文件的命名规则：每段对应一个临时文件，路径为 `output_path` 同目录下的 `{output_stem}_temp_{index}.md`。

### 第四步：并发启动所有子 Agent

同时为 `1` 到 `total` 的每个段落各启动一个独立子 Agent，等待所有子 Agent 回复完成后进入第五步。

每个子 Agent 收到的任务如下，启动前将 `{index}`、`{source_path}`、`{temp_path}` 替换为实际值（`{temp_path}` = `{output_stem}_temp_{index}.md` 的完整路径）：

---

你是一位会议纪要助手，请处理第 **{index}** 段会议转写，依次完成以下两步。

**A. 获取本段内容**

运行以下命令并解析 JSON 输出，其中 `meeting_info` 为会议基本信息，`speech` 为本段转写原文（含 `### 段落标题`）：

```bash
python scripts/get_section.py {index} {source_path}
```

**B. 生成本段会议纪要并写入临时文件**

根据 `meeting_info` 和 `speech`，按以下要求总结本段内容：

1. 先理解会议背景（主题、参与人、目的），再阅读转写原文
2. 结合会议背景修正 ASR 识别错误（人名、公司名、专业术语等）
3. 提取与会议主题相关的实质性观点，忽略口语废话、重复表达和无关闲谈
4. 保留原段落标题（`### xxx`）不变，在标题下方逐行列出观点
5. 每个观点使用数字序号（1. 2. 3.），语言简洁，使用书面语

输出格式：

```
### 原段落标题
1. 观点一
2. 观点二
```

生成完成后，将纪要内容写入临时文件 `{temp_path}`，然后向主 Agent 回复：`第 {index} 段已完成`，不要输出纪要内容。

---

### 第五步：校验临时文件数量，缺失则重试

检查 `output_path` 同目录下 `{output_stem}_temp_*.md` 文件的数量是否等于 `total`：

- 数量一致 → 继续第六步
- 数量不足 → 找出缺失的段落编号，对每个缺失段落重新启动子 Agent 补写（使用第四步相同的任务模板）

**重试规则**：对每个缺失段落单独计数，最多重试 3 次。若某段落在 3 次尝试后临时文件仍不存在，告知用户该段落编号并终止流程。

### 第六步：运行 finalize.py，合并并清理

```bash
python scripts/finalize.py {output_path} {total}
```

脚本将所有临时文件按 index 顺序合并写入 `output_path`，并自动删除全部临时文件。

### 第七步：确认输出

告知用户生成文件的完整路径（即 `output_path`）。

## 脚本说明

**prepare.py**（脚本1）：清洗 ASR 噪声、回写源文件、解析分段，初始化输出文件，返回段落总数和路径信息

```bash
python scripts/prepare.py ./某次会议转写.md
# 输出：JSON（total、source_path、output_path）
# 副作用1：将 ASR 噪声清洗后的内容回写至源文件
# 副作用2：创建 ./某次会议转写_会议纪要.md（含基本信息和 ## 会议纪要 标题）
```

**get_section.py**（脚本2）：按编号返回指定段落的基本信息和转写原文，供子 Agent 使用

```bash
python scripts/get_section.py 2 ./某次会议转写.md
# 输出：JSON（section_index、total、meeting_info、speech）
```

**finalize.py**（脚本3）：按 index 顺序合并所有临时文件至输出文件，并删除临时文件

```bash
python scripts/finalize.py ./某次会议转写_会议纪要.md 5
# 副作用：合并 5 个临时文件至输出文件，删除全部临时文件
```

**validate.py**：单独校验文件格式，可在运行 prepare.py 前独立使用

```bash
python scripts/validate.py ./某次会议转写.md
# 输出：校验通过 或 具体错误信息
```

## 参考资料

- 转写文件模板：[会议转写模板.md](assets/会议转写模板.md)

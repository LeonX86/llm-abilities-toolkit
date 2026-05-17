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
- [ ] 第四步：逐段启动子 Agent
- [ ] 第五步：确认输出
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

### 第四步：逐段启动子 Agent

按 `1` 到 `total` 的顺序，依次为每个段落启动一个独立子 Agent（顺序执行，等待1个子 Agent 报告完成任务之后才启动下一个，避免并发写入冲突）。

每个子 Agent 收到的任务如下，启动前将 `{index}`、`{source_path}`、`{output_path}` 替换为实际值：

---

你是一位会议纪要助手，请处理第 **{index}** 段会议转写，依次完成以下三步。

**A. 获取本段内容**

运行以下命令并解析 JSON 输出，其中 `meeting_info` 为会议基本信息，`speech` 为本段转写原文（含 `### 段落标题`）：

```bash
python scripts/get_section.py {index} {source_path}
```

**B. 生成本段会议纪要**

根据 `meeting_info` 和 `speech`，按以下要求总结本段内容：

1. 先理解会议背景（主题、参与人、目的），再阅读转写原文
2. 结合会议背景修正 ASR 识别错误（人名、公司名、专业术语等）
3. 提取与会议主题相关的实质性观点，忽略口语废话、重复表达和无关闲谈
4. 保留原段落标题（`### xxx`）不变，在标题下方逐行列出观点
5. 每个观点使用数字序号（1. 2. 3.），语言简洁，使用书面语

输出格式（直接输出纪要内容，禁止输出其他任何无关内容）：

```
### 原段落标题
1. 观点一
2. 观点二
```

**C. 将纪要写入输出文件**

将 B 步生成的纪要内容作为第二个参数传入，多行内容中的换行用 `\n` 表示，写入 `{output_path}`：

```bash
python scripts/write_section.py {output_path} "### 原段落标题\n1. 观点一\n2. 观点二"
```

完成后只需向主 Agent 回复：`第 {index} 段已完成`，不要输出纪要内容。

---

### 第五步：确认输出

所有子 Agent 完成后，告知用户生成文件的完整路径（即 `output_path`）。

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

**write_section.py**（脚本3）：将子 Agent 生成的纪要文本作为参数追加写入输出文件，`\n` 会被转换为真实换行

```bash
python scripts/write_section.py ./某次会议转写_会议纪要.md "### 段落标题\n1. 观点一\n2. 观点二"
# 副作用：将第二个参数的内容追加到会议纪要文件末尾
```

**validate.py**：单独校验文件格式，可在运行 prepare.py 前独立使用

```bash
python scripts/validate.py ./某次会议转写.md
# 输出：校验通过 或 具体错误信息
```

## 参考资料

- 转写文件模板：[会议转写模板.md](assets/会议转写模板.md)

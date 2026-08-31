---
name: opencode-config-updater
description: 备份并修改 opencode 全局配置文件（opencode.jsonc / opencode.json）中 modelscope provider 的模型列表：查询 models.dev 的模型信息并按模板写入。只要用户提到添加/新增/更新/更换/替换/删除 opencode 的模型或模型配置（如"opencode 添加 Qwen/Qwen3.5-27B"、"opencode 只保留 xx 模型"、"帮我更新 opencode 的模型配置"、"给 opencode 换个模型"）就使用本 skill。仅支持 modelscope provider，不适用于查询 API 价格或修改其他工具的配置。
---

# opencode-config-updater

备份并修改 opencode 全局配置文件中 `provider.modelscope` 的模型列表：根据用户给出的 ModelScope 模型 id 查询 models.dev 的模型信息，按固定模板写入配置，最后提醒用户重启 opencode。**只操作 `provider.modelscope`**；用户要求改其他 provider，或只是想查 API 价格时，本 skill 不适用。配置文件已存在时，所有修改仅限于 `provider.modelscope` 节点内部——文件中已存在的 mcp、其他 provider 等任何配置和 JSONC 注释都禁止改动。

修改方式：**查询、备份、校验交给脚本，改文件由你用 Read + Edit 直接做文本级修改**。这是保住用户 JSONC 注释与既有格式的唯一方式，禁止用脚本重写整个配置文件。

## 触发与模式判定

| 用户表达 | 模式 | 行为 |
|---|---|---|
| "添加 xx 模型"、"新增 xx"、"再加个 xx" | `add` | 保留现有全部内容，新增该模型（已存在则刷新其条目） |
| "更新 xx 模型的配置/信息" | `add` | 保留其他模型，重新查询并覆盖该模型条目 |
| "只保留 xx"、"只留 xx"、"删掉其他模型只要 xx" | `replace` | 清空原有 whitelist 和 models，只写入指定模型 |

多个模型一次处理：一次查询、一次备份、一次修改。`replace` 会删掉原有模型条目（有备份可恢复），语义拿不准时先问一句再执行。

## 工作流程

### 1. 解析用户的模型 id

- 用户给的是 ModelScope 的完整原始 id（形如 `Qwen/Qwen3.8-Flash-Next`）。**原样使用，禁止改写**：大小写、组织前缀、拼写都以用户输入为准。ModelScope 的 id 与 models.dev / 厂商官方 id 存在差异是正常的，模型要生效就必须把完整原始 id 同时写进 `whitelist` 和 `models`。
- id 必须包含 `/`；用户只给了裸名称时，先向用户确认完整 id 再继续。

### 2. 查询模型信息

```bash
python "<本skill目录>/scripts/query_model_info.py" "Qwen3.8-Flash-Next" --json
```

- 传 id 斜杠后面的裸名称（脚本按它匹配 models.dev，自动吸收两边组织前缀的差异）。
- 返回 `null`（退出码 1）→ 整个模型未查到，未做任何修改。把 id 告诉用户并询问：确认拼写后重试；或用户手工提供字段（此时按"配置文件格式"的模板条目手工构造，只能含模板字段）；或放弃。
- 退出码 3 → 网络错误，告知用户稍后重试。
- 从 JSON 结果中记下模板字段（name/tool_call/reasoning/temperature/cost/limit/modalities）哪些缺失——稍后写入时省略并告知用户。结果里的其他信息（description、benchmarks、open_weights、provider_id 等）仅供展示，禁止写入配置。

### 3. 定位并备份配置文件

```bash
python "<本skill目录>/scripts/backup_config.py"
```

- `action=backed_up`：记下 `config_path` 和 `backup_path`，进入第 4 步修改。
- `action=missing`：没有配置文件。按"配置文件格式"的模板新建 `opencode.jsonc`（whitelist 和 models 里直接填好新模型），用 Write 写入 `create_dir` 指向的目录，然后跳到第 5 步校验。
- `parse_ok=false`：现有配置本身解析失败，告知用户并停止（除非用户明确坚持）。
- 无论哪种情况，备份都先于任何修改。

### 4. 直接修改配置文件（保留注释与格式）

用 Read 读取 `config_path`，用 Edit 做文本级修改：

- `add` 模式：在 `provider.modelscope.whitelist` 数组追加新 id（已存在则不动）；在 `models` 对象里新增该 id 的条目；若该 id 已有条目则原位替换那一段文本（刷新信息）。
- `replace` 模式：`whitelist` 与 `models` 只保留指定 id，删除其他模型条目对应的文本段（可连同该条目的专属注释一起删除）。
- **JSONC 注释、缩进、键顺序一律保持原样**；mcp、其他 provider、theme 等其他配置一个字符都不许动；`provider.modelscope`（或其中的 whitelist/models）不存在时才新增该节点。
- 条目内容从第 2 步的查询结果构造，只允许模板字段，查不到的字段直接省略。

### 5. 校验修改结果

```bash
python "<本skill目录>/scripts/validate_config.py" --mode add --require "Qwen/Qwen3.8-Flash-Next" --backup "<backup_path>"
```

（replace 用 `--mode replace`；新建配置时没有 `--backup`；多个 id 重复传 `--require`）

- `status=ok`：向用户报告配置文件路径、备份文件路径，以及每个模型的 `missing_fields`（逐项告知用户哪些字段没查到、已在配置中省略）。
- `status=invalid`：按 `errors` 修复文件后重新校验，直到通过；反复修不好就把 errors 原文告知用户并停止。
- 校验器强制保证：JSON 合法；`provider.modelscope` 之外与备份零差异；add 模式注释零增删、原有模型原样保留；条目无模板外字段；add/replace 语义正确。

### 6. 提醒重启

修改完成后必须明确提醒用户：**重启 opencode（完全退出后重新打开）配置才会生效**。

## 硬性规则

- 配置文件已存在时，所有修改**仅限于 `provider.modelscope` 节点内部**：已存在的 mcp、其他 provider、theme、keybinds 等任何其他配置一律禁止改动（校验器比对备份强制保证）。
- 已有配置必须先用 backup_config.py 备份；修改一律用文本编辑（Read + Edit），**必须原样保留用户保存的 JSONC 注释和既有格式**，禁止用脚本或整文件重写的方式改配置。
- 修改完成后必须运行 validate_config.py，校验不通过不得向用户交付。
- 每个模型条目只允许模板字段，**禁止写入任何其他字段**（open_weights、release_date、attachment、provider、structured_output 等查询结果里的其他信息一律不要）。
- 整个模型查不到 → 询问用户（见第 2 步）；个别字段查不到 → 直接省略该字段并告知用户。
- 配置文件路径由脚本解析，不要自己猜路径。

## 脚本参考

| 脚本 | 用途 | 退出码 |
|---|---|---|
| `query_model_info.py` | 查 models.dev 模型信息（`--json` 机器可读） | 0 查到 / 1 未查到 / 3 网络错误 |
| `backup_config.py` | 定位配置文件并备份（`action=backed_up/missing`） | 0 成功 / 4 备份失败 |
| `validate_config.py` | 校验修改结果（`errors` 给出失败原因） | 0 通过 / 3 找不到配置 / 4 校验失败 |

- 配置目录按顺序尝试：`$XDG_CONFIG_HOME/opencode` → `~/.config/opencode`（Windows 即 `%USERPROFILE%\.config\opencode`，例如 `C:\Users\Leon\.config\opencode`）。
- 目录内依次查找 `opencode.jsonc`、`opencode.json`；两者都在时修改 `opencode.jsonc`。
- 备份命名：`<原文件名>.bak-YYYYMMDDTHHMMSS`，如 `opencode.jsonc.bak-20260829T143022`，保存在配置文件同目录。
- JSONC 注释（含 `//` 与 `/* */`）和尾随逗号在解析时兼容，但文件内容必须原样保留注释；`https://` 这类字符串里的 `//` 不会被误认为注释。
- 所有脚本都支持 `--config-dir` 覆盖配置目录（调试/测试用）。

## 配置文件格式

新建配置时使用此模板（同 `assets/opencode.template.jsonc`），把新模型条目填进 `models`、完整原始 id 填进 `whitelist` 后写入：

```jsonc
{
    "$schema": "https://opencode.ai/config.json",
    "provider": {
        "modelscope": {
            "whitelist": [],
            "models": {}
        }
    }
}
```

`models` 里每个模型条目的形态（字段齐全时；查不到的项直接省略）：

```json
{
    "name": "Qwen3.5 27B",
    "tool_call": true,
    "reasoning": true,
    "temperature": true,
    "cost": {"input": 0.26, "output": 2.09, "cache_read": 0.00, "cache_write": 0.00},
    "limit": {"context": 262144, "output": 65536},
    "modalities": {"input": ["text", "image"], "output": ["text"]}
}
```

这些字段的值全部来自 models.dev 查询结果：`name/tool_call/reasoning/temperature/cost/limit/modalities`。

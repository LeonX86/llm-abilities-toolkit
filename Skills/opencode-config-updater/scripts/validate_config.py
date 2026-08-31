#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验 agent 直接文本编辑后的 opencode 配置文件。

在修改配置文件之后运行，强制保证：
    1. 文件仍是合法的 JSON/JSONC；
    2. provider.modelscope 之外（mcp、其他 provider、theme 等）与备份完全一致；
    3. add 模式下 .jsonc 的注释一条不少、一条不多（不允许动用户保存的注释），
       原有模型的 whitelist 项与条目原样保留；
    4. 模型条目字段不超出模板允许范围（禁止多填）；
    5. add 模式必含指定 id；replace 模式 whitelist/models 恰好等于指定 id
       （replace 允许连同被删模型条目一起删除其注释，故不检查注释）。

用法:
    python validate_config.py --mode add --require "Qwen/Qwen3.8-Flash-Next" --backup <bak文件>
    python validate_config.py --mode replace --require "ZhipuAI/GLM-5.1" --backup <bak文件>
    python validate_config.py                                # 只做解析与结构检查
    python validate_config.py --config-file <路径> [--backup <路径>]

输出: stdout JSON（errors 给出失败原因；entries 给出每个条目缺失的模板字段）
退出码: 0 校验通过; 3 找不到配置文件; 4 校验失败
"""

import argparse
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config_common import config_dir_candidates, locate_config, parse_jsonc_file

TEMPLATE_MODEL_FIELDS = ("name", "tool_call", "reasoning", "temperature", "cost", "limit", "modalities")
COST_KEYS = ("input", "output", "cache_read", "cache_write")
LIMIT_KEYS = ("context", "output")
MODALITY_KEYS = ("input", "output")
MAX_DIFF_PATHS = 8


def without_modelscope(d):
    """去掉 provider.modelscope 节点后的配置副本，用于越界比对。"""
    d = copy.deepcopy(d)
    provider = d.get("provider")
    if isinstance(provider, dict):
        provider.pop("modelscope", None)
        if not provider:
            d.pop("provider")
    return d


def diff_paths(modified, original, prefix="$"):
    """递归找出 modified 相对 original 的差异路径，用于错误信息定位。"""
    out = []
    if isinstance(modified, dict) and isinstance(original, dict):
        for k in sorted(set(modified) | set(original)):
            p = f"{prefix}.{k}"
            if k not in original:
                out.append(f"{p} (备份中不存在)")
            elif k not in modified:
                out.append(f"{p} (被删除)")
            else:
                out.extend(diff_paths(modified[k], original[k], p))
    elif modified != original:
        out.append(f"{prefix} 值被改动")
    return out


def check_entry_shape(model_id, entry, errors):
    """条目字段必须都在模板白名单内；返回缺失的模板字段（供告知用户，不算错误）。"""
    if not isinstance(entry, dict):
        errors.append(f"{model_id} 的条目必须是 JSON 对象")
        return []
    extra = [k for k in entry if k not in TEMPLATE_MODEL_FIELDS]
    if extra:
        errors.append(f"{model_id} 含模板外字段，禁止多填: {', '.join(extra)}")
    missing = [k for k in TEMPLATE_MODEL_FIELDS if k not in entry]
    if isinstance(entry.get("cost"), dict):
        missing = [m for m in missing if m != "cost"]
        missing += [f"cost.{k}" for k in COST_KEYS if k not in entry["cost"]]
    if isinstance(entry.get("limit"), dict):
        missing = [m for m in missing if m != "limit"]
        missing += [f"limit.{k}" for k in LIMIT_KEYS if k not in entry["limit"]]
    if isinstance(entry.get("modalities"), dict):
        missing = [m for m in missing if m != "modalities"]
        missing += [f"modalities.{k}" for k in MODALITY_KEYS if k not in entry["modalities"]]
    return missing


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="校验 opencode 配置修改结果")
    parser.add_argument("--mode", choices=("add", "replace"), default="add",
                        help="add=保留原有并新增；replace=只保留指定模型")
    parser.add_argument("--require", dest="required", action="append", default=[],
                        help="本次应写入的完整 model id，可重复传多个")
    parser.add_argument("--backup", help="修改前的备份文件路径，用于越界与注释比对")
    parser.add_argument("--config-file", help="直接指定配置文件路径（默认自动定位）")
    parser.add_argument("--config-dir", help="覆盖默认的 opencode 配置目录（调试/测试用）")
    args = parser.parse_args()
    required = list(dict.fromkeys(args.required))

    errors = []

    if args.config_file:
        config_path = Path(args.config_file)
    else:
        config_path, _ = locate_config(config_dir_candidates(args.config_dir))
    if config_path is None or not config_path.is_file():
        print(json.dumps({"status": "invalid", "config_path": None,
                          "errors": ["未找到 opencode 配置文件"], "entries": []},
                         ensure_ascii=False, indent=2))
        sys.exit(3)

    try:
        current, cur_comments = parse_jsonc_file(config_path)
    except json.JSONDecodeError as exc:
        print(json.dumps({"status": "invalid", "config_path": str(config_path),
                          "errors": [f"配置文件不是合法的 JSON/JSONC: {exc}"], "entries": []},
                         ensure_ascii=False, indent=2))
        sys.exit(4)

    # 结构检查
    provider = current.get("provider")
    ms = provider.get("modelscope") if isinstance(provider, dict) else None
    if not isinstance(ms, dict) or not isinstance(ms.get("whitelist"), list) \
            or not isinstance(ms.get("models"), dict):
        errors.append("provider.modelscope 结构不完整（需要 whitelist 列表和 models 对象）")
        ms = None
    whitelist = ms.get("whitelist") if ms else []
    models = ms.get("models") if ms else {}

    # 必含 id / replace 语义
    for rid in required:
        if args.mode == "add":
            if rid not in whitelist:
                errors.append(f"whitelist 缺少 {rid}")
            if rid not in models:
                errors.append(f"models 缺少 {rid}")
    if args.mode == "replace":
        if sorted(whitelist) != sorted(required):
            errors.append(f"replace 模式下 whitelist 应恰好为 {required}，实际 {whitelist}")
        if sorted(models) != sorted(required):
            errors.append(f"replace 模式下 models 应恰好为 {required}，实际 {list(models)}")

    # 条目字段（禁止多填；缺字段记录下来供告知用户）
    entries_info = []
    for mid, entry in models.items():
        missing = check_entry_shape(mid, entry, errors)
        entries_info.append({"model_id": mid, "missing_fields": missing})

    # 与备份比对：越界改动、注释增删、add 模式原有内容保留
    if args.backup:
        backup_path = Path(args.backup)
        if not backup_path.is_file():
            errors.append(f"备份文件不存在: {args.backup}")
        else:
            try:
                backup, bak_comments = parse_jsonc_file(backup_path)
            except json.JSONDecodeError as exc:
                errors.append(f"备份文件解析失败: {exc}")
                backup = None
            if backup is not None:
                if without_modelscope(backup) != without_modelscope(current):
                    diffs = diff_paths(without_modelscope(current), without_modelscope(backup))
                    errors.append("provider.modelscope 之外的配置被改动（禁止，必须与备份一致）: "
                                  + "; ".join(diffs[:MAX_DIFF_PATHS]))
                if args.mode == "add":
                    if sorted(bak_comments) != sorted(cur_comments):
                        errors.append("JSONC 注释与备份不一致（add 模式不允许删除或新增注释）")
                    bak_provider = backup.get("provider")
                    bak_ms = bak_provider.get("modelscope") if isinstance(bak_provider, dict) else {}
                    bak_ms = bak_ms if isinstance(bak_ms, dict) else {}
                    bak_models = bak_ms.get("models", {})
                    bak_wl = bak_ms.get("whitelist", [])
                    if isinstance(bak_models, dict):
                        for mid, entry in bak_models.items():
                            if mid not in required and models.get(mid) != entry:
                                errors.append(f"原有模型 {mid} 的条目被改动（add 模式应原样保留）")
                    for wid in bak_wl:
                        if wid not in whitelist:
                            errors.append(f"原 whitelist 项 {wid} 被删除（add 模式应保留）")

    result = {
        "status": "invalid" if errors else "ok",
        "config_path": str(config_path),
        "mode": args.mode,
        "entries": entries_info,
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(4 if errors else 0)


if __name__ == "__main__":
    main()

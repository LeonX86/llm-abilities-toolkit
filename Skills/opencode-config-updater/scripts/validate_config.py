#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验 agent 直接文本编辑后的 opencode 配置文件。

在修改配置文件之后运行，强制保证：
    1. 文件仍是合法的 JSON/JSONC；
    2. provider.modelscope 之外（mcp、其他 provider、theme 等）与备份完全一致；
    3. 模型条目字段不超出模板允许范围（禁止多填）；
    4. 模式语义：
       - add：必含 --require 的 id；注释零增删；原有模型的条目与 whitelist 项原样保留；
       - swap：必含 --require 的新 id，--remove 的旧 id 必须已从 whitelist/models 消失，
         且旧 id 在备份中存在；除被删/新增的 id 外，注释、条目、whitelist 项按 add 的严格度保留；
       - replace：whitelist/models 恰好等于 --require（允许删除被删模型条目的注释，故不检查注释）。
    5. entries 中区分本次新出现的缺失字段（missing_fields_new）与修改前就缺失的字段
       （missing_fields_pre_existing），避免把历史缺字段误报成本次改动引入。

用法:
    python validate_config.py --mode add --require "Qwen/Qwen3.8-Flash-Next" --backup <bak文件>
    python validate_config.py --mode swap --require "deepseek-ai/DeepSeek-V4.1-Flash-1017" \
        --remove "deepseek-ai/DeepSeek-V4.1-Flash" --backup <bak文件>
    python validate_config.py --mode replace --require "ZhipuAI/GLM-5.1" --backup <bak文件>
    python validate_config.py                                # 只做解析与结构检查
    python validate_config.py --config-file <路径> [--backup <路径>]

输出: stdout JSON（errors 给出失败原因；entries 给出每个条目的缺失字段及新旧归属）
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


def collect_missing(entry):
    """返回条目缺失的模板字段（含嵌套子字段），不产生错误。"""
    if not isinstance(entry, dict):
        return []
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


def check_entry_shape(model_id, entry, errors):
    """条目字段必须都在模板白名单内；返回缺失的模板字段（供告知用户，不算错误）。"""
    if not isinstance(entry, dict):
        errors.append(f"{model_id} 的条目必须是 JSON 对象")
        return []
    extra = [k for k in entry if k not in TEMPLATE_MODEL_FIELDS]
    if extra:
        errors.append(f"{model_id} 含模板外字段，禁止多填: {', '.join(extra)}")
    return collect_missing(entry)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="校验 opencode 配置修改结果")
    parser.add_argument("--mode", choices=("add", "replace", "swap"), default="add",
                        help="add=保留原有并新增；swap=删旧增新；replace=只保留指定模型")
    parser.add_argument("--require", dest="required", action="append", default=[],
                        help="本次应写入的完整 model id，可重复传多个")
    parser.add_argument("--remove", dest="removed", action="append", default=[],
                        help="swap 模式要删除的完整 model id，可重复传多个")
    parser.add_argument("--backup", help="修改前的备份文件路径，用于越界与注释比对")
    parser.add_argument("--config-file", help="直接指定配置文件路径（默认自动定位）")
    parser.add_argument("--config-dir", help="覆盖默认的 opencode 配置目录（调试/测试用）")
    args = parser.parse_args()
    required = list(dict.fromkeys(args.required))
    removed = list(dict.fromkeys(args.removed))

    errors = []

    if removed and args.mode != "swap":
        errors.append("--remove 仅用于 swap 模式")

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

    # 目标 id / 各模式语义
    for rid in required:
        if args.mode in ("add", "swap"):
            if rid not in whitelist:
                errors.append(f"whitelist 缺少 {rid}")
            if rid not in models:
                errors.append(f"models 缺少 {rid}")
    if args.mode == "swap":
        if not required:
            errors.append("swap 模式必须用 --require 指定要写入的新 id")
        if not removed:
            errors.append("swap 模式必须用 --remove 指定要删除的旧 id")
        for rid in sorted(set(required) & set(removed)):
            errors.append(f"{rid} 不能同时出现在 --require 和 --remove 中")
        for rid in removed:
            if rid in whitelist:
                errors.append(f"whitelist 仍包含应删除的旧 id {rid}")
            if rid in models:
                errors.append(f"models 仍包含应删除的旧 id {rid}")
    if args.mode == "replace":
        if sorted(whitelist) != sorted(required):
            errors.append(f"replace 模式下 whitelist 应恰好为 {required}，实际 {whitelist}")
        if sorted(models) != sorted(required):
            errors.append(f"replace 模式下 models 应恰好为 {required}，实际 {list(models)}")

    # 先解析备份（用于区分缺失字段是新引入还是历史存在）
    backup = None
    bak_comments = []
    bak_ms = {}
    bak_models = {}
    bak_wl = []
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
                bak_provider = backup.get("provider")
                bak_ms = bak_provider.get("modelscope") if isinstance(bak_provider, dict) else {}
                bak_ms = bak_ms if isinstance(bak_ms, dict) else {}
                bak_models = bak_ms.get("models", {})
                bak_models = bak_models if isinstance(bak_models, dict) else {}
                bak_wl = bak_ms.get("whitelist", [])
                bak_wl = bak_wl if isinstance(bak_wl, list) else []

    # 条目字段（禁止多填；缺字段记录并标注是否本次新引入）
    entries_info = []
    for mid, entry in models.items():
        missing = check_entry_shape(mid, entry, errors)
        new_missing = missing
        pre_existing = []
        if mid in bak_models and isinstance(bak_models[mid], dict):
            bak_missing = set(collect_missing(bak_models[mid]))
            pre_existing = [f for f in missing if f in bak_missing]
            new_missing = [f for f in missing if f not in bak_missing]
        entries_info.append({
            "model_id": mid,
            "missing_fields": missing,
            "missing_fields_new": new_missing,
            "missing_fields_pre_existing": pre_existing,
        })

    # 与备份比对：越界改动、注释增删、未涉及内容保留
    if backup is not None:
        if without_modelscope(backup) != without_modelscope(current):
            diffs = diff_paths(without_modelscope(current), without_modelscope(backup))
            errors.append("provider.modelscope 之外的配置被改动（禁止，必须与备份一致）: "
                          + "; ".join(diffs[:MAX_DIFF_PATHS]))

        if args.mode in ("add", "swap"):
            if sorted(bak_comments) != sorted(cur_comments):
                errors.append(f"JSONC 注释与备份不一致（{args.mode} 模式不允许删除或新增注释）")
            for mid, entry in bak_models.items():
                if mid in required:
                    continue
                if args.mode == "swap" and mid in removed:
                    continue
                if models.get(mid) != entry:
                    errors.append(f"原有模型 {mid} 的条目被改动（{args.mode} 模式应原样保留）")
            for wid in bak_wl:
                if args.mode == "swap" and wid in removed:
                    continue
                if wid not in whitelist:
                    errors.append(f"原 whitelist 项 {wid} 被删除（{args.mode} 模式应保留）")

        if args.mode == "swap":
            for rid in removed:
                if rid not in bak_wl:
                    errors.append(f"要删除的旧 id {rid} 不在备份 whitelist 中")
                if rid not in bak_models:
                    errors.append(f"要删除的旧 id {rid} 不在备份 models 中")
            expected_wl = sorted(set(bak_wl) - set(removed) | set(required))
            actual_wl = sorted(set(whitelist))
            if actual_wl != expected_wl:
                errors.append(f"swap 模式 whitelist 终态不符，应为 {expected_wl}，实际 {actual_wl}")
            expected_models = sorted(set(bak_models) - set(removed) | set(required))
            actual_models = sorted(models)
            if actual_models != expected_models:
                errors.append(f"swap 模式 models 终态不符，应为 {expected_models}，实际 {actual_models}")

    result = {
        "status": "invalid" if errors else "ok",
        "config_path": str(config_path),
        "mode": args.mode,
        "required": required,
        "removed": removed,
        "entries": entries_info,
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(4 if errors else 0)


if __name__ == "__main__":
    main()
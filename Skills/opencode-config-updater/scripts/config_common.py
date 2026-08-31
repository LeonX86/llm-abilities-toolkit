#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""opencode 配置文件定位与 JSONC 解析的共享工具。

被 backup_config.py / validate_config.py 引用；query_model_info.py 保持自包含。
"""

import json
import os
from pathlib import Path

CONFIG_FILENAMES = ("opencode.jsonc", "opencode.json")


def config_dir_candidates(config_dir_override=None):
    """opencode 全局配置目录候选列表，按优先级排序。

    opencode 在所有平台上都使用 ~/.config/opencode（Windows 即
    %USERPROFILE%\\.config\\opencode）；设置了 XDG_CONFIG_HOME 时优先用它。
    """
    if config_dir_override:
        return [Path(config_dir_override)]
    candidates = []
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        candidates.append(Path(xdg) / "opencode")
    candidates.append(Path.home() / ".config" / "opencode")
    return candidates


def locate_config(candidates):
    """在候选目录中查找现有配置文件。返回 (路径或 None, 新建配置应使用的目录)。"""
    for d in candidates:
        for name in CONFIG_FILENAMES:
            p = d / name
            if p.is_file():
                return p, d
    return None, candidates[0]


def _comment_ranges(text):
    """找出全部注释的 (起, 止) 位置；字符串内部的 // 与 /* */ 不算注释。"""
    ranges = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        c = text[i]
        if in_string:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_string = False
            i += 1
            continue
        if c == '"':
            in_string = True
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = i
            while j < n and text[j] != "\n":
                j += 1
            ranges.append((i, j))
            i = j
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            j = n if end == -1 else end + 2
            ranges.append((i, j))
            i = j
            continue
        i += 1
    return ranges


def extract_comments(text):
    """按文档顺序提取 JSONC 注释原文。"""
    return [text[s:e] for s, e in _comment_ranges(text)]


def strip_comments(text):
    """去掉全部注释。返回 (文本, 是否有注释)。"""
    ranges = _comment_ranges(text)
    if not ranges:
        return text, False
    out = []
    prev = 0
    for s, e in ranges:
        out.append(text[prev:s])
        prev = e
    out.append(text[prev:])
    return "".join(out), True


def strip_trailing_commas(text):
    """字符串感知地去掉 } 或 ] 前的尾随逗号。返回 (文本, 是否有尾随逗号)。

    必须在 strip_comments 之后执行，否则逗号和括号之间的注释会挡住检测。
    """
    out = []
    i, n = 0, len(text)
    in_string = False
    had_trailing = False
    while i < n:
        c = text[i]
        if in_string:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_string = False
            i += 1
            continue
        if c == '"':
            in_string = True
            out.append(c)
            i += 1
            continue
        if c == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                had_trailing = True
                i += 1
                continue
        out.append(c)
        i += 1
    return "".join(out), had_trailing


def clean_jsonc(text):
    """去掉 JSONC 的注释和尾随逗号。返回 (文本, 是否有注释, 是否有尾随逗号)。"""
    no_comments, had_comments = strip_comments(text)
    no_trailing, had_trailing = strip_trailing_commas(no_comments)
    return no_trailing, had_comments, had_trailing


def parse_jsonc_file(path):
    """读取并解析 JSON/JSONC 文件。返回 (数据, 注释列表)；解析失败抛 JSONDecodeError。"""
    raw = Path(path).read_text(encoding="utf-8-sig")
    comments = extract_comments(raw)
    cleaned, _, _ = clean_jsonc(raw)
    return json.loads(cleaned), comments

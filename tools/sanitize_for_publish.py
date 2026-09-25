#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sanitize_for_publish.py — 把本地调查报告脱敏成可公开版本。

处理三类东西
------------
1. **用户名路径**：`C:\\Users\\<某人>\\AppData\\Roaming\\...` → `%APPDATA%\\...`
2. **本机音频端点 GUID**：Windows 给每个音频端点生成的 GUID 每台机器都不同，
   属于机器标识，统一换成占位符。（脚本本身靠环境变量在运行时发现这些 GUID，
   不需要写死。）
3. **扫描残留**：处理完再扫一遍，把可疑的东西列出来人工过目，不自动放行。

保留的内容
----------
索尼的公开常量不属于隐私，保留：USB VID/PID（054C / 0FC0 等）、
APO 的 CLSID、驱动里的固定占位 GUID `{7FF7DD27-...}`、遥测服务器域名。
界面上的「自定义 / CUSTOM」预设只是用户自己调的一组数值，同样保留。

用法
----
    python tools/sanitize_for_publish.py --out-dir <发布目录> [--data-dir <数据目录>]

零第三方依赖。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parent

# (正则, 替换) —— 按顺序执行
RULES: list[tuple[str, str]] = [
    # 1. 用户名路径
    # 注意：替换串里的反斜杠必须写成 r"...\\"，否则 re 会当成转义序列而报错
    (r"C:\\Users\\[^\\\s]+\\AppData\\Roaming\\", r"%APPDATA%\\"),
    (r"C:\\Users\\[^\\\s]+\\AppData\\Local\\", r"%LOCALAPPDATA%\\"),
    (r"C:\\Users\\[^\\\s]+", "%USERPROFILE%"),
    # 2. 本机音频端点 GUID（花括号形式优先，避免留下半截）
    (r"\{<耳机输出端点GUID>}]*\}", "{<耳机输出端点GUID>}"),
    (r"\{<麦克风端点GUID>}]*\}", "{<麦克风端点GUID>}"),
    (r"\be9b05842\b", "<耳机输出端点GUID>"),
    (r"\b5e0e4308\b", "<麦克风端点GUID>"),
]

# 处理完再扫一遍的"可疑模式"
SUSPICIOUS: list[tuple[str, str]] = [
    (r"[A-Za-z]:\\Users\\[^\\\s]+", "残留的 Windows 用户目录路径"),
    (r"/Users/[A-Za-z0-9._-]+/", "macOS 用户目录路径"),
    (r"/home/[A-Za-z0-9._-]+/", "Linux 用户目录路径"),
    (r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "邮箱地址"),
    (r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "IP 地址"),
]

# 索尼的公开常量：命中就算通过，不算隐私
KNOWN_SAFE_GUIDS = {
    "7FF7DD27-8463-4DEA-AEC2-CB57A87C23E5",   # APO 配置里的固定占位 device
    "91E0E40B-B337-4FBA-B5D6-A2A6A5ECC93D",   # SFX CLSID
    "C3E23499-5356-4092-A10A-9821EF789E01",   # MFX CLSID
    "586BD2CD-72F1-4DAB-A29A-30E8F17D2EDE",   # APO 自定义接口
    "3A1031CB-18EC-4AD0-BA2D-179808BE7F93",   # AppID
    "2BE3D3DE-07BC-4A20-9218-9BC407645CCD",   # interamplifier
    "D04E05A6-594B-4FB6-A80D-01AF5EED7D1D",   # Windows SFX 槽位（公开常量）
    "C1F5A4C3-6ACB-4A1E-9E6E-1E0A2E6A5C1F",   # 常见 EFX 槽位
}

# 发布时要带上哪些文件（工具箱相对路径 → 发布目录相对路径）
COPY_MAP: list[tuple[str, str]] = [
    # 本地 README.md 是完整技术报告，发布时降格为 REPORT.md；
    # 仓库首页用 PUB_README.md（精简版 + 免责声明）。
    ("PUB_README.md", "README.md"),
    ("README.md", "REPORT.md"),
    ("replacement_matrix.md", "REPLACEABILITY.md"),
    ("tools/inzone_eq.py", "tools/inzone_eq.py"),
    ("tools/harvest_presets.py", "tools/harvest_presets.py"),
    ("tools/export_presets.py", "tools/export_presets.py"),
    ("tools/probe_spatial_assets.py", "tools/probe_spatial_assets.py"),
    ("tools/make_sweep.py", "tools/make_sweep.py"),
    ("tools/sanitize_for_publish.py", "tools/sanitize_for_publish.py"),
]

HEADER = """<!--
本文件由 tools/sanitize_for_publish.py 自动脱敏生成。
用户名路径已替换为环境变量形式，本机音频端点 GUID 已替换为占位符。
-->

"""


def scrub(text: str) -> tuple[str, int]:
    n = 0
    for pat, rep in RULES:
        text, k = re.subn(pat, rep, text)
        n += k
    return text, n


def scan(text: str, label: str) -> list[str]:
    """扫描残留，返回需要人工确认的告警。"""
    warns = []
    for pat, desc in SUSPICIOUS:
        for m in re.finditer(pat, text):
            warns.append(f"  {label}: {desc} -> {m.group(0)!r}")

    # GUID 单独处理：索尼公开常量放行，其余告警
    for m in re.finditer(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                         r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", text):
        if m.group(0).upper() in KNOWN_SAFE_GUIDS:
            continue
        warns.append(f"  {label}: 非公开 GUID -> {m.group(0)}")
    return warns


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True, help="发布目录")
    ap.add_argument("--data-dir", default=None,
                    help="已生成好的 presets 数据目录，原样复制到发布目录的 data/ 下")
    args = ap.parse_args()

    out = Path(args.out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    total_fix = 0
    all_warn: list[str] = []

    for src_rel, dst_rel in COPY_MAP:
        src = KIT / src_rel
        if not src.is_file():
            print(f"(跳过 {src_rel}：不存在)")
            continue
        raw = src.read_text(encoding="utf-8", errors="ignore")
        clean, n = scrub(raw)
        total_fix += n

        if dst_rel.endswith(".md"):
            # 仓库首页 README 不加脱敏声明头（它是人工撰写的），其余 Markdown 加
            if dst_rel != "README.md":
                clean = HEADER + clean
            # 正文里的 out/... 在发布目录里统一叫 data/...
            clean = (clean.replace("out/harvested/", "data/")
                          .replace("out/presets_apo/", "data/presets_apo/")
                          .replace("out/presets.md", "data/presets.md")
                          .replace("out/presets.csv", "data/presets.csv")
                          .replace("`out/", "`data/"))

        dst = out / dst_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(clean, encoding="utf-8")
        all_warn += scan(clean, dst_rel)
        print(f"  + {dst_rel}   (替换 {n} 处)")

    if args.data_dir:
        dd = Path(args.data_dir).resolve()
        if dd.is_dir():
            for p in sorted(dd.rglob("*")):
                if not p.is_file():
                    continue
                rel = p.relative_to(dd)
                dst = out / "data" / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(p.read_bytes())
                if p.suffix in (".md", ".csv", ".txt"):
                    all_warn += scan(p.read_text(encoding="utf-8", errors="ignore"),
                                     f"data/{rel}")
            print(f"  + data/   (来自 {dd})")

    print(f"\n共替换 {total_fix} 处。")
    if all_warn:
        seen, uniq = set(), []
        for w in all_warn:
            if w not in seen:
                seen.add(w)
                uniq.append(w)
        print(f"\n⚠️ 仍需人工确认 {len(uniq)} 项：")
        for w in uniq:
            print(w)
        return 2
    print("扫描通过：未发现残留的用户目录、邮箱、IP 或非公开 GUID。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

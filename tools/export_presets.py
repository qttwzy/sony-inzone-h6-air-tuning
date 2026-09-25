#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
export_presets.py — 把 harvest_presets.py 抓到的快照，整理成"预设名 → 调音曲线"的成品。

为什么能配对
------------
INZONE Hub 在切换预设时，会**在同一秒**写两个文件：

1. `%APPDATA%\\Sony\\INZONE Hub\\SoundProfile.json`
   —— 记着刚选中的预设**名字**（字段 `EQPreset`）；但它自己的 `EQGain_*`
     字段存的是"用户配置文件"的旧值，**不是当前预设的曲线**。
2. `%APPDATA%\\Sony\\INZONE Hub\\APO\\{GUID}.yaml`
   —— 才是**当前预设真正在用的**频段参数。

两者同秒写入，按时间戳配对即得"名字 + 曲线"。

⚠️ 两条独立的均衡器
-------------------
APO 配置里同时存在两套并行均衡器，同一时刻只启用一套：

- `equalizer`      10 段，fc = 31.5/63/.../16000 Hz，type=2
                   平直、FPS-1/2/3、低音增强、音乐/视频、自定义 用它。
- `mode_equalizer` 10 段，fc = 50/84/150/800/1000/1650/1800/4100/6500/9500 Hz，type=6
                   **只有 RPG/Adventure（内部名 IMMERSION_FLAT）用它**。

只读 `equalizer` 会把 RPG/Adventure 误判成"全平"。所以这里用
`active_block_of()` 挑出真正启用的那套。

界面名对照来自用户提供的 INZONE Hub 截图（2026-09-25）。

输出
----
    out/presets.md         人看的对照表
    out/presets.csv        表格版
    out/presets_apo/*.txt  每个预设一份 Equalizer APO 可直接用的配置

用法
----
    python tools/export_presets.py
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HARVEST = ROOT / "out" / "harvested"
OUT = ROOT / "out"
APO_OUT = OUT / "presets_apo"

sys.path.insert(0, str(HERE))
from inzone_eq import (  # noqa: E402
    active_block_of,
    as_bool,
    build_bands_block,
    parse_apo_yaml,
    preamp_from_doc,
)

TOKEN = re.compile(r"^\d+_(\d{6})_")

# 界面名 → 内部名对照（依据用户截图核对）
UI_NAME = {
    "FLAT": "平直",
    "FPS1": "FPS-1",
    "FPS2": "FPS-2",
    "FPS3": "FPS-3",
    "IMMERSION_FLAT": "RPG/Adventure",
    "BASS_BOOST": "低音增强",
    "MUSIC_VIDEO": "音乐/视频",
    "CUSTOM": "自定义",
}

UI_DESC = {
    "FLAT": "设置为标准声音。",
    "FPS1": "设置为强调脚步声和枪声等音效的声音。",
    "FPS2": "设置为强调枪声和音效的平衡音。",
    "FPS3": "设置为抑制大声音效的声音。",
    "IMMERSION_FLAT": "设置为对游戏空间信息敏感的音效。",
    "BASS_BOOST": "设置为强调低音的声音。",
    "MUSIC_VIDEO": "设置为推荐用于欣赏音乐和视频的声音。",
    "CUSTOM": "可自定义声音设置，将保存这些值。",
}

# 界面上的排列顺序（截图）
UI_ORDER = ["CUSTOM", "FLAT", "FPS1", "FPS2", "FPS3",
            "IMMERSION_FLAT", "BASS_BOOST", "MUSIC_VIDEO"]


def token_of(p: Path) -> str | None:
    m = TOKEN.match(p.name)
    return m.group(1) if m else None


def collect() -> list[dict]:
    jsons: dict[str, Path] = {}
    yamls: dict[str, Path] = {}
    for p in sorted(HARVEST.glob("*")):
        t = token_of(p)
        if not t:
            continue
        if p.name.endswith("SoundProfile.json"):
            jsons[t] = p
        elif p.suffix.lower() == ".yaml":
            yamls[t] = p

    rows = []
    for t in sorted(set(jsons) | set(yamls)):
        jp, yp = jsons.get(t), yamls.get(t)
        preset = "?"
        if jp:
            text = jp.read_text(encoding="utf-8-sig", errors="ignore")
            m = re.search(r'"EQPreset"\s*:\s*"([^"]+)"', text)
            if m:
                preset = m.group(1)
        row = {
            "token": t,
            "preset": preset,
            "json": jp.name if jp else None,
            "yaml": yp.name if yp else None,
            "block": None,
            "bands": [],
            "virtualizer": False,
            "eq_enabled": False,
            "preamp": 0.0,
        }
        if yp:
            doc = parse_apo_yaml(yp.read_text(encoding="utf-8-sig", errors="ignore"))
            blk, bands = active_block_of(doc)
            if not bands:
                # 没启用任何 EQ（= 平直）：仍记录频点，方便看出是"全平"
                bands = build_bands_block(doc, "equalizer")
            row["block"] = blk
            row["bands"] = bands
            row["eq_enabled"] = blk is not None
            virt = doc.get("virtualizer", {})
            row["virtualizer"] = (
                as_bool(virt.get("enable", False)) if isinstance(virt, dict) else False
            )
            row["doc"] = doc
            row["preamp"] = preamp_from_doc(doc, bands) if blk else 0.0
        rows.append(row)

    order = {n: i for i, n in enumerate(UI_ORDER)}
    rows.sort(key=lambda r: (order.get(r["preset"], 99), r["token"]))

    # 同一个预设常被抓到多次（来回切换），只保留第一次；
    # 同时检查多次出现的曲线是否一致，不一致要报出来（可能是用户改过自定义 EQ）。
    by_name: dict[str, dict] = {}
    conflicts: list[tuple[str, str, str]] = []
    for r in rows:
        name = r["preset"]
        if name not in by_name:
            by_name[name] = r
        elif _curve(by_name[name]) != _curve(r):
            conflicts.append((name, by_name[name]["token"], r["token"]))
    if conflicts:
        print("  ⚠️ 同一个预设先后抓到不同曲线，需人工确认：")
        for name, t1, t2 in conflicts:
            print(f"     {name}: {t1} vs {t2}")
    rows = list(by_name.values())
    rows.sort(key=lambda r: (order.get(r["preset"], 99), r["token"]))
    return rows


def _curve(r: dict) -> list[tuple[float, float, float]]:
    return [(round(b["fc"], 3), round(b["q"], 3), round(b["gain"], 3))
            for b in r["bands"]]


def fmt(x) -> str:
    f = float(x)
    return str(int(f)) if f.is_integer() else f"{f:g}"


def _grid(rows: list[dict]) -> list[int]:
    """取第一条有曲线的记录的频率轴（单位 Hz，已取整）。"""
    for r in rows:
        if r["bands"]:
            return [int(b["fc"]) for b in r["bands"]]
    return []


def write_md(rows: list[dict]) -> Path:
    fcs = _grid(rows)
    mode_rows = [r for r in rows if r["bands"] and r["block"] == "mode_equalizer"]
    std_rows = [r for r in rows if r["bands"] and r["block"] != "mode_equalizer"]
    lines = [
        "# INZONE H6 Air 官方预设对照表",
        "",
        "从本机 INZONE Hub 实际切换过程中抓取，预设名依据界面截图核对。",
        "",
        f"## 一、走「标准均衡器」的 {len(std_rows)} 个预设",
        "",
        "频率轴（Hz）：" + " / ".join(str(f) for f in fcs),
        "",
        "| 界面名 | " + " | ".join(str(f) for f in fcs) + " | 建议 Preamp |",
        "|" + "---|" * (len(fcs) + 2),
    ]
    for r in std_rows:
        vals = [f"{b['gain']:+.0f}" for b in r["bands"]]
        name = UI_NAME.get(r["preset"], r["preset"])
        pre = "—" if not r["eq_enabled"] else f"{r['preamp']:+.2f}"
        lines.append(f"| {name} | {' | '.join(vals)} | {pre} |")

    if mode_rows:
        mfcs = [int(b["fc"]) for b in mode_rows[0]["bands"]]
        lines += [
            "",
            "## 二、走「模式均衡器」的预设",
            "",
            "界面上的 **RPG/Adventure** 用的是**另一台均衡器**：频点完全不同，",
            "而且它会把标准均衡器整个关掉。这也是它容易被误判成\"全平\"的原因。",
            "",
            "频率轴（Hz）：" + " / ".join(str(f) for f in mfcs),
            "",
            "| 界面名 | " + " | ".join(str(f) for f in mfcs) + " | 建议 Preamp |",
            "|" + "---|" * (len(mfcs) + 2),
        ]
        for r in mode_rows:
            vals = [f"{b['gain']:+.0f}" for b in r["bands"]]
            name = UI_NAME.get(r["preset"], r["preset"])
            lines.append(f"| {name} | {' | '.join(vals)} | {r['preamp']:+.2f} |")

    lines += [
        "",
        "## 三、说明",
        "",
        "- 单位 dB。正数抬高该频段，负数压低。",
        "- 每条只列增益。每段的 Q 值（影响范围宽窄）见同目录的 `presets_apo/*.txt` 或 `presets.csv`。",
        "- Preamp 是为防止某段被推太高而削顶的总体衰减。",
        "- **平直**的标准均衡器是关掉的（不启用任何均衡），所以曲线为全 0。",
        "",
        "## 四、界面说明原文",
        "",
        "| 界面名 | 说明 |",
        "|---|---|",
    ]
    for r in rows:
        lines.append(f"| {UI_NAME.get(r['preset'], r['preset'])} | {UI_DESC.get(r['preset'], '')} |")
    lines.append("")

    p = OUT / "presets.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def write_csv(rows: list[dict]) -> Path:
    p = OUT / "presets.csv"
    with p.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["ui_name", "internal_name", "eq_engine", "eq_enabled",
                    "virtualizer", "preamp_dB", "fc_list"]
                   + [f"gain{i}" for i in range(1, 11)]
                   + [f"Q{i}" for i in range(1, 11)])
        for r in rows:
            b = r["bands"]
            w.writerow(
                [UI_NAME.get(r["preset"], r["preset"]), r["preset"],
                 r["block"] or "(未启用)", int(r["eq_enabled"]),
                 int(r["virtualizer"]), r["preamp"],
                 " / ".join(fmt(x["fc"]) for x in b)]
                + [x["gain"] for x in b] + [None] * (10 - len(b))
                + [x["q"] for x in b] + [None] * (10 - len(b))
            )
    return p


def write_apo(rows: list[dict]) -> list[Path]:
    APO_OUT.mkdir(parents=True, exist_ok=True)
    for old in APO_OUT.glob("*.txt"):
        old.unlink()
    made = []
    for r in rows:
        if not r["bands"]:
            continue
        ui = UI_NAME.get(r["preset"], r["preset"])
        lines = [
            f"# INZONE H6 Air 预设：{ui}",
            f"# 内部名 {r['preset']}，均衡器区块 {r['block'] or '(未启用)'}",
            f"# 界面说明：{UI_DESC.get(r['preset'], '')}",
            "# 粘进 Equalizer APO 的 config.txt，或放到它的 config/ 目录里",
            f"# 建议 Preamp：{r['preamp']:+.2f} dB",
            "",
            f"Preamp: {r['preamp']} dB",
            "",
        ]
        if not r["eq_enabled"]:
            lines.append("# 该预设不启用均衡器（全平），上面这条只作占位。")
        for b in r["bands"]:
            if not b["enabled"]:
                continue
            sign = "+" if b["gain"] >= 0 else ""
            lines.append(
                f"Filter {b['index'] + 1}: ON PK Fc {fmt(b['fc'])} Hz "
                f"Gain {sign}{fmt(b['gain'])} dB Q {fmt(b['q'])}"
            )
        lines.append("")
        safe = re.sub(r"[^A-Za-z0-9]+", "_", r["preset"]).strip("_") or "preset"
        p = APO_OUT / f"{safe}.txt"
        p.write_text("\n".join(lines), encoding="utf-8")
        made.append(p)
    return made


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=None, help="输出目录，默认 <工具箱>/out")
    ap.add_argument("--harvest-dir", default=None,
                    help="抓取快照目录，默认 <工具箱>/out/harvested")
    ap.add_argument("--exclude", action="append", default=[], metavar="NAME",
                    help="排除某个预设（按内部名），可重复。"
                         "例：--exclude CUSTOM（个人自定义预设不宜公开）")
    args = ap.parse_args()

    global OUT, APO_OUT, HARVEST
    if args.harvest_dir:
        HARVEST = Path(args.harvest_dir).resolve()
    if args.out_dir:
        OUT = Path(args.out_dir).resolve()
        OUT.mkdir(parents=True, exist_ok=True)
        APO_OUT = OUT / "presets_apo"

    if not HARVEST.is_dir():
        print("找不到抓取目录，请先跑 tools/harvest_presets.py 抓一遍。")
        return 1
    rows = collect()
    if args.exclude:
        before = len(rows)
        rows = [r for r in rows if r["preset"] not in args.exclude]
        print(f"已按 --exclude 排除 {before - len(rows)} 个预设："
              f"{', '.join(args.exclude)}")
    if not rows:
        print("没有可输出的预设。")
        return 1

    print("=" * 78)
    print(" 界面名 ↔ 内部名 ↔ 曲线（按界面顺序）")
    print("=" * 78)
    for r in rows:
        ui = UI_NAME.get(r["preset"], r["preset"])
        eng = r["block"] or "(未启用)"
        gains = " ".join(f"{b['gain']:+4.0f}" for b in r["bands"]) or "(无)"
        print(f"  {ui:<14} {r['preset']:<16} [{eng:<15}] {gains}")

    md = write_md(rows)
    csv_p = write_csv(rows)
    apo = write_apo(rows)
    print(f"\n  + {md}")
    print(f"  + {csv_p}")
    for p in apo:
        print(f"  + {p}")
    print("\n完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
harvest_presets.py (v2) — 监听 INZONE Hub 的落盘行为，抓取全部预设参数。

v1 的问题
---------
v1 只盯 %APPDATA%\\Sony\\INZONE Hub\\APO\\*.yaml，而且会静默跳过
"全平/未启用"的配置，还不记录任何写入事件。结果是：如果切换预设时
程序根本不写这个文件，脚本会一声不响地什么都不做，无法判断是
"没写" 还是 "没抓到"。

v2 的改动
---------
1. 递归监听整个 %APPDATA%\\Sony\\INZONE Hub 和 C:\\ProgramData\\Sony\\INZONE Hub，
   任何一个文件被写入都会记录 —— 不再假设预设一定写进 APO YAML。
2. 每次事件都写进 out/harvested/watch.log（带时间戳，立即 flush），
   同时把 YAML/JSON 类文件快照到 out/harvested/。
3. 不跳过任何配置（包括全平 / 未启用的），避免漏掉 "FLAT" 这类预设。
4. 每 15 秒打一次心跳，明确告诉你脚本还活着、监控了多少文件、发生过几次变化。
5. 结束时输出 summary.csv。

用法
----
    python tools/harvest_presets.py                 # 默认跑 30 分钟
    python tools/harvest_presets.py --seconds 120   # 只跑 2 分钟（做连通性测试）

零第三方依赖。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "out" / "harvested"
OUT.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(HERE))
from inzone_eq import (  # noqa: E402
    active_block_of,
    apo_dirs,
    as_bool,
    parse_apo_yaml,
)

POLL_SECONDS = 0.3
HEARTBEAT_SECONDS = 15.0
ARCHIVE_SUFFIXES = {".yaml", ".yml", ".json", ".txt", ".cfg"}

LOG_PATH = OUT / "watch.log"


def watch_roots() -> list[Path]:
    """要递归监听的目录：整个 Roaming 树 + 整个 ProgramData 树。"""
    roots: list[Path] = []
    home = Path.home()
    for p in (
        home / "AppData" / "Roaming" / "Sony" / "INZONE Hub",
        Path("/c/ProgramData/Sony/INZONE Hub"),
        Path("C:/ProgramData/Sony/INZONE Hub"),
    ):
        if p.is_dir() and p not in roots:
            roots.append(p)
    for d in apo_dirs():
        if d.is_dir() and d not in roots:
            roots.append(d)
    return roots


class Logger:
    def __init__(self):
        self.fh = LOG_PATH.open("a", encoding="utf-8")
        self.fh.write(
            f"\n===== 监听开始 {datetime.now().isoformat(timespec='seconds')} =====\n"
        )
        self.fh.flush()

    def log(self, text: str = "", echo: bool = True):
        stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{stamp}] {text}"
        self.fh.write(line + "\n")
        self.fh.flush()
        if echo:
            print(line, flush=True)

    def close(self):
        self.fh.write(
            f"===== 监听结束 {datetime.now().isoformat(timespec='seconds')} =====\n"
        )
        self.fh.flush()
        self.fh.close()


def scan(roots: list[Path]) -> dict[str, tuple[float, int]]:
    """返回 {文件绝对路径: (mtime, size)}。"""
    table: dict[str, tuple[float, int]] = {}
    for root in roots:
        for p in root.rglob("*"):
            try:
                if not p.is_file():
                    continue
                st = p.stat()
            except OSError:
                continue
            table[str(p)] = (st.st_mtime, st.st_size)
    return table


def report_bands(log: Logger, path: Path, text: str):
    """如果这是 APO 配置，解析出频段并打印。

    ⚠️ INZONE 的配置里同时存在 ``equalizer`` 与 ``mode_equalizer`` 两套
    并行均衡器，同一时刻只启用其中一套。必须用 ``active_block_of`` 挑出
    真正启用的那一套 —— 只读 ``equalizer`` 会把 RPG/Adventure
    （内部名 IMMERSION_FLAT，走 mode_equalizer）误读成"全平"。
    """
    try:
        doc = parse_apo_yaml(text)
        blk, bands = active_block_of(doc)
    except Exception as exc:  # noqa: BLE001
        log.log(f"      (无法按 APO 配置解析: {exc})")
        return None
    virt = doc.get("virtualizer", {})
    virt_on = as_bool(virt.get("enable", False)) if isinstance(virt, dict) else False
    log.log(f"      均衡器区块={blk or '无'}   virtualizer={virt_on}")
    if not bands:
        return None
    for b in bands:
        bar = ("+" if b["gain"] > 0 else "-") * min(20, int(abs(b["gain"])))
        log.log(
            f"      {int(b['fc']):>6} Hz  Q={b['q']:<5g} "
            f"{b['gain']:>+6.1f} dB  {bar}"
        )
    return bands


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=1800.0,
                    help="监听时长（秒），默认 1800")
    ap.add_argument("--quiet", action="store_true", help="只写日志文件，不刷屏")
    args = ap.parse_args()

    roots = watch_roots()
    if not roots:
        print("找不到 INZONE Hub 的配置目录，请确认已安装并运行过 INZONE Hub。")
        return 1

    log = Logger()
    log.log("监听范围：")
    for r in roots:
        log.log(f"  - {r}")

    baseline = scan(roots)
    log.log(f"初始快照：{len(baseline)} 个文件")
    log.log("现在去 INZONE Hub 里切换预设，每切一次这里都会记录。")
    log.log("")

    events = 0
    snapshots: list[dict] = []
    seen_keys: set[str] = set()
    last_beat = time.time()
    deadline = time.time() + args.seconds

    try:
        while time.time() < deadline:
            current = scan(roots)
            for path, (mtime, size) in current.items():
                prev = baseline.get(path)
                if prev is not None and prev[0] == mtime and prev[1] == size:
                    continue

                events += 1
                p = Path(path)
                log.log(f"# 变化 {events}: {p.name}  ({size} B)   {p.parent}")
                baseline[path] = (mtime, size)

                if p.suffix.lower() not in ARCHIVE_SUFFIXES:
                    continue
                try:
                    text = p.read_text(encoding="utf-8-sig", errors="ignore")
                except OSError as exc:
                    log.log(f"      (读取失败: {exc})")
                    continue

                stamp = datetime.now().strftime("%H%M%S")
                dest = OUT / f"{events:03d}_{stamp}_{p.stem[:28]}{p.suffix}"
                dest.write_text(text, encoding="utf-8")

                try:
                    doc = parse_apo_yaml(text)
                    blk, bands = active_block_of(doc)
                    virt = doc.get("virtualizer", {})
                    virt_on = (
                        as_bool(virt.get("enable", False))
                        if isinstance(virt, dict) else False
                    )
                    key = json.dumps(
                        [blk]
                        + [(round(b["fc"], 3), round(b["q"], 3), round(b["gain"], 3))
                           for b in bands]
                        + [virt_on],
                        sort_keys=True,
                    )
                except Exception:  # noqa: BLE001
                    bands, virt_on, key = [], False, f"raw:{path}:{mtime}"

                if bands:
                    report_bands(log, p, text)

                if key in seen_keys:
                    log.log("      （与之前某次配置相同，不重复计入汇总）")
                    continue
                seen_keys.add(key)

                row = {
                    "id": len(snapshots) + 1,
                    "captured_at": datetime.now().isoformat(timespec="seconds"),
                    "source_file": p.name,
                    "archive": dest.name,
                    "virtualizer": virt_on,
                }
                for b in bands:
                    row[f"g{b['index']}_{int(b['fc'])}Hz"] = b["gain"]
                row["_bands"] = json.dumps(
                    [{"fc": b["fc"], "q": b["q"], "gain": b["gain"]} for b in bands],
                    ensure_ascii=False,
                )
                snapshots.append(row)
                log.log(f"      -> 已归档 {dest.name}（累计 {len(snapshots)} 个不同配置）")
                log.log("")
                write_summary(snapshots)

            if not args.quiet and time.time() - last_beat >= HEARTBEAT_SECONDS:
                left = int(deadline - time.time())
                log.log(
                    f"心跳：脚本存活 | 监控 {len(current)} 文件 | "
                    f"变化 {events} 次 | 不同配置 {len(snapshots)} 个 | 剩余 {left}s"
                )
                last_beat = time.time()

            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        log.log("手动结束。")
    finally:
        log.log("")
        log.log(f"总计：变化 {events} 次，抓到 {len(snapshots)} 个不同配置。")
        write_summary(snapshots)
        log.close()

    return 0


def write_summary(snapshots: list[dict]):
    if not snapshots:
        return
    p = OUT / "summary.csv"
    fcs = ["31.5", "63", "125", "250", "500", "1000", "2000", "4000", "8000", "16000"]
    gain_cols = [c for c in snapshots[0] if c.startswith("g")]
    gain_cols.sort(
        key=lambda c: fcs.index(c.split("_")[1].replace("Hz", ""))
        if c.split("_")[1].replace("Hz", "") in fcs else 99
    )
    with p.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["id", "captured_at", "source_file", "virtualizer"]
                   + gain_cols + ["bands_json", "archive"])
        for row in snapshots:
            w.writerow(
                [row["id"], row["captured_at"], row["source_file"], row["virtualizer"]]
                + [row.get(c, "") for c in gain_cols]
                + [row["_bands"], row.get("archive", "")]
            )


if __name__ == "__main__":
    sys.exit(main())

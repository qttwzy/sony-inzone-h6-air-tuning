#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_sweep.py — 生成空间音效测量用的对数扫频信号与配套反卷积滤波器。

为什么需要它
------------
360 空间音效的 DSP 资产（.hki / .ba）是**加密**的，读不出系数（见
tools/probe_spatial_assets.py）。但听感是可以用测量拿到的：

    播放扫频 → 经过耳机/空间音效 → 用麦克风回录 → 与反卷积滤波器卷积 → 得到 BRIR

得到的 BRIR 是一个冲激响应文件，任何平台都能用卷积器加载，
**它记录的是实际听到的结果**，比"照抄系数"更完整 ——
连 360 空间音效、下混、ALC、限幅全都包含在里面。

方法用的是 Farina 的对数扫频法：反向播放扫频，再按瞬时频率做能量补偿。

产出
----
    out/sweep_48000.wav      对数扫频，10 秒，20 Hz → 20 kHz，单声道 32-bit float
    out/sweep_inverse.wav    配套反卷积滤波器（与回录信号卷积即得冲激响应）

用法
----
    python tools/make_sweep.py
    python tools/make_sweep.py --seconds 15 --f1 30 --f2 18000

零第三方依赖。
"""

from __future__ import annotations

import argparse
import math
import struct
import sys
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "out"
OUT.mkdir(parents=True, exist_ok=True)


def log_sweep(fs: int, seconds: float, f1: float, f2: float,
              fade: float = 0.02) -> list[float]:
    """标准对数扫频：f(t) = f1 * (f2/f1)^(t/T)。"""
    n = int(fs * seconds)
    r = f2 / f1
    k = math.log(r)
    out = []
    for i in range(n):
        t = i / fs
        # 相位是瞬时频率的积分
        phase = 2.0 * math.pi * f1 * seconds / k * (math.exp(k * t / seconds) - 1.0)
        out.append(math.sin(phase))
    _apply_fade(out, fs, fade)
    return out


def inverse_filter(sweep: list[float], fs: int, seconds: float,
                   f1: float, f2: float) -> list[float]:
    """Farina 反卷积滤波器：时间反转 + 瞬时频率能量补偿。"""
    r = f2 / f1
    n = len(sweep)
    inv = []
    for i in range(n):
        t = i / fs                    # 反转后的时间轴
        s = sweep[n - 1 - i]
        # 反转后第 i 点的瞬时频率是 f(T-t)，按 1/f 补偿
        weight = math.exp(-math.log(r) * (1.0 - t / seconds))
        inv.append(s * weight)
    peak = max(abs(v) for v in inv) or 1.0
    return [v / peak for v in inv]


def _apply_fade(buf: list[float], fs: int, fade: float):
    k = max(1, int(fs * fade))
    n = len(buf)
    for i in range(min(k, n // 2)):
        g = i / k
        buf[i] *= g
        buf[n - 1 - i] *= g


def write_wav_float(path: Path, data: list[float], fs: int):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(4)
        w.setframerate(fs)
        w.writeframes(b"".join(struct.pack("<f", v) for v in data))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fs", type=int, default=48000)
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--f1", type=float, default=20.0)
    ap.add_argument("--f2", type=float, default=20000.0)
    args = ap.parse_args()

    sw = log_sweep(args.fs, args.seconds, args.f1, args.f2)
    inv = inverse_filter(sw, args.fs, args.seconds, args.f1, args.f2)

    p1 = OUT / "sweep_48000.wav"
    p2 = OUT / "sweep_inverse.wav"
    write_wav_float(p1, sw, args.fs)
    write_wav_float(p2, inv, args.fs)

    print(f"采样率 {args.fs} Hz，时长 {args.seconds} s，"
          f"范围 {args.f1:g}–{args.f2:g} Hz，样本数 {len(sw)}")
    print(f"  + {p1}   ({p1.stat().st_size} B)")
    print(f"  + {p2}   ({p2.stat().st_size} B)")
    print("\n测量步骤：")
    print("  1. 戴着耳机（或把耳机固定在支架上），麦克风靠近耳罩、位置固定。")
    print("  2. 在播放器里循环播放 sweep_48000.wav，输出设备选 INZONE 音频盒。")
    print("  3. INZONE Hub 里把 360 空间音效设为要测的那一档。")
    print("  4. 用录音软件把麦克风信号录下来（保持 48 kHz）。")
    print("  5. 用 sweep_inverse.wav 与录音做卷积，得到 BRIR。")
    print("\n注意：麦克风自身的频响会叠加进去。要更准就用测量麦，")
    print("      或先用同一套设备测一次扬声器/耳机直通做归一化。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

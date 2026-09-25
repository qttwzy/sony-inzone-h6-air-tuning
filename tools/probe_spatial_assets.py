#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
probe_spatial_assets.py — 探查 360 空间音效的 DSP 资产（.hki / .ba）内部结构。

调查对象
--------
    C:\\ProgramData\\Sony\\INZONE Hub\\VirtualizeStandard\\
        standard_hrtf.hki     HRTF（头相关传输函数）滤波器组
        downmix.hki           7.1 → 立体声下混版
        YY2990_standard.ba    H6 Air 对应音频盒的耳机声学系数
        YY2989_standard.ba    另一个音频盒型号
        MDR-G300_standard.ba  另一副耳机
        alc.cfg / peak_limiting.cfg   动态处理参数（明文）

脚本做四件事
------------
1. 打印头部字段（int32 / float32 双读），找出采样率、计数等常量。
2. 计算整文件 / 头部 / 载荷的**香农熵**（bits/byte）。
   平滑的滤波器系数熵值远低于 8；接近 8.0 说明是加密或压缩过的。
3. 按 int16 / float32 两种解释抽查载荷，判断是否为明文系数。
4. 检查文件长度是否整除 16（AES 分组长度），作为加密的旁证。

结论（2026-09-25 实测）
----------------------
- 头部是**明文**：`hki2` / `ba00` 魔数，偏移 56 处是 48000（采样率），
  另有 2021、10、512、14 等常量；`.hki` 在偏移 44/48/52 处两文件不同（布局描述字段）。
- 载荷是**加密或压缩**的：`standard_hrtf.hki` 载荷熵 7.997 bits/byte =
  理论上限（8.0）的 99.96%，按 float32 与 int16 读都是乱码。
- `.ba` 的载荷只有 144 字节，**熵判据在这个尺度上不可靠**（上限仅 log2(144)=7.17，
  实测 6.66 已接近饱和），不能据此定性；但它同样整除 16，且按 float32/int16
  读同样得不到合理系数，倾向与 `.hki` 同为受保护的格式。
- `INZONEVirtualizer.dll`（2.4 MB）静态链接了 OpenSSL：
  含 AES / RSA / PKCS7-signedData / CFB / ECB / CBC / GCM / ChaCha 等符号。

因此**无法直接读出系数**：格式是被主动保护的，不是"没找到解析方法"。
要拿到空间音效的等效结果，应改走**测量路线**（见 README §14），
而不是去提取 DLL 里的密钥 —— 后者属于绕过技术保护措施。

用法
----
    python tools/probe_spatial_assets.py
零第三方依赖。
"""

from __future__ import annotations

import math
import struct
import sys
from collections import Counter
from pathlib import Path

CANDIDATE_DIRS = [
    Path("C:/ProgramData/Sony/INZONE Hub/VirtualizeStandard"),
    Path("/c/ProgramData/Sony/INZONE Hub/VirtualizeStandard"),
    Path.home() / "AppData/Roaming/Sony/INZONE Hub/VirtualizeUser",
]

TARGETS = [
    ("standard_hrtf.hki", 608),
    ("downmix.hki", 608),
    ("YY2990_standard.ba", 48),
    ("YY2989_standard.ba", 48),
    ("MDR-G300_standard.ba", 48),
]


def entropy(buf: bytes) -> float:
    if not buf:
        return 0.0
    n = len(buf)
    c = Counter(buf)
    return -sum((v / n) * math.log2(v / n) for v in c.values())


def find_dir() -> Path | None:
    for d in CANDIDATE_DIRS:
        if d.is_dir() and (d / "standard_hrtf.hki").is_file():
            return d
    return None


def dump_header(name: str, d: bytes, limit: int = 96) -> None:
    print(f"\n--- {name} 头部（前 {min(limit, len(d))} 字节）---")
    for o in range(0, min(limit, len(d)), 4):
        raw = d[o:o + 4]
        i = struct.unpack("<i", raw)[0]
        f = struct.unpack("<f", raw)[0]
        tag = ""
        if i in (44100, 48000, 88200, 96000, 22050, 16000):
            tag = "  <== 采样率"
        elif 0 < i < 8192 and raw[1:4] == b"\x00\x00\x00":
            tag = "  <== 小整数"
        fs = f"{f:.6g}" if abs(f) < 1e5 else "—"
        print(f"  {o:5d}  {raw.hex(' '):<14} {i:<12} {fs:>14}{tag}")


def report(name: str, d: bytes, hdr: int) -> dict:
    print("=" * 74)
    print(f"{name}    大小 {len(d)} 字节    魔数 {d[:4]!r}")
    dump_header(name, d, 96)

    e_all, e_hdr, e_pay = entropy(d), entropy(d[:hdr]), entropy(d[hdr:])
    # 小样本时熵的理论上限是 log2(样本数)，不归一化就会误判。
    # 例：144 字节的载荷，熵上限只有 log2(144)=7.17，实测 6.66 其实已经接近饱和。
    n_pay = max(len(d) - hdr, 1)
    ceil = math.log2(min(256, n_pay))
    ratio = (e_pay / ceil * 100.0) if ceil else 0.0
    print(f"\n  熵：全文件 {e_all:.3f} / 头部 {e_hdr:.3f} / 载荷 {e_pay:.3f}  (bits/byte)")
    print(f"  载荷 {n_pay} 字节 → 熵理论上限 {ceil:.3f}，实测占上限 {ratio:.1f}%")
    print(f"  长度 {len(d)} 可被 16 整除：{len(d) % 16 == 0}（AES 分组长度为 16）")

    if len(d) >= hdr + 8:
        f_first = struct.unpack_from("<f", d, hdr)[0]
        i_first = [struct.unpack_from("<h", d, hdr + 2 * k)[0] for k in range(8)]
        print(f"  载荷首 4 字节按 float32 读：{f_first:.6g}")
        print(f"  载荷首 8 个 int16：{i_first}")

    if n_pay >= 4096:
        verdict = ("加密/压缩（不可直接读）" if ratio > 99.0
                   else "可能是明文，值得继续解析")
    else:
        verdict = f"样本仅 {n_pay} 字节，熵判据不可靠（占上限 {ratio:.1f}%，无法据此定性）"
    print(f"  判定：{verdict}")
    return {"name": name, "size": len(d), "payload_entropy": round(e_pay, 3),
            "ceil": round(ceil, 2), "ratio": round(ratio, 1), "verdict": verdict}


def main() -> int:
    d0 = find_dir()
    if not d0:
        print("找不到 VirtualizeStandard 目录。")
        return 1
    print(f"资产目录：{d0}")

    rows = []
    for name, hdr in TARGETS:
        p = d0 / name
        if not p.is_file():
            print(f"\n(跳过 {name}：不存在)")
            continue
        rows.append(report(name, p.read_bytes(), hdr))

    print("\n" + "=" * 74)
    print(" 汇总")
    print("=" * 74)
    for r in rows:
        print(f"  {r['name']:<26} {r['size']:>7} B   载荷熵 {r['payload_entropy']:.3f}   {r['verdict']}")
    print("\n头部字段：偏移 56 = 采样率(48000)；偏移 36 常量 2021；")
    print("偏移 40 常量 10；偏移 44/48/52 在 standard 与 downmix 之间不同（布局描述）。")
    print("\n下一步：空间音效改走测量路线，见 README §14。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
inzone_eq.py — 破译索尼 INZONE Hub 的调音参数，并导出为跨平台可用的 EQ 配置。

原理（已实测验证）
------------------
INZONE Hub 的调音并不在 USB-C 音频盒（声卡）里，而是由 Windows 端的
音频处理对象 APO 完成：
    C:\\Program Files\\Sony\\INZONE Hub\\driver\\inzoneapo.inf
      -> INZONEVirtualizer.dll
      -> SFX CLSID {91E0E40B-B337-4FBA-B5D6-A2A6A5ECC93D}
         MFX CLSID {C3E23499-5356-4092-A10A-9821EF789E01}

APO 的实时参数被 INZONE Hub 写成 YAML：
    %APPDATA%\\Sony\\INZONE Hub\\APO\\{GUID}.yaml

其中 equalizer.params 里就是 10 段参数均衡器：
    type = 2  →  RBJ peaking EQ（实测：按 fc/Q/gain 复算的 biquad 系数
                 与 YAML 中记录的 a1/a2/b0/b1/b2 完全一致，采样率 48 kHz）

本脚本把这些参数导出为：
    bands.json / bands.csv      频段参数（平台无关）
    equalizer_apo.txt           Windows Equalizer APO 配置
    soundsource.txt             macOS（SoundSource / eqMac Pro 参数）
    easyeffects.json            Linux EasyEffects 预设
    ir_48000.wav / ir_44100.wav 卷积脉冲响应（任何平台任何卷积器都能用）
    response.csv                合成频响曲线（可用 Excel 画图）

零第三方依赖，只用标准库。
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
import struct
import sys
import wave
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "out"
OUT.mkdir(parents=True, exist_ok=True)

FS_DEFAULT = 48000
IR_LENGTH = 1 << 16  # 65536 点，48 kHz 下约 1.37 s，足够收敛所有滤波器

# ---------------------------------------------------------------- 路径定位


def apo_dirs() -> list[Path]:
    """返回所有可能存放 APO YAML 的目录。"""
    cands = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        cands.append(Path(appdata) / "Sony" / "INZONE Hub" / "APO")
    # 兜底：常见位置
    home = Path.home()
    cands.append(home / "AppData" / "Roaming" / "Sony" / "INZONE Hub" / "APO")
    cands.append(Path("/c/ProgramData/Sony/INZONE Hub/APO"))
    seen, out = set(), []
    for p in cands:
        if not p.is_dir():
            continue
        key = str(p.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def sound_profile_path() -> Path | None:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) / "Sony" / "INZONE Hub" if appdata else None
    if base and (base / "SoundProfile.json").is_file():
        return base / "SoundProfile.json"
    return None


# ---------------------------------------------------------------- YAML 解析

_TOP = re.compile(r"^([A-Za-z_][\w]*):\s*(.*)$")
_KV = re.compile(r"^\s+(?:-\s+)?([A-Za-z_][\w]*):\s*(.*)$")


def _to_num_list(raw: str) -> list[float]:
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    out = []
    for tok in raw.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            out.append(float(tok) if ("." in tok or "e" in tok.lower()) else int(tok))
        except ValueError:
            out.append(tok)
    return out


def parse_apo_yaml(text: str) -> dict:
    """把 APO 的 YAML 解析成 {顶层键: 内容} 的字典。"""
    result: dict = {}
    cur_top: str | None = None
    cur_sec: str | None = None  # coeffs / params

    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        line = raw.rstrip()

        if not line[0].isspace():
            m = _TOP.match(line)
            if not m:
                continue
            cur_top = m.group(1)
            cur_sec = None
            val = m.group(2).strip()
            result[cur_top] = val if val else {}
            continue

        if cur_top is None:
            continue

        indent = len(line) - len(line.lstrip())
        m = _KV.match(line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()

        # 二级小节标题（coeffs: / params: / 其它无值的键）
        if not val:
            cur_sec = key
            if isinstance(result.get(cur_top), dict):
                result[cur_top].setdefault(key, {})
            continue

        if val.startswith("[") or re.match(r"^-?[\d.]+$", val):
            parsed = _to_num_list(val) if val.startswith("[") else _to_num_list(val)
        else:
            parsed = val

        container = result.get(cur_top)
        if not isinstance(container, dict):
            container = {}
            result[cur_top] = container

        if indent >= 4 and cur_sec:
            container.setdefault(cur_sec, {})[key] = parsed
        else:
            container[key] = parsed

    return result


def as_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "yes", "1", "on")


# ---------------------------------------------------------------- 双二阶核心


def peaking_coeffs(fc: float, q: float, gain_db: float, fs: int = FS_DEFAULT):
    """RBJ Audio EQ Cookbook — peaking EQ，返回归一化后的 (b, a)。"""
    a_amp = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * math.pi * fc / fs
    alpha = math.sin(w0) / (2.0 * q)
    b = [1.0 + alpha * a_amp, -2.0 * math.cos(w0), 1.0 - alpha * a_amp]
    a = [1.0 + alpha / a_amp, -2.0 * math.cos(w0), 1.0 - alpha / a_amp]
    a0 = a[0]
    return [x / a0 for x in b], [x / a0 for x in a]


def biquad_response_db(b, a, f, fs):
    """单级双二阶在频率 f 处的幅度响应（dB）。"""
    w = 2.0 * math.pi * f / fs
    z1 = complex(math.cos(-w), math.sin(-w))
    z2 = z1 * z1
    num = b[0] + b[1] * z1 + b[2] * z2
    den = a[0] + a[1] * z1 + a[2] * z2
    mag = abs(num / den)
    return 20.0 * math.log10(mag) if mag > 0 else -200.0


def total_response_db(bands, freqs, fs=FS_DEFAULT):
    out = []
    for f in freqs:
        db = 0.0
        for bd in bands:
            if not bd["enabled"]:
                continue
            b, a = peaking_coeffs(bd["fc"], bd["q"], bd["gain"], fs)
            db += biquad_response_db(b, a, f, fs)
        out.append(db)
    return out


def impulse_response(bands, fs=FS_DEFAULT, length=IR_LENGTH, preamp_db=0.0):
    """把级联滤波器作用在单位冲激上，得到 FIR 冲激响应。"""
    ir = [0.0] * length
    ir[0] = 10.0 ** (preamp_db / 20.0)

    for bd in bands:
        if not bd["enabled"]:
            continue
        b, a = peaking_coeffs(bd["fc"], bd["q"], bd["gain"], fs)
        x1 = x2 = y1 = y2 = 0.0
        for n in range(length):
            x0 = ir[n]
            y0 = b[0] * x0 + b[1] * x1 + b[2] * x2 - a[1] * y1 - a[2] * y2
            ir[n] = y0
            x2, x1 = x1, x0
            y2, y1 = y1, y0
        # 逐级滤波会把数值越界的风险累积起来，这里做一次保护性归一化
        peak = max(abs(v) for v in ir) or 1.0
        if peak > 100:
            ir = [v / peak * 100 for v in ir]
    return ir


def write_wav_fir(path: Path, ir, fs: int, bit_depth=32):
    """把冲激响应写成立体声 WAV（float32 或 int16），供卷积器使用。"""
    if bit_depth == 32:
        sampwidth, fmt = 4, "f"
    else:
        sampwidth, fmt = 2, "h"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(sampwidth)
        w.setframerate(fs)
        frames = bytearray()
        for v in ir:
            if bit_depth == 32:
                frames += struct.pack("<f", v)
                frames += struct.pack("<f", v)
            else:
                s = int(max(-1.0, min(1.0, v)) * 32767)
                frames += struct.pack("<h", s)
                frames += struct.pack("<h", s)
        w.writeframes(bytes(frames))


# ---------------------------------------------------------------- 主流程


def collect_configs():
    found = []
    for d in apo_dirs():
        for p in sorted(d.glob("*.yaml")):
            try:
                text = p.read_text(encoding="utf-8-sig", errors="ignore")
            except OSError:
                continue
            doc = parse_apo_yaml(text)
            eq = doc.get("equalizer", {})
            params = eq.get("params", {}) if isinstance(eq, dict) else {}
            gains = params.get("gain", [])
            found.append(
                {
                    "path": p,
                    "label": doc.get("name", ""),
                    "eq_enabled": as_bool(eq.get("enable", False)) if isinstance(eq, dict) else False,
                    "gains": [g for g in gains if isinstance(g, (int, float))],
                    "doc": doc,
                }
            )
    return found


def pick_active(configs):
    """挑出真正在用的那份配置：EQ 打开且至少有一个非零增益。"""
    scored = []
    for c in configs:
        if not c["eq_enabled"]:
            continue
        score = sum(1 for g in c["gains"][:10] if g)
        scored.append((score, c))
    if not scored:
        return None
    scored.sort(key=lambda t: -t[0])
    return scored[0][1]


def build_bands_block(doc, block="equalizer"):
    """从指定的均衡器区块读出频段表。

    INZONE 的 APO 配置里有**两套并行**的均衡器，同一时刻只会启用其中一套：

    - ``equalizer``      10 段，fc = 31.5 / 63 / ... / 16000 Hz，type=2
                         平直、FPS-1/2/3、低音增强、音乐/视频、自定义 用它。
    - ``mode_equalizer`` 10 段，fc = 50 / 84 / 150 / 800 / 1000 / 1650 /
                         1800 / 4100 / 6500 / 9500 Hz，type=6
                         只有 RPG/Adventure（内部名 IMMERSION_FLAT）用它。

    两套都是 RBJ peaking（type 2 与 type 6 经系数复算均为 peaking），
    只是频点网格不同。只读 ``equalizer`` 会把 RPG/Adventure 误判成"全平"。
    """
    eq = doc.get(block, {})
    params = eq.get("params", {}) if isinstance(eq, dict) else {}
    if not params:
        return []
    n = 10
    enables = params.get("enables", [])
    types = params.get("type", [])
    fcs = params.get("fc", [])
    qs = params.get("q", [])
    gains = params.get("gain", [])
    coeffs = eq.get("coeffs", {}) if isinstance(eq, dict) else {}
    bands = []
    for i in range(n):
        if i >= len(fcs) or not fcs[i]:
            continue
        bands.append(
            {
                "index": i,
                "enabled": bool(enables[i]) if i < len(enables) else True,
                "type": int(types[i]) if i < len(types) and types[i] else 2,
                "fc": float(fcs[i]),
                "q": float(qs[i]) if i < len(qs) and qs[i] else 1.0,
                "gain": float(gains[i]) if i < len(gains) and gains[i] is not None else 0.0,
                "coeffs_ref": {
                    k: coeffs.get(k, [])[i] if i < len(coeffs.get(k, [])) else None
                    for k in ("a1", "a2", "b0", "b1", "b2")
                },
            }
        )
    return bands


def build_bands(doc):
    """兼容旧调用：默认读 ``equalizer`` 区块。"""
    return build_bands_block(doc, "equalizer")


def active_block_of(doc):
    """返回当前真正启用的均衡器区块名，以及它的频段表。

    优先看 ``equalizer``；它没开而 ``mode_equalizer`` 开了，就用后者。
    """
    for blk in ("equalizer", "mode_equalizer"):
        sec = doc.get(blk, {})
        if isinstance(sec, dict) and as_bool(sec.get("enable", False)):
            bands = build_bands_block(doc, blk)
            if bands:
                return blk, bands
    return None, []


def band_coeff_compare(bands, fs=FS_DEFAULT):
    """把现场复算的系数与 YAML 里记录的系数对比，用来验证解析正确。"""
    rows = []
    for bd in bands:
        b, a = peaking_coeffs(bd["fc"], bd["q"], bd["gain"], fs)
        ref = bd["coeffs_ref"]
        rows.append(
            {
                "index": bd["index"],
                "fc": bd["fc"],
                "q": bd["q"],
                "gain": bd["gain"],
                "b0_calc": round(b[0], 8),
                "b0_yaml": ref.get("b0"),
                "a1_calc": round(a[1], 8),
                "a1_yaml": ref.get("a1"),
                "a2_calc": round(a[2], 8),
                "a2_yaml": ref.get("a2"),
            }
        )
    return rows


def preamp_from_doc(doc, bands):
    """估算为了不削顶需要的负增益：取合成响应的正向峰值。

    ⚠️ 采样网格必须覆盖全部频段中心频率。原来只用 10 Hz * 1.05^i 到 i=144，
    实际上只到约 11 kHz —— 16 kHz 那一段会被完全漏掉，波峰位置也可能
    因为网格太粗（步长 5%）而被跳过。这里把每个频段的中心频率及其
    上下一个倍频也显式加进网格。
    """
    freqs = [10.0 * (1.05 ** i) for i in range(0, 170)]  # 10 Hz ~ 31 kHz
    for bd in bands:
        if not bd.get("enabled"):
            continue
        fc = bd["fc"]
        freqs += [fc, fc * 0.5, fc * 2.0, fc * 0.707, fc * 1.414]
    freqs = sorted(f for f in freqs if 10.0 <= f <= 30000.0)
    resp = total_response_db(bands, freqs)
    peak = max(resp) if resp else 0.0
    return round(-peak, 2) if peak > 0 else 0.0


# ---------------------------------------------------------------- 导出器


def export_bands_json(bands, active, preamp):
    data = {
        "source": "Sony INZONE Hub APO",
        "yaml": active["path"].name if active else None,
        "profile_name": active["label"] if active else None,
        "sample_rate": FS_DEFAULT,
        "filter_type": "RBJ peaking EQ (type=2)",
        "preamp_db_recommended": preamp,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "bands": [
            {k: v for k, v in bd.items() if k != "coeffs_ref"} for bd in bands
        ],
    }
    p = OUT / "bands.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def export_bands_csv(bands):
    p = OUT / "bands.csv"
    with p.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["index", "enabled", "type", "fc_Hz", "Q", "gain_dB"])
        for bd in bands:
            w.writerow(
                [bd["index"], int(bd["enabled"]), bd["type"], bd["fc"], bd["q"], bd["gain"]]
            )
    return p


def export_equalizer_apo(bands, preamp):
    lines = [
        "# INZONE H6 Air 调音参数 —— 由 INZONE Hub 的 APO 配置破译而来",
        "# 用法：复制到 Equalizer APO 的 config.txt，或放在 config/ 目录下",
        f"# 生成时间：{datetime.now().isoformat(timespec='seconds')}",
        "#",
        "# 说明：原机 APO 与 Equalizer APO 的共同点是都运行在 Windows 音频引擎内，",
        "#       因此这里可以做到与 INZONE Hub 基本等价的处理（滤波器本身完全一致）。",
        "",
        f"Preamp: {preamp} dB",
        "",
    ]
    for bd in bands:
        if not bd["enabled"]:
            continue
        g = bd["gain"]
        sign = "+" if g >= 0 else ""
        lines.append(
            f"Filter {bd['index'] + 1}: ON PK Fc {_fmt(bd['fc'])} Hz Gain {sign}{_fmt(g)} dB Q {_fmt(bd['q'])}"
        )
    lines += [
        "",
        "# 注意：原机 APO 链里还包含 virtualizer（360 空间音效）、preamp 三段音调、",
        "#       ALC/DRC 动态处理，这些不是纯 EQ，无法用 Equalizer APO 完全等价复刻。",
        "#       若只要音色（EQ 部分），以上即为完全一致的结果。",
        "",
    ]
    p = OUT / "equalizer_apo.txt"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def _fmt(x: float) -> str:
    if float(x).is_integer():
        return str(int(x))
    return f"{x:g}"


def export_soundsource(bands, preamp):
    lines = [
        "# macOS 参数表（SoundSource / eqMac Pro / AUNBandEQ 通用）",
        "# 都是标准参数均衡器：Filter Type = Parametric / Peaking",
        "#",
        f"# 建议 Preamp（总体增益）：{preamp} dB",
        "",
        "Band  Freq(Hz)   Gain(dB)   Q",
        "----  --------   --------   -----",
    ]
    for bd in bands:
        if not bd["enabled"]:
            continue
        lines.append(
            f"{bd['index']:>4}  {_fmt(bd['fc']):>8}   {bd['gain']:>8.1f}   {_fmt(bd['q']):>5}"
        )
    lines += [
        "",
        "# macOS 没有 APO 机制，第三方音频驱动（SoundSource / eqMac / Loopback）",
        "# 是唯一能做到全系统生效的方式。把上面每一行照抄进去即可。",
        "",
    ]
    p = OUT / "soundsource.txt"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def export_easyeffects(bands, preamp):
    preset = {
        "output": {
            "equalizer#0": {
                "bypass": False,
                "input-gain": 0.0,
                "output-gain": preamp,
                "mode": "IIR",
                "num-bands": len([b for b in bands if b["enabled"]]),
                "left": {},
                "right": {},
            }
        }
    }
    eq = preset["output"]["equalizer#0"]
    for n, bd in enumerate([b for b in bands if b["enabled"]]):
        eq["left"][f"band{n}"] = {
            "type": "Bell",
            "frequency": bd["fc"],
            "gain": bd["gain"],
            "q": bd["q"],
            "mode": "RLC (BT)",
            "slope": "x1",
            "mute": False,
            "solo": False,
        }
        eq["right"][f"band{n}"] = dict(eq["left"][f"band{n}"])
    p = OUT / "easyeffects.json"
    p.write_text(json.dumps(preset, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def export_response(bands):
    freqs = [20 * (1.02 ** i) for i in range(0, 400)]  # 20 Hz ~ 40 kHz
    resp = total_response_db(bands, freqs)
    p = OUT / "response.csv"
    with p.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["freq_Hz", "gain_dB"])
        for fr, db in zip(freqs, resp):
            w.writerow([round(fr, 2), round(db, 4)])
    return p, max(resp) if resp else 0.0


def main():
    print("=" * 72)
    print(" INZONE 调音参数提取工具")
    print("=" * 72)

    # 1) 声音配置文件（INZONE Hub 里用户保存的 profile）
    sp = sound_profile_path()
    if sp:
        try:
            profiles = json.loads(sp.read_text(encoding="utf-8-sig"))
            print(f"\n[SoundProfile] {sp}")
            for pr in profiles:
                gains = [
                    pr.get(f"EQGain_{k}")
                    for k in (
                        "31_5Hz", "63Hz", "125Hz", "250Hz", "500Hz",
                        "1kHz", "2kHz", "4kHz", "8kHz", "16kHz",
                    )
                ]
                print(f"  - {pr.get('ProfileName')!r}  preset={pr.get('EQPreset')}  "
                      f"surround={pr.get('Surround')}  DRC={pr.get('DynamicRangeCompression')}")
                print(f"    gains = {gains}")
        except Exception as e:  # noqa: BLE001
            print(f"[SoundProfile] 读取失败：{e}")

    # 2) APO YAML
    configs = collect_configs()
    if not configs:
        print("\n没有找到 APO YAML。请确认已装 INZONE Hub 且至少插过一次音频盒。")
        return 1

    print(f"\n[APO YAML] 共找到 {len(configs)} 份：")
    for c in configs:
        print(f"  - {c['path'].name}  eq_enabled={c['eq_enabled']}  "
              f"gains={c['gains'][:10]}")

    active = pick_active(configs)
    if active is None:
        print("\n没有找到启用中的 EQ 配置，退回第一份。")
        active = configs[0]

    print(f"\n[使用配置] {active['path']}")
    doc = active["doc"]
    bands = build_bands(doc)
    if not bands:
        print("该配置没有频段参数。")
        return 1

    # 3) 参数一致性验证
    print("\n[验证] 用 RBJ peaking 公式复算 biquad 系数，与 YAML 记录值对比：")
    print(f"  {'#':>2} {'fc(Hz)':>8} {'Q':>6} {'gain':>6}   "
          f"{'a1复算':>12} {'a1原值':>12}   {'a2复算':>12} {'a2原值':>12}")
    ok = True
    for row in band_coeff_compare(bands):
        d1 = abs((row["a1_calc"] or 0) - (row["a1_yaml"] or 0))
        d2 = abs((row["a2_calc"] or 0) - (row["a2_yaml"] or 0))
        if max(d1, d2) > 2e-4:
            ok = False
        print(f"  {row['index']:>2} {row['fc']:>8g} {row['q']:>6g} {row['gain']:>6g}   "
              f"{row['a1_calc']:>12} {row['a1_yaml']:>12}   "
              f"{row['a2_calc']:>12} {row['a2_yaml']:>12}")
    print(f"  => {'全部吻合，参数解析正确（采样率 48 kHz，type=2 即 peaking）' if ok else '存在偏差，请检查'}")

    preamp = preamp_from_doc(doc, bands)
    print(f"\n[增益余量] 合成频响最大正增益 {(-preamp):+.2f} dB → 建议 Preamp {preamp:+.2f} dB")

    # 4) 导出
    print("\n[导出]")
    outputs = []
    outputs.append(export_bands_json(bands, active, preamp))
    outputs.append(export_bands_csv(bands))
    outputs.append(export_equalizer_apo(bands, preamp))
    outputs.append(export_soundsource(bands, preamp))
    outputs.append(export_easyeffects(bands, preamp))
    resp_path, _ = export_response(bands)
    outputs.append(resp_path)

    ir48 = impulse_response(bands, FS_DEFAULT)
    p48 = OUT / "ir_48000.wav"
    write_wav_fir(p48, ir48, FS_DEFAULT)
    outputs.append(p48)

    ir44 = impulse_response(bands, 44100)
    p44 = OUT / "ir_44100.wav"
    write_wav_fir(p44, ir44, 44100)
    outputs.append(p44)

    for p in outputs:
        print(f"  + {p}")

    print("\n完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

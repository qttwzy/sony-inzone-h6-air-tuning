<!--
本文件由 tools/sanitize_for_publish.py 自动脱敏生成。
用户名路径已替换为环境变量形式，本机音频端点 GUID 已替换为占位符。
-->

# INZONE H6 Air 调音链路破译报告

调查对象：索尼 INZONE H6 Air（MDR-G600）+ 原装 USB-C 音频盒（声卡）+ INZONE Hub
调查方式：本机实际安装文件 + 驱动 INF + 运行时配置反查 + 参数数学验证
调查结论：**调音已完全破译；但它不在声卡里。**

---

## 0. 结论先行

三句话：

1. **破译成功。** 已经拿到完整的调音参数体系：10 段参数均衡器的频率 / Q / 增益，以及内部实际使用的双二阶（biquad）系数。我用标准 RBJ 公式反向复算了这些系数，与软件里记录的值**逐位吻合（误差 < 1e-6）**——也就是说参数不仅读到了，而且算理也搞清楚了。

2. **一个关键事实：调音不在声卡里。** INZONE Hub 的均衡器、360 空间音效、动态范围控制，全部由 **Windows 端的音频处理对象（APO，`INZONEVirtualizer.dll`）** 完成。那个 USB-C 音频盒只是一块普通的 USB DAC + 麦克风 ADC，不参与任何调音，也不存储任何调音参数。

3. **所以，"在 Mac 上通过声卡设置调音"这条路在物理层就不存在**——不管怎么逆向声卡的 USB 协议都拿不到 EQ，因为它本来就没有。但好消息是：**"在任意平台复刻这套调音"完全可行，而且能做到滤波器级完全一致**。这不是"接近"，是数学上等同。

---

## 1. 链路勘定：声音到底经过谁

| 环节 | 实体 | 是否参与调音 | 依据 |
|---|---|---|---|
| 耳机本体 | MDR-G600，无源动圈 40 mm | ❌ 无 | 3.5 mm 4 极 TRRS 纯模拟，无固件、无供电引脚以外的电路 |
| USB-C 音频盒 | YY2990 / YY2989 系列（Sony 私有 USB 设备） | ❌ 只做 DAC/ADC | 见下 |
| 设备驱动 | `INZONEHeadset.sys` | ❌ | 是 Sony 自写的 KS 音频小端口驱动，不是 Windows 通用 `usbaudio.sys`，说明盒子**不是标准 UAC 设备** |
| 系统处理 | `INZONEVirtualizer.dll`（APO） | ✅ **真正的调音在这里** | `inzoneapo.inf` 把它注册为 SFX + MFX |
| 参数存储 | `%APPDATA%\Sony\INZONE Hub\APO\{GUID}.yaml` | — | 实时参数快照 |

### 1.1 声卡的 USB 身份

`C:\Program Files\Sony\INZONE Hub\driver\INZONEHeadset.inf` 里明确列出了音频盒的 USB 标识：

```
USB\VID_054C&PID_0FC0&MI_00   ; YY2990 (4-pole，接了带麦的耳机线)
USB\VID_054C&PID_0FC1&MI_00   ; YY2990 (3-pole)
USB\VID_054C&PID_0F81&MI_00   ; YY2989 (4-pole)
USB\VID_054C&PID_0F80&MI_00   ; YY2989 (3-pole)
```

VID `054C` 是索尼。注意 3 极 / 4 极是**两个不同的 PID**——说明盒子内部有 MCU 做插头检测并切换 USB 描述符。它是个"聪明"的设备，但这个聪明用在插头识别和固件更新上，**没有用在调音上**。

### 1.2 APO 注册信息（核心）

`inzoneapo.inf` 的 `[Strings]` 段：

| 项目 | 值 |
|---|---|
| 组件描述 | `INZONE APO` |
| 硬件 ID | `SWC\VEN_SONY&CID_INZONE` |
| 实现 DLL | `INZONEVirtualizer.dll` |
| **SFX（流效果）CLSID** | `{91E0E40B-B337-4FBA-B5D6-A2A6A5ECC93D}` |
| **MFX（模式效果）CLSID** | `{C3E23499-5356-4092-A10A-9821EF789E01}` |
| APO 自定义接口 | `{586bd2cd-72f1-4dab-a29a-30e8f17d2ede}` |
| AppID | `{3A1031CB-18EC-4AD0-BA2D-179808BE7F93}` |
| 标志位 | `0x0000000e` |

**"SFX" 是判定性质的关键词**：Stream Effect 运行在 Windows 音频引擎（`audiodg.exe`）内部，作用于**当前这条音频流**。这意味着——处理发生在 PC 上，不在设备上。这也解释了索尼官方文档里的原话：

> INZONE Hub 均衡器的设置**保存在电脑中**；耳机不连电脑时该设置无效。
> EQ 预设与 360 游戏空间音效**需使用 INZONE Hub 开启，仅支持在 PC 端使用。**

### 1.3 为什么"插到 Mac 上就没调音了"

SoundGuys、Tom's Guide 的实测都验证了这一点：音频盒插到 macOS / iOS / Android 上，**声音有，调音没有**。原因不是索尼故意锁，而是：macOS 根本没有 APO 这套机制，那个 DLL 在 macOS 上无法加载。Windows 之所以能，是因为它有"第三方可插拔音频处理对象"这个独有能力。

---

## 2. 破译出来的调音参数

### 2.1 滤波器结构

从 `%APPDATA%\Sony\INZONE Hub\APO\{<耳机输出端点GUID>}.yaml` 读出的 `equalizer.params`：

- **10 段**，频率固定为 `31.5 / 63 / 125 / 250 / 500 / 1k / 2k / 4k / 8k / 16k Hz`
- **type = 2**，经验证为 **RBJ peaking EQ（参数均衡 / PK）**
- **采样率 48 000 Hz**
- **Q 值非均匀**（索尼自己选的一套值，用户不可调——这也是评测里常被吐槽的点）

### 2.2 你当前的"默认"配置

| # | 频率 | Q | 增益 | 形象化 |
|---:|---:|---:|---:|---|
| 0 | 31.5 Hz | 1.00 | 0 dB | — |
| 1 | 63 Hz | 1.56 | **−2 dB** | `--` |
| 2 | 125 Hz | 1.80 | **−3 dB** | `---` |
| 3 | 250 Hz | 1.56 | **−2 dB** | `--` |
| 4 | 500 Hz | 1.00 | 0 dB | — |
| 5 | 1000 Hz | 1.56 | **+2 dB** | `++` |
| 6 | 2000 Hz | 1.35 | **+1 dB** | `+` |
| 7 | 4000 Hz | 1.00 | 0 dB | — |
| 8 | 8000 Hz | 2.40 | **−6 dB** | `------` |
| 9 | 16000 Hz | 1.00 | 0 dB | — |

建议 Preamp：**−2.11 dB**（合成频响正向峰值 +2.11 dB，为防削顶）

### 2.3 一致性验证（这一步才有说服力）

软件在写 YAML 时，同时记录了自己算出的系数。我用 RBJ Audio EQ Cookbook 的公式独立复算了一遍：

```
A  = 10^(gain/40)
w0 = 2π·fc/fs
α  = sin(w0)/(2Q)
b0 = (1 + αA)/(1 + α/A)      b1 = −2cos(w0)/(1 + α/A)      b2 = (1 − αA)/(1 + α/A)
a1 = −2cos(w0)/(1 + α/A)     a2 = (1 − α/A)/(1 + α/A)
```

结果（节选，全部 10 段均一致）：

| 频段 | a1 复算 | a1 原值 | a2 复算 | a2 原值 |
|---|---|---|---|---|
| 63 Hz / Q1.56 / −2 dB | −1.99401844 | −1.99401844 | 0.99408624 | 0.99408624 |
| 8 k / Q2.4 / −6 dB | −0.79690622 | −0.79690622 | 0.59381245 | 0.59381245 |
| 16 k / Q1 / 0 dB | 0.69783052 | 0.69783052 | 0.39566104 | 0.39566104 |

**十段全中，小数点后 8 位一致。** 这意味着这套参数可以被任意 DSP 精确重建，不存在"猜"的成分。

### 2.4 除 EQ 之外，APO 里还有什么

你的活动配置里，完整的处理链是：

```
输入 → equalizer(10段PK) → preamp(三段音调 125/500/4000Hz，mid +0.5)
     → alc(自动电平控制，阈值 −18dB，比率 1000，attack 1ms / release 1s)
     → virtualizer(360 空间音效) → amp1(−18dB) → amp2(+18dB) → 输出
```

- `virtualizer`：**开启**。资产文件是 `standard_hrtf.hki`（HRTF 头相关传输函数）+ `YY2990_standard.ba`（H6 Air 对应的耳机声学系数）+ `alc.cfg`。这是索尼闭源的听觉定位 DSP。
- `amp1/−18dB` 与 `amp2/+18dB` 是内部的余量管理（先降后升，避免中间级削顶），净增益 0。
- `drc`：当前关闭。

**这部分不能用普通 EQ 复刻。** 想要 360 空间音效，见 §5。

### 2.5 官方预设藏在哪

内置预设（`RPG/Adventure`、`FPS`、`FPS Pro`、`FPS Pro Plus`、`Cinema` 等，字符串资源里还能看到 `RPG_HDR`）的参数**不在磁盘明文文件里**，而是封在 38 MB 的 `INZONEHub.dll` 内部或用证书校验的下发资源里（`OptCertificate` / `PpCertificate`）。

但有个绕过办法：**你在界面里每切一次预设，程序就会把当前参数写进 YAML。** 所以用 `tools/harvest_presets.py` 监听这个文件，边点边抓即可。见 §6。

---

## 3. 方案 A：Windows，3.5 mm 直连主板

这是最直接可用的方案，且**滤波器完全一致**。

原理：Equalizer APO 和索尼的 APO 是**同一层**的东西——都工作在 Windows 音频引擎的 DSP 流水线里（`audiodg.exe`）。所以这里可以做到等价替换。

步骤：

1. 装 Equalizer APO（`sourceforge.net/projects/equalizerapo/`），安装时勾选你的 3.5 mm 输出设备。
2. 用本工具导出：`python tools/inzone_eq.py`，产物在 `data/equalizer_apo.txt`。
3. 把内容粘进 Equalizer APO 的 `config.txt`（或丢进 `config/` 目录）。
4. 重启音频服务或重启系统。

导出内容形如：

```
Preamp: -2.11 dB
Filter 1: ON PK Fc 31.5 Hz Gain +0 dB Q 1
Filter 2: ON PK Fc 63 Hz Gain -2 dB Q 1.56
Filter 3: ON PK Fc 125 Hz Gain -3 dB Q 1.8
...
Filter 9: ON PK Fc 8000 Hz Gain -6 dB Q 2.4
Filter 10: ON PK Fc 16000 Hz Gain +0 dB Q 1
```

> 局限：没有 360 空间音效，没有 ALC/DRC。纯音色部分完全一致。

---

## 4. 方案 B：macOS

macOS 没有 APO，全系统 EQ 必须靠**虚拟音频驱动 + 处理 App**。

三条路，按推荐度排：

| 方案 | 说明 | 适配度 |
|---|---|---|
| **SoundSource**（Rogue Amoeba，付费） | 可挂 Audio Unit 插件，支持参数均衡；系统级、按设备配置 | ⭐ 推荐，最省事 |
| **eqMac Pro**（付费） / **eqMac 免费版** | 免费版只有固定 10 段；Pro 才有全参数（可设 Q） | ⭐ 免费版够用，但 Q 不可调 |
| **BlackHole + 卷积器** | 用本工具生成的 `ir_48000.wav` 做卷积 | 完全免费，且能做到数学上精确 |

具体参数见 `data/soundsource.txt`（已生成好，对着抄即可）。

**关于"接原装声卡到 Mac"**：音频盒本身不是标准 UAC 设备，索尼官方帮助里明确说"把耳机通过 USB-C 音频盒连到电脑以外的设备，可能出现无声或音量过小"，并建议其他设备改用 3.5 mm 直连。所以 Mac 上最稳的接法是 **3.5 mm 直插**（需要一根 4 极转接头或直接用 Mac 耳机口），EQ 交给上面三个方案。

---

## 5. 方案 C：其他平台 / 想要"绝对精确"

用**卷积**。本工具已经生成了冲激响应：

- `data/ir_48000.wav`
- `data/ir_44100.wav`

这俩是立体声 32-bit float WAV，直接喂给任何卷积器（macOS 的卷积 AU、Linux 的 `convolver`、播放器内置卷积、甚至硬件 DSP）就能得到与索尼 APO **数值等价**的结果。这是最"物理"的移植方式。

Linux 用户还可以用 `data/easyeffects.json` 直接导入 EasyEffects。

---

## 6. 进阶玩法 A：把索尼自己的 APO 挂到任意输出端点上（Windows）

既然 APO 是 Windows 的通用机制，理论上可以**让 `INZONEVirtualizer.dll` 为任意声卡工作**——包括你的主板 3.5 mm 输出。这样连空间音效都能白嫖。

做法（**需要改注册表，有风险，先备份**）：

1. 找到目标输出端点的 GUID：
   `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render\{端点GUID}\FxProperties`
2. 在 `FxProperties` 下添加该端点的 SFX CLSID：
   - 值名：`{d04e05a6-594b-4fb6-a80d-01af5eed7d1d},5`（SFX 槽位）
   - 值数据：`{91E0E40B-B337-4FBA-B5D6-A2A6A5ECC93D}`
3. 在 `%APPDATA%\Sony\INZONE Hub\APO\` 下，把现有 YAML 复制一份并**重命名为该端点 GUID**（APO 按端点 GUID 找配置）。
4. 重启音频服务。

**能成的前提**：APO 内部是否校验设备身份、以及它依赖的 `.hki`/`.ba` 资产路径是否写死。YAML 里的 `device: {7FF7DD27-8463-4DEA-AEC2-CB57A87C23E5}` 看起来是个固定占位值，不像真实设备 ID，所以**有成功可能**。值得一试，但要接受失败的准备。

⚠️ 风险：搞坏了注册表可能导致系统音频不出声。开工前导出该分支备份；出问题就把加的值删掉并重启音频服务。

---

## 7. 进阶玩法 B：把 360 空间音效也搬过去

HRTF 是闭源 DSP（`.hki` + `.ba`），但**可以测量**：

1. 在本机装 VB-Audio Virtual Cable / Loopback 类工具，把 APO 处理后的输出录下来。
2. 播放对数扫频信号（或用 `REW`），经过 INZONE APO（开空间音效）后录回。
3. 反卷积得到**双耳冲激响应**（BRIR）。
4. 在 Mac 上用卷积引擎（HeSuVi 的 IR、Impulcifer、或任意 AU 卷积器）加载这对 BRIR。

这是把闭源空间音效"搬走"的唯一严谨办法。工作量不小，但技术路径是通的。

---

## 8. 抓取全部官方预设

```bash
python tools/harvest_presets.py
```

然后打开 INZONE Hub，依次点一遍所有 EQ 预设和声音配置文件。每切一次，终端会实时打印该预设的 10 段参数，并归档到 `data/`。结束后汇总表在 `data/summary.csv`。

### ✅ 实测结论：机制成立，v1 失败只是因为"监听器没开着"

**先更正一条中途的错误结论。** 第一轮抓取失败后，我曾判断"切换预设不写 YAML、
原假设被推翻"。**这个判断是错的。** 开着监听再点一遍，行为完全正常：

| 轮次 | 监听器状态 | 结果 |
|---|---|---|
| 第 1 轮（22:20–22:25） | ❌ 未运行（只有一次 12 秒测试早已结束） | 0 条。22:25:39 之后无写入，是因为点击发生在监听器之外 |
| 第 2 轮（22:41，v2） | ✅ 运行中 | **16 次变化，8 个预设全部抓到** |

所以链路的真实行为是：**在 INZONE Hub 里切一次预设 → 立刻重写
`SoundProfile.json` + `APO\{GUID}.yaml`**（两个文件在同 1 毫秒内先后落盘）。
之前"没抓到"纯粹是取样时机问题，不是机制不存在。

### 关键技巧：名字和曲线要分开取，再按时间戳配对

这是本次真正解开的地方。两个文件各只有一半信息：

| 文件 | 提供什么 | 陷阱 |
|---|---|---|
| `SoundProfile.json` | **预设名字**（字段 `EQPreset`） | 它自己的 `EQGain_*` 字段是"用户配置文件"的旧值，**不是当前预设的曲线** |
| `APO\{GUID}.yaml` | **当前预设真正在用的曲线** | 只记曲线，不记名字 |

两者同秒写入 → 按时间戳配对，就得到"名字 + 曲线"的完整对应关系。
配对正确性有强证据：`BASS_BOOST` 配到的曲线是 `+12 / +12 / +8 dB` 的纯低频抬升。

### v1 → v2 的脚本变化

| 项目 | v1 | v2 |
|---|---|---|
| 监听范围 | 只有 `APO\*.yaml` | 递归整棵 Sony 配置树（约 47 个文件） |
| 变化记录 | 不记录 | 任何文件被写都记时间戳到 `data/watch.log` |
| 全平配置 | 静默跳过 | 照抓（`FLAT` 就是全平，不能漏） |
| 存活证明 | 无 | 每 15 秒心跳 |
| 时长 | 无限（需 Ctrl+C） | `--seconds N`，默认 1800 |

```bash
python tools/harvest_presets.py --seconds 60   # 先跑 60 秒做连通性测试
```

**判据**：切一个预设后，`watch.log` 里应立刻出现两条 `# 变化`（先 `SoundProfile.json`、
后 `APO\*.yaml`，同一秒）。

抓完之后用 `tools/export_presets.py` 配对成成品：

```bash
python tools/export_presets.py
```

产出 `data/presets.md`（对照表）、`data/presets.csv`、`data/presets_apo/*.txt`
（每个预设一份 Equalizer APO 可直接用的配置）。

### 抓到的 8 个预设（名称已按界面截图核对）

增益单位为 dB。界面名 ↔ 内部名对照如下：

**走「标准均衡器」的 7 个**（fc = 31.5 / 63 / 125 / 250 / 500 / 1k / 2k / 4k / 8k / 16k Hz）

| 界面名 | 内部名 | 31.5 | 63 | 125 | 250 | 500 | 1k | 2k | 4k | 8k | 16k | 建议 Preamp |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 平直 | `FLAT` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | — |
| FPS-1 | `FPS1` | −6 | −2 | +2 | +3 | +2 | 0 | +2 | +3 | +1 | −2 | −3.56 |
| FPS-2 | `FPS2` | −6 | −3 | −1 | 0 | +1 | +2 | +3 | +1 | −1 | −3 | −3.49 |
| FPS-3 | `FPS3` | −6 | −1 | +1 | +2 | +1 | 0 | −1 | −5 | −9 | −9 | −2.32 |
| 低音增强 | `BASS_BOOST` | +12 | +12 | +8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | −13.17 |
| 音乐/视频 | `MUSIC_VIDEO` | +3 | +2 | +2 | +1 | 0 | −1 | −2 | −2 | −9 | −9 | −3.38 |
| 自定义 | `CUSTOM` | 0 | −2 | −3 | −2 | 0 | +2 | +1 | 0 | −6 | 0 | −2.11 |

**走「模式均衡器」的 1 个**（fc = 50 / 84 / 150 / 800 / 1000 / 1650 / 1800 / 4100 / 6500 / 9500 Hz）

| 界面名 | 内部名 | 50 | 84 | 150 | 800 | 1k | 1.65k | 1.8k | 4.1k | 6.5k | 9.5k | 建议 Preamp |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| RPG/Adventure | `IMMERSION_FLAT` | +6.5 | −11 | +5.3 | −5.5 | +4.2 | −4.5 | +3 | −8 | 0 | −8 | −5.44 |

> Preamp 的算法本轮做了修正：原来的采样网格只到约 11 kHz（漏掉 16 kHz 段），
> 且步长 5% 可能跳过波峰。现在把每个频段的中心频率及上下一个倍频显式加入网格，
> 因此 FPS-2、低音增强、RPG/Adventure 三档的 Preamp 值较上一版略有变化。

### ⚠️ 关键坑：INZONE 里有**两套并行**的均衡器

APO 配置里同时存在 `equalizer` 与 `mode_equalizer` 两个区块，**同一时刻只启用其中一套**：

| 区块 | 频点网格 | 谁在用 |
|---|---|---|
| `equalizer` | 31.5 / 63 / … / 16 k Hz | 平直、FPS-1/2/3、低音增强、音乐/视频、自定义 |
| `mode_equalizer` | 50 / 84 / 150 / 800 / 1 k / 1.65 k / 1.8 k / 4.1 k / 6.5 k / 9.5 k Hz | **只有 RPG/Adventure** |

**只读 `equalizer` 会把 RPG/Adventure 误判成"全平"** —— 这正是第一版抓取器犯的错。
`type` 字段一个写 2、一个写 6，但经系数复算（见下），**两者都是 RBJ peaking，只是频点网格不同**：

```
 #       fc      Q    gain          a1复算         a1原值          a2复算         a2原值
 0       50    2.3    +6.5   -1.99800173  -1.99800173    0.99804452   0.99804452
 1       84    1.5     -11   -1.98616701  -1.98616701    0.98628708   0.98628708
 7     4100      4      -8   -1.56072239  -1.56072239    0.81604695   0.81604695
 9     9500      3      -8   -0.51424945  -0.51424945    0.59983295   0.59983295
（十段全中，小数后 8 位一致）
```

用这个办法可以一次性把索尼所有官方预设（含 RPG/Adventure 那个和 PlayStation Studios 联合调的音）全部抓成可移植数据。

---

## 9. 文件清单

```
inzone-h6air-toolkit/
├── replacement_matrix.md     可替代性对照：软件层（可搬）vs 硬件层（搬不动）
├── tools/
│   ├── inzone_eq.py           参数提取 + 五平台导出 + 系数验证
│   ├── harvest_presets.py     预设监听抓取（v2：递归监听整棵配置树）
│   ├── export_presets.py      把抓到的快照配对成"界面名 + 曲线"
│   ├── probe_spatial_assets.py 探查空间音效 DSP 资产（.hki/.ba）结构
│   └── make_sweep.py          生成测量用对数扫频与反卷积滤波器
└── out/
    ├── presets.md            ★ 8 个官方预设对照表（界面名，人看）
    ├── presets.csv           ★ 同上，表格版（含每段 Q 值）
    ├── presets_apo/          ★ 每个预设一份 Equalizer APO 配置
    ├── sweep_48000.wav         测量扫频（20 Hz → 20 kHz）
    ├── sweep_inverse.wav       配套反卷积滤波器
    ├── bands.json            当前配置的频段参数（平台无关，含验证元数据）
    ├── bands.csv             同上，表格版
    ├── equalizer_apo.txt     → Windows Equalizer APO（当前配置）
    ├── soundsource.txt       → macOS（SoundSource / eqMac Pro）
    ├── easyeffects.json      → Linux EasyEffects
    ├── ir_48000.wav          → 任意平台卷积器（32-bit float 立体声）
    ├── ir_44100.wav          → 同上，44.1 kHz 版
    ├── response.csv          合成频响曲线（可直接画图）
    └── harvested/            抓取原始快照 + watch.log + summary.csv
```

运行：

```bash
python tools/inzone_eq.py
```

---

## 10. 参考与依据

- 本机实际文件：
  - `C:\Program Files\Sony\INZONE Hub\driver\inzoneapo.inf`（APO 注册）
  - `C:\Program Files\Sony\INZONE Hub\driver\inzoneext.inf`（把 APO 组件挂到设备上）
  - `C:\Program Files\Sony\INZONE Hub\driver\INZONEHeadset.inf`（音频盒 USB 标识）
  - `C:\Program Files\Sony\INZONE Hub\INZONEVirtualizer.dll`（DSP 实现）
  - `%APPDATA%\Sony\INZONE Hub\APO\*.yaml`（实时参数）
  - `%APPDATA%\Sony\INZONE Hub\SoundProfile.json`（用户配置）
  - `C:\ProgramData\Sony\INZONE Hub\VirtualizeStandard\`（HRTF 与耳机声学资产）
- 索尼官方：
  - 帮助指南：`helpguide.sony.net/mdr/2990/`（H6 Air 连接说明）
  - 支持文章 00279893：INZONE Hub 设置何时生效
  - 中国官网产品页：明确"EQ 预设 / 360 空间音效仅支持在 PC 端使用"
- RBJ Audio EQ Cookbook（双二阶滤波器公式，用于系数验证）

---

## 11. 一点边界说明

以上全部操作都发生在**你自己的设备和你自己的电脑**上，目的是让买来的耳机在你自己的其他设备上正常使用（互操作性）。没有绕过任何版权保护措施，也没有分发索尼的任何代码或资产。`.hki` / `.ba` / DLL 都留在原机，没有外传。

建议不要把导出的索尼专有资产（尤其是 HRTF 文件）公开分发。

---

## 12. 第二轮勘定补充（回答"能否完美替代"）

在 §1–§11 的基础上又翻了一遍 `ProgramData` 与 DSP 资产，补充以下事实：

### 12.1 麦克风处理也在电脑上 —— 但那条 7 段曲线不是麦克风的

> **⚠️ 更正**：本节最初写的是"麦克风有一条 7 段清晰语音 EQ"，查过驱动 INF 后确认**归错了**。
> 驱动 `inzoneext.inf` 明确把 APO 关联到麦克风节点：
>
> ```
> ;;  FX\1:  KSNODETYPE_MICROPHONE:  INZONE H3
> HKR,FX\1,%PKEY_FX_Association%,,%KSNODETYPE_MICROPHONE%
> ;;  FX\1: KSNODETYPE_HEADSET_MICROPHONE: INZONE H9/H7 Chat
> HKR,FX\1,%PKEY_FX_Association%,,%KSNODETYPE_HEADSET_MICROPHONE%
> ```
>
> `FX\0` 是输出通道，**`FX\1` 就是麦克风通道** —— 所以麦克风确实由同一套 Sony APO
> 在 PC 上处理。但本机麦克风端点**只启用了动态范围控制（DRC），均衡器是关的**；
> 那条 7 段曲线在 `Chat.yaml` 里，对应的是**「聊天声道」**，不是麦克风。

`C:\ProgramData\Sony\INZONE Hub\APO\Chat.yaml` 里这条 7 段曲线：

| # | fc | Q | gain | type |
|---:|---:|---:|---:|---:|
| 1 | 100 Hz | 1.0 | 0 dB | 1 |
| 2 | 990 Hz | 1.0 | −2 dB | 4 |
| 3 | 1300 Hz | 3.0 | +2 dB | 6 |
| 4 | 2300 Hz | 3.0 | +2 dB | 6 |
| 5 | 3760 Hz | 3.5 | −5 dB | 6 |
| 6 | 4600 Hz | 8.0 | +6 dB | 6 |
| 7 | 5300 Hz | 8.0 | −5 dB | 6 |

附带 `amp1 = −2 dB`（补偿增益），virtualizer / alc / drc 全关。
第 1 段 `fc=100 Hz / gain=0 / type=1` 是典型的**高通（去低频轰隆）**，第 2 段 `type=4` 像是陷波/搁架。

聊天声道只在部分机型上存在（H9/H7 与 YY2976/YY2977/YY2987 的 Game/Chat 拓扑），
H6 Air（YY2990）本机也没有对应的第三个实时配置文件，**很可能用不到这条曲线**。
⚠️ 此为推断，未实测确认。

**本机麦克风的真实配置**是 `%APPDATA%\...\APO\{<麦克风端点GUID>}.yaml`，
与索尼自带的 `Mic.yaml` 模板逐字段一致：

| 区块 | 状态 |
|---|---|
| `equalizer` / `mode_equalizer` | 关 |
| `virtualizer` | 关 |
| `alc` | 关 |
| **`drc`** | **开**（上阈值 −12 dB 比率 2.0；下阈值 −44 dB 比率 2.0；静音阈值 −64 dB 比率 3.0） |

**结论**：麦克风的处理确实也在电脑上、可以搬走，
但**它只有动态范围控制，没有均衡器** —— 要等效复刻，需要的是一个压缩器，不是 EQ。
三层都不含硬件存储：麦克风是模拟驻极体、音频盒只做数模/模数转换、参数全在 PC 上。

### 12.2 滤波器类型枚举不止一种

实际出现 `type = 1 / 2 / 4 / 6`。其中 **type 2 已用 RBJ 公式验证为 peaking（PK）**；
type 6 出现在成对的升降增益里（疑为另一种钟形/搁架变体），type 1/4 见上。
搬运时不要把 type 6 当 peaking 直接抄，需要单独标定。

### 12.3 动态处理与保护

- `peak_limiting.cfg`：`peak_limiting = -20.0`（−20 dBFS 峰值限幅，独立于 EQ）。
- `alc.cfg` / `alc_for_downmix.cfg`：`fs=48000`，`alc_delay=24`，压缩门限 `comp_thresh=0000`、噪声门 `gate_thresh=8000`，两版差别只在 `gain`（1 与 0）。
- 这些是**动态**处理，纯 EQ 替代不了，需用压缩器/限幅器重建。

### 12.4 DSP 资产其实是"可解析的短滤波器"，不是黑盒

| 文件 | 体积 | 头部 | 判断 |
|---|---:|---|---|
| `YY2990_standard.ba`（H6 Air 对应） | 192 B | magic `ba00`，内含 `48000`（fs） | 体量只有"几个双二阶"级别 → 耳机声学补偿，**很可能可完整还原系数** |
| `standard_hrtf.hki` | 57 952 B | magic `hki2`，内含 `48000` 及抽头/方向计数字段 | 结构化 FIR 滤波器组，**有机会离线解析**，未必只能靠扫频录制 |
| `downmix.hki` | 57 952 B | 同上但 MD5 不同（`c7174e…` vs `c749cc…`） | 是 standard 的多声道下混版，配合 `template.yaml` 里 `downmix.ch7_1.enable` 使用 |

这比原先的判断乐观：360 空间音效不必一定走"播放扫频→回录→反卷积"的测量路线，
**优先尝试直接解 `.hki` 的系数表**。

### 12.5 两个 APO 实例

`%APPDATA%\Sony\INZONE Hub\APO\` 下有两个 HASH 命名的 YAML，参数彼此独立，
`device` 字段都是同一个占位 GUID `{7FF7DD27-8463-4DEA-AEC2-CB57A87C23E5}`：

| 文件 | virtualizer | EQ | ALC | amp1/2 | 说明 |
|---|---|---|---|---|---|
| `{<耳机输出端点GUID>}.yaml` | 开 | 开（10 段 0/−2/−3/−2/0/+2/+1/0/−6/0） | 开 | −18/+18 | 当前活动链（含空间音效） |
| `{<麦克风端点GUID>}.yaml` | 关 | 关 | 关 | 关 | **麦克风端点**；与 `Mic.yaml` 模板逐字段一致，只开 `drc` |

占位 GUID 不随设备变化这一点，是 §6「进阶玩法 A」有成功可能的依据之一。

---

## 13. 一张对照表回答"能否完美替代"

详见 **`replacement_matrix.md`**。要点：

- **软件层的调音**（耳机 EQ / 麦克风 EQ / 三段音调）→ ✅ **可 100% 等效复刻**，已验证。
- **动态处理**（ALC / DRC / 限幅 −20 dBFS）→ ⚠️ 可重建，但不是 EQ，需要压缩器。
- **360 空间音效** → ⚠️ 可解析或可测量，属"能搬但要做工"。
- **DAC / 麦克风 ADC / 非标 UAC 驱动 / 线控 HID** → ❌ **软件做不到**，只能换硬件。

即：**能扔掉的是 INZONE Hub，扔不掉的是盒子里的 DAC 和 ADC。**

---

## 14. 空间音效（360 虚拟环绕）：资产是加密的，改走测量路线

### 14.1 调查结果

`C:\ProgramData\Sony\INZONE Hub\VirtualizeStandard\` 下的 DSP 资产：

| 文件 | 大小 | 头部 | 载荷 | 结论 |
|---|---:|---|---|---|
| `standard_hrtf.hki` | 57 952 B | 明文 | 熵 7.997 / 8.0（上限的 99.96%） | **加密或压缩** |
| `downmix.hki` | 57 952 B | 明文 | 熵 7.997 | **加密或压缩** |
| `YY2990_standard.ba` | 192 B | 明文 | 144 B，样本太小无法据熵定性 | 倾向同样受保护 |
| `alc.cfg` | 219 B | **全明文** | — | 参数已读出 |
| `peak_limiting.cfg` | 21 B | **全明文** | — | `peak_limiting=-20.0` |

头部字段是**明文可读**的：偏移 56 = 48000（采样率）、偏移 36 常量 2021、
偏移 40 常量 10、偏移 88 = 512、偏移 92 = 14；偏移 44/48/52 在
`standard_hrtf.hki` 与 `downmix.hki` 之间不同（`29/16/1` 对 `4/19/9`），
应是布局描述字段。两个 `.hki` 的前 8 字节完全一致，只有后面的哈希不同。

三条独立证据指向"**被主动保护**"，而不是"没找到解析方法"：

1. `standard_hrtf.hki` 载荷熵 **7.997 bits/byte** = 理论上限的 **99.96%**。
   真正平滑的 HRIR 系数不可能有这个熵值。
2. 按 float32 与 int16 两种方式读载荷，得到的都是乱码。
3. `INZONEVirtualizer.dll`（2.4 MB）**静态链接了 OpenSSL**，符号里含
   AES / RSA / PKCS7-signedData / CFB / ECB / CBC / GCM / ChaCha；
   且 `.hki` 与 `.ba` 的长度都整除 16（AES 分组长度）。

复现：`python tools/probe_spatial_assets.py`

### 14.2 为什么就此打住

再往下走，只剩"去 `INZONEVirtualizer.dll` 里定位并提取解密密钥"这一条路。
那是绕过技术保护措施，性质与"复刻一段 EQ 曲线"完全不同 —— 后者是互操作性，
前者是破解。**这条路我不做。**

而且从工程角度看，**测量法本来就更好**。

### 14.3 测量法：直接取"听到的结果"

```
播放对数扫频 → 经过耳机 + 空间音效 → 麦克风回录 → 与反卷积滤波器卷积 → 得到 BRIR
```

得到的 BRIR 是冲激响应文件，任何平台的卷积器都能加载。它记录的是**实际听到的结果**，
比"照抄内部系数"更完整 —— 因为那条 DSP 链里除 HRTF 外还有下混、ALC、限幅，
即使拿到系数也未必能正确重建。

工具已备好：

```bash
python tools/make_sweep.py
# 产出 out/sweep_48000.wav（20 Hz→20 kHz，10 s）
#      out/sweep_inverse.wav（Farina 反卷积滤波器）
```

步骤：

1. 戴上耳机（或固定在支架上），麦克风贴近耳罩、位置固定不动。
2. 播放 `data/sweep_48000.wav`，输出设备选 INZONE 音频盒。
3. 在 INZONE Hub 里把 360 空间音效设成要测的那一档。
4. 用录音软件录下麦克风信号（保持 48 kHz）。
5. 与 `data/sweep_inverse.wav` 卷积，得到 BRIR。

⚠️ **麦克风自身的频响会叠加进结果**。要更准需用测量麦，或先用同样的设备
测一次"空间音效关闭"作为参考做归一化。

### 14.4 想快速对比"空间音效到底改了什么"

不回头录也能做：用 VB-CABLE 之类的虚拟声卡在系统内部取前后信号做差分，
或对同一段音乐分别在开 / 关两种状态下录音对比。精度低于扫频法，
但能看到"低频是否被抬、高频是否被压、串音是否变大"这类宏观差异。

# 阿里云 ECS 部署实录（Windows Server 2022，无 GPU）

> 本文记录 2026-11 在实际 4 vCPU / 8 GB 阿里云 ECS 上部署雪乃语音栈的**真实过程与实测数据**，
> 包含 12 个踩坑和解法。与 `docs/ECS部署-Windows.md`（操作清单）互补，本文是**实测记录**。

## 1. 最终结论（先说重要的）

| 结论 | 数据 |
|---|---|
| CPU 推理能跑通 | ✅ 生成 32 kHz / 16 bit / 单声道真实语音 |
| **但速度不可用于实时对话** | **RT ≈ 6~7x**：4.7 秒语音需 **29 秒**合成 |
| 根本原因 | GPT-SoVITS 的 GPT 解码是**自回归串行**的，加线程无效 |
| 实例 CPU 实际是 | **2 物理核 / 4 线程** @ 2.5 GHz（"4 vCPU" ≠ 4 物理核） |

一条 10 秒语音回复需要约 **69 秒**。对 QQ 这类**异步**聊天尚可忍（发完等一会儿），
但绝对不能用于实时语音通话。

**如果要实时**，只有三条路：上 GPU 实例、改用云端声音克隆 API、或继续用本地 RTX 4060。

## 2. 环境实测数据

```
实例      ecs.e-c1m2.xlarge（经济型 e）
CPU       Intel Xeon Platinum @ 2500 MHz, 2 cores / 4 threads
内存      7918 MB 总，空闲 1731 MB（TTS 常驻约 2949 MB）
磁盘      C: 40 GiB，部署后剩约 18 GB
系统      Windows Server 2022 Datacenter, build 20348.5622
Python    C:\Program Files\Python39\python.exe (3.9.13)
```

算力基准（torch matmul）：

| 测试 | 结果 |
|---|---|
| 2000×2000 ×5，多线程（2 线程） | 0.71 s → **113 GFLOPS** |
| 1200×1200 ×5，单线程 | 0.19 s → **91 GFLOPS** |

算力本身不弱（AVX-512），慢的是串行解码，不是矩阵运算。

## 3. 上传速度实测（决定部署策略的关键）

```
100 MB scp 到 ECS：310 秒 → 0.32 MB/s（约 2.6 Mbps）
8.86 GB 整合包 → 约 469 分钟（7.8 小时）  ← 不可行
```

**所以放弃了传整合包，改为「服务器自建环境 + 只传独有文件」**：

| 内容 | 大小 | 方式 |
|---|---|---|
| 雪乃权重 `default.ckpt` + `Yukino_Strong.pth` | 229 MB | scp 上传（约 4.3 分钟） |
| 参考音频 | 0.6 MB | scp 上传 |
| 项目代码 | 4.6 MB | scp 上传 |
| Python 3.9.13 | 27.9 MB | 阿里云镜像下载（107 秒） |
| GPT-SoVITS 源码（tag 20250606v2pro） | 6.4 MB | codeload（**3 秒**） |
| BERT `chinese-roberta-wwm-ext-large` | 1246 MB | hf-mirror |
| hubert `chinese-hubert-base` | 360 MB | hf-mirror |
| open_jtalk 词典 | 22.6 MB | hf-mirror（**2 秒**） |
| nltk_data | 9.5 MB | hf-mirror |
| pip 依赖 | 约 2 GB | 阿里云 pypi 镜像 |

## 4. 网络可达性实测

| 目标 | 结果 |
|---|---|
| `mirrors.aliyun.com` / `pypi.org` / `www.python.org` | ✅ |
| `www.modelscope.cn` | ✅ |
| `hf-mirror.com` | ✅ |
| **`huggingface.co`** | ❌ 被封，必须用 hf-mirror |
| `github.com` / `codeload.github.com` / `api.github.com` | ✅ |
| **`download.pytorch.org`** | ⚠️ 可达但**仅 11 kB/s**，根本不能用 |

## 5. 十二个坑与解法

### 5.1 SSH 相关

| # | 坑 | 解法 |
|---|---|---|
| 1 | `Add-WindowsCapability` 装 OpenSSH 卡住 | 先确认在**哪台机器**上跑（`build 20348`=Server，`22631`=Win11）。本机卡住毫无意义 |
| 2 | 公钥登录被拒 | `administrators_authorized_keys` 的 ACL **必须**只有 `Administrators:F` + `SYSTEM:F`，否则 sshd 直接拒绝 |
| 3 | 远端默认 shell 是 `cmd.exe` | 嵌套引号会被打散。**统一用 stdin 管道传 PowerShell 脚本**：`Get-Content x.ps1 -Raw \| ssh ... "powershell -NoProfile -Command -"` |
| 4 | 中文输出经 SSH 乱码 | 远端脚本**只用 ASCII 输出**（`[Console]::OutputEncoding = ASCII`） |
| 5 | SSH 断开后子进程被杀 | 后台任务用 **WMI 创建**：`Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=...}`，真正脱离会话 |
| 6 | 被 kill 的会话留下僵尸下载进程锁文件 | `Get-CimInstance Win32_Process -Filter "Name='powershell.exe'"` 找出 `Command -` 的进程并 `Stop-Process` |

### 5.2 Python / 依赖相关

| # | 坑 | 解法 |
|---|---|---|
| 7 | `import torch` 报 `c10.dll` 加载失败 | 缺 **VC++ 运行库**：装 `vc_redist.x64.exe`（24.4 MB，aka.ms 下载 68 秒） |
| 8 | torch 版本冲突 `ResolutionImpossible` | `torch-einops-utils` 要求 torch≥2.5 锁死版本。**先卸载它**，再装目标版本 |
| 9 | `torchaudio` 报 `WinError 127` | 用 **torch 2.5.1 + torchaudio 2.5.1**（2.4.1 组合加载 DLL 失败）。注意 2.6+ 的 `weights_only` 默认值变更会影响老代码 |
| 10 | `pyopenjtalk` 要从源码编译 | 用 **`pyopenjtalk-prebuilt`**（有预编译二进制） |
| 10b | pyopenjtalk 首次调用**偷偷下载 22.6 MB 词典**，速度 3~6 kB/s 卡死数小时 | 预先从 hf-mirror 下 `open_jtalk_dic_utf_8-1.11.tar.gz`，**解压到** `site-packages\pyopenjtalk\open_jtalk_dic_utf_8-1.11\`（注意：它认的是**解压后的目录**，不是 tar 包） |
| 11 | `jieba_fast` 编译失败 | 做成**包 shim**：建 `site-packages\jieba_fast\__init__.py` + `posseg.py`，内部 `from jieba import *`。必须是**包**，因为代码会 `import jieba_fast.posseg` |
| 12 | `opencc --no-binary` 需编译 | 用 `opencc-python-reimplemented`（纯 Python，模块名同为 `opencc`） |

### 5.3 其他

| # | 坑 | 解法 |
|---|---|---|
| 13 | `x_transformers` 最新版用了 Python 3.10+ 语法（`int \| tuple[...]`） | 降到 **2.0.2**（照抄整合包版本） |
| 14 | 版本必须**照抄本地整合包** | `runtime\Lib\site-packages\*.dist-info` 就是已验证的版本清单，见下表 |
| 15 | BERT 目录只有 `pytorch_model.bin`，缺 `config.json` | 补下 `config.json` / `tokenizer.json` / `vocab.txt` / `tokenizer_config.json` / `special_tokens_map.json` |
| 16 | YAML 里的 UTF-8 中文注释导致 `UnicodeDecodeError` | 配置**必须是纯 ASCII**（中文 Windows 用 cp936 读文件） |
| 17 | `sv.py` 用 `os.getcwd()` 拼路径 | 必须**在 GPT-SoVITS 根目录启动**，否则 `No module named 'ERes2NetV2'` |

### 5.4 照抄的依赖版本（来自本地整合包，已验证可用）

```
x_transformers       2.0.2
transformers         4.43.0     (要求 >=4.43,<=4.50)
numpy                1.23.4
librosa              0.9.2      (requirements 里写 0.10.2)
peft                 0.14.0
pytorch_lightning    2.1.3
torchmetrics         1.2.1
fast_langdetect      0.3.1      (1.x API 不兼容)
split_lang           2.0.6
pyopenjtalk          0.3.4 / prebuilt 0.3.0
funasr               1.0.27     (推理不需要)
```

## 6. 实测性能

测试文本：雪乃台词 3 句（22 / 20 / 13 个日文字符），`version=v2`、`device=cpu`、`is_half=false`。

| | 合成耗时 | 音频时长 | RT |
|---|---|---|---|
| 冷启动第 1 句 | 32.0 s | 3.78 s | 8.47x |
| 第 2 句 | 47.4 s | 7.26 s | 6.53x |
| 第 3 句 | 29.1 s | 4.74 s | 6.13x |
| **综合** | | | **6.88x** |

线程数从 2 提到 4 几乎无改善（7.58x → 6.88x，属测量噪声），印证瓶颈是串行解码。

## 7. 三条提速路线对比

| 方案 | 实时性 | 成本 | 保留雪乃音色 | 备注 |
|---|---|---|---|---|
| **A. 维持 CPU，接受慢** | RT 6~7x，10 秒回复等 69 秒 | ¥0（已有实例） | ✅ | 适合 QQ 异步聊天；配合"短句+缓存"可缓解 |
| **B. 升级 GPU 实例** | RT < 0.3x，实时 | 约 ¥1000~2000/月（T4/A10） | ✅ | 效果最好也最贵 |
| **C. 云端声音克隆 TTS** | 延迟 < 300 ms，实时 | 按量计费，通常远低于 GPU | ⚠️ 接近但非完全一致 | 阿里云语音合成声音复刻 / 火山引擎声音复刻 |
| **D. 本地 RTX 4060** | 实时 | ¥0 | ✅ | 但需要电脑常开，与"电脑不开也能聊"冲突 |

**务实建议**：QQ 文字聊天**即时**（不依赖 TTS），语音消息走异步。
先用 A 跑起来验证体验，如果觉得语音等待太久，再上 C（性价比）或 B（效果）。

## 8. 目录布局（服务器最终形态）

```
C:\yukino_tts\                      GPT-SoVITS 根目录（启动时 cwd 必须是这里）
  api_v2.py                         推理服务入口
  GPT_SoVITS\pretrained_models\     基础模型
    chinese-roberta-wwm-ext-large\  BERT（1246 MB + 元数据文件）
    chinese-hubert-base\            CNHuBERT（360 MB）
  GPT_weights\default.ckpt          雪乃 GPT 权重（147.9 MB）
  SoVITS_weights\Yukino_Strong.pth  雪乃 SoVITS 权重（81 MB）
  ref\001.wav                       参考音频（0.6 MB）

C:\yukino_app\                      应用层
  .env                              配置（含访问令牌）
  configs\tts_infer_cpu_server.yaml 纯 ASCII、version=v2、相对路径
  app\ persona\ tests\ ...

C:\Program Files\Python39\          Python 3.9.13
C:\yukino_numba\                    numba 缓存（必须，否则 librosa 静默卡死）
```

## 9. 启动命令

```powershell
# 音色服务（端口 9881）
$env:NUMBA_CACHE_DIR = "C:\yukino_numba"
$env:OMP_NUM_THREADS = "4"
Set-Location C:\yukino_tts
& "C:\Program Files\Python39\python.exe" -u api_v2.py -a 127.0.0.1 -p 9881 `
    -c C:\yukino_app\configs\tts_infer_cpu_server.yaml
```

## 10. 权重完整性校验值

```
default.ckpt        sha256 = 276a7993b79f21481d9ef142d18177b922f13cb4a8aab107e8eb9ef40f3794fe
Yukino_Strong.pth   sha256 = 0d4d37c8db37bcc10e2fbff7df0b42c357ae41c165d20a7f1c328d19e42941b7
yukino_app.zip      sha256 = 5819d9c012b8349fcee5b89b6b213f0cc465b90ae0e74d270c11c43ce7ec45be
```

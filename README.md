# 雪之下雪乃 · AI 语音伴侣

本地运行的雪乃语音对话机器人：**DeepSeek 说话 → 雪乃音色朗读 → 立绘跟着情绪变**。

- 声音：GPT-SoVITS v2 + 自己训练的雪乃音色（Strong / Weak 两档）
- 大脑：DeepSeek（OpenAI 兼容协议），可换 ollama 本地模型兜底
- 记忆：SQLite 全量落库 + 好感度持久化 + 关键词召回
- 界面：Streamlit 网页版 / 零依赖终端版

---

## 快速开始

### 1. 启动本地思考模型（默认，不需要 API Key）

```powershell
ollama serve                     # 另开一个窗口，或让它开机自启
ollama list                      # 确认有 qwen2:7b（本项目默认用它）
```

模型不叫 `qwen2:7b` 就改 `.env` 里的 `OLLAMA_MODEL`。想换云端 API：

```dotenv
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-xxxx
```

### 2. 配置

```powershell
Copy-Item .env.example .env
# 默认 LLM_PROVIDER=ollama，无需填 Key 即可跑
```

> `.env` 已在 `.gitignore` 里，不会被提交。

### 3. 装依赖（只有网页版需要）

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

终端版 `app/cli.py` **不需要装任何东西**，标准库直接跑。

> 本机如果装了「GitHub 加速」类工具（Watt Toolkit / SteamTools），
> 它们会用自签证书中间人代理 HTTPS，导致 pip 报证书错误。此时设置：
> `$env:PIP_CERT='D:\Yukino\configs\windows-roots.pem'`（该文件由 `scripts/git-net.ps1` 生成）。

### 3. 启动

```powershell
.\scripts\run.ps1              # 网页版（自动拉起语音服务）
.\scripts\run.ps1 -Cli         # 终端版
.\scripts\run.ps1 -Mock        # 离线演示（不用 ollama 也不用 API）
.\scripts\run.ps1 -NoTts       # 纯文字
```

语音服务单独启动（需要独立窗口）：

```powershell
.\scripts\start_tts.ps1                  # 强语气档
.\scripts\start_tts.ps1 -Voice weak      # 弱语气档
```

首次加载约 15~40 秒；期间对话会自动降级为纯文字，不会报错白屏。

---

## 目录结构

```
D:\Yukino\
├─ app\                      应用代码（核心链路零依赖）
│   ├─ config.py             配置加载（标准库 .env 解析）
│   ├─ cli.py                终端对话
│   ├─ pipeline.py           流式编排：LLM → 标签协议 → 分句 → TTS
│   ├─ llm\
│   │   ├─ client.py         OpenAI 兼容流式客户端（urllib + SSE）
│   │   ├─ persona.py        人格卡渲染
│   │   └─ protocol.py       标签协议解析（替代原来的正则拆括号）
│   ├─ state\
│   │   ├─ db.py             SQLite 建表
│   │   ├─ session.py        好感度持久化 + 代码级防刷分
│   │   └─ memory.py         长期记忆
│   ├─ tts\
│   │   ├─ gsv.py            GPT-SoVITS 客户端
│   │   └─ cache.py          音频缓存 / WAV 头修补 / 拼接 / 体检
│   └─ ui\
│       ├─ streamlit_app.py  网页界面
│       ├─ expressions.py    表情差分
│       └─ assets\           立绘与表情
├─ persona\yukino.json       人格卡（人设 / 语气 / 好感度规则 / 输出格式）
├─ configs\
│   └─ tts_infer_yukino.yaml 语音服务配置（CUDA + v2 + 雪乃权重）
├─ scripts\
│   ├─ start_tts.ps1         启动语音服务
│   ├─ run.ps1               一键启动
│   └─ dedupe.ps1            重复文件报告/整理
├─ tests\                    58 个单元测试
├─ docs\
│   ├─ 改造方案.md
│   └─ 同类项目调研.md
├─ .env.example
├─ requirements.txt
└─ README.md
```

---

## 常用命令

```powershell
# 测试
python -m unittest discover -s tests -t . -v

# UI 冒烟测试（用假 streamlit 桩跑一遍 app/ui/streamlit_app.py，
# 不需要真的装 streamlit，也不会开浏览器）
$env:PYTHONPATH='D:\Yukino\tests\ui_stub;D:\Yukino'
$env:YUKINO_MOCK_LLM='1'
$env:STUB_CHAT_INPUT='今天有点累'
python tests\ui_smoke.py

# 配置自检
python -m app.config

# 终端对话
python -m app.cli
python -m app.cli --mock --once "今天有点累"

# 重复文件报告（不修改文件）
.\scripts\dedupe.ps1 -ShowObsolete
.\scripts\dedupe.ps1 -Apply            # 硬链接去重
```

---

## 本地大模型 vs 云端 API（重要）

默认走**本地 ollama**（`qwen2:7b`），不需要 API Key、不联网、不花钱。但要清楚代价：

| 维度 | 本地 ollama (qwen2:7b Q4) | DeepSeek API |
|---|---|---|
| 生成速度（TTS 常驻时） | **8 tok/s** | 网络决定，通常更快 |
| 生成速度（TTS 停止时） | **38~42 tok/s** | — |
| 标签协议遵循 | 加 few-shot 后 **5/5** | 原生就好 |
| 日语台词质量 | 一般，偶尔用错语域（如「〜ぞ」） | 明显更好 |
| 费用 / 隐私 | 免费 / 全本地 | 按量计费 / 需联网 |

### 那个 5 倍差距是怎么回事

实测同一条命令、同一个模型：

| 场景 | GPU 显存占用 | 生成速度 |
|---|---|---|
| GPT-SoVITS 常驻 | 7418 / 8188 MiB | **8 tok/s** |
| GPT-SoVITS 停止 | 5992 MiB | **38~42 tok/s** |

8 GB 显存被「GPT-SoVITS + 7B 模型 + 其他桌面程序」占满后，驱动会把溢出的层丢到系统内存，
`ollama ps` 仍显示 `100% GPU`，但实际性能已经崩了。这是硬件硬约束，不是代码能绕过的。

**三条对策**（按推荐顺序）：

1. **缩短回复 + 限制输出**：`.env` 里 `LLM_MAX_TOKENS=240`（默认）。雪乃本来就只该说 1~3 句。
2. **用完即卸载**：`OLLAMA_KEEP_ALIVE=0`。生成完立刻还显存给 TTS，代价是下一轮重新加载 2~8 s。
3. **换更小的模型**：`ollama pull qwen2.5:3b-instruct`（约 2 GB），能和 TTS 共存且全速。联网后建议优先做这一步。

### 小模型的格式补偿

`qwen2:7b` 裸跑时 5 个标签只有 **1/3 次写全**，还常漏 `<ja>` 开标签。做了两处修复后稳定 5/5：

1. `persona/yukino.json` 里加 `few_shot`（3 个完整示例）—— 最有效的单项改动
2. **历史消息必须还原成标签格式**（`Session.history_messages`）—— 之前历史里只放裸日语，
   模型会模仿「先裸说一句再补标签」，导致 `<ja>` 丢失、整句台词被丢弃
3. 解析器容错：只有 `</ja>` 没有 `<ja>` 时，把闭合标签前的文本当作台词

---

## 实测性能（本机 RTX 4060 Laptop 8G）

| 环节 | 实测 |
|---|---|
| TTS 整句（非流式 `synth()`，**当前默认**） | 1.3 ~ 2.3 s |
| TTS 流式 mode=1，收完整句 | 2.96 s |
| TTS 流式 mode=3，收完整句 | 4.01 s |
| TTS 流式 mode=3 的**首块** | 0.40 s（但收完整句要 4.0 s） |
| TTS 缓存命中 | ~0 s |
| **端到端首响（冷缓存）** | **1.59 s** |
| 端到端首响（缓存命中） | 0.00 s |

**关于流式的一个反直觉结论（实测出来的）**：
`api_v2` 一旦开启 `streaming_mode`，服务端会**自动关闭并行推理**（日志原话：「不支持同时开启并行推理和流式推理模式」）和**分桶处理**，导致整句合成总耗时反而**更长**（2.3 s → 3.0~4.0 s）。

所以：

- 当前架构是「整句收完再播」（`winsound` / `st.audio` 都需要完整 WAV），**关流式最快** —— `TTS_STREAM=false` 就是默认值。
- 只有实现「边收边播」（WebSocket 分块下发 + 浏览器音频队列）时，流式才值得开；那时的正确策略才是「首句 mode 3 抢首响、后续 mode 1 保音质」。

### 试听样例

`docs/samples/` 里是本机真实生成的音频，可直接播放确认音色：

| 文件 | 内容 |
|---|---|
| `01_それはどうかしら.wav` | 2.06 s |
| `02_別に_あなたのためじゃないわ.wav` | 3.42 s |
| `03_そう.wav` | 0.94 s |
| `04_流式mode3_头修补后.wav` | 6.1 s |

`04` 同时是 `fix_wav_header()` 的证明：修补前 `data` 长度是 0（frames=0，播放器认为没声音），修补后才是这 6.1 s 的正常音频。

---

## 关键修复记录

改造前这套代码**一句声音都听不到**，原因有三层，都已修复：

### 1. 权重配对是错的

| 文件 | 真实身份 | 能不能用 |
|---|---|---|
| `GPT_weights/default.ckpt` | GPT-SoVITS **s1** 权重（内部路径 `logs/Yukino_Strong/`） | ✅ 用 |
| `GPT_weights/Yukino_Weak-e25.ckpt` | s1 权重（`logs/Yukino_Weak/` e25） | ✅ 用 |
| `SoVITS_weights/Yukino_Strong.pth` | GPT-SoVITS **v2 SoVITS** 权重 | ✅ 用 |
| `SoVITS_weights/Yukino_Weak_0*.pth` | v2 SoVITS 权重 | ✅ 用 |
| **`default.pth` / `YUK95.pth`（517 MB）** | **so-vits-svc 模型**（配 `kmeans_10000YUK95.pt`、`YUK.json` 里的 `ssl_dim`/`gin_channels`/`spk`） | ❌ GPT-SoVITS 加载不了 |

`yukino_voice/.../weight.json` 里 `"SoVITS": {}` 空着，就是因为那个 `default.pth` 根本不是 GPT-SoVITS 权重。
（验证方式：读 `.pth` 内 pickle 的键名 —— GPT-SoVITS 的是 `enc_p.ssl_proj` / `enc_p.mrte` / `dec`；s1 的是 `model.ar_text_embedding` 等。）

### 2. 语音调用参数全错

`startAPI.bat` 启动的是 `api.py`，但 `yukino_web.py` 用的是 `api_v2.py` 的参数名，而且缺必填的 `ref_audio_path`：

| 代码发的 | api.py 要的 | api_v2.py 要的 |
|---|---|---|
| `text_lang` | `text_language` | `text_lang` ✅ |
| `prompt_lang` | `prompt_language` | `prompt_lang` ✅ |
| `speed_factor` | `speed` | `speed_factor` ✅ |
| `spk` | 不存在 | 不存在 ❌ |
| — | `refer_wav_path` | `ref_audio_path` ❌ **缺失** |

现在统一走 `api_v2.py`（`scripts/start_tts.ps1`），参数按 `api_v2` 的 schema 传。

### 3. 参考音频的转写文本对不上

原代码 `prompt_text = "あなた、いきなり姉さんのことを言い当てるから驚いたわ。"`，
但参考音频文件名（原作者标注的转写）是 `001.あなたいきなり姉さんのこと言い当てるから驚いたわ.wav`。
**`prompt_text` 必须和音频里真实说的话逐字一致**，否则音色会漂。已修正。

### 顺便修掉的坑

- **numba 缓存必须可写**：`import librosa` 时 numba 要往缓存目录写测试文件；若目录只读，进程会**静默卡死**（不是报错）。`start_tts.ps1` 已把 `NUMBA_CACHE_DIR` 指到 `data\numba_cache`。
- **`text_split_method` 用 `cut0`**：客户端已经按句切好，再让服务端 `cut5` 二次切句会产出 `なら、。` 这种垃圾。
- **纯标点碎片不要送去合成**：`…` 这样的句子会让服务端抛「请输入有效文本」并断连，客户端表现为 `IncompleteRead`。`is_speakable()` 已过滤。
- **流式 WAV 头要修补**：`streaming_mode` 返回的 WAV 头里 `data` 长度是 0，播放器会当成空音频。`fix_wav_header()` 按实际字节回填。
- **PowerShell 脚本必须带 BOM**：无 BOM 的 UTF-8 会被 Windows PowerShell 5.1 当 ANSI 读，中文注释直接导致解析失败。

---

## 设计要点

**为什么核心链路零依赖**：配置解析、标签协议、OpenAI 兼容流式客户端、SQLite 状态、TTS 客户端、WAV 处理全部用标准库手写。少一个依赖少一个坑，也让「没网装不了包」时仍可运行与测试。唯一强依赖是网页版的 `streamlit`。

**为什么用标签协议而不是 JSON mode**：
```
<ja>それはどうかしら。</ja>
<zh>那可不好说吧。</zh>
<act>微微挑眉</act>
<mood>tsundere</mood>
<aff>-2</aff>
```
JSON 必须等整个对象闭合才能用，与「边生成边播」冲突。标签协议在 `</ja>` 一出现就能取到台词送去合成 —— 这是首包延迟能压到 1 s 量级的前提。

**好感度为什么可信**：规则由代码强制，不信模型自觉。
- 单轮幅度 ≤ `per_turn_max`（默认 3）
- 同一句话在最近 5 轮内重复 → 直接扣分（防无脑复读讨好）
- 每日净增上限（默认 +10）
- 每一次变化都落 SQLite，可回溯「为什么扣分」

---

## 已知限制

- Streamlit 不支持真正的无缝音频队列，多句台词在网页上靠「首句 autoplay + 整体回放」近似；终端版是逐句同步播放，体验更接近真人。
- 没有语音输入（ASR）和打断，仍是打字 → 回复。见 `requirements.txt` 里的可选依赖与 `docs/改造方案.md` M6。
- 立绘是「表情差分」（6 张色调变体），不是真正的 Live2D/VRM。
- 长期记忆的关键词召回是简易版，没有向量检索。

---

## ⚠ 安全：需要你手动做的一件事

旧代码里硬编码的 API Key（`sk-4672…（已在源码中移除，请立即吊销）`）**已经随仓库推送到 GitHub**，并且仍在 Git 历史里。代码里已经清掉，但**你必须去控制台吊销这个 Key**：

1. 打开 <https://platform.deepseek.com/api_keys>
2. 删除 `sk-46729bef...`
3. 新建一个，填进 `.env`

清理 Git 历史的方法见 `docs/改造方案.md` 的 2.4 节。

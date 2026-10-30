# QQ 官方机器人接入

> 目标：**不用电脑开机、不用同一网络**，在 QQ 里和雪乃聊天（文字 + 语音）。

## 1. 为什么选 QQ 官方机器人

| 方案 | 能否满足「电脑不开 + 不同网络」 | 结论 |
|---|---|---|
| 官方机器人（WebSocket 长连接） | ✅ 服务器主动连出去，不需要公网回调 | **采用** |
| 官方机器人（Webhook 回调） | 需要公网 URL + 域名（部分场景还要备案） | 不用 |
| 个人号协议库（如 go-cqhttp） | ❌ 违反平台协议，有封号风险 | 排除 |
| 企业微信 / Telegram | 也可行，但 QQ 是你要的入口 | 后续可扩展 |

关键优势：**长连接是「服务器 → 腾讯」的出站连接**，所以
不需要公网 IP 绑定、不需要开入站端口、不需要备案。

## 2. 架构

```
手机 QQ ──► 腾讯服务器 ──► (WebSocket 长连接) ──► 你的 ECS
                                                    │
                                      ┌─────────────┴──────────────┐
                                      │ app.qq（新增）              │
                                      │  api.py      开放平台 API   │
                                      │  gateway.py  WS 客户端      │
                                      │  silk.py     WAV → SILK     │
                                      │  bot.py      业务编排       │
                                      └─────────────┬──────────────┘
                                                    │
                                      app/pipeline.py（复用现有）
                                        ├─ DeepSeek API（文字，秒级）
                                        └─ GPT-SoVITS（语音，CPU 慢）

出站还需要：DeepSeek API、本地 9881 端口的 TTS
```

## 3. 代码结构

| 文件 | 职责 |
|---|---|
| `app/qq/api.py` | 取 access_token、发文字、上传语音、发语音 |
| `app/qq/gateway.py` | **纯 stdlib 的 WebSocket 客户端**（RFC 6455）+ 网关会话（identify/心跳/重连/续期） |
| `app/qq/silk.py` | WAV → 24 kHz PCM → SILK（QQ 语音消息格式） |
| `app/qq/bot.py` | 事件 → pipeline → 先回文字、再补语音 |

**没有引入任何第三方依赖**：WebSocket 自己实现（约 200 行），
SILK 编码器做成多后端探测（`pysilk` / `pilk` / `silk_v3_encoder.exe`），
一个都没有时优雅降级成「只发文字」，不会让消息失败。

## 4. 关键设计：先文字、后语音

CPU 服务器上 TTS 是 **RT 6~7 倍**（4 秒语音要合成 29 秒）。
如果等语音合成完再回消息，用户要干等一分钟。

所以 `pipeline.respond()` 加了 `speak` 开关：

```python
# 第一步：只出文字，跳过 TTS（约 2~5 秒）
for ev in pipeline.respond(text, speak=False):
    ...
send_text(turn.zh)          # 立刻回文字

# 第二步：逐句合成，补发语音
for sent in turn.sentences[:2]:
    wav = pipeline.speak(sent)      # 慢，但此时文字已经送达
    send_voice(silk.encode(wav))
```

体验上就是「**秒回文字 + 语音稍后跟上**」，而不是「沉默一分钟然后突然回两条」。

## 5. 踩过的坑

| # | 坑 | 解法 |
|---|---|---|
| 1 | WebSocket 用不了现成库（要保持零依赖） | 自己实现 RFC 6455 最小客户端：掩码、126/127 长度、分片、ping/pong。测试覆盖帧编码 |
| 2 | `pilk.encode()` 签名不明 | **是文件式 API**：`pilk.encode(pcm_path, silk_path, pcm_rate)`。返回值不是状态码（实测返回 3），判据是产出文件存在且以 `#!SILK_V3` 开头 |
| **3** | **SILK 容器格式错，QQ 报 `850019 富媒体文件格式不支持`** | 这个坑花了最久。实测三种容器：<br>· 标准 `#!SILK_V3` + frames → ❌ 850019<br>· `\x02` + frames（丢魔数）→ ❌ 40093008<br>· **`\x02` + `#!SILK_V3` + frames（10 字节头）→ ✅ 通过**<br>线索来自 `pilk` 自己：`get_duration` 判断首字节 `0x02` 后 `seek(10)`，说明腾讯头是 10 字节 |
| 4 | SILK 需要 24 kHz，TTS 输出 32 kHz | 用 stdlib `audioop.ratecv` 重采样，不引第三方库 |
| 5 | 一开始以为语音要用 `url`（base64 不支持） | 实测 `file_data`(base64) 与 `url` 都能工作，前提是容器格式对。默认走 base64，失败再退回公网 url（`/media/<name>?t=<token>` 路由） |
| 6 | QQ 断线重连会重推事件 | 用 message id 做幂等去重（10 分钟窗口） |
| 7 | 远端的 `powershell -Command "..."` 在 cmd 手里会被打散 | 一律 **scp 脚本文件 + `-File` 执行**，不要内联引号 |
| 8 | `.env` 出现非 ASCII 会让 Python 用 cp936 解码失败 | `.env` / yaml / ps1 一律纯 ASCII |
| 9 | 控制台看远端日志是乱码 | PowerShell 的 `*>` 重定向写 UTF-16LE，中文全乱。把日志文件 scp 回本地按 UTF-16 解码再读 |

## 6. 配置项

```ini
# .env
QQ_APP_ID=你的AppID
QQ_APP_SECRET=你的AppSecret
QQ_VOICE=1          # 1=补发语音，0=只发文字
QQ_MAX_VOICE=2      # 每条消息最多几条语音（QQ 被动回复有条数上限）
```

## 7. 运行与自检

```powershell
# 只验证凭据+网关连通，不处理消息（最常用）
Set-Location C:\yukino_app
& "C:\Program Files\Python39\python.exe" -m app.qq --dry-run

# 正式运行
& "C:\Program Files\Python39\python.exe" -m app.qq
```

`--dry-run` 会依次打印：access_token 前缀、网关地址、WebSocket 握手结果。
这三步都通过，说明 AppID/AppSecret 正确且出网正常。

## 8. 上线流程（QQ 侧）

1. [q.qq.com](https://q.qq.com) 创建机器人 → 拿到 **AppID / AppSecret**
2. **沙箱阶段**：在「开发 → 沙箱配置」里把自己（或测试者）的 QQ 号加进去，
   否则别人给机器人发消息不会推到你的服务
3. 用手机 QQ 搜索机器人名字 / 扫后台二维码添加，发第一条消息
4. 功能稳定后提交审核 → 通过后对所有 QQ 用户开放

## 9. 验证清单

| 检查 | 命令/期望 |
|---|---|
| 凭据有效 | `python -m app.qq --dry-run` 输出"登录成功" |
| 网关连接 | 日志出现 `网关已连接 wss://...` 与 `登录成功 session=...` |
| 收到消息 | 日志出现 `[c2c] 收到：...` |
| 回出文字 | 日志 `已回文字（N 字）`，QQ 里收到 |
| 发语音 | 日志 `SILK 编码成功` + `语音已发（第 1 条）` |
| 不重复回 | 同一 message id 只处理一次 |

## 10. 已知限制

- **语音延迟大**：CPU 推理 RT 6~7 倍，一条 5 秒语音要 30 秒。
  这是硬件限制，不是代码问题（见 `docs/ECS部署实录.md`）。
- **QQ 被动回复有条数上限**：默认 `QQ_MAX_VOICE=2`，别一次发太多。
- **消息类型有限**：目前处理纯文本；图片/表情会以空内容被忽略。
- **单用户串行**：一个 TTS 进程，消息排队处理，适合个人陪伴场景。

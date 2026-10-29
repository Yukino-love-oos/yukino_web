# 部署到阿里云 ECS（Windows Server 2022 / 4核8G / 无 GPU）

> 本文针对你截图里那台实例的实际配置编写。

## 0. 你的实例现状

| 项 | 值 | 评价 |
|---|---|---|
| 实例规格 | `ecs.e-c1m2.xlarge` = **4 vCPU / 8 GiB** | ✅ 正好够 CPU 推理 |
| 操作系统 | Windows Server 2022 数据中心版（带 GUI） | ⚠️ 自身要吃 2.5~3 GB 内存 |
| 公网 IP | 42.120.61.220 | ⚠️ 截图已暴露，建议改安全组 + 换 IP |
| 私网 IP | 172.28.147.151 | — |
| 带宽 | 100 Mbps 峰值，**按流量计费** | 出方向流量要花钱（音频流量很小） |
| 系统盘 | ESSD Entry 40 GiB | ⚠️ Windows 占约 20 GB，只剩 ~20 GB |
| 地域 | 华东1（杭州） | 离手机近，好 |
| 付费 | **按量付费 → 转包年包月** | ⚠️ 忘记转会持续扣费 |
| GPU | ❌ 无 | 走 CPU 推理（实测可行） |

---

## 1. 三个必须先处理的坑

### 坑 1：磁盘只有 40 GB，装不下 22 GB 整合包

Windows 自身约 20 GB，剩 20 GB。**必须精简上传**：用 `scripts/pack_for_ecs.ps1`
把 22 GB 砍到 **约 8.86 GB**（剔除 UVR5/ASR/BigVGAN/v3v4/v2Pro 底模等 13 GB 无用文件）。

### 坑 2：内存 8 GB 被 Windows 吃掉 3 GB

| 组件 | 实测/预估内存 |
|---|---|
| Windows Server 2022（GUI） | 2.5 ~ 3.0 GB |
| GPT-SoVITS（CPU 模式，实测工作集） | **3.1 ~ 3.3 GB** |
| app.server + QQ 机器人 | 0.2 ~ 0.3 GB |
| **合计** | **约 6.0 ~ 6.6 GB / 8 GB** |

**结论：必须走 DeepSeek API，不能在服务器上再跑本地大模型。**
ollama 跑 7B 要 4.4 GB，加上 TTS 必爆内存。

应对：
- 设置**虚拟内存 8 GB**（系统属性 → 高级 → 性能 → 虚拟内存），留缓冲
- 关闭"桌面体验"里用不到的东西（如不需要图形界面，可重装 Server Core，直接省 2 GB）
- 上线后开任务管理器盯着内存

### 坑 3：安全（你这台已经把公网 IP 和 RDP 暴露在截图里了）

| 动作 | 做法 |
|---|---|
| **限制 RDP 来源** | 安全组 → 入方向 3389，授权对象改成**你自己的固定 IP**，不要 `0.0.0.0/0` |
| 强密码 | 改 Administrator 密码为 16 位以上随机串 |
| **不放行 Web 端口** | QQ 机器人用 WebSocket **出网**，不需要任何入站端口；调试期临时放行 8501 即可 |
| 关闭自动更新自动重启 | `sconfig`（Server Core）或 `gpedit.msc` → Windows 更新 → 不自动重启；否则半夜重启服务全挂 |
| 电源计划 | 设为「高性能」 |
| 建议换公网 IP | 控制台可更换弹性公网 IP |

---

## 2. 部署步骤

### 步骤 1：服务器初始化

RDP 登录后：

```powershell
# 1) 设虚拟内存 8 GB（图形界面操作：系统 → 高级系统设置 → 性能设置 → 高级 → 更改）
# 2) 电源计划设为高性能
powercfg /setactive SCHEME_HIGH

# 3) 关闭 Windows 自动重启（组策略）
gpedit.msc   # 计算机配置 → 管理模板 → Windows 组件 → Windows 更新 → 不自动重启
```

> **不需要**另外安装 Python。整合包自带的 `runtime\python.exe`（3.9.13）已验证能跑
> 本项目的全部代码（68 个单元测试全绿）。

### 步骤 2：本机打包

```powershell
cd D:\Yukino
.\scripts\pack_for_ecs.ps1                      # 生成 D:\Yukino\_pack_ecs\yukino_tts（约 8.86 GB）

# 顺便把项目代码也打个包（只有几十 MB，但服务器访问 GitHub 可能不稳）
Compress-Archive -Path 'D:\Yukino\app','D:\Yukino\configs','D:\Yukino\persona','D:\Yukino\tests','D:\Yukino\scripts','D:\Yukino\requirements.txt','D:\Yukino\.env.example' `
                 -DestinationPath 'D:\Yukino\_pack_ecs\yukino_app.zip'
```

### 步骤 3：上传

三种方式，按可靠性排序：

| 方式 | 速度 | 说明 |
|---|---|---|
| **阿里云 OSS 中转**（推荐） | 快 | 本机传 OSS → 服务器内网下载（内网流量免费） |
| RDP 驱动器重定向 | 慢但简单 | 连接时勾选「本地资源 → 驱动器」，直接拖拽 |
| rclone / WinSCP (SFTP) | 中 | 服务器需开 22 端口 |

8.86 GB 按家用上行 30 Mbps 估算约 **40 分钟**。

### 步骤 4：解压

```powershell
# 服务器上
mkdir C:\yukino_tts
Expand-Archive -Path C:\Users\Administrator\Downloads\yukino_tts.zip -DestinationPath C:\
# 解压后目录结构应为 C:\yukino_tts\runtime\python.exe 等

mkdir C:\yukino_app
Expand-Archive -Path C:\Users\Administrator\Downloads\yukino_app.zip -DestinationPath C:\yukino_app
```

### 步骤 5：配置 `.env`

```powershell
cd C:\yukino_app
Copy-Item .env.example .env
notepad .env
```

关键项（**必须改**）：

```dotenv
# 1) 思考模型：4C8G 内存装不下本地 LLM，必须用 API
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-你的新key

# 2) 指向本机 CPU 模式的 TTS
GSV_API_BASE=http://127.0.0.1:9881

# 3) 参考音频路径改成服务器的
GSV_REF_AUDIO=C:/yukino_tts/参考音频/001.あなたいきなりの…wav

# 4) 访问令牌（必设）
YUKINO_TOKEN=一个强口令

# 5) 关闭流式（整句收完再播，见 README 实测）
TTS_STREAM=false
```

### 步骤 6：启动 TTS 并**实测速度**

```powershell
cd C:\yukino_app
.\scripts\server_start_all.ps1 -TtsDir C:\yukino_tts -Token 你的令牌
```

它会先起 TTS、等就绪、再起 Web 服务。**第一次务必单独测速**：

```powershell
# 另开一个窗口
cd C:\yukino_app
C:\yukino_tts\runtime\python.exe scripts\bench_tts.py --port 9881
```

**判读标准**：

| 结果 | 结论 |
|---|---|
| 长句 ≤ 8 s | ✅ 正常用，语音和文字一起发 |
| 长句 8~15 s | 🟡 只对"重要回复"发语音，其余发文字 |
| 长句 > 15 s | 🔴 考虑限制句长 / 只用短句回复 / 或回滚到云 TTS |

把这台的实测数字记在 `docs/` 里，后面调优有依据。

### 步骤 7：验证 Web 服务

服务器上打开浏览器访问 `http://127.0.0.1:8501/?t=你的令牌`，
能看到页面并能对话（此时手机还连不上，见步骤 9）。

### 步骤 8：开机自启（任务计划程序）

```powershell
# 以管理员身份运行
$action  = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument '-NoProfile -ExecutionPolicy Bypass -File C:\yukino_app\scripts\server_start_all.ps1 -TtsDir C:\yukino_tts -Token 你的令牌'
$trigger = New-ScheduledTaskTrigger -AtStartup
$set     = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName 'YukinoAI' -Action $action -Trigger $trigger -Settings $set -Principal $principal
```

`-ExecutionTimeLimit 0` 表示不超时，适合常驻服务。

### 步骤 9：手机访问（两步走）

1. **现在（调试期）**：安全组临时放行 8501，手机浏览器打开 `http://42.120.61.220:8501/?t=令牌` 验证
   —— ⚠️ 裸 HTTP + 公网，只用于你自己临时测试，验完立刻关掉
2. **正式**：装 **Tailscale**（`winget install Tailscale.Tailscale`），手机同账号登录，
   用 `100.x.x.x:8501` 访问。**免备案、免开端口、端到端加密**（详见 `docs/移动端部署.md` 方案 B）

### 步骤 10：QQ 机器人（**并行启动，周期最长**）

现在就可以去申请，别等服务完美：

1. [q.qq.com](https://q.qq.com) 注册开发者 → 创建机器人 → 拿 `AppID / Token / Secret`
2. **接入方式选 WebSocket 长连接**（不用公网回调、不用域名、不用 ICP 备案）
3. 提交机器人审核（功能说明写清楚：个人助理、闲聊、语音回复）
4. 审核期间我这边先把代码写好，通过后直接上线

---

## 3. 成本估算（参考量级，以官网为准）

| 项 | 月成本 |
|---|---|
| ECS 4C8G（包年折月） | ¥60 ~ 120 |
| 公网流量（按 GB 计费，聊天音频每月几十 MB） | ¥1 ~ 5 |
| DeepSeek API（每天 100 轮） | ¥10 ~ 40 |
| **合计** | **约 ¥70 ~ 165 / 月** |
| ~~GPU 云服务器~~ | ~~¥1200 ~ 2500~~ |

**没有 GPU 成本**，这是 CPU 推理方案最大的价值。

---

## 4. 常见问题

**Q：启动 TTS 后内存爆了 / 系统卡死**
A：CPU 模式常驻约 3.3 GB + Windows 3 GB 已接近 8 GB 上限。设 8 GB 虚拟内存；关闭不用的开机启动项；必要时重装 Server Core（省 2 GB）。

**Q：`import librosa` 卡死不动**
A：numba 缓存目录不可写。`server_start_all.ps1` 已自动设置 `NUMBA_CACHE_DIR`，
手动启动时也要设：`$env:NUMBA_CACHE_DIR='C:\yukino_app\data\numba_cache'`。

**Q：bench 很慢（长句 >15 s）**
A：① 确认没有其他程序抢 CPU ② ECS 的 vCPU 是共享的，性能波动正常 ③ 改用配置里的短句策略（让雪乃只说 1 句）④ 或只在特定时机发语音。

**Q：`找不到 api_v2.py` / 权重路径错**
A：`tts_infer_cpu_server.yaml` 用的是**相对路径**，必须在整合包根目录（`C:\yukino_tts`）下启动，`server_start_all.ps1` 已处理好。

**Q：手机连不上**
A：优先用 Tailscale（步骤 9 正式方案）。裸公网 IP 方式需要安全组放行 + 记得验完关掉。

**Q：想换成 Linux**
A：可以，但整合包是 Windows 版（`runtime\python.exe`、`ffmpeg.exe`）。需要自己装 Python 3.9 + CPU 版 torch，
再 pip 装 GPT-SoVITS 依赖。**能省约 2 GB 内存**，但配置成本高。等 Windows 方案跑通了再考虑。

---

## 5. 部署检查清单

- [ ] 安全组 RDP 已限制来源 IP
- [ ] Administrator 密码已改强密码
- [ ] 虚拟内存已设 8 GB
- [ ] Windows 自动重启已关闭
- [ ] `yukino_tts` 已解压，体积约 8.86 GB
- [ ] `yukino_app` 已解压，`.env` 已填 DeepSeek Key 与令牌
- [ ] `bench_tts.py` 实测记录已写入 docs
- [ ] Web 页面本机可访问、能对话、有声音
- [ ] 任务计划 `YukinoAI` 已注册，重启服务器后自动恢复
- [ ] Tailscale 已装，手机可访问
- [ ] QQ 机器人已提交审核

---

## 6. 打包产物的本地验证结果（2026-10-26 已完成）

为确认「精简集传到服务器能跑」，我直接在本地用**打包目录里的 runtime** 做了完整冒烟测试：

| 检查项 | 结果 |
|---|---|
| 从 `_pack_ecs\yukino_tts` 启动 `api_v2.py` | ✅ 成功，加载 30 秒 |
| `users.pth` 已改相对路径，导入不再指向原目录 | ✅ |
| 4 线程 CPU 合成短句「そう。」 | ✅ 1.84 s |
| 4 线程 CPU 合成中句「それはどうかしら。」 | ✅ 3.47 s |
| 4 线程 CPU 合成长句「別に、あなたのためじゃないわ。」 | ✅ 5.01 s |
| 平均实时倍率 | 0.55x（3 句回复约 **11 秒**） |
| 打包体积 | **8.86 GB**（原 22 GB） |

**所以：4 vCPU 的 ECS 预期表现 ≈ 上面这组数字**，在你那台机器上应该与之接近。

### 打包时踩到并已修复的两个坑（记录备查）

1. **`runtime\Lib\site-packages\users.pth` 写死了绝对路径**
   原文件内容是 6 条 `D:\Yukino\...`，换机器后完全失效，导致 Python 仍从原目录导入
   （或直接 `ModuleNotFoundError`）。打包脚本现在会重写成相对路径：
   ```
   ../../../
   ../../../GPT_SoVITS/BigVGAN
   ../../../GPT_SoVITS
   ../../../tools
   ```

2. **不能只按白名单挑子目录**，会漏模块。实测漏掉了：
   - `GPT_SoVITS/eres2net/`（`sv.py` 用 `sys.path.append(cwd/GPT_SoVITS/eres2net)`）
   - `GPT_SoVITS/pretrained_models/sv/`（103 MB，`sv.py` 要加载 `pretrained_eres2netv2w24s4ep4.ckpt`）
   - `tools/audio_sr.py` + `tools/AP_BWE_main/`（`TTS.py` 有 `from tools.audio_sr import AP_BWE`）

   现在的策略是「**整目录复制 + 只删明确无用的大块**」，比白名单安全。

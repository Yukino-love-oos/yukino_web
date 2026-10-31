# NapCat（小号）安装与并行接入指南

> 目标：**官方机器人继续跑在服务器上（长期稳定）**，同时用**小号 + NapCat**
> 体验「个人号」形态（无被动回复限制、能主动发消息、能进任意群）。
> 两个号共用同一份人格/记忆/好感度，因为代码里已经抽象了通道层。

## 0. 架构

```
[你的电脑]                                    [阿里云 ECS]
NapCat(小号) 直连 QQ
  ├ 127.0.0.1:3000  OneBot HTTP  ──┐
  └ 127.0.0.1:3001  OneBot WS    ──┤  ssh -R 反向隧道
                                    ▼
                        服务器 127.0.0.1:3000 / 3001
                                    │
                    Yukino Bot #2（QQ_BACKEND=onebot）
                                    │
                    ┌───────────────┴───────────────┐
                共用 persona / memory / 好感度 / 同一份 SQLite
                                    │
                    Yukino Bot #1（官方机器人，常驻）
```

- 隧道是**从你电脑连出去**的，服务器上的 3000/3001 只绑在 127.0.0.1，**不暴露公网**
- 官方机器人完全不受影响；NapCat 只在你电脑开着的时候在线

## 1. 安装 NapCat（在你的电脑上）

安装包我已经下载好放在 `D:\Yukino\_napcat\`：

| 文件 | 大小 | 用途 |
|---|---|---|
| `NapCat.Shell.Windows.OneKey.zip` | 0.9 MB | **一键安装器**（推荐） |
| `onekey\`（已解压） | — | 同上，可直接用 |

### 步骤

1. **先装 QQ NT**（桌面版 QQ，不是旧版）
   - 如果没装，去 <https://im.qq.com> 下 Windows 版装上，**登录一次再退出**
   - 登录用你的**小号**（确认能正常登录）

2. **运行安装器**
   - 打开 `D:\Yukino\_napcat\onekey\NapCatInstaller.exe`
   - 它会要求选择 QQ 安装目录（一般在 `C:\Program Files\Tencent\QQNT` 或 `D:\Program Files\Tencent\QQNT`）
   - 选好后点安装，它会自动把 NapCat 注入进 QQ 目录

3. **启动 NapCat**
   - 安装完成后，去 QQ 安装目录下的 `bootmain\` 里运行 `napcat.bat`
   - 首次会弹出二维码 → **用手机 QQ（小号）扫码登录**
   - 登录成功后窗口会打印 `WebUI` 地址（一般 `http://127.0.0.1:6099`）和 token

4. **配置 OneBot 11**
   - 浏览器打开 WebUI（用窗口里打印的 token 登录）
   - 进「网络配置」，确认这两项存在（没有就新建）：
     | 类型 | 端口 | 说明 |
     |---|---|---|
     | HTTP 服务 | `3000` | 我用来发消息 |
     | WebSocket 服务（正向） | `3001` | 我用来收消息 |
   - **给它们设一个 access token**（随便一串，例如 `yukino-<随机>`），记下来

5. **验证端口**
   在 PowerShell 里跑：
   ```powershell
   Test-NetConnection 127.0.0.1 -Port 3000 -InformationLevel Quiet
   Test-NetConnection 127.0.0.1 -Port 3001 -InformationLevel Quiet
   ```
   两个都应该是 `True`。

## 2. 建立反向隧道（在你的电脑上）

我已经写好脚本：`D:\Yukino\scripts\start_napcat_tunnel.bat`

- **双击运行**，窗口会显示「connecting...」，连上后停在那里
- **这个窗口要一直开着**（关掉 = 小号下线，官方机器人不受影响）

它做的事：把本机 3000/3001 通过 SSH 反向隧道映射到服务器的 127.0.0.1:3000/3001。

> 断线会自动重连（5 秒一次）。开机自启可以把这个 bat 放进「启动」文件夹。

## 3. 告诉我，我来接上

把这两样发我：

1. **OneBot 的 access token**（第 4 步设的那个）
2. 确认隧道窗口已连上

我会在服务器上：
```powershell
# 写入 token
Set-Content C:\upload\onebot_token.txt -Value '<token>'
# 起第二个机器人进程（不影响官方机器人）
powershell -File C:\upload\run_qq_onebot.ps1
```
然后先跑 `python -m app.qq --dry-run` 验证 NapCat 的 HTTP + WS 都通，
再给你小号发一条测试消息确认全链路。

## 4. 两种形态的能力对照

| | 官方机器人（现有） | NapCat 小号（新增） |
|---|---|---|
| 需要电脑开机 | ❌ | ✅ |
| 合规 | ✅ | ❌ 违反用户协议，有封号风险 |
| 被动回复限制 | 有 msg_id/msg_seq | 无 |
| 主动发消息 | 有额度 | 想发就发 |
| 群聊 | 需审核 | 任意群（**只在被 @ 时回应**） |
| 语音 | ✅ | ✅ |
| 表情包 | ✅ | ✅ |
| 记忆 / 好感度 / 心情 | ✅ | ✅（与官方共享同一份） |

## 5. 如果出问题

| 现象 | 排查 |
|---|---|
| `NapCatInstaller.exe` 找不到 QQ | 手动指定 QQNT 目录（含 `QQ.exe` 的那层） |
| 扫码后立刻掉线 | 换手机 QQ 版本 / 关掉 QQ 的「设备锁」再试 |
| 服务器连不上 3000 | 隧道窗口没连上；或 NapCat 的 HTTP 服务没启用 |
| `dry-run` 报 401 | token 不对，检查 `.env` / `onebot_token.txt` 和 NapCat WebUI 里的是否一致 |
| 小号被冻结 | 这是云/自动化登录的固有风险；官方机器人不受影响 |

## 6. 回滚

只要不动 `.env`，官方机器人永远是主通道。想彻底停掉 NapCat 形态：

```powershell
# 服务器上
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'app\.qq' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
# 然后用 run_qq.ps1 重启官方机器人即可
```

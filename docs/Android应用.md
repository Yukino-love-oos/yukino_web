# Android App（雪乃）

> 一个 **113 KB 的壳**：全屏 WebView 加载服务器上的网页 + 悬浮球 + 主动消息弹窗 + 应用内更新。
> 人格 / 记忆 / 好感度 / 语音全在服务器，**App 本身几乎不用更新**。

## 1. 为什么这么设计

```
手机 App（壳）  ──HTTP──►  服务器（D:\Yukino 那套）
   WebView                 · FastAPI/http.server 页面
   悬浮球                   · 人格卡、多层记忆、艾宾浩斯
   通知                     · TTS（GPT-SoVITS）
   更新检查                 · 主动关心 timer
```

- **她升级 = 服务器改代码**，App 不用重发
- **App 升级** = 换壳（悬浮球、通知、UI），频率很低
- 所以采用「方案 B：应用内检查更新 + 手动点安装」，不上应用商店

## 2. 构建（不依赖 Gradle / Android Studio）

工具链（一次装好，约 270 MB）：

```
D:\Yukino\_android\sdk\            android SDK
  ├ platforms\android-34\android.jar
  ├ build-tools\34.0.0\            aapt2 / d8 / zipalign / apksigner
  └ platform-tools\adb.exe
D:\Yukino\_android\app\            工程源码
```

构建命令：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\Yukino\_android\build_apk.ps1
# 产物: D:\Yukino\_android\build\yukino-1.0.apk
```

流程（脚本里一步步都有）：

```
aapt2 compile res        → 编译资源
aapt2 link               → base.apk + R.java
javac                    → classes
d8                       → classes.dex
jar --update             → dex 塞进 apk
zipalign                 → 4 字节对齐
apksigner sign           → 签名
```

> ⚠️ 构建脚本必须是**纯 ASCII** 的 `.ps1`。PowerShell 5.1 在没有 BOM 时按 ANSI 解析，
> 中文注释会把脚本撕碎（这个坑在 bat 上踩过一次）。

## 3. 签名密钥（重要）

```
密钥文件: D:\Yukino\_android\yukino.jks
别名    : yukino
密码    : yukino2026
SHA-256 : e65458e6776ebebc7bf29e9646d9e53ae4b7b00c5f2721f3fc76ad6f69e8d5d5
```

**丢了就再也无法覆盖升级**（只能卸载重装，聊天记录在服务器所以不受影响，但设置会丢）。
所以备份了两份：本地 + `C:\upload\yukino.jks`（服务器）。

升级新版本时必须：

1. `Cfg.java` 里 `VERSION_CODE` +1
2. `build_apk.ps1` 里 `$VER_CODE` / `$VER_NAME` 同步
3. 文件名带版本号（`yukino-1.1.apk`）

## 4. 版本发布流程（方案 B）

```powershell
# 1) 改版本号并构建
# 2) 上传 APK
scp _android\build\yukino-1.1.apk 服务器:C:/yukino_app/data/apk/
# 3) 更新 version.json（App 启动时 / 每小时查一次）
#    {"versionCode": 2, "versionName": "1.1",
#     "url": "/apk/yukino-1.1.apk", "notes": "修了悬浮球漂移"}
```

App 侧逻辑：`GET /api/app/version` → `versionCode > Cfg.VERSION_CODE` →
弹「有新版本」→ 点下载 → 浏览器下载 APK → 系统安装器覆盖安装（数据保留）。

## 5. App 做了什么

| 文件 | 职责 |
|---|---|
| `MainActivity.java` | 全屏 WebView 加载页面；申请通知/悬浮窗权限；每小时检查更新 |
| `ProactiveService.java` | 前台服务：轮询 `/api/app/proactive`（30 秒）+ 悬浮球 + 横幅通知 |
| `BootReceiver.java` | 开机尝试拉起服务（HyperOS 可能拦，失败忽略） |
| `AppLog.java` | 崩溃上报 `/api/app/log`（我连不上 adb，只能靠它） |
| `Cfg.java` | 服务器地址 / token / 版本号 |

服务器端配套接口：

| 接口 | 用途 |
|---|---|
| `GET /api/app/version` | 版本信息（读 `data/apk/version.json`） |
| `GET /api/app/proactive?since=N[&init=1]` | 用 `since_id` 游标拉主动消息，**不消费** web 端的 delivered 标记 |
| `GET /apk/<file>?t=` | 分发 APK |
| `POST /api/app/log` | 接收 App 崩溃日志 |

> `init=1`：首次启动只返回当前游标、不返回历史，避免装好瞬间被十几条旧消息连环弹窗。

## 6. 通知为什么用轮询

国内 **FCM 不可用**，第三方推送（极光/个推）要接 SDK 且收费。
前台服务每 30 秒轮询一次是最朴素的方案，代价约 **3~5% 电量/天**。

通知渠道用 `IMPORTANCE_HIGH` → 系统横幅弹出（QQ 那种效果），需要：

- 系统通知权限允许
- 「允许横幅 / 通知样式」
- HyperOS 里别设成「静默通知」

## 7. HyperOS（红米）保活清单

| 设置 | 路径 |
|---|---|
| 自启动 | 设置 → 应用设置 → 应用管理 → 雪乃 → 自启动 → 允许 |
| 省电策略 | 同上 → 省电策略 → **无限制** |
| 锁定后台 | 最近任务 → 长按卡片 → 加锁 |
| 悬浮窗 | 设置 → 应用设置 → 权限管理 → 显示在其它应用上层 → 雪乃 |
| 通知 | 设置 → 通知管理 → 雪乃 → 允许通知 + 横幅 + 锁屏 |

即便全开了，HyperOS 大版本升级后仍可能被回收——重新打开一次 App 即可。

## 8. 已知限制

| 限制 | 说明 |
|---|---|
| 明文 HTTP | 没域名/证书；`usesCleartextTraffic=true` 放行 |
| 轮询延迟 | 最多 30 秒才收到主动消息 |
| 不能静默更新 | Android 安全模型限制，必须用户点安装 |
| 悬浮球可能消失 | 国产 ROM 保活问题，非代码可解 |
| 只支持 Android | iOS 不允许悬浮窗，只能 PWA |

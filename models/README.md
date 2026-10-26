# 模型目录说明

**模型文件不进 Git**（`.gitignore` 已排除 `*.pth` / `*.ckpt` / `*.pt`）。
本文件只记录「有哪些模型、在哪、是什么、怎么校验」。

---

## 1. 实际在用的模型（GPT-SoVITS v2）

| 用途 | 文件 | 大小 | 说明 |
|---|---|---|---|
| GPT（s1）· 强语气 | `yukino/Yukino/GPT_weights/default.ckpt` | 147.9 MB | 内部路径 `logs/Yukino_Strong/` |
| GPT（s1）· 弱语气 | `yukino/Yukino/GPT_weights/Yukino_Weak-e25.ckpt` | 147.9 MB | `logs/Yukino_Weak/`，第 25 epoch |
| SoVITS（v2）· 强 | `yukino/Yukino/SoVITS_weights/Yukino_Strong.pth` | 81.0 MB | keys: `enc_p.ssl_proj` / `enc_p.mrte` / `dec` |
| SoVITS（v2）· 弱 01 | `yukino/Yukino/SoVITS_weights/Yukino_Weak_01.pth` | 81.0 MB | 同上 |
| SoVITS（v2）· 弱 02 | `yukino/Yukino/SoVITS_weights/Yukino_Weak_02.pth` | 81.0 MB | 同上 |
| 参考音频 | `yukino/Yukino/参考音频/001.あなたいきなりの…wav` | 0.6 MB | 与 `prompt_text` 逐字对应 |

训练时间戳 2024-05-25 → 属 **v2 世代**，配置文件里 `version: v2`（不是 v2Pro）。

## 2. 不要用的模型

| 文件 | 大小 | 为什么不能用 |
|---|---|---|
| `yukino/Yukino/default.pth` | 517 MB | **so-vits-svc** 模型，GPT-SoVITS 加载不了 |
| `yukino/Yukino/default.zip` | 598 MB | 同上（打包） |
| `yukino_voice/.../SoVITS_weights/default.pth` | 517 MB | 同上（副本） |
| `YUK4th95%/YUK95.pth` | 517 MB | 同上（原始下载） |
| `YUK4th95%/kmeans_10000YUK95.pt` | 14.7 MB | so-vits-svc 的聚类特征，GPT-SoVITS 不用 |
| `YUK4th95%/YUK.json` | 2 KB | so-vits-svc 训练配置（含 `ssl_dim`/`gin_channels`/`spk`） |

判定依据（不用装 torch 也能验）：`.pth` / `.ckpt` 本质是 zip，读 `data.pkl` 里的键名即可区分：
- GPT-SoVITS SoVITS → `enc_p.ssl_proj.*`、`enc_p.mrte.*`、`dec.*`
- GPT-SoVITS s1    → `model.ar_text_embedding.*`、`model.ar_audio_embedding.*`
- so-vits-svc      → 配 `kmeans_*.pt`，配置里有 `spk`/`ssl_dim`/`gin_channels`

## 3. 校验

```powershell
# 快速核对是否还是同一份权重
Get-FileHash 'D:\Yukino\yukino\Yukino\SoVITS_weights\Yukino_Strong.pth' -Algorithm SHA256
Get-FileHash 'D:\Yukino\yukino\Yukino\GPT_weights\default.ckpt'         -Algorithm SHA256
```

## 4. 备份建议

权重是**最贵的资产**（重新训练约 10 小时 GPU）。建议把上面表格里 5 个文件 + 3 段参考音频
打包放网盘，并在 `docs/` 里记下 sha256。不要把 22 GB 的整合包一起备份——整合包可以重新下载。

## 5. 授权

`YUK95.pth` 等来自 B 站 UP 主 **Hiki 老师** 的「免费分享」，原作者说明文件目前遗失
（`YUK4th95%/作者：B站Hiki老师 模型免费分享 使用说明出处/` 是空目录）。
**个人使用无碍；公开发布或再分发前必须先补回并遵守原作者条款**（通常要求署名、禁止商用）。
角色「雪之下雪乃」为版权角色，商用/公开分发需另行评估。

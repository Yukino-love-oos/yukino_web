"""雪之下雪乃 · QQ 官方机器人适配器。

模块划分：
  api.py      QQ 开放平台 OpenAPI（取 token / 发消息 / 上传语音）
  gateway.py  纯 stdlib 的 WebSocket 客户端 + QQ 网关会话（identify/心跳/重连）
  silk.py     WAV -> SILK 转换（QQ 语音消息格式），无后端时优雅降级
  bot.py      业务逻辑：事件 -> pipeline -> 先文字后语音

入口：
  python -m app.qq            正式运行
  python -m app.qq --dry-run  只验证凭据与网关连通
"""

from .api import INTENT_GROUP_AND_C2C_EVENT, QQApi, QQBotError
from .bot import QQBot, main

__all__ = ["QQApi", "QQBotError", "QQBot", "INTENT_GROUP_AND_C2C_EVENT", "main"]

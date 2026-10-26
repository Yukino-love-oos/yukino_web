"""Streamlit Web 界面 —— 雪之下雪乃的部室。

启动：
    python -m streamlit run app/ui/streamlit_app.py
或直接用一键脚本：
    .\\scripts\\run.ps1 -Web

相比原 yukino_web.py 的改动：
  · 人格卡外置（persona/yukino.json），不再把提示词写死在代码里
  · 输出用标签协议解析，动作/翻译/好感度分别渲染，不再靠正则拆括号
  · 好感度落 SQLite，刷新页面不丢
  · 立绘由 <mood> 驱动表情差分
  · 语音链路修正（ref_audio_path + cut0 + 流式首句）
  · 语音服务挂掉时自动降级为纯文字，不白屏
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

# 允许 `streamlit run app/ui/streamlit_app.py` 时正确导入 app 包
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from app.ui.expressions import MOOD_LABELS, expression_path  # noqa: E402

st.set_page_config(page_title="雪之下雪乃の奉仕部部室", page_icon="❄️", layout="wide")


# ---------------------------------------------------------------- 资源装配
@st.cache_resource(show_spinner="雪乃正在准备部室…")
def get_pipeline(mock: bool):
    if mock:
        os.environ["YUKINO_MOCK_LLM"] = "1"
    else:
        os.environ.pop("YUKINO_MOCK_LLM", None)

    from app.config import load_settings
    from app.pipeline import build_pipeline

    settings = load_settings()
    return build_pipeline(settings)


def audio_widget(container, data: bytes, *, autoplay: bool) -> None:
    """兼容不同 Streamlit 版本的 autoplay 参数。"""
    try:
        container.audio(data, format="audio/wav", autoplay=autoplay)
    except TypeError:
        container.audio(data, format="audio/wav")


# ---------------------------------------------------------------- 侧边栏
st.sidebar.title("❄️ 部室ステータス")


def _key_present() -> bool:
    """.env 文件存在不等于填了 Key —— 要真的看值。"""
    if os.environ.get("DEEPSEEK_API_KEY", "").strip():
        return True
    try:
        from app.config import ROOT as APP_ROOT, parse_dotenv

        return bool(parse_dotenv(APP_ROOT / ".env").get("DEEPSEEK_API_KEY", "").strip())
    except Exception:
        return False


has_key = _key_present()
mock_mode = st.sidebar.toggle(
    "离线演示模式", value=not has_key, help="用内置假模型跑通链路，不消耗 API 额度"
)
if not has_key and not mock_mode:
    st.sidebar.error("未检测到 DEEPSEEK_API_KEY，请填写 `.env` 后再关闭演示模式。")

try:
    pipeline = get_pipeline(mock_mode)
except Exception as e:
    st.error(f"初始化失败：{e}")
    st.stop()

session = pipeline.session
persona = pipeline.persona
stats = session.stats()

st.sidebar.metric("好感度", f"{stats['affinity']} / 100", help=f"当前阶段：{stats['band']}")
st.sidebar.progress(max(0.0, min(1.0, (stats["affinity"] + 100) / 200)))
st.sidebar.caption(
    f"阶段 **{stats['band']}** · 累计 **{stats['total_turns']}** 轮 · 今日 **{stats['today_gain']:+d}**"
)

st.sidebar.divider()

tts_ok = pipeline.tts.health()
if tts_ok:
    st.sidebar.success("语音服务：已连接")
else:
    st.sidebar.warning("语音服务：未启动（纯文字模式）")
    st.sidebar.code(".\\scripts\\start_tts.ps1", language="powershell")

voice = pipeline.voice_for_affinity(stats["affinity"])
st.sidebar.caption(f"音色档位：`{voice}`　模型：`{getattr(pipeline.llm, 'model', '?')}`")

with st.sidebar.expander("缓存 / 数据"):
    cs = pipeline.cache.stats()
    st.write(f"TTS 缓存：{cs.get('files', 0)} 条 / {cs.get('mb', 0)} MB")
    st.write(f"数据库：`{pipeline.settings.db_path.name}`")
    c1, c2 = st.columns(2)
    if c1.button("清空缓存", use_container_width=True):
        n = pipeline.cache.clear()
        st.toast(f"已清空 {n} 条")
    if c2.button("重置好感度", use_container_width=True):
        session.affinity = 0
        st.rerun()

st.sidebar.divider()
if st.sidebar.button("🔄 重新载入", use_container_width=True):
    st.cache_resource.clear()
    st.rerun()

# ---------------------------------------------------------------- 主界面
st.title("雪之下雪乃の奉仕部部室")
left, right = st.columns([1, 2], gap="large")

with left:
    mood_img = st.empty()
    expr = expression_path("calm")
    if expr:
        mood_img.image(str(expr), use_container_width=True)
    audio_first = st.empty()
    audio_full = st.empty()
    mood_caption = st.empty()

with right:
    chat = st.container(height=560)
    with chat:
        for row in session.recent_turns(30):
            if row["user_text"]:
                with st.chat_message("user", avatar="👤"):
                    st.markdown(row["user_text"])
            if row["ja"]:
                with st.chat_message("assistant", avatar="❄️"):
                    if row["act"]:
                        st.caption(f"（{row['act']}）")
                    st.markdown(row["ja"])
                    if row["zh"]:
                        st.caption(row["zh"])
                    if row["aff_delta"]:
                        st.caption(f"好感度 {row['aff_delta']:+d} → {row['aff_after']}")

    prompt = st.chat_input("日本語で話しかけてください（请用日语对话）")

# ---------------------------------------------------------------- 处理输入
if prompt:
    with chat:
        with st.chat_message("user", avatar="👤"):
            st.markdown(prompt)

        with st.chat_message("assistant", avatar="❄️"):
            act_slot = st.empty()
            text_slot = st.empty()
            status_slot = st.empty()
            status_slot.caption("雪乃は考えています…")

            spoken: list[str] = []
            audios: list[bytes] = []
            turn = None
            delta = 0
            reason = ""
            first_played = False

            for ev in pipeline.respond(prompt):
                kind = ev["type"]

                if kind == "sentence":
                    spoken.append(ev["text"])
                    text_slot.markdown("".join(spoken))
                    if ev["audio"]:
                        audios.append(ev["audio"])
                        if not first_played:
                            first_played = True
                            audio_widget(audio_first, ev["audio"], autoplay=True)

                elif kind == "error":
                    status_slot.error(ev["message"])

                elif kind == "done":
                    turn = ev["turn"]
                    delta = ev["delta"]
                    reason = ev["reason"]

            status_slot.empty()

            if turn is not None:
                if turn.act:
                    act_slot.caption(f"（{turn.act}）")
                if not spoken and turn.ja:
                    text_slot.markdown(turn.ja)
                if turn.zh:
                    st.caption(turn.zh)

                sign = f"{delta:+d}" if delta else "±0"
                line = f"好感度 {sign} → {session.affinity}（{session.stats()['band']}）"
                if reason:
                    line += f"　·　{reason}"
                st.caption(line)

                if len(audios) > 1:
                    from app.tts.cache import merge_wav

                    merged = merge_wav(audios)
                    if merged:
                        audio_widget(audio_full, merged, autoplay=False)

                expr = expression_path(turn.expression)
                if expr:
                    mood_img.image(str(expr), use_container_width=True)
                mood_caption.caption(
                    f"气分：{MOOD_LABELS.get(turn.expression, turn.expression)}"
                )

                with st.expander("本轮 system prompt"):
                    st.code(
                        pipeline.build_messages(prompt)[0]["content"], language="text"
                    )

    # 注意：这里刻意不调用 st.rerun()。
    # 一旦 rerun，页面重绘会打断正在播放的语音；侧边栏数值在下次交互时刷新即可。

"""零依赖 Web 界面 —— 只用标准库（http.server），不需要 streamlit。

为什么存在：本机 pip 装包受网络/证书/权限影响很大（见 README 的 TLS 一节），
而「网页版」本身并不需要 streamlit —— 一个 http.server + 内嵌页面就够。

启动：
    python -m app.server            # 默认 http://127.0.0.1:8501
    python -m app.server --port 8600
或：
    .\\scripts\\run.ps1 -Serve

接口：
    GET  /              聊天页面
    GET  /api/state     好感度 / 轮数 / 服务状态
    POST /api/chat      {"text": "..."} -> 回复 + 若干段 wav(base64)
    GET  /assets/*.png  立绘与表情
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .config import load_settings
from .pipeline import build_pipeline
from .ui.expressions import MOOD_LABELS, expression_path

log = logging.getLogger("yukino.server")

ROOT = Path(__file__).resolve().parent.parent
ASSETS = Path(__file__).resolve().parent / "ui" / "assets"

_PIPELINE = None
_LOCK = threading.Lock()

PAGE = """<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>雪之下雪乃の奉仕部部室</title>
<style>
  :root{--bg:#12141c;--panel:#1b1f2b;--line:#2c3242;--text:#e6e9f2;--dim:#8b93a7;
        --accent:#8fb6e8;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);
       font-family:"Microsoft YaHei","Hiragino Sans",system-ui,sans-serif}
  .wrap{display:flex;height:100vh;gap:18px;padding:18px}
  .left{width:38%;max-width:460px;display:flex;flex-direction:column;gap:12px}
  .art{flex:1;background:var(--panel);border:1px solid var(--line);border-radius:14px;
       overflow:hidden;display:flex;align-items:center;justify-content:center}
  .art img{width:100%;height:100%;object-fit:cover}
  .right{flex:1;display:flex;flex-direction:column;background:var(--panel);
         border:1px solid var(--line);border-radius:14px;overflow:hidden}
  header{padding:14px 18px;border-bottom:1px solid var(--line)}
  header h1{margin:0;font-size:17px;font-weight:600;letter-spacing:.5px}
  header .sub{color:var(--dim);font-size:12px;margin-top:4px}
  .bar{height:6px;background:#0e1017;border-radius:3px;overflow:hidden;margin-top:10px}
  .bar>div{height:100%;width:50%;background:linear-gradient(90deg,#5b7fb5,#9ec8f5);
           transition:width .4s}
  #chat{flex:1;overflow-y:auto;padding:16px 18px;display:flex;flex-direction:column;gap:14px}
  .msg{max-width:82%;padding:10px 14px;border-radius:12px;line-height:1.65;font-size:14px;
       white-space:pre-wrap;word-break:break-word}
  .me{align-self:flex-end;background:#2b3a52;border:1px solid #3a4d6b}
  .her{align-self:flex-start;background:#242938;border:1px solid var(--line)}
  .her .act{color:var(--dim);font-size:12px;margin-bottom:6px}
  .her .zh{color:#a9b4c9;font-size:12.5px;margin-top:8px;border-top:1px dashed #333a4c;padding-top:8px}
  .meta{color:var(--dim);font-size:11.5px;margin-top:8px}
  form{display:flex;gap:10px;padding:14px 18px;border-top:1px solid var(--line)}
  input{flex:1;background:#0f1219;border:1px solid var(--line);border-radius:10px;
        padding:12px 14px;color:var(--text);font-size:14px;outline:none}
  input:focus{border-color:#4b6a94}
  button{background:#3b5f8f;color:#fff;border:0;border-radius:10px;padding:0 22px;
         font-size:14px;cursor:pointer}
  button:disabled{opacity:.5;cursor:default}
  .hint{color:var(--dim);font-size:12px;padding:0 18px 12px}
</style></head>
<body><div class="wrap">
  <div class="left">
    <div class="art"><img id="art" src="/assets/yukino.png" alt="雪乃"></div>
    <div class="bar"><div id="bar"></div></div>
    <div class="hint" id="status">连接中…</div>
  </div>
  <div class="right">
    <header>
      <h1>雪之下雪乃の奉仕部部室</h1>
      <div class="sub" id="sub">好感度 —</div>
    </header>
    <div id="chat"></div>
    <form id="f">
      <input id="q" placeholder="日本語で話しかけてください（请用日语对话）" autocomplete="off">
      <button id="send">送信</button>
    </form>
  </div>
</div>
<script>
const chat=document.getElementById('chat'), art=document.getElementById('art');
const bar=document.getElementById('bar'), sub=document.getElementById('sub');
const statusEl=document.getElementById('status'), q=document.getElementById('q');
const send=document.getElementById('send');
const moodArt={calm:'calm',tsundere:'tsundere',soft:'soft',angry:'angry',shy:'shy',tired:'tired'};

function add(cls,html){const d=document.createElement('div');d.className='msg '+cls;
  d.innerHTML=html;chat.appendChild(d);chat.scrollTop=chat.scrollHeight;return d;}
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}

async function playAll(list){
  for(const b64 of list){
    await new Promise(res=>{
      const a=new Audio('data:audio/wav;base64,'+b64);
      a.onended=res; a.onerror=res; a.play().catch(res);
    });
  }
}

async function refresh(){
  try{
    const r=await fetch('/api/state'); const s=await r.json();
    sub.textContent='好感度 '+(s.affinity>0?'+':'')+s.affinity+' / 100　·　'+s.band+
                    '　·　累计 '+s.total_turns+' 轮';
    bar.style.width=((s.affinity+100)/200*100)+'%';
    statusEl.textContent='思考模型: '+s.provider+' / '+s.model+'　·　语音: '+(s.tts_ok?'已连接':'未启动');
    art.src='/assets/'+(moodArt[s.mood]||'yukino')+'.png';
    for(const m of s.history||[]){
      if(m.user_text) add('me',esc(m.user_text));
      if(m.ja) add('her',(m.act?'<div class="act">（'+esc(m.act)+'）</div>':'')+esc(m.ja)+
                          (m.zh?'<div class="zh">'+esc(m.zh)+'</div>':''));
    }
  }catch(e){ statusEl.textContent='无法连接服务: '+e; }
}

document.getElementById('f').addEventListener('submit',async ev=>{
  ev.preventDefault();
  const text=q.value.trim(); if(!text) return;
  q.value=''; send.disabled=true;
  add('me',esc(text));
  const thinking=add('her','<span style="color:#8b93a7">雪乃は考えています…</span>');
  try{
    const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},
                                    body:JSON.stringify({text})});
    const d=await r.json();
    thinking.remove();
    if(d.error){ add('her','<span style="color:#e08">'+esc(d.error)+'</span>'); }
    else{
      const t=d.turn;
      add('her',(t.act?'<div class="act">（'+esc(t.act)+'）</div>':'')+esc(t.ja||'')+
                 (t.zh?'<div class="zh">'+esc(t.zh)+'</div>':'')+
                 '<div class="meta">好感度 '+(d.delta>0?'+':'')+d.delta+' → '+d.affinity+
                 '　·　首响 '+d.first_audio_s.toFixed(2)+'s</div>');
      for(const w of (d.notices||[])) add('her','<span style="color:#c9a66b">'+esc(w)+'</span>');
      if(d.audios&&d.audios.length) playAll(d.audios);
    }
    await refresh();
  }catch(e){ thinking.remove(); add('her','<span style="color:#e08">请求失败: '+esc(e)+'</span>'); }
  send.disabled=false; q.focus();
});
refresh();
</script></body></html>
"""


def get_pipeline():
    global _PIPELINE
    if _PIPELINE is None:
        settings = load_settings()
        _PIPELINE = build_pipeline(settings)
    return _PIPELINE


class Handler(BaseHTTPRequestHandler):
    server_version = "YukinoServer/2.0"

    # ---------- 工具 ----------
    def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _json(self, obj: dict, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def log_message(self, fmt, *args):  # 安静一点
        log.debug("%s - %s", self.address_string(), fmt % args)

    # ---------- 路由 ----------
    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"))
            return
        if path == "/api/state":
            self._state()
            return
        if path.startswith("/assets/"):
            self._asset(path[len("/assets/"):])
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self):
        if urlparse(self.path).path != "/api/chat":
            self._send(404, b"not found", "text/plain")
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            text = (payload.get("text") or "").strip()
        except Exception as e:
            self._json({"error": f"请求体无法解析：{e}"}, 400)
            return
        if not text:
            self._json({"error": "内容为空"}, 400)
            return
        self._chat(text)

    # ---------- 业务 ----------
    def _asset(self, name: str):
        name = Path(name).name  # 防目录穿越
        p = ASSETS / "expressions" / name
        if not p.is_file():
            p = ASSETS / name
        if not p.is_file():
            self._send(404, b"no asset", "text/plain")
            return
        self._send(200, p.read_bytes(), "image/png")

    def _state(self):
        try:
            pipe = get_pipeline()
        except Exception as e:
            self._json({"error": f"初始化失败：{e}"}, 500)
            return
        st = pipe.session.stats()
        hist = [
            {
                "user_text": r["user_text"],
                "ja": r["ja"],
                "zh": r["zh"],
                "act": r["act"],
            }
            for r in pipe.session.recent_turns(30)
        ]
        self._json(
            {
                **st,
                "mood": pipe.session.get("mood", "calm") or "calm",
                "provider": getattr(pipe.llm, "provider", pipe.settings.llm_provider),
                "model": getattr(pipe.llm, "model", pipe.settings.model),
                "tts_ok": pipe.tts.health(timeout=1.5),
                "history": hist,
            }
        )

    def _chat(self, text: str):
        try:
            pipe = get_pipeline()
        except Exception as e:
            self._json({"error": f"初始化失败：{e}"}, 500)
            return

        audios: list[str] = []
        notices: list[str] = []
        turn = None
        delta = 0
        first_audio: float | None = None

        import time

        t0 = time.time()
        with _LOCK:  # 单用户本地应用，避免并发抢显存
            try:
                for ev in pipe.respond(text):
                    kind = ev.get("type")
                    if kind == "sentence":
                        if ev.get("audio"):
                            if first_audio is None:
                                first_audio = time.time() - t0
                            audios.append(base64.b64encode(ev["audio"]).decode("ascii"))
                    elif kind in ("info", "warn"):
                        notices.append(ev.get("message", ""))
                    elif kind == "error":
                        notices.append(ev.get("message", ""))
                    elif kind == "done":
                        turn = ev["turn"]
                        delta = ev["delta"]
            except Exception as e:
                log.exception("对话失败")
                self._json({"error": f"对话失败：{e}"}, 500)
                return

        if turn is None:
            self._json({"error": "没有生成回复"}, 500)
            return

        try:
            pipe.session.set("mood", turn.expression)
        except Exception:
            pass

        self._json(
            {
                "turn": turn.to_dict(),
                "delta": delta,
                "affinity": pipe.session.affinity,
                "audios": audios,
                "first_audio_s": round(first_audio or 0.0, 2),
                "notices": [n for n in notices if n],
            }
        )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="雪之下雪乃 · 零依赖网页版")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8501)
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    settings = load_settings()
    print("=" * 56)
    print("  雪之下雪乃 · 零依赖网页版")
    print("=" * 56)
    print(f"  地址     : http://{args.host}:{args.port}")
    print(f"  思考模型 : {settings.llm_provider} / "
          f"{settings.ollama_model if settings.llm_provider == 'ollama' else settings.model}")
    print(f"  语音服务 : {settings.gsv_api_base}")
    print("=" * 56)
    print("  Ctrl+C 退出")

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

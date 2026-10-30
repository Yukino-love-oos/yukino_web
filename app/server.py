"""零依赖 Web 界面（手机可用）—— 只用标准库。

不依赖 streamlit：本机 pip 受网络/证书/权限影响大，而网页版只需要 http.server。
针对手机做了四件事：
  1. 响应式布局：窄屏自动改成「顶部小立绘 + 下方对话」单列
  2. PWA：/manifest.webmanifest + /sw.js，iOS/Android 可「添加到主屏幕」当 App 用
  3. iOS 音频解锁：AudioContext 在首次点击（发送消息）时解锁并逐段播放
  4. 访问令牌：公网/局域网暴露时用 --token 或 YUKINO_TOKEN 开启，防止别人白嫖显卡

启动：
    python -m app.server                      # 仅本机 http://127.0.0.1:8501
    python -m app.server --host 0.0.0.0       # 局域网可访问（手机同一 Wi-Fi）
    python -m app.server --host 0.0.0.0 --token 1234
或：
    .\\scripts\\start_mobile.ps1               # 自动探测局域网 IP 并打印手机地址

接口：
    GET  /                    聊天页面
    GET  /manifest.webmanifest  PWA 清单
    GET  /sw.js                Service Worker
    GET  /api/state            好感度 / 轮数 / 服务状态
    POST /api/chat             {"text": "..."} -> 回复 + 若干段 wav(base64)
    GET  /assets/*.png         立绘与表情
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import load_settings
from .pipeline import build_pipeline

log = logging.getLogger("yukino.server")

ASSETS = Path(__file__).resolve().parent / "ui" / "assets"
# QQ 语音消息需要「公网可访问的 URL」而不是 base64，所以把 SILK 临时文件放在这里
# 由 /media/<name>?t=<token> 提供，机器人发完就删（见 app/qq/bot.py）。
MEDIA = Path(__file__).resolve().parent.parent / "data" / "qq_media"

_PIPELINE = None
_LOCK = threading.Lock()
TOKEN = ""

PAGE = """<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>雪之下雪乃の奉仕部部室</title>
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#12141c">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="雪乃">
<link rel="apple-touch-icon" href="/assets/yukino.png">
<style>
  :root{--bg:#12141c;--panel:#1b1f2b;--line:#2c3242;--text:#e6e9f2;--dim:#8b93a7;
        --accent:#8fb6e8}
  *{box-sizing:border-box}
  html,body{height:100%}
  body{margin:0;background:var(--bg);color:var(--text);
       font-family:"Microsoft YaHei","Hiragino Sans","PingFang SC",system-ui,sans-serif;
       -webkit-text-size-adjust:100%;overscroll-behavior:none}
  .wrap{display:flex;height:100dvh;gap:18px;padding:18px;
        padding-bottom:calc(18px + env(safe-area-inset-bottom))}
  .left{width:38%;max-width:460px;display:flex;flex-direction:column;gap:12px}
  .art{flex:1;min-height:0;background:var(--panel);border:1px solid var(--line);
       border-radius:14px;overflow:hidden;display:flex;align-items:center;justify-content:center}
  .art img{width:100%;height:100%;object-fit:cover;object-position:center top}
  .meta{display:flex;flex-direction:column;gap:6px}
  .bar{height:6px;background:#0e1017;border-radius:3px;overflow:hidden}
  .bar>div{height:100%;width:50%;background:linear-gradient(90deg,#5b7fb5,#9ec8f5);
           transition:width .4s}
  .hint{color:var(--dim);font-size:12px;line-height:1.5}
  .right{flex:1;min-width:0;display:flex;flex-direction:column;background:var(--panel);
         border:1px solid var(--line);border-radius:14px;overflow:hidden}
  header{padding:14px 18px;border-bottom:1px solid var(--line)}
  header h1{margin:0;font-size:17px;font-weight:600;letter-spacing:.5px}
  header .sub{color:var(--dim);font-size:12px;margin-top:4px}
  #chat{flex:1;overflow-y:auto;padding:16px 18px;display:flex;flex-direction:column;
        gap:14px;-webkit-overflow-scrolling:touch}
  .msg{max-width:82%;padding:10px 14px;border-radius:12px;line-height:1.65;font-size:14px;
       white-space:pre-wrap;word-break:break-word}
  .me{align-self:flex-end;background:#2b3a52;border:1px solid #3a4d6b}
  .her{align-self:flex-start;background:#242938;border:1px solid var(--line)}
  .her .act{color:var(--dim);font-size:12px;margin-bottom:6px}
  .her .zh{color:#a9b4c9;font-size:12.5px;margin-top:8px;border-top:1px dashed #333a4c;
           padding-top:8px}
  .meta-line{color:var(--dim);font-size:11.5px;margin-top:8px}
  form{display:flex;gap:10px;padding:14px 18px;border-top:1px solid var(--line)}
  input{flex:1;min-width:0;background:#0f1219;border:1px solid var(--line);
        border-radius:10px;padding:12px 14px;color:var(--text);font-size:15px;outline:none}
  input:focus{border-color:#4b6a94}
  button{background:#3b5f8f;color:#fff;border:0;border-radius:10px;padding:0 20px;
         font-size:15px;cursor:pointer;flex:0 0 auto}
  button:disabled{opacity:.5;cursor:default}
  /* ---------- 手机：单列，顶部一条紧凑状态栏 ---------- */
  @media (max-width: 820px){
    .wrap{flex-direction:column;gap:0;padding:0;height:100dvh}
    .left{width:100%;max-width:none;flex:0 0 auto;flex-direction:row;align-items:center;
          gap:12px;padding:10px 14px;border-bottom:1px solid var(--line)}
    .art{width:64px;height:64px;flex:0 0 auto;border-radius:12px}
    .art img{object-position:center 18%}
    .meta{flex:1;min-width:0}
    .hint{display:none}
    .right{border:0;border-radius:0;flex:1;min-height:0}
    header{padding:10px 14px}
    header h1{font-size:15px}
    #chat{padding:12px 14px}
    .msg{max-width:92%;font-size:15px}
    form{padding:10px 12px;padding-bottom:calc(10px + env(safe-area-inset-bottom))}
    input{font-size:16px}   /* 16px 以上才不会触发 iOS 自动放大 */
  }
</style></head>
<body><div class="wrap">
  <div class="left">
    <div class="art"><img id="art" src="/assets/yukino.png" alt="雪乃"></div>
    <div class="meta">
      <div class="bar"><div id="bar"></div></div>
      <div class="hint" id="status">连接中…</div>
    </div>
  </div>
  <div class="right">
    <header>
      <h1>雪之下雪乃の奉仕部部室</h1>
      <div class="sub" id="sub">好感度 —</div>
    </header>
    <div id="chat"></div>
    <form id="f">
      <input id="q" placeholder="日本語で話しかけてください" autocomplete="off"
             enterkeyhint="send">
      <button id="send">送信</button>
    </form>
  </div>
</div>
<script>
const chat=document.getElementById('chat'), art=document.getElementById('art');
const bar=document.getElementById('bar'), sub=document.getElementById('sub');
const statusEl=document.getElementById('status'), q=document.getElementById('q');
const send=document.getElementById('send');
const MOODS=['calm','tsundere','soft','angry','shy','tired'];
let ac=null, firstTurn=true;

function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function add(cls,html){const d=document.createElement('div');d.className='msg '+cls;
  d.innerHTML=html;chat.appendChild(d);chat.scrollTop=chat.scrollHeight;return d;}

/* iOS/Android 需要用户手势才能出声：在点击「送信」时解锁 */
function unlockAudio(){
  if(!ac){ const A=window.AudioContext||window.webkitAudioContext; if(A) ac=new A(); }
  if(ac && ac.state==='suspended') ac.resume();
}

async function playAll(list){
  if(!list||!list.length) return;
  if(ac){
    for(const b64 of list){
      try{
        const bin=atob(b64), buf=new Uint8Array(bin.length);
        for(let i=0;i<bin.length;i++) buf[i]=bin.charCodeAt(i);
        const ab=await new Promise((res,rej)=>ac.decodeAudioData(buf.buffer,res,rej));
        await new Promise(res=>{const s=ac.createBufferSource();s.buffer=ab;
          s.connect(ac.destination);s.onended=res;s.start();});
      }catch(e){ /* 解码失败就跳过，别卡住整轮 */ }
    }
    return;
  }
  for(const b64 of list){
    await new Promise(res=>{const a=new Audio('data:audio/wav;base64,'+b64);
      a.onended=res;a.onerror=res;a.play().catch(res);});
  }
}

async function refresh(scroll){
  try{
    const r=await fetch('/api/state',{cache:'no-store'});
    if(r.status===401){ statusEl.textContent='需要访问令牌'; return; }
    const s=await r.json();
    sub.textContent='好感度 '+(s.affinity>0?'+':'')+s.affinity+' / 100 · '+s.band+
                    ' · 累计 '+s.total_turns+' 轮';
    bar.style.width=((s.affinity+100)/200*100)+'%';
    statusEl.textContent='思考: '+s.provider+' / '+s.model+' · 语音: '+
                         (s.tts_ok?'已连接':'未启动');
    art.src='/assets/'+(MOODS.includes(s.mood)?s.mood:'yukino')+'.png';
    if(scroll && !firstTurn) return;
    chat.innerHTML='';
    for(const m of s.history||[]){
      if(m.user_text) add('me',esc(m.user_text));
      if(m.ja) add('her',(m.act?'<div class="act">（'+esc(m.act)+'）</div>':'')+esc(m.ja)+
                          (m.zh?'<div class="zh">'+esc(m.zh)+'</div>':''));
    }
    firstTurn=false;
  }catch(e){ statusEl.textContent='无法连接: '+e; }
}

document.getElementById('f').addEventListener('submit',async ev=>{
  ev.preventDefault();
  const text=q.value.trim(); if(!text) return;
  unlockAudio();
  q.value=''; send.disabled=true;
  add('me',esc(text));
  const thinking=add('her','<span style="color:#8b93a7">雪乃は考えています…</span>');
  try{
    const r=await fetch('/api/chat',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({text})});
    const d=await r.json();
    thinking.remove();
    if(d.error){ add('her','<span style="color:#e08">'+esc(d.error)+'</span>'); }
    else{
      const t=d.turn;
      add('her',(t.act?'<div class="act">（'+esc(t.act)+'）</div>':'')+esc(t.ja||'')+
                 (t.zh?'<div class="zh">'+esc(t.zh)+'</div>':'')+
                 '<div class="meta-line">好感度 '+(d.delta>0?'+':'')+d.delta+' → '+d.affinity+
                 ' · 首响 '+d.first_audio_s.toFixed(2)+'s</div>');
      for(const w of (d.notices||[])) add('her','<span style="color:#c9a66b">'+esc(w)+'</span>');
      if(d.audios&&d.audios.length) playAll(d.audios);
    }
    await refresh(true);
  }catch(e){ thinking.remove(); add('her','<span style="color:#e08">请求失败: '+esc(e)+'</span>'); }
  send.disabled=false; q.focus();
});
if('serviceWorker' in navigator){ navigator.serviceWorker.register('/sw.js').catch(()=>{}); }
refresh(false);
</script></body></html>
"""

MANIFEST = {
    "name": "雪之下雪乃の奉仕部部室",
    "short_name": "雪乃",
    "description": "本地运行的雪之下雪乃语音对话伴侣",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#12141c",
    "theme_color": "#12141c",
    "icons": [
        {"src": "/assets/yukino.png", "sizes": "600x1156", "type": "image/png", "purpose": "any"}
    ],
}

SW_JS = """
// 只缓存静态资源；/api/* 永远走网络，避免读到旧的好感度
const CACHE='yukino-v1';
const ASSETS=['/assets/yukino.png','/assets/calm.png','/assets/tsundere.png',
              '/assets/soft.png','/assets/angry.png','/assets/shy.png','/assets/tired.png'];
self.addEventListener('install',e=>{
  e.waitUntil(caches.open(CACHE).then(c=>c.addAll(ASSETS).catch(()=>{})).then(()=>self.skipWaiting()));
});
self.addEventListener('activate',e=>{
  e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==CACHE).map(k=>caches.delete(k))))
    .then(()=>self.clients.claim()));
});
self.addEventListener('fetch',e=>{
  const u=new URL(e.request.url);
  if(u.pathname.startsWith('/api/')||u.pathname==='/') return;
  if(!u.pathname.startsWith('/assets/')) return;
  e.respondWith(caches.match(e.request).then(hit=>hit||fetch(e.request)));
});
"""


def lan_ips() -> list[str]:
    """探测本机局域网 IPv4，用于打印手机可访问的地址。"""
    ips: set[str] = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith(("192.168.", "10.", "172.")) and not ip.startswith("172.17."):
                ips.add(ip)
    except Exception:
        pass
    return sorted(ips)


def get_pipeline():
    global _PIPELINE
    if _PIPELINE is None:
        _PIPELINE = build_pipeline(load_settings())
    return _PIPELINE


class Handler(BaseHTTPRequestHandler):
    server_version = "YukinoServer/2.1"

    # ---------- 工具 ----------
    def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8",
              extra_headers: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _json(self, obj: dict, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def log_message(self, fmt, *args):
        log.debug("%s - %s", self.address_string(), fmt % args)

    # ---------- 鉴权 ----------
    def _token_ok(self) -> bool:
        if not TOKEN:
            return True
        if self.headers.get("X-Token") == TOKEN:
            return True
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "yt" and v == TOKEN:
                return True
        qs = parse_qs(urlparse(self.path).query)
        return qs.get("t", [""])[0] == TOKEN

    # ---------- 路由 ----------
    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/manifest.webmanifest":
            self._send(200, json.dumps(MANIFEST, ensure_ascii=False).encode("utf-8"),
                       "application/manifest+json; charset=utf-8")
            return
        if path == "/sw.js":
            self._send(200, SW_JS.encode("utf-8"), "application/javascript; charset=utf-8")
            return
        if path.startswith("/assets/"):
            self._asset(path[len("/assets/"):])
            return

        if not self._token_ok():
            self._send(
                401,
                ("<meta charset='utf-8'><body style='font-family:sans-serif;background:#12141c;"
                 "color:#e6e9f2;padding:40px'><h3>需要访问令牌</h3>"
                 "<p>请在网址后加上 <code>?t=你的令牌</code>，例如：</p>"
                 f"<p><code>http://{self.headers.get('Host','')}/?t=YOUR_TOKEN</code></p>"
                 "</body>").encode("utf-8"),
            )
            return

        if path in ("/", "/index.html"):
            qs = parse_qs(urlparse(self.path).query)
            headers = {}
            if TOKEN and qs.get("t", [""])[0] == TOKEN:
                # 用 ?t= 进来之后种 cookie，之后就不必带令牌了
                headers["Set-Cookie"] = f"yt={TOKEN}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000"
            self._send(200, PAGE.encode("utf-8"), extra_headers=headers)
            return
        if path == "/api/state":
            self._state()
            return
        if path.startswith("/media/"):
            self._media(path[len("/media/"):])
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self):
        if urlparse(self.path).path != "/api/chat" or not self._token_ok():
            self._json({"error": "unauthorized"}, 401) if not self._token_ok() else \
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
        for p in (ASSETS / "expressions" / name, ASSETS / name):
            if p.is_file():
                self._send(200, p.read_bytes(), "image/png",
                           {"Cache-Control": "public, max-age=86400"})
                return
        self._send(404, b"no asset", "text/plain")

    def _media(self, name: str):
        """给 QQ 拉取语音 SILK 用。只允许 data/qq_media 下的单层文件名。"""
        name = Path(name).name
        if not name or name.startswith("."):
            self._send(404, b"no media", "text/plain")
            return
        p = MEDIA / name
        try:
            data = p.read_bytes()
        except OSError:
            self._send(404, b"no media", "text/plain")
            return
        ctype = "audio/silk" if name.endswith(".silk") else "application/octet-stream"
        self._send(200, data, ctype, {"Cache-Control": "no-store"})

    def _state(self):
        try:
            pipe = get_pipeline()
        except Exception as e:
            self._json({"error": f"初始化失败：{e}"}, 500)
            return
        hist = [
            {"user_text": r["user_text"], "ja": r["ja"], "zh": r["zh"], "act": r["act"]}
            for r in pipe.session.recent_turns(30)
        ]
        self._json({
            **pipe.session.stats(),
            "mood": pipe.session.get("mood", "calm") or "calm",
            "provider": getattr(pipe.llm, "provider", pipe.settings.llm_provider),
            "model": getattr(pipe.llm, "model", pipe.settings.model),
            "tts_ok": pipe.tts.health(timeout=1.5),
            "history": hist,
        })

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
        t0 = time.time()

        with _LOCK:  # 单用户，避免并发抢显存
            try:
                for ev in pipe.respond(text):
                    kind = ev.get("type")
                    if kind == "sentence":
                        if ev.get("audio"):
                            if first_audio is None:
                                first_audio = time.time() - t0
                            audios.append(base64.b64encode(ev["audio"]).decode("ascii"))
                    elif kind in ("info", "warn", "error"):
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

        self._json({
            "turn": turn.to_dict(),
            "delta": delta,
            "affinity": pipe.session.affinity,
            "audios": audios,
            "first_audio_s": round(first_audio or 0.0, 2),
            "notices": [n for n in notices if n],
        })


def main(argv=None) -> int:
    global TOKEN

    ap = argparse.ArgumentParser(description="雪之下雪乃 · 零依赖网页版（手机可用）")
    ap.add_argument("--host", default="127.0.0.1",
                    help="127.0.0.1=仅本机；0.0.0.0=局域网/公网可访问")
    ap.add_argument("--port", type=int, default=8501)
    ap.add_argument("--token", default=os.environ.get("YUKINO_TOKEN", ""),
                    help="访问令牌；对外暴露时强烈建议设置")
    args = ap.parse_args(argv)

    TOKEN = (args.token or "").strip()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    settings = load_settings()
    public = args.host not in ("127.0.0.1", "localhost")

    print("=" * 60)
    print("  雪之下雪乃 · 零依赖网页版")
    print("=" * 60)
    print(f"  监听     : {args.host}:{args.port}")
    print(f"  思考模型 : {settings.llm_provider} / "
          f"{settings.ollama_model if settings.llm_provider == 'ollama' else settings.model}")
    print(f"  语音服务 : {settings.gsv_api_base}")
    print(f"  访问令牌 : {'已开启' if TOKEN else '未设置'}")
    if public:
        print("  手机访问 :")
        for ip in lan_ips():
            suffix = f"?t={TOKEN}" if TOKEN else ""
            print(f"      http://{ip}:{args.port}/{suffix}")
        if not TOKEN:
            print("  ⚠ 未设令牌且对外监听：同一网络内任何人都能用你的显卡")
    else:
        print(f"  本机访问 : http://127.0.0.1:{args.port}")
    print("=" * 60)
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

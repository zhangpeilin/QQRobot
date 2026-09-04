"""QQ 群媒体归档系统 - 控制面板 Web UI（端口 8090）

功能: 启动/停止 NapCat 与 bot、查看运行状态、查看日志、打开二维码/归档目录。
风格: 仿 NapCat WebUI 暗色主题。
"""

import hashlib
import json
import socket
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).parent
LOGS_DIR = ROOT / "logs"
NAPCAT_DIR = ROOT / "NapCat"
ARCHIVE_DIR = ROOT / "data" / "archive"
PORT = 8899

# 状态缓存
_cache = {"ts": 0.0, "data": {}}
_qq_cache = {"ts": 0.0, "data": None}
_stdio_log = None


def _redirect_stdio() -> None:
    """pythonw 下 stdout/stderr 为 None，http.server 写访问日志会崩掉连接。"""
    global _stdio_log
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    _stdio_log = open(LOGS_DIR / "ui_server.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = _stdio_log
    sys.stderr = _stdio_log


def _port_open(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            return s.connect_ex(("127.0.0.1", port)) == 0
    except Exception:
        return False


def _proc_names() -> set:
    """单次 tasklist 调用获取全部进程名"""
    try:
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, timeout=6,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        names = set()
        for line in out.stdout.decode("gbk", errors="replace").splitlines():
            if line.strip().startswith('"'):
                names.add(line.strip().split('"')[1].lower())
        return names
    except Exception:
        return set()


def _napcat_token() -> str:
    try:
        cfg = json.loads((NAPCAT_DIR / "config" / "webui.json").read_text("utf-8"))
        return str(cfg.get("token", ""))
    except Exception:
        return ""


def _qq_status() -> dict:
    now = time.time()
    if _qq_cache["data"] is not None and now - _qq_cache["ts"] < 10:
        return _qq_cache["data"]
    token = _napcat_token()
    result = {"online": False, "uin": "", "nick": "", "error": ""}
    if not token:
        result["error"] = "无 token"
    else:
        try:
            h = hashlib.sha256((token + ".napcat").encode()).hexdigest()
            req = Request(
                "http://127.0.0.1:6099/api/auth/login",
                data=json.dumps({"hash": h}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            cred = json.loads(urlopen(req, timeout=2).read())["data"]["Credential"]
            req2 = Request(
                "http://127.0.0.1:6099/api/QQLogin/GetQQLoginInfo",
                data=b"{}",
                headers={"Authorization": "Bearer " + cred, "Content-Type": "application/json"},
                method="POST",
            )
            d = json.loads(urlopen(req2, timeout=2).read())["data"]
            result = {"online": bool(d.get("online")), "uin": d.get("uin", ""), "nick": d.get("nick", "")}
        except Exception as e:
            result["error"] = str(e)[:80]
    _qq_cache["ts"], _qq_cache["data"] = now, result
    return result


def _status() -> dict:
    now = time.time()
    if now - _cache["ts"] < 2.0:
        return _cache["data"]
    names = _proc_names()
    data = {
        "napcat_running": "napcatwinbootmain.exe" in names,
        "qq_running": "qq.exe" in names,
        "bot_running": _port_open(8080),
        "port_3001": _port_open(3001),
        "port_6099": _port_open(6099),
        "port_8080": _port_open(8080),
        "qq": _qq_status(),
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _cache["ts"], _cache["data"] = now, data
    return data


def _decode_log_line(line: bytes) -> str:
    # 同一文件里可能混有旧 GBK 和新 UTF-8，必须按行判断
    try:
        return line.decode("utf-8")
    except UnicodeDecodeError:
        return line.decode("gbk", errors="replace")


def _tail_log(path: Path, lines: int = 200) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 128 * 1024))
            raw = f.read()
        if size > 128 * 1024:
            nl = raw.find(b"\n")
            if nl != -1:
                raw = raw[nl + 1 :]
        text_lines = [_decode_log_line(line) for line in raw.splitlines()]
        return "\n".join(text_lines[-lines:])
    except Exception as e:
        return f"日志读取失败: {e}"


def _logs(name: str, lines: int) -> str:
    fmap = {
        "bot": LOGS_DIR / "bot_out.log",
        "archiver": LOGS_DIR / "bot_err.log",
    }
    return _tail_log(fmap.get(name, LOGS_DIR / "bot_out.log"), max(10, min(lines, 1000)))


def _start_napcat() -> str:
    if _status()["napcat_running"]:
        return "NapCat 已在运行"
    subprocess.Popen(
        ["cmd", "/c", "start_napcat.bat"],
        cwd=str(ROOT),
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )
    return "NapCat 启动中（可能需要 UAC 授权 + 扫码）"


def _stop_napcat() -> str:
    subprocess.Popen(
        ["powershell", "-NoProfile", "-Command",
         "Start-Process taskkill -ArgumentList '/F','/T','/IM','QQ.exe','/IM','QQEX.exe','/IM','NapCatWinBootMain.exe' -Verb runAs"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return "NapCat 停止中（UAC 确认后生效）"


def _run_hidden(args: list[str], timeout: int = 8) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=True,
        timeout=timeout,
        cwd=str(ROOT),
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def _bot_pids() -> list[int]:
    """python.exe / pythonw.exe 里命令行带 bot.py 的进程（不含 ui_server）。"""
    ps = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { "
        "($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') -and "
        "$_.CommandLine -and ($_.CommandLine -match '(^|\\s|\\\\)bot\\.py(\\s|\"|$)') "
        "} | ForEach-Object { $_.ProcessId }"
    )
    try:
        out = _run_hidden(["powershell", "-NoProfile", "-Command", ps]).stdout
        text = out.decode("utf-8", errors="replace")
        pids = []
        for tok in text.split():
            if tok.isdigit():
                pids.append(int(tok))
        return pids
    except Exception:
        return []


def _listening_pid(port: int) -> int | None:
    try:
        out = _run_hidden(["netstat", "-ano"]).stdout.decode("gbk", errors="replace")
    except Exception:
        return None
    suffix = f":{port}"
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        if parts[3].upper() != "LISTENING":
            continue
        if parts[1] == f"0.0.0.0:{port}" or parts[1].endswith(suffix):
            try:
                return int(parts[-1])
            except ValueError:
                return None
    return None


def _pid_is_python(pid: int) -> bool:
    try:
        out = _run_hidden(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").Name"]
        ).stdout.decode("utf-8", errors="replace").strip().lower()
        return out in {"python.exe", "pythonw.exe"}
    except Exception:
        return False


def _kill_pids(pids: list[int]) -> None:
    seen: set[int] = set()
    for pid in pids:
        if pid in seen or pid <= 0:
            continue
        seen.add(pid)
        try:
            _run_hidden(["taskkill", "/F", "/T", "/PID", str(pid)], timeout=10)
        except Exception:
            pass


def _start_bot() -> str:
    if _status()["bot_running"]:
        return "bot 已在运行"
    import os as _os
    env = dict(_os.environ)
    env["PYTHONIOENCODING"] = "utf-8"  # 新日志统一 UTF-8，避免 GBK 乱码
    subprocess.Popen(
        [sys.executable, "bot.py"],
        cwd=str(ROOT),
        stdout=open(LOGS_DIR / "bot_out.log", "a", encoding="utf-8", buffering=1),
        stderr=open(LOGS_DIR / "bot_err.log", "a", encoding="utf-8", buffering=1),
        env=env,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return "bot 启动中（日志为 UTF-8 追加模式）"


def _stop_bot() -> str:
    _cache["ts"] = 0.0
    pids = _bot_pids()
    listen = _listening_pid(8080)
    if listen and _pid_is_python(listen) and listen not in pids:
        pids.append(listen)
    if not pids and not _port_open(8080):
        return "bot 未在运行"
    _kill_pids(pids)
    deadline = time.time() + 6
    while time.time() < deadline:
        still = _bot_pids()
        if not still and not _port_open(8080):
            _cache["ts"] = 0.0
            return "bot 已停止"
        if still:
            _kill_pids(still)
        time.sleep(0.25)
    _cache["ts"] = 0.0
    if _port_open(8080) or _bot_pids():
        return "bot 停止失败，进程仍在"
    return "bot 已停止"


def _open_dir(path: Path) -> str:
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(["explorer", str(path)])
    return "已打开目录"


HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>QQ 归档控制面板</title>
<style>
:root{--bg:#0a0a12;--card:#14141f;--border:#26263a;--text:#e8e8f0;--muted:#8a8aa3;
--green:#22c55e;--red:#ef4444;--yellow:#eab308;--blue:#3b82f6;--accent:#3390ff;}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:"Segoe UI","Microsoft YaHei",sans-serif;min-height:100vh}
.header{display:flex;align-items:center;gap:14px;padding:18px 28px;border-bottom:1px solid var(--border);
background:linear-gradient(180deg,#14142a,#0a0a12)}
.header h1{font-size:20px;font-weight:600}
.header .sub{color:var(--muted);font-size:12px;margin-top:2px}
.badge{padding:3px 12px;border-radius:99px;font-size:12px;font-weight:600}
.badge.ok{background:rgba(34,197,94,.15);color:var(--green);border:1px solid rgba(34,197,94,.4)}
.badge.down{background:rgba(239,68,68,.15);color:var(--red);border:1px solid rgba(239,68,68,.4)}
.wrap{padding:24px 28px;display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:18px}
.card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:18px}
.card h2{font-size:15px;font-weight:600;margin-bottom:14px;display:flex;align-items:center;gap:8px}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block}
.dot.ok{background:var(--green);box-shadow:0 0 8px var(--green)}
.dot.down{background:var(--red);box-shadow:0 0 8px var(--red)}
.row{display:flex;justify-content:space-between;padding:6px 0;font-size:13px;color:var(--muted);border-bottom:1px dashed #1e1e30}
.row b{color:var(--text);font-weight:500}
.btns{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
button{background:#1e1e32;border:1px solid var(--border);color:var(--text);padding:8px 16px;
border-radius:9px;cursor:pointer;font-size:13px;transition:.15s}
button:hover{background:#2a2a45;border-color:var(--accent)}
button.primary{background:rgba(51,144,255,.18);border-color:var(--accent);color:#7db8ff}
button.danger:hover{background:rgba(239,68,68,.2);border-color:var(--red)}
.logpanel{grid-column:1/-1;background:var(--card);border:1px solid var(--border);border-radius:14px;padding:18px}
.tabs{display:flex;gap:8px;margin-bottom:12px}
.tab{padding:7px 18px;border-radius:8px;cursor:pointer;font-size:13px;color:var(--muted);border:1px solid transparent}
.tab.active{background:#1e1e32;color:var(--text);border-color:var(--accent)}
pre{background:#0d0d16;border:1px solid #1c1c2e;border-radius:10px;padding:14px;font-family:Consolas,monospace;
font-size:12px;line-height:1.5;height:460px;overflow:auto;white-space:pre-wrap;word-break:break-all;color:#c8c8d8}
.toast{position:fixed;top:18px;right:24px;background:#1e2a1e;border:1px solid var(--green);color:#a7e8b7;
padding:10px 18px;border-radius:10px;font-size:13px;opacity:0;transition:.3s;z-index:99}
</style>
</head>
<body>
<div class="header">
  <h1>🐧 QQ 归档控制面板</h1>
  <div class="sub" id="clock">--</div>
  <span style="flex:1"></span>
  <span class="badge" id="bNapcat">检测中</span>
  <span class="badge" id="bBot">检测中</span>
</div>
<div class="wrap">
  <div class="card">
    <h2><span class="dot" id="dNapcat"></span>NapCat 协议层</h2>
    <div class="row">注入进程 <b id="sNapcatProg">--</b></div>
    <div class="row">QQ 进程 <b id="sQQProg">--</b></div>
    <div class="row">WebUI 6099 <b id="s6099">--</b></div>
    <div class="row">OneBot 3001 <b id="s3001">--</b></div>
    <div class="row">小号登录 <b id="sQQ">--</b></div>
    <div class="btns">
      <button class="primary" onclick="act('/api/napcat/start')">启动 NapCat</button>
      <button class="danger" onclick="act('/api/napcat/stop')">停止 NapCat</button>
      <button onclick="act('/api/open_qrcode')">打开二维码目录</button>
    </div>
  </div>
  <div class="card">
    <h2><span class="dot" id="dBot"></span>归档 Bot</h2>
    <div class="row">bot 进程 <b id="sBotProg">--</b></div>
    <div class="row">HTTP 8080 <b id="s8080">--</b></div>
    <div class="btns">
      <button class="primary" onclick="act('/api/bot/start')">启动 Bot</button>
      <button class="danger" onclick="act('/api/bot/stop')">停止 Bot</button>
      <button onclick="act('/api/open_archive')">打开归档目录</button>
    </div>
  </div>
  <div class="logpanel">
    <div class="tabs">
      <div class="tab active" onclick="switchTab('archiver',this)">归档日志</div>
      <div class="tab" onclick="switchTab('bot',this)">Bot 输出</div>
      <button style="margin-left:auto" onclick="loadLog()">刷新</button>
      <button onclick="auto=!auto;this.textContent=auto?'自动刷新:开':'自动刷新:关'">自动刷新:开</button>
    </div>
    <pre id="log">加载中...</pre>
  </div>
</div>
<div class="toast" id="toast"></div>
<script>
let cur='archiver', auto=true;
function toast(msg){const t=document.getElementById('toast');t.textContent=msg;t.style.opacity=1;
setTimeout(()=>t.style.opacity=0,2500)}
async function act(url){const r=await fetch(url,{method:'POST'});const d=await r.json();toast(d.msg||d.error||'ok')}
function switchTab(name,el){cur=name;document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
el.classList.add('active');loadLog()}
async function loadLog(){const r=await fetch('/api/logs?name='+cur+'&lines=300');const d=await r.json();
const el=document.getElementById('log');const stick=el.scrollTop+el.clientHeight>el.scrollHeight-40;
el.textContent=d.log;if(stick)el.scrollTop=el.scrollHeight}
function dot(el,ok){el.className='dot '+(ok?'ok':'down')}
function badge(el,ok,txt){el.textContent=txt;el.className='badge '+(ok?'ok':'down')}
async function refresh(){
  try{
    const r=await fetch('/api/status');const s=await r.json();
    document.getElementById('clock').textContent=s.time;
    badge(document.getElementById('bNapcat'),s.napcat_running,s.napcat_running?'NapCat 运行中':'NapCat 已停止');
    badge(document.getElementById('bBot'),s.bot_running,s.bot_running?'Bot 运行中':'Bot 已停止');
    dot(document.getElementById('dNapcat'),s.napcat_running);
    dot(document.getElementById('dBot'),s.bot_running);
    document.getElementById('sNapcatProg').textContent=s.napcat_running?'✓':'✗';
    document.getElementById('sQQProg').textContent=s.qq_running?'✓':'✗';
    document.getElementById('s6099').textContent=s.port_6099?'监听中':'未监听';
    document.getElementById('s3001').textContent=s.port_3001?'监听中':'未监听';
    document.getElementById('s8080').textContent=s.port_8080?'监听中':'未监听';
    document.getElementById('sBotProg').textContent=s.bot_running?'✓':'✗';
    const q=s.qq;document.getElementById('sQQ').textContent=q.online?('在线 '+q.uin+' '+q.nick):'离线';
    document.getElementById('sQQ').style.color=q.online?'var(--green)':'var(--red)';
  }catch(e){}
}
setInterval(()=>{refresh();if(auto)loadLog()},3000);
refresh();loadLog();
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, ctype: str = "application/json"):
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj):
        self._send(json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._send(HTML.encode("utf-8"), "text/html")
        elif self.path.startswith("/api/status"):
            self._json(_status())
        elif self.path.startswith("/api/logs"):
            import urllib.parse as up
            q = up.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            self._json({"log": _logs(q.get("name", ["bot"])[0], int(q.get("lines", ["200"])[0]))})
        else:
            self._json({"error": "not found"})

    def do_POST(self):
        path = self.path
        actions = {
            "/api/napcat/start": _start_napcat,
            "/api/napcat/stop": _stop_napcat,
            "/api/bot/start": _start_bot,
            "/api/bot/stop": _stop_bot,
            "/api/open_qrcode": lambda: _open_dir(NAPCAT_DIR / "cache"),
            "/api/open_archive": lambda: _open_dir(ARCHIVE_DIR),
        }
        fn = actions.get(path)
        if fn:
            try:
                self._json({"msg": fn()})
            except Exception as e:
                self._json({"error": str(e)})
        else:
            self._json({"error": "unknown action"})


def main():
    _redirect_stdio()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
        print(f"QQ 归档控制面板: http://127.0.0.1:{PORT}", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    except Exception:
        import traceback
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()

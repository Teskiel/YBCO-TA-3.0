#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""noisesweep Dashboard — 最终操作面板（零第三方依赖，stdlib 实现）。

启动：
    python noisesweep_dashboard.py --config noisesweep_config.json --port 8000

浏览器打开 http://127.0.0.1:8000 ，勾选模块 + 填参数 → 「运行」。
服务端把请求转成一条 ``noisesweep.py <子命令>``，用 ``subprocess`` 以
``-u``（无缓冲）+ ``PYTHONIOENCODING=utf-8`` 启动，stdout/stderr 逐行
回流到日志区（前端轮询 /api/log）。

关键设计：
  * 进程级单例锁 —— 同一时刻只允许一个测量子进程运行，防仪器并发争抢。
  * Dashboard 与测量子进程解耦 —— 子进程是独立进程，Dashboard 崩溃/刷新
    不影响已启动的测量；测量完成与否由 checkpoint 保证续跑。
  * 零依赖 —— 只用 http.server / json / subprocess / threading，不 import
    PyQt5 / matplotlib / h5py，可在实验机无图形环境运行。
"""

import argparse
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

# ---- 全局状态（单例锁保护）----
_STATE = {
    "proc": None,        # subprocess.Popen | None
    "log_lines": [],     # 累积的日志行
}
_LOCK = threading.Lock()


def _build_command(args_list, dry_run, config_path):
    """拼一条 noisesweep.py 子命令。"""
    cmd = [sys.executable, "-u", str(SCRIPT_DIR / "noisesweep.py"),
           "--config", str(config_path)]
    if dry_run:
        cmd.append("--dry-run")
    cmd.extend(args_list)
    return cmd


def _pump(proc):
    """读子进程 stdout（逐行）累积到日志区；进程退出后追加退出码。"""
    for line in proc.stdout:
        with _LOCK:
            _STATE["log_lines"].append(line)
    proc.wait()
    with _LOCK:
        _STATE["log_lines"].append(
            "\n[进程退出，code={}]\n".format(proc.returncode)
        )


class Handler(BaseHTTPRequestHandler):
    config_path = None  # 由 main 注入

    def log_message(self, fmt, *args):  # 静默访问日志，避免刷屏
        pass

    # ---- 路由 ----
    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._send(200, "text/html; charset=utf-8", HTML.encode("utf-8"))
        elif self.path.startswith("/api/log"):
            self._api_log()
        elif self.path.startswith("/api/status"):
            self._api_status()
        else:
            self._send(404, "text/plain; charset=utf-8", b"not found")

    def do_POST(self):
        if self.path == "/api/run":
            self._api_run()
        elif self.path == "/api/stop":
            self._api_stop()
        else:
            self._send(404, "text/plain; charset=utf-8", b"not found")

    # ---- 处理器 ----
    def _api_run(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception as exc:
            self._send(400, "application/json", json.dumps(
                {"ok": False, "error": "bad request: {}".format(exc)}).encode())
            return
        args_list = body.get("args") or []
        dry_run = bool(body.get("dry_run"))

        with _LOCK:
            if _STATE["proc"] is not None and _STATE["proc"].poll() is None:
                self._send(409, "application/json", json.dumps(
                    {"ok": False, "error": "已有测量在运行，请先停止或等待完成",
                     "running": True}).encode())
                return
            _STATE["log_lines"] = []
            cmd = _build_command(args_list, dry_run, self.config_path)
            env = dict(os.environ)
            env["PYTHONIOENCODING"] = "utf-8"
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", env=env,
                cwd=str(SCRIPT_DIR),
            )
            _STATE["proc"] = proc
            threading.Thread(target=_pump, args=(proc,), daemon=True).start()
            _STATE["log_lines"].append("$ {}\n".format(" ".join(cmd)))

        self._send(200, "application/json", json.dumps(
            {"ok": True, "pid": proc.pid}).encode())

    def _api_stop(self):
        with _LOCK:
            proc = _STATE["proc"]
            if proc is None or proc.poll() is not None:
                self._send(200, "application/json", json.dumps(
                    {"ok": True, "stopped": False}).encode())
                return
            proc.terminate()
        self._send(200, "application/json", json.dumps(
            {"ok": True, "stopped": True, "pid": proc.pid}).encode())

    def _api_log(self):
        # ?after=N 增量返回
        try:
            after = int(self.path.split("after=", 1)[1].split("&", 1)[0])
        except Exception:
            after = 0
        with _LOCK:
            lines = _STATE["log_lines"][after:]
            total = len(_STATE["log_lines"])
            running = _STATE["proc"] is not None and _STATE["proc"].poll() is None
        self._send(200, "application/json", json.dumps(
            {"lines": lines, "total": total, "running": running}).encode())

    def _api_status(self):
        with _LOCK:
            running = _STATE["proc"] is not None and _STATE["proc"].poll() is None
            pid = _STATE["proc"].pid if _STATE["proc"] else None
        self._send(200, "application/json", json.dumps(
            {"running": running, "pid": pid}).encode())

    def _send(self, code, ctype, payload):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)


# =========================================================================
# 单文件 HTML（最终操作面板）
# =========================================================================

HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>noisesweep 操作面板</title>
<style>
  :root { --bg:#0f1420; --panel:#1a2130; --line:#2a3346; --fg:#dbe2ef;
          --accent:#4da3ff; --ok:#39d98a; --warn:#ffb454; --err:#ff6b6b; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:"Segoe UI",system-ui,sans-serif; background:var(--bg);
         color:var(--fg); font-size:14px; }
  header { padding:14px 22px; background:var(--panel); border-bottom:1px solid var(--line);
           display:flex; align-items:center; gap:14px; }
  header h1 { font-size:16px; margin:0; font-weight:600; }
  #status { margin-left:auto; font-size:12px; padding:4px 10px; border-radius:12px;
            background:#2a3346; }
  #status.running { background:#1d3327; color:var(--ok); }
  main { display:grid; grid-template-columns:380px 1fr; gap:16px; padding:16px 22px;
         height:calc(100vh - 62px); }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:8px;
          padding:16px; }
  h2 { font-size:13px; margin:0 0 12px; color:#8fa1bd; text-transform:uppercase;
       letter-spacing:.04em; }
  .field { margin-bottom:10px; }
  .field label { display:block; font-size:12px; color:#9fb0ca; margin-bottom:4px; }
  .field input, .field select { width:100%; padding:7px 9px; background:#111828;
        border:1px solid var(--line); border-radius:6px; color:var(--fg); font-size:13px; }
  .row { display:flex; gap:10px; } .row .field { flex:1; }
  .checks { display:flex; flex-wrap:wrap; gap:14px; margin-bottom:12px; }
  .checks label { font-size:13px; display:flex; align-items:center; gap:6px; cursor:pointer; }
  button { padding:9px 16px; border:0; border-radius:6px; font-size:13px; cursor:pointer;
           font-weight:600; }
  #run { background:var(--accent); color:#06121f; }
  #stop { background:#3a2230; color:var(--err); }
  #logbox { background:#0a0e17; border:1px solid var(--line); border-radius:6px;
            padding:12px; overflow:auto; height:100%; font-family:Consolas,monospace;
            font-size:12px; line-height:1.5; white-space:pre-wrap; word-break:break-all; }
  .logcard { display:flex; flex-direction:column; }
</style>
</head>
<body>
<header>
  <h1>noisesweep 操作面板</h1>
  <span id="status">空闲</span>
</header>
<main>
  <div class="card">
    <h2>测量任务</h2>
    <div class="field">
      <label>模块 / 子命令</label>
      <select id="cmd">
        <option value="run">run — 全自动 变温×谐振×功率</option>
        <option value="scan">scan — 当前温度 n 中心频率扫描</option>
        <option value="noise">noise — 对已有精扫文件补噪声</option>
        <option value="temp read">temp read — 读当前温度</option>
        <option value="temp sweep">temp sweep — 设点+等稳+扫描</option>
        <option value="laser set">laser set — 设激光功率</option>
        <option value="laser off">laser off — 关激光</option>
        <option value="track">track — 追踪表查询</option>
        <option value="chip">chip — 芯片标定库预测</option>
        <option value="iq check">iq check — IQ 校准文件校验</option>
        <option value="checkpoint show">checkpoint show — 查看断点</option>
        <option value="checkpoint clear">checkpoint clear — 清除断点</option>
      </select>
    </div>

    <div id="params"></div>

    <div class="checks">
      <label><input type="checkbox" id="dryrun"> dry-run（mock 空跑）</label>
      <label><input type="checkbox" id="skipnoise"> 跳过噪声</label>
    </div>

    <div class="row">
      <button id="run">运行</button>
      <button id="stop">停止</button>
    </div>
  </div>

  <div class="card logcard">
    <h2>实时日志</h2>
    <div id="logbox"></div>
  </div>
</main>

<script>
const $ = id => document.getElementById(id);

// 每个命令需要的参数字段定义
const FIELDS = {
  "run": [],
  "scan": [
    {k:"freqs", label:"中心频率列表 (GHz, 空格分隔，依序对应 res1,res2,…)", ph:"4.5 4.6 4.7", req:true},
    {k:"res", label:"谐振器名（仅单频率时用，可空）", ph:"res1"},
    {k:"power", label:"激光功率 (mW)", ph:"0"},
    {k:"target", label:"目标温度 (K)", ph:"77"},
  ],
  "noise": [{k:"s21", label:"精扫文件路径", ph:".../fine_s21.h5", req:true}],
  "temp read": [],
  "temp sweep": [{k:"target", label:"目标温度 (K)", ph:"77", req:true}],
  "laser set": [{k:"power", label:"激光功率 (mW)", ph:"1.0", req:true}],
  "laser off": [],
  "track": [{k:"target", label:"目标温度 (K)", ph:"77", req:true},
            {k:"res", label:"谐振器名 (可空)", ph:"res1"}],
  "chip": [{k:"target", label:"目标温度 (K)", ph:"77", req:true}],
  "iq check": [{k:"file", label:"IQ 校准文件路径", ph:".../iq.txt", req:true}],
  "checkpoint show": [],
  "checkpoint clear": [],
};

function renderParams() {
  const cmd = $("cmd").value;
  const box = $("params");
  box.innerHTML = "";
  for (const f of FIELDS[cmd] || []) {
    const d = document.createElement("div");
    d.className = "field";
    d.innerHTML = `<label>${f.label}</label>
      <input id="p_${f.k}" placeholder="${f.ph || ""}">`;
    box.appendChild(d);
  }
}

function collectArgs() {
  const cmd = $("cmd").value;
  const args = cmd.split(" ");
  for (const f of FIELDS[cmd] || []) {
    const v = $("p_" + f.k).value.trim();
    if (f.req && !v) { alert("请填写：" + f.label); return null; }
    if (!v) continue;
    // 字段 → CLI 参数映射
    const map = {
      freqs: () => v.split(/\\s+/),
      res: () => ["--res", v],
      power: () => ["--power-mw", v],
      target: () => ["--target-k", v],
      s21: () => [v],
      file: () => [v],
    };
    args.push(...(map[f.k]()));
  }
  if ($("skipnoise").checked && ["run","scan","temp sweep"].includes(cmd)) {
    args.push("--skip-noise");
  }
  return args;
}

let after = 0, timer = null;

async function poll() {
  try {
    const r = await fetch("/api/log?after=" + after);
    const d = await r.json();
    if (d.lines.length) {
      const box = $("logbox");
      box.textContent += d.lines.join("");
      after = d.total;
      box.scrollTop = box.scrollHeight;
    }
    $("status").textContent = d.running ? "运行中" : "空闲";
    $("status").className = d.running ? "running" : "";
    $("run").disabled = d.running;
  } catch (e) {}
}

$("cmd").addEventListener("change", renderParams);
$("run").addEventListener("click", async () => {
  const args = collectArgs();
  if (!args) return;
  const r = await fetch("/api/run", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({args, dry_run: $("dryrun").checked}),
  });
  const d = await r.json();
  if (!d.ok) { alert(d.error || "启动失败"); return; }
  $("logbox").textContent = "";
  after = 0;
});
$("stop").addEventListener("click", async () => {
  await fetch("/api/stop", {method: "POST"});
});

renderParams();
timer = setInterval(poll, 400);
poll();
</script>
</body>
</html>
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description="noisesweep Dashboard（最终操作面板）")
    ap.add_argument("--config", default="noisesweep_config.json",
                    help="noisesweep config JSON 路径")
    ap.add_argument("--port", type=int, default=8000, help="HTTP 端口")
    ap.add_argument("--host", default="127.0.0.1", help="绑定地址")
    args = ap.parse_args(argv)

    config_path = str(Path(args.config).resolve())
    if not Path(config_path).exists():
        print("config 不存在: {}".format(config_path), file=sys.stderr)
        return 2

    Handler.config_path = config_path
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print("noisesweep Dashboard 已启动: http://{}:{}".format(args.host, args.port))
    print("config: {}".format(config_path))
    print("Ctrl+C 退出（测量子进程独立运行，不受影响）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

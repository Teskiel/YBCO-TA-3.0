# -*- coding: utf-8 -*-
"""
后台僵尸进程清理工具。

Bun runtime 崩溃后，subprocess 启动的 Python 进程可能泄漏。
此工具输出 PowerShell 命令到 stdout，由 Claude Code 通过 Bash 执行。

用法:
  python zombie_killer.py               # 输出扫描 + 终止的 PS 命令
  python zombie_killer.py --dry-run     # 仅扫描，不终止
  python zombie_killer.py --execute     # 直接通过 Bash 执行清理

设计: 因为 Bun 沙箱限制了 Python subprocess 对 PowerShell 的调用，
所有的进程操作通过 Bash 工具执行 PS 命令。

也可作为库导入:
  from zombie_killer import kill_zombie_processes
  killed, freed_mb = kill_zombie_processes()
"""

import argparse
import logging
import subprocess
import sys

_log = logging.getLogger(__name__)

# 判定阈值：python.exe 进程（非 pythonw）内存超过此值视为僵尸
ZOMBIE_MEM_THRESHOLD_MB = 80

# PS 扫描命令
SCAN_SCRIPT = rf"""
Write-Host '--- Python 进程扫描 ---'
Get-CimInstance Win32_Process -Filter "Name LIKE '%python%'" |
    ForEach-Object {{
        $parentName = try {{ (Get-Process -Id $_.ParentProcessId -EA 0).ProcessName }}
                      catch {{ '<gone>' }}
        $isZombie = ($_.WorkingSetSize/1MB -gt {ZOMBIE_MEM_THRESHOLD_MB}) -and ($_.Name -notlike '*pythonw*')
        $marker = if ($isZombie) {{ '[ZOMBIE]' }} else {{ '[OK]' }}
        Write-Host ("{{0}} PID={{1}} Parent={{2}}({{3}}) Mem={{4:F1}}MB" -f
            $marker, $_.ProcessId, $_.ParentProcessId, $parentName,
            ($_.WorkingSetSize/1MB))
    }}
"""

# PS 终止命令（终止高内存的 python.exe，排除 pythonw 和当前进程）
KILL_SCRIPT = r"""
$zombies = Get-CimInstance Win32_Process -Filter "Name LIKE '%python%'" |
    Where-Object {
        ($_.WorkingSetSize/1MB -gt %d) -and ($_.Name -notlike '*pythonw*') -and ($_.ProcessId -ne %d)
    }
if (-not $zombies) {
    Write-Host 'No zombie processes found.'
    exit 0
}
$totalMem = ($zombies | Measure-Object -Property WorkingSetSize -Sum).Sum / 1MB
Write-Host ("Found {0} zombie(s), total {1:F0} MB" -f @($zombies).Count, $totalMem)
foreach ($z in $zombies) {
    Write-Host ("  Killing PID {0} ({1:F0} MB)..." -f $z.ProcessId, ($z.WorkingSetSize/1MB))
    $r = (Get-WmiObject Win32_Process -Filter "ProcessId=$($z.ProcessId)").Terminate()
    Write-Host ("    ReturnValue={0}" -f $r.ReturnValue)
}
Write-Host 'Done.'
"""


# ---------------------------------------------------------------------------
# 可编程 API — 从实验启动流程中调用
# ---------------------------------------------------------------------------

def kill_zombie_processes(dry_run=False, log_callback=None):
    """终止泄漏的 Python 僵尸进程（非 pythonw，内存 > 阈值）。

    应在启动新实验 worker 之前调用，清理上次运行残留的 watchdog /
    子进程等。

    Args:
        dry_run: True 时仅扫描，不终止。
        log_callback: 可选 callable(msg)，接收诊断消息。

    Returns:
        (killed_count: int, freed_mb: float)
    """
    import os as _os

    current_pid = _os.getpid()
    killed = 0
    freed_mb = 0.0

    def _emit(msg):
        if log_callback:
            log_callback(msg)
        _log.info(msg)

    if sys.platform != "win32":
        _emit("[zombie_killer] 非 Windows 平台，跳过")
        return 0, 0.0

    try:
        ps_script = rf"""
$zombies = Get-CimInstance Win32_Process -Filter "Name LIKE '%python%'" |
    Where-Object {{
        ($_.WorkingSetSize/1MB -gt {ZOMBIE_MEM_THRESHOLD_MB}) -and
        ($_.Name -notlike '*pythonw*') -and
        ($_.ProcessId -ne {current_pid})
    }}
if (-not $zombies) {{
    Write-Host 'NO_ZOMBIES'
    exit 0
}}
foreach ($z in $zombies) {{
    $mb = [math]::Round($z.WorkingSetSize/1MB, 1)
    Write-Host ("ZOMBIE PID={0} Mem={1}MB" -f $z.ProcessId, $mb)
}}
"""
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True, text=True, timeout=15,
            creationflags=0x08000000 if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )

        if "NO_ZOMBIES" in result.stdout:
            _emit("[zombie_killer] 未发现僵尸进程")
            return 0, 0.0

        zombie_pids = []
        for line in result.stdout.splitlines():
            if line.startswith("ZOMBIE PID="):
                parts = line.split()
                pid_str = parts[0].split("=")[1]
                mem_str = parts[1].split("=")[1].replace("MB", "")
                zombie_pids.append((int(pid_str), float(mem_str)))

        if not zombie_pids:
            _emit("[zombie_killer] 未发现僵尸进程")
            return 0, 0.0

        if dry_run:
            for pid, mem in zombie_pids:
                _emit(f"[zombie_killer] [DRY-RUN] 发现僵尸 PID={pid} ({mem:.1f}MB)")
            return 0, sum(m for _, m in zombie_pids)

        # 终止
        kill_script = r"""
$pids = @(%s)
foreach ($pid in $pids) {
    try {
        Stop-Process -Id $pid -Force -ErrorAction Stop
        Write-Host ("KILLED PID=" + $pid)
    } catch {
        Write-Host ("FAIL PID=" + $pid + " " + $_.Exception.Message)
    }
}
""" % ",".join(str(p[0]) for p in zombie_pids)

        kill_result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", kill_script],
            capture_output=True, text=True, timeout=15,
            creationflags=0x08000000 if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )

        for line in kill_result.stdout.splitlines():
            if line.startswith("KILLED PID="):
                pid = int(line.split("=")[1])
                mem = next((m for p, m in zombie_pids if p == pid), 0)
                killed += 1
                freed_mb += mem
                _emit(f"[zombie_killer] 已终止僵尸进程 PID={pid} ({mem:.1f}MB)")
            elif line.startswith("FAIL PID="):
                _emit(f"[zombie_killer] 终止失败: {line}")

        if killed:
            _emit(f"[zombie_killer] 清理完成: 终止 {killed} 个进程, "
                  f"释放 ~{freed_mb:.0f}MB")
        else:
            _emit("[zombie_killer] 所有僵尸进程已终止")

    except subprocess.TimeoutExpired:
        _emit("[zombie_killer] 扫描超时（15s），跳过僵尸清理")
    except Exception as exc:
        _emit(f"[zombie_killer] 清理异常（非致命）: {exc}")

    return killed, freed_mb


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="清理 Bun 崩溃残留的僵尸 Python 进程")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅扫描，不终止")
    parser.add_argument("--execute", action="store_true",
                        help="直接通过 Bash 执行清理")
    args = parser.parse_args()

    if args.execute:
        print(SCAN_SCRIPT.strip())
        print(KILL_SCRIPT.strip() % (ZOMBIE_MEM_THRESHOLD_MB, 0))
    elif args.dry_run:
        print(SCAN_SCRIPT.strip())
    else:
        print(SCAN_SCRIPT.strip())
        print(KILL_SCRIPT.strip() % (ZOMBIE_MEM_THRESHOLD_MB, 0))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

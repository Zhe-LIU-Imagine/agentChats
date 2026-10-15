#!/usr/bin/env python3
"""验证 hermes_agent 的 pidfile 机制 + GUI 托管逻辑（import 真实 gui 模块，不建窗口）。"""
import os
import sys
import time
import subprocess

BASE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(BASE, "hermes_agent.py")
PIDFILE = os.path.join(BASE, "data", "hermes_agent.pid")
sys.path.insert(0, BASE)

import gui  # 只 import，不创建 Tk 窗口

read_pidfile = gui._read_pidfile
pid_alive = gui._pid_alive


def _hermes_agent_pids():
    """跨平台：返回当前运行中的 hermes_agent.py 进程 PID 列表（仅标准库，无第三方依赖）。
    macOS/Linux 用 pgrep；Windows 无 pgrep，且 tasklist 只看得到映像名看不到命令行，
    故用 PowerShell 按命令行匹配（wmic 已在 Win11 弃用）。"""
    if os.name == "nt":
        # 精确匹配命令行以 hermes_agent.py 结尾，避免误匹配「正在处理含该串文本消息」的 Hermes 子进程
        ps_cmd = ("Get-CimInstance Win32_Process | "
                  "Where-Object { $_.CommandLine -like '*hermes_agent.py' } | "
                  "Select-Object -ExpandProperty ProcessId")
        argv = ["powershell", "-NoProfile", "-Command", ps_cmd]
    else:
        argv = ["pgrep", "-f", "hermes_agent\\.py$"]   # 精确匹配结尾（-fl 模糊匹配会误匹配含该串文本的 Hermes 子进程）
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=15).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    pids = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        tok = line.split()[0] if os.name != "nt" else line   # pgrep 首列是 PID；PowerShell 每行纯 PID
        if tok.isdigit():
            pids.append(int(tok))
    return pids


# 0. 清理：杀旧 hermes_agent 进程 + 删旧 pidfile
for pid in _hermes_agent_pids():
    if pid != os.getpid():
        try:
            os.kill(pid, 9)
            print(f"[0] 杀掉旧进程 {pid}")
        except (OSError, SystemError):
            pass
if os.path.exists(PIDFILE):
    os.remove(PIDFILE)
time.sleep(0.3)

# 1. spawn hermes_agent（模拟 GUI _ensure_hermes_agent 的 spawn）
proc = subprocess.Popen([sys.executable, SCRIPT], cwd=BASE,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
time.sleep(2.0)
pid = read_pidfile()
print(f"[1] spawn 后 pidfile={pid} alive={pid_alive(pid)} 子进程PID={proc.pid}")
assert pid and pid_alive(pid), "启动后应写 pidfile 且进程存活"
assert pid == proc.pid, f"pidfile({pid}) 应 == 子进程 PID({proc.pid})"

# 2. 模拟 GUI _ensure：已在跑 → 复用不重复起（决策：pid and pid_alive(pid) -> return）
decision = bool(read_pidfile() and pid_alive(read_pidfile()))
print(f"[2] 已在线，_ensure 决策=复用不重复 spawn：{decision}")
assert decision, "已在线时 _ensure 应判定复用"

# 3. SIGKILL（模拟异常退出，pidfile 残留死 pid）
proc.kill(); proc.wait(); time.sleep(0.3)
stale = read_pidfile()
print(f"[3] kill 后 pidfile 残留={stale} alive={pid_alive(stale)}")
assert not pid_alive(stale), "死进程 pid 应判定不存活"

# 4. 再 spawn（模拟 _ensure 发现死了 → spawn 新进程）
proc2 = subprocess.Popen([sys.executable, SCRIPT], cwd=BASE,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
time.sleep(2.0)
pid2 = read_pidfile()
print(f"[4] 新 pidfile={pid2} alive={pid_alive(pid2)} 子进程PID={proc2.pid}")
assert pid2 == proc2.pid and pid_alive(pid2), "新进程应写新 pidfile 且存活"

# 5. terminate（模拟 GUI _stop_hermes_agent）
proc2.terminate()
try:
    proc2.wait(timeout=3)
except subprocess.TimeoutExpired:
    proc2.kill(); proc2.wait()
print(f"[5] terminate 后子进程 poll={proc2.poll()}")

# 看子进程上线日志
out2 = proc2.stdout.read() if proc2.stdout else ""
print("--- 子进程启动日志（前 3 行）---")
for l in out2.splitlines()[:3]:
    print("  " + l)
assert proc2.poll() is not None, "terminate 后进程应退出"

# 清理 pidfile
if os.path.exists(PIDFILE):
    os.remove(PIDFILE)

print("🎉 hermes_agent 托管逻辑（pidfile + 复用判断 + 停止）验证全部通过")

#!/usr/bin/env python3
"""验证 hermes_agent 的待回复持久化 + 优雅退出 + 下次开机补发。"""
import json
import os
import signal
import socket
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(BASE, "hermes_agent.py")
PENDING = os.path.join(BASE, "data", "pending_replies.json")
CONV = os.path.join(BASE, "data", "conversations.json")
PIDFILE = os.path.join(BASE, "data", "hermes_agent.pid")
TESTLOG = os.path.join(BASE, "data", "hermes_test.log")
sys.path.insert(0, BASE)

import hermes_agent as ha   # 有 __main__ 保护，import 不跑 main


def clean():
    for f in (PENDING, CONV, PIDFILE, TESTLOG):
        if os.path.exists(f):
            os.remove(f)
    ha.conversations.clear()


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


def kill_all_hermes():
    for pid in _hermes_agent_pids():
        if pid != os.getpid():
            try:
                os.kill(pid, 9)
            except (OSError, SystemError):
                pass
    time.sleep(0.3)


def send_msg(text, port=4567, conv_id=None):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    payload = {"text": text}
    if conv_id:
        payload["conversation_id"] = conv_id
    msg = {"type": "msg", "msg_id": "t_" + str(time.time()), "from": "test_peer",
           "to": "hermes_agent", "payload": payload}
    sock.sendto(json.dumps(msg, ensure_ascii=False).encode("utf-8"), ("127.0.0.1", port))
    sock.close()


def start_hermes():
    f = open(TESTLOG, "a", encoding="utf-8")
    return subprocess.Popen([sys.executable, SCRIPT], cwd=BASE,
                            stdout=f, stderr=subprocess.STDOUT)


def read_log():
    try:
        with open(TESTLOG, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


# ============ 1. 纯函数：pending 写/删 ============
clean()
ha._pending_add("peerA", "第一条", "conv_1")
ha._pending_add("peerA", "第二条", "conv_1")
assert len(ha._pending_load()) == 2, "pending 应写入 2 条"
ha._pending_remove("peerA", "第一条", "conv_1")
left = ha._pending_load()
assert len(left) == 1 and left[0]["text"] == "第二条", "删除后应剩 1 条且内容正确"
print("[1] pending 写/删 逻辑 ✅")

# ============ 1b. 纯函数：conversations 持久化 ============
ha.conversations["conv_1"] = {"round": 3, "last_seen": time.time()}
ha._conv_save()
ha.conversations.clear()
ha._conv_load()
assert ha.conversations.get("conv_1", {}).get("round") == 3, "conversations 持久化后轮次应恢复为 3"
print("[1b] conversations 持久化 ✅")

# ============ 2. 端到端：收到消息 → pending 写入 ============
clean()
kill_all_hermes()
proc = start_hermes()
time.sleep(2.0)
send_msg("请回复：测试待回复", conv_id="conv_e2e")
time.sleep(1.2)   # 等 on_msg 处理（_pending_add 在监听线程立即执行）
pending = ha._pending_load()
print(f"[2] 收到消息后 pending = {[p['text'] for p in pending]}")
assert any(p["text"] == "请回复：测试待回复" for p in pending), "收到消息应实时写入 pending"
print("[2] 收到消息实时写 pending ✅")

# ============ 3. SIGKILL（暴力杀）→ pending 保留 ============
proc.kill(); proc.wait(); time.sleep(0.3)
pending = ha._pending_load()
assert any(p["text"] == "请回复：测试待回复" for p in pending), "SIGKILL 后 pending 应保留在磁盘"
print("[3] SIGKILL 后 pending 保留 ✅")

# ============ 4. 重启 → 补发 ============
proc2 = start_hermes()
time.sleep(2.5)
log = read_log()
assert "[补发]" in log, f"重启后应打印补发日志，实际日志：\n{log[-500:]}"
print("[4] 重启后补发（日志含 [补发]）✅")
proc2.terminate()
try:
    proc2.wait(timeout=5)
except subprocess.TimeoutExpired:
    proc2.kill(); proc2.wait()
time.sleep(0.5)

# ============ 5. 优雅退出（SIGTERM）→ 处理完当前、落盘、退出 ============
# Windows 无 POSIX SIGTERM：Popen.send_signal(SIGTERM) 会直接 TerminateProcess（硬杀），
# 不触发 hermes_agent 的 _handle_signal → 无法打印「优雅退出」日志，故本段在 Windows 跳过。
if os.name == "nt":
    print("[5] SIGTERM 优雅退出 ⏭️ 跳过（Windows 无 POSIX 信号，优雅退出仅 macOS/Linux 可测）")
else:
    clean()
    kill_all_hermes()
    proc3 = start_hermes()
    time.sleep(2.0)
    proc3.send_signal(signal.SIGTERM)
    try:
        proc3.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc3.kill(); proc3.wait()
    log = read_log()
    assert "[退出] 优雅退出" in log and "[退出] 完成" in log, f"应打印优雅退出日志，实际：\n{log[-600:]}"
    assert proc3.poll() is not None, "SIGTERM 后进程应退出"
    print("[5] SIGTERM 优雅退出 ✅")

# 清理
kill_all_hermes()
clean()
print("🎉 待回复持久化 + 补发 + 优雅退出 全部验证通过")

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UDP 多 Agent 通讯 GUI 界面（Tkinter）。

功能：
  - 设置里可选端口 / 广播地址 / peers
  - 显示与各设备的聊天记录（本地 SQLite 持久化）
  - 设备身份用持久化的 device_id（IP 变化身份不变，聊天记录延续）

用法：python3 gui.py
"""

import os
import queue
import subprocess
import sys
import time
import tkinter as tk
from tkinter import ttk, messagebox

# Windows 控制台 GBK 编码导致中文乱码，强制 UTF-8 输出（跨平台安全）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from agent import Agent, get_local_ips
from identity import get_or_create_device_id, get_device_id, set_device_id
from chat_store import ChatStore
from config import load_config, save_config

# Hermes 自动回复 agent 的 agent_id（与 hermes_agent.py 一致：hermes_ + MAC）
HERMES_AGENT_ID = "hermes_" + get_device_id()

# current_conv 的哨兵：表示「设备层」（显示该设备全部消息，发消息不带主题）
_DEVICE = object()

# 本目录的 hermes_agent.py 及其 PID/日志文件（GUI 托管它的生命周期）
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HERMES_AGENT_SCRIPT = os.path.join(_BASE_DIR, "hermes_agent.py")
HERMES_PIDFILE = os.path.join(_BASE_DIR, "data", "hermes_agent.pid")
HERMES_LOG = os.path.join(_BASE_DIR, "data", "hermes_agent.log")

# hermes_agent 看门狗：GUI 启动状态下每 5 分钟检查一次存活，下线则重新拉起
HERMES_WATCHDOG_INTERVAL_MS = 5 * 60 * 1000


def _read_pidfile() -> int:
    """读 hermes_agent 的 PID 文件；失败返回 None。"""
    try:
        with open(HERMES_PIDFILE, encoding="utf-8") as f:
            return int(f.read().strip())
    except Exception:
        return None


def _hermes_agent_pids() -> list:
    """返回本机运行中的 hermes_agent.py 进程 PID 列表（跨平台，仅标准库）。

    macOS/Linux 用 pgrep 精确匹配命令行结尾（hermes_agent\\.py$），
    Windows 用 PowerShell Get-CimInstance 按 CommandLine 匹配。
    用「命令行匹配」而非 os.kill(pid,0)：后者在 Windows 上因 PID 快速复用会
    误判死进程为存活（test_hermes_follow 第 3 步 Windows 必现失败）。
    """
    if sys.platform == "win32":
        ps_cmd = ("Get-CimInstance Win32_Process | "
                  "Where-Object { $_.CommandLine -like '*hermes_agent.py' } | "
                  "Select-Object -ExpandProperty ProcessId")
        argv = ["powershell", "-NoProfile", "-Command", ps_cmd]
    else:
        argv = ["pgrep", "-f", r"hermes_agent\.py$"]
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=15).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    pids = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        tok = line.split()[0] if sys.platform != "win32" else line
        if tok.isdigit():
            pids.append(int(tok))
    return pids


def _pid_alive(pid) -> bool:
    """判断 hermes_agent 的 PID 是否仍在运行（跨平台）。

    用「命令行匹配」探测而非 os.kill(pid,0)：os.kill(pid,0) 在 Windows 上因 PID
    快速复用会误判死进程为存活（进程被杀后 PID 被立即复用，os.kill(pid,0) 返回
    成功）。故统一查「本机 hermes_agent.py 进程 PID 列表」里是否含该 pid。
    """
    if not pid or pid <= 0:
        return False
    return pid in _hermes_agent_pids()


def parse_peers(s: str) -> dict:
    """解析 'a=1.2.3.4:9001,b=1.2.3.5:9002' -> {a:(ip,port), ...}"""
    peers = {}
    for part in (s or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part or ":" not in part:
            continue
        aid, addr = part.split("=", 1)
        ip, port = addr.rsplit(":", 1)
        try:
            peers[aid] = (ip, int(port))
        except ValueError:
            continue
    return peers


def payload_to_text(payload) -> str:
    """把消息 payload 转成可显示的文本。"""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        for k in ("text", "msg", "message", "content"):
            if k in payload:
                return str(payload[k])
        return str(payload)
    return str(payload)


class ChatGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.device_id = get_or_create_device_id()
        self.store = ChatStore()
        self.agent = None
        self.msg_queue = queue.Queue()

        cfg = load_config()
        self.port = cfg["port"]
        self.broadcast = cfg["broadcast"]
        self.peers = cfg["peers"]
        self.max_rounds = cfg.get("max_rounds", 50)

        self.current_peer = None          # 当前选中的聊天对象 device_id
        self.current_conv = _DEVICE       # 当前选中主题：_DEVICE=设备层 / None=未分类 / 字符串=某主题
        self._conv_map = {}               # 主题节点 item_id -> (peer_id, conv_id)
        self._conv_seq = 0                # 主题节点 item_id 计数器
        self._hermes_proc = None          # 本 GUI spawn 的 hermes_agent 子进程
        self._hermes_logf = None          # hermes_agent 日志文件句柄
        self._watchdog_active = False     # hermes_agent 看门狗是否启用（GUI 启动状态才启用）
        self._watchdog_scheduled = False  # 看门狗 after 循环是否已注册（防重复）

        self._build_ui()
        self._update_ip_label()
        self._refresh_tree()
        self._user_interacted = False       # 用户是否手动点过启停
        self.root.after(200, self._poll_queue)
        self.root.after(2000, self._periodic_refresh)
        self.root.after(30000, self._auto_start)   # 30 秒未手动操作则自动上线

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        self.root.title("UDP Agent 通讯")
        self.root.geometry("720x520")

        # 顶部：本机信息 + 控制按钮
        top = ttk.Frame(self.root, padding=(8, 6))
        top.pack(fill="x")

        self.id_label = ttk.Label(top, text=f"本机 ID：{self.device_id}")
        self.id_label.pack(side="left")

        self.ip_label = ttk.Label(top, text="", foreground="#555555")
        self.ip_label.pack(side="left", padx=8)

        self.status_label = ttk.Label(top, text="● 离线", foreground="gray")
        self.status_label.pack(side="left", padx=12)

        ttk.Button(top, text="设置", command=self._open_settings).pack(side="right")
        self.start_btn = ttk.Button(top, text="启动", command=self._toggle)
        self.start_btn.pack(side="right", padx=4)

        # 自动回复开关（默认开）：控制 Hermes agent 是否自动回复
        self.auto_reply_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="自动回复", variable=self.auto_reply_var,
                        command=self._on_toggle_auto_reply).pack(side="right", padx=8)

        # 主体：左设备列表 + 右聊天记录
        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True, padx=8, pady=4)

        left = ttk.Frame(body, width=220)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        ttk.Label(left, text="设备 / 主题（●在线 ○离线）").pack(anchor="w")
        self.tree = ttk.Treeview(left, show="tree", selectmode="browse")
        self.tree.pack(fill="both", expand=True, pady=4)
        self.tree.bind("<<TreeviewSelect>>", self._on_select_tree)

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))

        self.chat_text = tk.Text(right, state="disabled", wrap="word")
        self.chat_text.pack(fill="both", expand=True)
        self.chat_text.tag_configure("sent", foreground="#1a6fd4")
        self.chat_text.tag_configure("received", foreground="#222222")
        self.chat_text.tag_configure("system", foreground="#999999")

        # 底部：输入 + 发送
        bottom = ttk.Frame(self.root, padding=(8, 6))
        bottom.pack(fill="x")

        self.input_entry = ttk.Entry(bottom)
        self.input_entry.pack(side="left", fill="x", expand=True)
        self.input_entry.bind("<Return>", self._send)
        ttk.Button(bottom, text="发送", command=self._send).pack(side="left", padx=4)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ 设置

    def _open_settings(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("设置")
        dlg.geometry("400x340")
        dlg.transient(self.root)
        dlg.grab_set()

        ttk.Label(dlg, text="本机 ID：").grid(row=0, column=0, sticky="e", padx=6, pady=8)
        id_var = tk.StringVar(value=self.device_id)
        ttk.Entry(dlg, textvariable=id_var, width=20).grid(row=0, column=1, sticky="w")
        ttk.Label(dlg, text="格式建议 系统_3位数（如 mac_123），改后重启生效", foreground="gray").grid(
            row=1, column=1, sticky="w")

        ttk.Label(dlg, text="端口：").grid(row=2, column=0, sticky="e", padx=6, pady=8)
        port_var = tk.StringVar(value=str(self.port))
        ttk.Entry(dlg, textvariable=port_var, width=20).grid(row=2, column=1, sticky="w")

        ttk.Label(dlg, text="广播地址：").grid(row=3, column=0, sticky="e", padx=6, pady=8)
        bc_var = tk.StringVar(value=self.broadcast)
        ttk.Entry(dlg, textvariable=bc_var, width=20).grid(row=3, column=1, sticky="w")

        ttk.Label(dlg, text="peers：").grid(row=4, column=0, sticky="e", padx=6, pady=8)
        peers_str = ",".join(f"{k}={v[0]}:{v[1]}" for k, v in self.peers.items())
        peers_var = tk.StringVar(value=peers_str)
        ttk.Entry(dlg, textvariable=peers_var, width=20).grid(row=4, column=1, sticky="w")
        ttk.Label(dlg, text="格式 a=1.2.3.4:9001,b=...", foreground="gray").grid(
            row=5, column=1, sticky="w")

        ttk.Label(dlg, text="每话题最大轮次：").grid(row=6, column=0, sticky="e", padx=6, pady=8)
        mr_var = tk.StringVar(value=str(self.max_rounds))
        ttk.Entry(dlg, textvariable=mr_var, width=20).grid(row=6, column=1, sticky="w")
        ttk.Label(dlg, text="同一话题超过此轮次自动结束", foreground="gray").grid(
            row=7, column=1, sticky="w")

        def ok():
            try:
                self.port = int(port_var.get().strip())
                mr = int(mr_var.get().strip())
            except ValueError:
                messagebox.showerror("错误", "端口和最大轮次必须是数字")
                return
            new_id = id_var.get().strip()
            id_changed = bool(new_id and new_id != self.device_id)
            if id_changed and not set_device_id(new_id):
                messagebox.showerror("错误", "本机 ID 不合法（不能为空、不能含空格、长度 ≤ 32）")
                return
            self.broadcast = bc_var.get().strip() or "255.255.255.255"
            self.peers = parse_peers(peers_var.get())
            self.max_rounds = mr
            save_config({"port": self.port, "broadcast": self.broadcast,
                         "peers": self.peers, "max_rounds": mr})
            dlg.destroy()
            msgs = []
            if id_changed:
                msgs.append(f"本机 ID 已改为 {new_id}")
            if self.agent:
                r = self.agent.send(HERMES_AGENT_ID, {"text": f"/max_rounds {mr}"})
                if r.get("ok"):
                    msgs.append("最大轮次已同步给 Hermes")
                else:
                    msgs.append("Hermes agent 不在线，最大轮次未同步")
            msg = "设置已保存。"
            if msgs:
                msg += "\n" + "；".join(msgs)
            msg += "\n（本机 ID / 端口 / 广播 / peers 重启后生效）"
            messagebox.showinfo("提示", msg)

        ttk.Button(dlg, text="确定", command=ok).grid(row=8, column=0, pady=12)
        ttk.Button(dlg, text="取消", command=dlg.destroy).grid(row=8, column=1)

    # ------------------------------------------------------------------ 启停

    def _toggle(self):
        self._user_interacted = True
        if self.agent is None:
            self._start()
        else:
            self._stop()

    def _on_toggle_auto_reply(self):
        """勾选/取消「自动回复」→ 发命令给 Hermes agent 切换开关。"""
        if self.agent is None:
            return
        state = "on" if self.auto_reply_var.get() else "off"
        r = self.agent.send(HERMES_AGENT_ID, {"text": f"/auto_reply {state}"})
        if not r.get("ok"):
            messagebox.showwarning("提示", "Hermes agent 不在线，开关未生效")

    def _auto_start(self):
        """GUI 打开 30 秒后，若用户未手动点过启停且仍离线，则自动上线。"""
        if self.agent is None and not self._user_interacted:
            self._start()

    def _start(self):
        try:
            self.agent = Agent(
                agent_id=self.device_id,
                port=self.port,
                broadcast_addr=self.broadcast,
                peers=self.peers,
            )
            self.agent.on_message(self._on_message)
            self.agent.on_duplicate(self._on_duplicate)
            self.agent.start()
        except OSError as e:
            self.agent = None
            messagebox.showerror("启动失败", f"端口 {self.port} 可能被占用：\n{e}")
            return
        self.status_label.config(text=f"● 在线（端口 {self.port}）", foreground="green")
        self.start_btn.config(text="停止")
        self._refresh_tree()
        self._ensure_hermes_agent()
        # 启动 hermes_agent 看门狗：每 5 分钟检查存活，下线则重新拉起
        self._watchdog_active = True
        if not self._watchdog_scheduled:
            self._watchdog_scheduled = True
            self.root.after(HERMES_WATCHDOG_INTERVAL_MS, self._hermes_watchdog)

    def _stop(self):
        if self.agent:
            self.agent.stop()
            self.agent = None
        self.status_label.config(text="● 离线", foreground="gray")
        self.start_btn.config(text="启动")
        self._refresh_tree()
        self._watchdog_active = False     # 停止看门狗（hermes_agent 跟随 GUI 一起停）
        self._stop_hermes_agent()

    # ------------------------------------------------------------ Hermes 托管

    def _ensure_hermes_agent(self):
        """确保 hermes_agent（4567）在跑：已在跑不重复起，否则 spawn 子进程。

        让 hermes_agent 跟随本 GUI 生命周期——打开 GUI 自动拉起，关闭一起停。
        若 hermes_agent 已由 launchd/systemd/任务计划等外部方式常开，则复用不重复起。
        """
        pid = _read_pidfile()
        if pid and _pid_alive(pid):
            return                       # 已在跑（无论谁起的），不重复起
        try:
            self._hermes_logf = open(HERMES_LOG, "a", encoding="utf-8")
            self._hermes_proc = subprocess.Popen(
                [sys.executable, HERMES_AGENT_SCRIPT],
                cwd=_BASE_DIR,
                stdout=self._hermes_logf, stderr=subprocess.STDOUT,
            )
        except Exception as e:
            print(f"[GUI] 拉起 hermes_agent 失败：{e}", file=sys.stderr)

    def _hermes_watchdog(self):
        """hermes_agent 看门狗：每 5 分钟检查存活，下线则重新拉起。

        仅 GUI 处于「启动」状态（_watchdog_active=True）时运行；用户点「停止」后
        不再看护（hermes_agent 跟随 GUI 一起停）。
        """
        if not self._watchdog_active:
            self._watchdog_scheduled = False   # GUI 已停止，终止循环
            return
        try:
            self._ensure_hermes_agent()
        except Exception as e:
            print(f"[GUI] hermes_agent 看门狗检查失败：{e}", file=sys.stderr)
        self.root.after(HERMES_WATCHDOG_INTERVAL_MS, self._hermes_watchdog)

    def _stop_hermes_agent(self):
        """发 SIGTERM 触发 hermes_agent 优雅退出（外部常开的不碰）。

        hermes_agent 收到 SIGTERM 后会处理完当前消息、把剩余待回复落盘再退出。
        GUI 不阻塞等它（它退得快则几秒，最久 Hermes 超时 180 秒）。
        """
        proc = getattr(self, "_hermes_proc", None)
        if proc is not None and proc.poll() is None:
            proc.terminate()   # SIGTERM → 优雅退出，不 wait 不 kill
        self._hermes_proc = None
        logf = getattr(self, "_hermes_logf", None)
        if logf is not None:
            try:
                logf.close()
            except Exception:
                pass
            self._hermes_logf = None

    # ------------------------------------------------------------------ 消息

    def _on_message(self, src, payload):
        """监听线程回调：只入队，不直接碰 UI。"""
        if isinstance(payload, dict) and payload.get("sync"):
            # Hermes 同步来的对话：payload 带真实 peer、说话者、主题
            raw_peer = payload.get("peer", src)
            who = payload.get("who", "peer")
            if raw_peer == self.device_id:
                # 对话对象是本机 GUI 自己 → 实际在和本机 hermes_agent 对话，
                # peer 归一为 hermes_agent，方向反转（who="peer"=我发的=sent）
                peer = HERMES_AGENT_ID
                direction = "received" if who == "hermes" else "sent"
            else:
                peer = raw_peer
                direction = "received" if who == "peer" else "sent"
            conv = payload.get("conversation_id")
            self.msg_queue.put((peer, str(payload.get("text", "")), direction, conv))
        else:
            conv = payload.get("conversation_id") if isinstance(payload, dict) else None
            self.msg_queue.put((src, payload_to_text(payload), "received", conv))

    def _on_duplicate(self, ip, port, agent_id):
        """同名冲突回调（监听线程）→ 调度到主线程弹警告。"""
        def _show():
            messagebox.showwarning(
                "同名冲突",
                f"检测到另一台设备用了相同 ID「{agent_id}」（来自 {ip}:{port}）。\n"
                f"请在其中一台机器的「设置 → 本机 ID」里改成不同的 ID。")
        self.root.after(0, _show)

    def _poll_queue(self):
        try:
            while True:
                peer, text, direction, conv = self.msg_queue.get_nowait()
                self.store.add(peer, direction, text, conversation_id=conv)
                # 收到新消息但还没选中任何设备 → 自动选中发送方（设备层），让用户立刻看到
                if self.current_peer is None and peer:
                    self.current_peer = peer
                    self.current_conv = _DEVICE
                self._refresh_tree()
                if self.current_peer == peer:
                    self._load_chat(peer, self.current_conv)
        except queue.Empty:
            pass
        self.root.after(200, self._poll_queue)

    def _send(self, event=None):
        if self.agent is None:
            messagebox.showwarning("未启动", "请先点「启动」上线")
            return
        peer = self.current_peer
        if not peer:
            messagebox.showwarning("未选设备", "请先在左侧选择一个设备")
            return
        text = self.input_entry.get().strip()
        if not text:
            return
        conv = None if self.current_conv is _DEVICE else self.current_conv
        payload = {"text": text}
        if conv:
            payload["conversation_id"] = conv
        r = self.agent.send(peer, payload)
        if r.get("ok"):
            if peer != HERMES_AGENT_ID:
                # 发给本机 hermes_agent 时不本地存，等它 sync 回来补（避免「本地未分类 + sync 带主题」重复）
                self.store.add(peer, "sent", text, conversation_id=conv)
            self.input_entry.delete(0, "end")
            self._load_chat(peer, self.current_conv)
        else:
            # 未送达：保留输入框文字，可再次点「发送」手动重发
            messagebox.showwarning(
                "发送失败",
                f"未送达（{r.get('reason', '未知')}）：{r.get('error', '')}\n\n"
                f"消息已保留在输入框，可再次点「发送」重试。")

    # ------------------------------------------------------------------ 设备列表 / 聊天记录

    def _new_conv_node(self, peer_id, conv_id):
        """生成主题节点 id 并登记映射。"""
        self._conv_seq += 1
        nid = f"conv{self._conv_seq}"
        self._conv_map[nid] = (peer_id, conv_id)
        return nid

    def _refresh_tree(self):
        online = self.agent.list_agents() if self.agent else {}
        history = self.store.list_peers()
        # 合并：在线 + 历史（去重，保留顺序）
        ordered = []
        seen = set()
        for pid in list(online.keys()) + history:
            if pid not in seen:
                seen.add(pid)
                ordered.append(pid)

        sel_peer = self.current_peer
        sel_conv = self.current_conv
        self.tree.delete(*self.tree.get_children())
        self._conv_map.clear()
        self._conv_seq = 0

        for pid in ordered:
            if pid in online:
                info = online[pid]
                model = (info.get("meta") or {}).get("model")
                if model:      # 对方广播了模型类型，优先显示模型
                    label = f"● {pid} ({model})"
                else:
                    label = f"● {pid} ({info['ip']}:{info['port']})"
            else:
                label = f"○ {pid}"
            dev_node = self.tree.insert("", "end", iid=pid, text=label, open=True)
            # 第二层：该设备下的通讯主题
            for conv_id, title, _ts in self.store.list_conversations(pid):
                t = str(title).strip()
                short = t[:20] + ("…" if len(t) > 20 else "")
                if conv_id is None:
                    tlabel = f"（未分类）{short}"
                else:
                    tlabel = short or f"{conv_id}"
                nid = self._new_conv_node(pid, conv_id)
                self.tree.insert(dev_node, "end", iid=nid, text=tlabel)

        # 恢复选中
        if sel_peer and sel_peer in ordered:
            if sel_conv is _DEVICE:
                self.tree.selection_set(sel_peer)
            else:
                for nid, (p, c) in self._conv_map.items():
                    if p == sel_peer and c == sel_conv:
                        self.tree.selection_set(nid)
                        break
            self.tree.see(sel_peer)

    def _on_select_tree(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        nid = sel[0]
        if nid in self._conv_map:
            peer_id, conv_id = self._conv_map[nid]
            self.current_peer = peer_id
            self.current_conv = conv_id
        else:
            # 设备节点（nid 即 peer_id）
            self.current_peer = nid
            self.current_conv = _DEVICE
        self._load_chat(self.current_peer, self.current_conv)

    def _load_chat(self, peer_id, conv=_DEVICE):
        self.chat_text.config(state="normal")
        self.chat_text.delete("1.0", "end")
        if conv is _DEVICE:
            rows = self.store.get_history(peer_id)
        else:
            rows = self.store.get_history(peer_id, conv)
        for direction, text, ts in rows:
            t = time.strftime("%H:%M", time.localtime(ts))
            if direction == "sent":
                self.chat_text.insert("end", f"[{t}] 我：{text}\n", "sent")
            else:
                name = "Hermes" if peer_id == HERMES_AGENT_ID else peer_id
                self.chat_text.insert("end", f"[{t}] {name}：{text}\n", "received")
        self.chat_text.config(state="disabled")
        self.chat_text.see("end")

    def _update_ip_label(self):
        """刷新本机 IP 显示（IP 可能变化，如 WiFi 重连）。"""
        ips = get_local_ips()
        self.ip_label.config(text=f"IP: {' / '.join(ips)}")

    def _periodic_refresh(self):
        self._update_ip_label()
        if self.agent:
            self._refresh_tree()
        self.root.after(2000, self._periodic_refresh)

    # ------------------------------------------------------------------ 退出

    def _on_close(self):
        self._stop()
        self.store.close()
        self.root.destroy()


def main():
    root = tk.Tk()
    ChatGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()

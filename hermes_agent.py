#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hermes 接入 agentChats：收到消息后交给完整 Hermes 处理并自动回复。

「接 Hermes」= 调用 `hermes chat -q`（完整 Hermes agent，能操作电脑），
而不是只调 LLM 的 key。agent 发来的「帮我看看 /tmp 下有什么」这类指令，
Hermes 会真正执行，再把结果回传。

多轮对话控制（在消息里显式写明，而非靠隐式判断）：
  - conversation_id：话题线程 ID。同一话题各轮带同一 ID，新话题不带（重新计数）。
  - 轮次上限：每个话题最多 max_rounds 轮（默认 50，可配置），超过发「结束语句」。
  - 结束语句：当轮次到上限、或 Hermes 判定「无新信息」（如对方只回「好的/谢谢」）
    时，发送带 end: true 的结束语句。接收方收到 end: true 就不再让 Hermes 回复。

防无限循环（三层）：
  1. 不回自己的消息
  2. 不回「自动回复」消息（is_auto_reply == True）
  3. 不回「结束语句」（end == True）
  （外加 agent 层的 msg_id 去重）

开关/命令（在聊天框发，或由 GUI 设置页发）：
  /auto_reply on|off   切换自动回复
  /max_rounds N        设置每个话题的最大轮次

端口约定：
  - 本进程监听 4567 = 「统一消息端口」，所有 agent 发消息都发往这里。
  - GUI 的 4568 是本机查看器端口，只显示对话，不接收 agent 消息。
"""

import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid

# Windows 控制台 GBK 编码导致中文乱码，强制 UTF-8 输出（跨平台安全）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from agent import Agent, get_local_ips
from config import load_config
from identity import get_device_id

PORT = 4567                            # 统一消息端口：所有 agent 发消息都发往这里
INBOX = os.path.join(BASE_DIR, "data", "hermes_agent_inbox.log")
CONFIG = os.path.join(BASE_DIR, "data", "hermes_config.json")
PIDFILE = os.path.join(BASE_DIR, "data", "hermes_agent.pid")   # 进程 PID 文件（GUI 据此判断 hermes_agent 是否在跑）
PENDING_FILE = os.path.join(BASE_DIR, "data", "pending_replies.json")  # 待回复消息（进程被杀后下次启动补发）
CONV_FILE = os.path.join(BASE_DIR, "data", "conversations.json")       # 话题轮次状态（重启后继续）

GUI_ID = get_device_id()               # 本机 GUI 的 device_id（= 网卡 MAC 身份，稳定唯一）
HERMES_AGENT_ID = "hermes_" + GUI_ID   # hermes_agent 的 agent_id（MAC 派生，跨机唯一，避免都叫 hermes_agent 撞 ID）

# 代码分发：其他 agent（如 Windows 队友）发「请发代码」时，把本目录这些文件内容回给它。
# 走「响应通道」（对方主动发消息 → 本进程响应），绕开对方防火墙的 UDP 主动入站限制。
CODE_SYNC_FILES = ["agent.py", "hermes_agent.py", "gui.py", "identity.py"]
CODE_SYNC_TRIGGERS = ("/pull_code", "请发代码", "拉代码")
# 代码分发请求的最大字符长度：超过视为「长文本描述」（如更新通知/调试反馈），
# 即使含关键词也不触发，避免把描述里顺带提到的「拉代码」误判为请求。
MAX_CODE_SYNC_REQ_LEN = 30

# 代码分发接收：收到的文件不直接覆盖主目录，先落「暂存区」，由 Hermes 审查后再决定是否部署。
INCOMING_DIR = os.path.join(BASE_DIR, "data", "incoming")   # 代码分发暂存区（隔离，不直接覆盖正式文件）
BACKUP_DIR = os.path.join(BASE_DIR, "data", "backup")       # 部署前备份旧文件（可回滚）
CODE_SYNC_END_MARKER = "__COMPLETE__"   # 发送端发完所有文件后发的「完成标志」，接收端据此触发审查


def _safe_fname(fname):
    """净化文件名：只取 basename，拒绝路径分隔符/非法名，防路径穿越。

    对方传来的 fname 可能含 ``../`` 等路径穿越（如 ``../../.ssh/authorized_keys``），
    必须净化后才能拼接到暂存目录。返回 None 表示拒绝。
    """
    if not fname or fname in (".", ".."):
        return None
    if "/" in fname or "\\" in fname:
        return None
    if fname != os.path.basename(fname):
        return None
    return fname


def _is_code_sync_request(text):
    """判断消息是否请求「代码分发」。

    只在「明确、简短的请求」时触发，避免「描述性长文本里顺带提到拉代码」被误判为请求。
    （曾发生：Mac 发「代码更新通知」描述新功能写了「拉代码命令」，命中关键词导致本机
    反向把代码推回 Mac。）

    触发条件（任一）：
      1. 精确命令：消息去空格后等于触发词（/pull_code、请发代码、拉代码）；
      2. 简短请求：消息长度 ≤ MAX_CODE_SYNC_REQ_LEN 且含明确请求关键词。
    长文本（如更新通知、调试反馈）即使含关键词也不触发。
    """
    t = text.strip().lower()
    if t in CODE_SYNC_TRIGGERS:
        return True
    if len(t) > MAX_CODE_SYNC_REQ_LEN:
        return False
    for kw in ("请求更新代码", "更新代码", "发代码", "同步代码", "拉代码", "pull_code"):
        if kw in t:
            return True
    return False


def _load_gui_addr():
    """GUI 地址从 config.json 读 GUI 端口（本机查看器，固定 127.0.0.1），默认 4568。
    统一 4568，避免 9000 被交易网关等其他服务占用。"""
    port = 4568
    try:
        port = int(load_config(os.path.join(BASE_DIR, "data")).get("port", 4568))
    except Exception:
        port = 4568
    return ("127.0.0.1", port)


GUI_ADDR = _load_gui_addr()            # 本机 GUI 地址（仅查看对话，不接收 agent 消息）


def _write_pidfile():
    """写 PID 文件，供 GUI 判断 hermes_agent 是否在跑（避免重复起 / 误杀）。"""
    try:
        os.makedirs(os.path.dirname(PIDFILE), exist_ok=True)
        with open(PIDFILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass


def _remove_pidfile():
    """退出时删 PID 文件。"""
    try:
        if os.path.isfile(PIDFILE):
            os.remove(PIDFILE)
    except Exception:
        pass


def _find_hermes_bin():
    """Hermes 可执行文件：优先 PATH（Windows 用 hermes.exe，macOS/Linux 用 hermes），
    macOS 再按 home 目录常见安装路径兜底（用 ~ 相对 home，不写死用户名）。
    找不到返回 None，调用时（_gen_reply）报友好错误，而不是落到错误的硬编码路径。"""
    exe = "hermes.exe" if sys.platform == "win32" else "hermes"
    p = shutil.which(exe)
    if p:
        return p
    if sys.platform == "darwin":
        candidate = os.path.expanduser("~/.hermes/hermes-agent/venv/bin/hermes")
        if os.path.isfile(candidate):
            return candidate
    return None


HERMES_BIN = _find_hermes_bin()
HERMES_TIMEOUT = 180                    # 单次 hermes 处理超时（秒）


def _get_hermes_model():
    """获取 Hermes 当前使用的模型（供 announce 广播告知其他 agent 自己的模型类型）。

    调用 `hermes config get model` 解析出 default(模型名) + provider。
    失败返回 None（广播就不带模型信息，向后兼容旧代码）。
    """
    if not HERMES_BIN:
        return None
    try:
        r = subprocess.run(
            [HERMES_BIN, "config", "get", "model"],
            capture_output=True, text=True, timeout=15,
            encoding="utf-8", errors="replace",
        )
        if r.returncode != 0:
            return None
        model = provider = None
        for line in (r.stdout or '').splitlines():
            line = line.strip()
            if line.startswith("default:"):
                model = line.split(":", 1)[1].strip()
            elif line.startswith("provider:"):
                provider = line.split(":", 1)[1].strip()
        if model or provider:
            return {"model": model, "provider": provider}
    except Exception:
        pass
    return None

END_STATEMENT = "【对话结束】"           # 发给对方的结束语句（可读）
RESTART_STATEMENT = "【重新开始】"        # 重开话题语句（带 conversation_id，轮次清零）
END_MARKER = "[END]"                    # Hermes 判定「无新信息」时返回的内部标记
DEFAULT_MAX_ROUNDS = 50


def _load_config():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False)


_cfg = _load_config()
auto_reply = bool(_cfg.get("auto_reply", True))
max_rounds = int(_cfg.get("max_rounds", DEFAULT_MAX_ROUNDS))


def _gen_reply(text):
    """调用完整 Hermes 处理消息，返回其回复（或 END_MARKER 表示该结束了）。"""
    prompt = (
        f"{text}\n\n"
        "[附加指令] 若这条消息没有新的信息或问题需要你处理"
        "（例如只是「好的」「谢谢」「收到」「知道了」这类礼貌性收尾），"
        f"请只回复 {END_MARKER} 三个字符，不要加任何其他文字，表示对话可以结束；"
        "否则正常回复。"
    )
    if not HERMES_BIN:
        return "[Hermes 未配置] 找不到 hermes 可执行文件，请把它加入系统 PATH"
    try:
        r = subprocess.run(
            [HERMES_BIN, "chat", "-q", prompt, "-Q"],
            capture_output=True, text=True, timeout=HERMES_TIMEOUT,
            cwd=os.path.expanduser("~"),
            encoding="utf-8", errors="replace",
        )
        out = (r.stdout or '').strip()
        if r.returncode != 0 and not out:
            return f"[Hermes 调用失败] {(r.stderr or '').strip()[:200]}"
        return out
    except subprocess.TimeoutExpired:
        return f"[Hermes 超时] 处理超过 {HERMES_TIMEOUT} 秒"
    except Exception as e:
        return f"[Hermes 调用异常] {type(e).__name__}: {e}"


def _gen_reply_raw(text):
    """调用完整 Hermes 处理文本，返回回复（不追加 [END] 指令）。

    用于「代码审查」等需要明确结论的场景：审查 prompt 已明确要求回复
    [DEPLOY]/[REJECT]，追加 [END] 指令会干扰 Hermes 判断。
    """
    if not HERMES_BIN:
        return "[Hermes 未配置] 找不到 hermes 可执行文件，请把它加入系统 PATH"
    try:
        r = subprocess.run(
            [HERMES_BIN, "chat", "-q", text, "-Q"],
            capture_output=True, text=True, timeout=HERMES_TIMEOUT,
            cwd=os.path.expanduser("~"),
            encoding="utf-8", errors="replace",
        )
        out = (r.stdout or '').strip()
        if r.returncode != 0 and not out:
            return f"[Hermes 调用失败] {(r.stderr or '').strip()[:200]}"
        return out
    except subprocess.TimeoutExpired:
        return f"[Hermes 超时] 处理超过 {HERMES_TIMEOUT} 秒"
    except Exception as e:
        return f"[Hermes 调用异常] {type(e).__name__}: {e}"


# ---- 主动通知用户（agentChats 需要用户授权/配合时，通过 Hermes 已配置的通讯方式通知）----
# 通知目标：用「具体 target」而非 bare platform name——bare feishu 没有 home channel 会报错。
# 这两条是用户（机器主人）的飞书 DM + 微信 DM；部署到别的机器时用 `hermes send --list`
# 查本机实际 target 并更新 config 的 notify_targets（见 PROTOCOL.md）。
NOTIFY_TARGETS_DEFAULT = [
    "feishu:oc_d65ac63471f188f5dcd65208422bd495",    # 用户飞书 DM
    "weixin:o9cq8058vpuTjk2POHq5DmdDUvSM@im.wechat",  # 用户微信 DM（bare weixin 同指 Home）
]
NOTIFY_SEND_TIMEOUT = 30                        # 单次 hermes send 超时（秒）

# 判定「需要用户授权/配合」的关键词（自动通知兜底）。宁可多通知不可漏通知。
NOTIFY_KEYWORDS = (
    "需要授权", "需要配合", "需要用户", "请用户", "需要你", "请你确认",
    "需要确认", "需要人工", "等待授权", "需要你介入", "请你操作", "需要操作",
)


def _discover_notify_targets():
    """自动发现本机 Hermes 已配置的通知目标（只取 DM，即用户本人私聊）。

    用 `hermes send --list` 列出本机可用 target，解析出带 (dm) 标记的 target，
    去掉 topic 后缀并去重。这解决「跨机器部署时 notify_targets 还是 Mac 的 ID」
    的问题：每台机器启动时自动发现自己的 DM，无需手动改 config。
    """
    if not HERMES_BIN:
        return []
    try:
        r = subprocess.run(
            [HERMES_BIN, "send", "--list"],
            capture_output=True, text=True, timeout=NOTIFY_SEND_TIMEOUT,
            encoding="utf-8", errors="replace",
        )
        if r.returncode != 0:
            return []
    except Exception as e:
        print(f"[通知] 自动发现目标失败: {type(e).__name__}: {e}", flush=True)
        return []

    targets, seen = [], set()
    for line in (r.stdout or '').splitlines():
        line = line.strip()
        if "(dm)" not in line:
            continue
        target = line.replace("(dm)", "").strip()
        if " / topic" in target:              # 去掉 topic 后缀，只留 platform:xxx
            target = target.split(" / topic")[0].strip()
        if not target or ":" not in target:
            continue
        if target in seen:                    # 去重（同一 DM 可能以 bare/topic 出现多次）
            continue
        seen.add(target)
        targets.append(target)
    return targets


def _ensure_notify_targets():
    """启动时确保 notify_targets 已配置：config 没有就自动发现并写入。

    agent 部署到新机器后，第一次启动会自动跑 `hermes send --list` 解析本机 DM，
    写入 data/hermes_config.json 的 notify_targets——之后 _notify_user 直接用，
    跨机器无需手动改。config 里已有时（用户手改过或自动发现过）则尊重现成配置。
    """
    if _cfg.get("notify_targets"):
        return
    discovered = _discover_notify_targets()
    if discovered:
        _cfg["notify_targets"] = discovered
        _save_config(_cfg)
        print(f"[通知] 已自动发现通知目标并写入 config：{discovered}", flush=True)
    else:
        print("[通知] 自动发现通知目标失败，后续将用硬编码默认值兜底", flush=True)


def _notify_user(text):
    """通过 `hermes send` 主动通知用户（发到 feishu/wechat 等 Hermes 已配置渠道）。

    agentChats 里的 agent 只能互相通讯，用户看不到中间过程。当某台机器的 agent
    需要用户授权或配合时，调用本函数把信息主动推到用户能看到的渠道（feishu/wechat）。
    不阻塞主流程：失败只打日志（通知是尽力而为，漏了还能用 /notify_user 显式触发）。
    """
    if not HERMES_BIN:
        print("[通知] 找不到 hermes 可执行文件，无法通知用户", flush=True)
        return False
    targets = _cfg.get("notify_targets")
    if not targets:                     # config 没配置（自动发现失败时），现场再发现一次
        targets = _discover_notify_targets()
    if not targets:                     # 还是没发现，用硬编码默认兜底
        targets = NOTIFY_TARGETS_DEFAULT
    if isinstance(targets, str):
        targets = [targets]
    ok = False
    for t in targets:
        try:
            r = subprocess.run(
                [HERMES_BIN, "send", "-t", t, text],
                capture_output=True, text=True, timeout=NOTIFY_SEND_TIMEOUT,
                encoding="utf-8", errors="replace",
            )
            if r.returncode == 0:
                print(f"[通知] 已发到 {t}: {text[:60]}...", flush=True)
                ok = True
            else:
                print(f"[通知] 发 {t} 失败: {(r.stderr or '').strip()[:100]}", flush=True)
        except Exception as e:
            print(f"[通知] 发 {t} 异常: {type(e).__name__}: {e}", flush=True)
    return ok


def _needs_user(text):
    """判断 Hermes 回复是否表示「需要用户授权/配合」（用于自动通知兜底）。"""
    if not text:
        return False
    for kw in NOTIFY_KEYWORDS:
        if kw in text:
            return True
    return False


# 话题轮次状态 {conv_id: {"round": N, "last_seen": ts}}
conversations = {}
conv_lock = threading.Lock()

# 回复队列元素：(src, text, ready, conv_id, is_end)
#   ready=True  → text 已是最终回复文本，直接发（命令确认）
#   ready=False → text 是用户原始消息，worker 调 Hermes 处理后再发
#   is_end=True → 发送结束语句（end: true）
reply_queue = queue.Queue()
gui_sync_queue = queue.Queue()   # 同步对话给 GUI 查看器：元素 (peer, text, who, conv_id)
code_sync_queue = queue.Queue()  # 代码分发队列：元素 (src, fname)


# 优雅退出信号：收到 SIGTERM/SIGINT 后停止收新消息，处理完当前消息再退出
_stopping = threading.Event()


def _handle_signal(signum, frame):
    _stopping.set()


# ---- 待回复消息持久化（收到消息实时写盘，进程任何时候被杀都不丢回复）----
_pending_lock = threading.Lock()


def _pending_load():
    try:
        with open(PENDING_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _pending_save(pending):
    try:
        os.makedirs(os.path.dirname(PENDING_FILE), exist_ok=True)
        with open(PENDING_FILE, "w", encoding="utf-8") as f:
            json.dump(pending, f, ensure_ascii=False)
    except Exception:
        pass


def _pending_add(src, text, conv_id):
    """收到消息时实时记录「待回复」，进程被杀后下次启动补发。"""
    with _pending_lock:
        pending = _pending_load()
        pending.append({"src": src, "text": text, "conv_id": conv_id, "ts": time.time()})
        _pending_save(pending)


def _pending_remove(src, text, conv_id):
    """回复已送达，从待回复列表删除。"""
    with _pending_lock:
        pending = _pending_load()
        pending = [p for p in pending if not (p.get("src") == src and p.get("text") == text)]
        _pending_save(pending)


def _retry_pending(src=None):
    """重试待回复。src 给定时只重试发给该 src 的（对方有新消息=链路可能已恢复的信号）；
    None 重试全部（启动时用）。"""
    pending = _pending_load()
    if not pending:
        return
    to_retry = [p for p in pending if src is None or p.get("src") == src]
    if not to_retry:
        return
    tag = src or "全部"
    print(f"[补发] {tag} 触发重试 {len(to_retry)} 条待回复", flush=True)
    for p in to_retry:
        reply_queue.put((p.get("src"), p.get("text"), False, p.get("conv_id"), False))


def _replay_pending():
    """启动时把上次未完成的待回复重新入队处理（下次开机补发）。"""
    _retry_pending(None)


# ---- 话题轮次状态持久化（重启后轮次计数延续）----
def _conv_save():
    try:
        os.makedirs(os.path.dirname(CONV_FILE), exist_ok=True)
        with conv_lock:
            data = dict(conversations)
        with open(CONV_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def _conv_load():
    try:
        with open(CONV_FILE, encoding="utf-8") as f:
            data = json.load(f)
        with conv_lock:
            conversations.update(data)
    except Exception:
        pass


def _next_conv_id(payload):
    """取话题 ID：消息带就用它的，不带则开新话题。"""
    conv_id = payload.get("conversation_id")
    if not conv_id:
        conv_id = "conv_" + uuid.uuid4().hex[:8]
    return conv_id


def on_msg(src, payload):
    global auto_reply, max_rounds
    line = f"[{time.strftime('%H:%M:%S')}] {src}: {payload}"
    print("收到消息: " + line, flush=True)
    with open(INBOX, "a", encoding="utf-8") as f:
        f.write(line + "\n")

    if not isinstance(payload, dict):
        return
    text = str(payload.get("text", "")).strip()

    # ---- 代码分发接收：收到 code_sync 分片，重组写文件（不走 Hermes AI）----
    if payload.get("code_sync"):
        _handle_code_sync_receive(payload, src)
        return

    # ---- 命令：切换自动回复开关 ----
    if text.startswith("/auto_reply"):
        parts = text.split()
        if len(parts) >= 2 and parts[1] in ("on", "off"):
            auto_reply = (parts[1] == "on")
            _cfg["auto_reply"] = auto_reply
            _save_config(_cfg)
            state = "已开启" if auto_reply else "已关闭"
            print(f"[开关] 自动回复 {state}", flush=True)
            reply_queue.put((src, f"✅ 自动回复{state}", True, None, False))
        return

    # ---- 命令：设置最大轮次 ----
    if text.startswith("/max_rounds"):
        parts = text.split()
        if len(parts) >= 2 and parts[1].isdigit():
            max_rounds = int(parts[1])
            _cfg["max_rounds"] = max_rounds
            _save_config(_cfg)
            print(f"[配置] 最大轮次 = {max_rounds}", flush=True)
            reply_queue.put((src, f"✅ 每个话题最大轮次已设为 {max_rounds}", True, None, False))
        return

    # ---- 命令：手动重发待回复（对方掉线时未送达的回复都在这）----
    if text.startswith("/resend"):
        _retry_pending(None)
        n = len(_pending_load())
        reply_queue.put((src, f"✅ 已触发重发所有待回复（当前 {n} 条）", True, None, False))
        return

    # ---- 主动通知用户：agent 请求把某件事通知用户（发到 feishu/wechat 等已配置渠道）----
    if text.startswith("/notify_user"):
        body = text[len("/notify_user"):].strip()
        if not body:
            reply_queue.put((src, "⚠️ 用法：/notify_user <要通知用户的内容>", True, None, False))
            return
        _notify_user(f"[agentChats 通知] {src} 请求通知你：\n{body}")
        reply_queue.put((src, "✅ 已通过 Hermes 通讯渠道通知用户", True, None, False))
        return

    # ---- 重开语句：重置话题轮次 ----
    if text == RESTART_STATEMENT:
        conv_id = payload.get("conversation_id")
        if conv_id:
            with conv_lock:
                conversations.pop(conv_id, None)   # 删除话题状态 = 轮次清零
            print(f"[重开] 话题 {conv_id} 轮次已清零", flush=True)
            reply_queue.put((src, f"✅ 话题 {conv_id} 已重新开始（轮次清零）", True, conv_id, False))
        else:
            reply_queue.put((src, "⚠️ 重开需在消息里带 conversation_id", True, None, False))
        return

    # ---- 拉代码：主动请求目标发代码给本机（开接收开关 + 发「请发代码」请求）----
    if text.startswith("/fetch_code"):
        parts = text.split()
        target = parts[1] if len(parts) >= 2 else None
        _open_code_rx()
        if target:
            r = agent.send(target, {"text": "请发代码"})
            if r.get("ok"):
                reply_queue.put((src, f"✅ 已开启接收开关并向 {target} 请求代码", True, None, False))
            else:
                reply_queue.put((src, f"⚠️ 已开启接收开关，但向 {target} 发请求失败：{r.get('error','')}", True, None, False))
        else:
            aids = [aid for aid in agent.list_agents().keys() if aid != agent.agent_id]
            for aid in aids:
                agent.send(aid, {"text": "请发代码"})
            reply_queue.put((src, f"✅ 已开启接收开关并向所有在线 agent 请求代码：{aids}", True, None, False))
        return

    # ---- 代码分发：收到「请发代码/请求更新代码」请求，直接把代码文件发给请求方（不走 Hermes AI）----
    if _is_code_sync_request(text):
        for fname in CODE_SYNC_FILES:
            code_sync_queue.put((src, fname))
        code_sync_queue.put((src, CODE_SYNC_END_MARKER))   # 发完后的完成标志，通知对方触发审查
        print(f"[代码分发] {src} 请求代码，将发送 {len(CODE_SYNC_FILES)} 个文件", flush=True)
        return

    # ---- 自动回复（防循环）----
    if not auto_reply:
        return
    if src == agent.agent_id:              # 1) 不回自己
        return
    if payload.get("end"):                 # 3) 收到结束语句，不再回复
        return
    if payload.get("is_auto_reply"):       # 2) 不回自动回复（防 A<->B 循环）
        return
    if not text:
        return

    # ---- 话题 + 轮次计数 ----
    conv_id = _next_conv_id(payload)
    with conv_lock:
        conv = conversations.get(conv_id, {"round": 0, "last_seen": time.time()})
        conv["round"] += 1
        conv["last_seen"] = time.time()
        conversations[conv_id] = conv
        round_num = conv["round"]

    # 轮次超限 → 发结束语句
    if round_num > max_rounds:
        print(f"[结束] 话题 {conv_id} 已达 {max_rounds} 轮上限，发结束语句", flush=True)
        reply_queue.put((src, END_STATEMENT, True, conv_id, True))
        return

    _retry_pending(src)   # 对方有新消息 = 链路可能已恢复，重试之前没发出去的待回复
    reply_queue.put((src, text, False, conv_id, False))   # 入队原始消息，worker 调 Hermes
    _pending_add(src, text, conv_id)           # 实时记录「待回复」，进程被杀也能下次补发
    gui_sync_queue.put((src, text, "peer", conv_id))   # 同步「收到的消息」给 GUI 查看器


def _reply_worker():
    """独立发送线程：调 Hermes 处理后 send（监听线程保持空闲可收 ACK）。

    处理完一条后：回复送达则从 pending 删除，未送达保留（下次启动补发）。
    收到退出信号时，处理完当前消息即停止（剩余 pending 已在磁盘）。
    """
    while True:
        item = reply_queue.get()
        if item is None or item[0] is None:
            break
        src, text, ready, conv_id, is_end = item
        orig_text = text               # 原始消息（用于 _pending_remove 匹配）

        if is_end:
            payload = {"text": END_STATEMENT, "is_auto_reply": True, "end": True}
            if conv_id:
                payload["conversation_id"] = conv_id
            r = agent.send(src, payload)
            print(f"[结束] -> {src}: {END_STATEMENT}  ({r.get('reason', 'delivered')})", flush=True)
            gui_sync_queue.put((src, END_STATEMENT, "hermes", conv_id))
            continue

        if not ready:
            print(f"[Hermes] 正在处理（话题 {conv_id}）：{text[:60]}...", flush=True)
            text = _gen_reply(text)
            # 自动兜底：Hermes 回复表示「需要用户授权/配合」时，主动通知用户
            if _needs_user(text):
                _notify_user(f"[agentChats 通知] {src} 需要你授权/配合：\n{text}")

        # Hermes 判定「无新信息」→ 发结束语句
        if text.strip().upper() == END_MARKER.upper() or text.strip() == END_STATEMENT:
            payload = {"text": END_STATEMENT, "is_auto_reply": True, "end": True, "conversation_id": conv_id}
            r = agent.send(src, payload)
            print(f"[结束] -> {src}: {END_STATEMENT}（Hermes 判定无新信息）", flush=True)
            gui_sync_queue.put((src, END_STATEMENT, "hermes", conv_id))
        else:
            payload = {"text": text, "is_auto_reply": True, "conversation_id": conv_id}
            r = agent.send(src, payload)
            print(f"[自动回复] -> {src}: {text[:80]}...  ({r.get('reason', 'delivered')})", flush=True)
            gui_sync_queue.put((src, text, "hermes", conv_id))

        # 这条是「待回复」消息（ready=False）：送达则删 pending，未送达保留补发
        if not ready and not is_end:
            if r.get("ok"):
                _pending_remove(src, orig_text, conv_id)
            else:
                print(f"[pending] 回复未送达（{r.get('reason')}），保留待下次补发", flush=True)

        # 优雅退出：处理完当前消息即停止（剩余 pending 已在磁盘，下次启动补发）
        if _stopping.is_set():
            break


def _gui_sync_worker():
    """把对话同步给 GUI 查看器（尽力而为，失败不影响主流程）。"""
    while True:
        peer, text, who, conv_id = gui_sync_queue.get()
        if peer is None:
            break
        try:
            # 用 send_direct 直接发 GUI_ADDR：GUI 不单播心跳给 hermes，会被
            # 心跳超时剔除，用 send() 查在线表会误判 offline。
            agent.send_direct(GUI_ADDR, GUI_ID,
                              {"text": text, "sync": True, "peer": peer, "who": who,
                               "conversation_id": conv_id})
        except Exception:
            pass


CHUNK_SIZE = 5000   # 代码分片每片最大字符数（UDP 单包有大小限制，超了会 Message too long）

# ---- 代码分发接收：收到的分片缓存 {file: {"total": N, "chunks": {idx: content}}} ----
code_rx_cache = {}
code_rx_lock = threading.Lock()

# 接收开关：默认关。只有本机「主动请求代码」（发「请发代码」）后才设为 True，
# 避免对方误推旧代码、无条件写文件覆盖本机新代码（曾发生过 3.11 反向推旧代码覆盖事故）。
code_rx_accepting = False
_code_rx_timer = None            # 接收开关超时重置定时器（打开后自动关闭，避免一直开着）
CODE_RX_TIMEOUT = 60             # 接收开关自动关闭超时（秒）


def _close_code_rx():
    """关闭代码接收开关（超时自动调用）。"""
    global code_rx_accepting
    code_rx_accepting = False
    print("[代码接收] 开关已关（超时自动关闭）", flush=True)


def _open_code_rx(timeout=CODE_RX_TIMEOUT):
    """打开代码接收开关（本机主动拉代码时调用），timeout 秒后自动关闭。"""
    global code_rx_accepting, _code_rx_timer
    code_rx_accepting = True
    if _code_rx_timer is not None:
        _code_rx_timer.cancel()
    _code_rx_timer = threading.Timer(timeout, _close_code_rx)
    _code_rx_timer.daemon = True
    _code_rx_timer.start()
    print(f"[代码接收] 开关已开，{timeout} 秒后自动关闭", flush=True)


def _handle_code_sync_receive(payload, src):
    """收到 code_sync 分片，按 file+chunk 重组；全部到齐后按 chunk 序号拼接写到暂存区。

    分片可能乱序到达，靠 chunk 序号（非到达顺序）重组；全部到齐才写文件（避免写半截）。
    只在「本机请求代码」（code_rx_accepting=True）时接收，否则忽略。
    收到的文件只落「暂存区」data/incoming/，不直接覆盖主目录——由 Hermes 审查后再决定部署。
    """
    if not code_rx_accepting:
        return                      # 本机没请求代码，忽略（防止对方主动推代码覆盖本机）
    fname = payload.get("file")
    chunk = payload.get("chunk")
    total = payload.get("total")
    content = payload.get("content", "")
    if not fname or chunk is None or total is None:
        return
    # 完成标志：发送端发完所有文件后发这个，触发 Hermes 审查
    if fname == CODE_SYNC_END_MARKER:
        _trigger_review(src)
        return
    # 净化文件名，防路径穿越（对方可能传 ../../ 之类）
    safe = _safe_fname(fname)
    if safe is None:
        print(f"[代码接收] 拒绝非法文件名 {fname!r}（防路径穿越）", flush=True)
        return
    with code_rx_lock:
        entry = code_rx_cache.setdefault(safe, {"total": total, "chunks": {}, "src": src})
        if entry["total"] != total:      # 同文件 total 不一致（异常），重置
            entry = {"total": total, "chunks": {}, "src": src}
            code_rx_cache[safe] = entry
        entry["chunks"][chunk] = content
        if len(entry["chunks"]) == total:   # 全部到齐，重组写暂存区
            full = "".join(entry["chunks"][i] for i in range(total))
            try:
                os.makedirs(INCOMING_DIR, exist_ok=True)
                path = os.path.join(INCOMING_DIR, safe)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(full)
                print(f"[代码接收] {safe} 重组完成（{total} 片）已写入暂存区 data/incoming/", flush=True)
            except Exception as e:
                print(f"[代码接收] {safe} 写入暂存区失败: {e}", flush=True)
            del code_rx_cache[safe]


def _code_sync_worker():
    """把代码文件内容分片发给请求方（响应通道，绕开对方防火墙 UDP 主动入站限制）。"""
    while True:
        item = code_sync_queue.get()
        if item is None:
            break
        src, fname = item
        info = agent.list_agents().get(src)
        if not info:
            print(f"[代码分发] {src} 不在线，跳过", flush=True)
            continue
        ip = info["ip"]
        addr = (ip, PORT)   # 统一端口：代码分发也发对方的 4567（对方应常开 hermes_agent 监听 4567）
        # 完成标志：发完所有文件后，通知对方「这组代码发完了」，触发对方审查
        if fname == CODE_SYNC_END_MARKER:
            agent.send_to(src, addr, {"code_sync": True, "file": CODE_SYNC_END_MARKER,
                                      "chunk": 0, "total": 1, "content": ""})
            print(f"[代码分发] {src} 全部文件发送完成，已发完成标志", flush=True)
            continue
        print(f"[代码分发] 目标 {src} @ {ip}:{PORT}", flush=True)
        path = os.path.join(BASE_DIR, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            # 分片：UDP 单包有大小上限（loopback ~8KB，跨网靠 IP 分片也不可靠），
            # 超了 sendto 抛 Message too long。按 5000 字符分片，JSON 编码后仍远小于上限。
            chunks = [content[i:i + CHUNK_SIZE] for i in range(0, len(content), CHUNK_SIZE)]
            total = len(chunks)
            for idx, chunk in enumerate(chunks):
                r = agent.send_to(src, addr, {"code_sync": True, "file": fname,
                                              "chunk": idx, "total": total, "content": chunk})
                state = r.get("reason", "delivered")
                if state == "delivered":
                    print(f"[代码分发] {fname} 第 {idx+1}/{total} 片 已送达", flush=True)
                else:
                    print(f"[代码分发] {fname} 第 {idx+1}/{total} 片: {state} "
                          f"({r.get('error', '')[:60]})", flush=True)
        except Exception as e:
            print(f"[代码分发] {fname} 失败: {e}", flush=True)


# ---- 代码分发审查：收到的文件落暂存区后，交给 Hermes 审查，判断是否部署 ----
review_queue = queue.Queue()   # 审查队列：元素 (src,)


def _trigger_review(src):
    """暂存区有完整文件后，入队触发 Hermes 审查。"""
    review_queue.put(src)
    files = os.listdir(INCOMING_DIR) if os.path.isdir(INCOMING_DIR) else []
    print(f"[代码接收] 已触发 Hermes 审查：{files}", flush=True)


def _deploy_incoming():
    """把暂存区文件部署到主目录（先备份旧文件到 data/backup/）。返回是否部署成功。"""
    files = [f for f in os.listdir(INCOMING_DIR) if os.path.isfile(os.path.join(INCOMING_DIR, f))] \
        if os.path.isdir(INCOMING_DIR) else []
    if not files:
        return False
    os.makedirs(BACKUP_DIR, exist_ok=True)
    for fn in files:
        src_path = os.path.join(INCOMING_DIR, fn)
        dst_path = os.path.join(BASE_DIR, fn)
        if os.path.isfile(dst_path):          # 先备份旧文件，再覆盖
            shutil.copy2(dst_path, os.path.join(BACKUP_DIR, fn))
        shutil.copy2(src_path, dst_path)
        print(f"[部署] {fn} 已部署（旧文件备份到 data/backup/）", flush=True)
    return True


def _review_worker():
    """审查暂存区文件：调 Hermes 判断，安全则部署 + 触发优雅重启（看门狗重拉新代码）。"""
    while True:
        item = review_queue.get()
        if item is None:
            break
        src = item
        files = [f for f in os.listdir(INCOMING_DIR) if os.path.isfile(os.path.join(INCOMING_DIR, f))] \
            if os.path.isdir(INCOMING_DIR) else []
        if not files:
            print("[审查] 暂存区为空，跳过", flush=True)
            continue
        prompt = (
            f"刚收到 {len(files)} 个代码文件，已暂存在 {INCOMING_DIR}：{files}\n"
            f"请逐个读取审查：判断是否合法、安全，有无恶意内容（删除文件/外传数据/危险命令/可疑网络请求等）。\n"
            f"只回复一行结论，不要多余说明：\n"
            f"  - 全部安全：回复 [DEPLOY]\n"
            f"  - 有危险：回复 [REJECT] <原因>\n"
        )
        verdict = _gen_reply_raw(prompt)
        upper = verdict.strip().upper()
        # 用「包含 [DEPLOY]」而非 startswith：Hermes 输出可能带警告前缀
        # （如「Warning: Unknown toolsets: rl\n[DEPLOY]」），导致误判为拒绝。
        if "[DEPLOY]" in upper:
            if _deploy_incoming():
                print("[审查] Hermes 判定安全，已部署，触发优雅重启（看门狗将重拉新代码）", flush=True)
                _stopping.set()   # 优雅退出，看门狗检测到进程死后重拉新代码
            else:
                print("[审查] 部署失败（暂存区为空）", flush=True)
        else:
            print(f"[审查] Hermes 拒绝部署：{verdict[:200]}", flush=True)
            try:
                agent.send(src, {"text": f"代码已收到，但 Hermes 审查拒绝部署：{verdict[:200]}",
                                 "is_auto_reply": True})
            except Exception:
                pass


agent = None   # 模块级占位，main() 里赋值，供 on_msg/worker 等函数引用


def main():
    """程序入口：启动 agent + worker 线程 + 主循环（优雅退出 + 补发）。"""
    global agent

    # 优雅退出：SIGTERM（GUI 关闭时由 GUI 发）/ SIGINT（Ctrl+C）
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    model_meta = _get_hermes_model()   # 上线广播带上自己的模型类型
    agent = Agent(HERMES_AGENT_ID, port=PORT, peers={GUI_ID: GUI_ADDR}, unified_port=PORT,
                  meta=model_meta)
    agent.on_message(on_msg)
    agent.start()
    _write_pidfile()

    reply_thread = threading.Thread(target=_reply_worker, daemon=True, name="hermes-reply")
    reply_thread.start()
    threading.Thread(target=_gui_sync_worker, daemon=True, name="hermes-gui-sync").start()
    threading.Thread(target=_code_sync_worker, daemon=True, name="hermes-code-sync").start()
    threading.Thread(target=_review_worker, daemon=True, name="hermes-review").start()

    # 恢复上次的话题轮次状态 + 补发上次未完成的回复（下次开机补发）
    _conv_load()
    _ensure_notify_targets()   # 启动时自动发现并配置通知目标（跨机器无需手动改 config）
    _replay_pending()

    print(f"[上线] hermes_agent @ {get_local_ips()}:{PORT}  自动回复={'开' if auto_reply else '关'}  "
          f"最大轮次={max_rounds}  接 Hermes={HERMES_BIN}", flush=True)

    try:
        while not _stopping.is_set():
            _stopping.wait(5)
            agents = agent.list_agents()
            if agents:
                info = {aid: f"{d['ip']}:{d['port']}"
                        + (f" [{d['meta']['model']}]" if d.get("meta", {}).get("model") else "")
                        for aid, d in agents.items()}
                print(f"[在线] {info}", flush=True)
            # 清理长时间未活跃的话题（防内存无限增长）
            now = time.time()
            with conv_lock:
                stale = [cid for cid, c in conversations.items() if now - c["last_seen"] > 3600]
                for cid in stale:
                    del conversations[cid]
            _conv_save()   # 定期持久化轮次状态
    except KeyboardInterrupt:
        pass
    finally:
        print("[退出] 优雅退出：等当前消息处理完、剩余待回复已落盘", flush=True)
        reply_queue.put(None)                    # 唤醒 worker（若阻塞在 get）
        reply_thread.join(timeout=HERMES_TIMEOUT + 10)   # 等 worker 处理完当前消息
        gui_sync_queue.put((None, None, None, None))
        code_sync_queue.put(None)
        review_queue.put(None)                   # 唤醒审查 worker
        _conv_save()                             # 退出前再存一次轮次状态
        agent.stop()
        _remove_pidfile()
        print("[退出] 完成", flush=True)


if __name__ == "__main__":
    main()

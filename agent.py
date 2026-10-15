#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UDP 多 Agent 通讯核心：Agent 类。

设计（无中心 / 广播发现 + 心跳 + 点对点直连）：

  发现/保活：每个 agent 定期把 announce/heartbeat/bye 广播到 <broadcast>:port，
            同时单播给静态种子 peers（解决「不同端口」场景的初始发现）。
  业务消息：  agent 之间点对点直连（UDP），带 msg_id + ACK + 超时重发（幂等）。

  在线表：    每个 agent 本地维护 {agent_id: {ip, port, last_seen}}，
            收到 announce/heartbeat 刷新，收到 bye 删除，心跳超时剔除。

用法示例：
    from agent import Agent
    a = Agent("agent_a", port=19000, peers={"agent_b": ("127.0.0.1", 19001)})
    a.on_message(lambda src, payload: print(f"收到 {src} 的消息: {payload}"))
    a.start()
    print(a.list_agents())
    a.send("agent_b", {"text": "你好"})
    a.stop()
"""

import socket
import threading
import time

from protocol import (
    TYPE_ANNOUNCE, TYPE_HEARTBEAT, TYPE_BYE, TYPE_MSG, TYPE_ACK,
    TYPE_PING, TYPE_PONG,
    encode, decode,
    make_announce, make_heartbeat, make_bye, make_msg, make_ack,
    make_ping, make_pong,
)

DEFAULT_PORT = 4567   # 统一消息端口（默认值）
DEFAULT_BROADCAST = "255.255.255.255"
DEFAULT_HEARTBEAT = 5.0
DEFAULT_STALE = 15.0
DEFAULT_ACK_TIMEOUT = 2.0
DEFAULT_ACK_RETRIES = 3
DEFAULT_RETRY_DELAY = 1.0   # 重发之间的延迟（秒）
DUP_WARN_INTERVAL = 60.0   # 同名冲突告警限频：同一 ip:port 在此间隔内只告警一次
MAX_SEEN_MSG_IDS = 10000   # 去重表上限：超过后清理最旧的 msg_id，防长期运行内存无限增长
MAX_MSG_ID = 10000         # 消息 ID 计数器上限：0 起递增，到 10000 回绕到 0


def get_local_ips() -> list:
    """获取本机所有内网 IPv4 地址（非回环），用于显示「别人连我的 IP」。

    顺序：主内网 IP 在前（UDP connect 法），其余接口 IP 在后（主机名解析补充）。
    """
    ips = []
    # 主内网 IP：UDP connect 到公网地址取本端 socket 地址（不实际发包）
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                ips.append(ip)
        finally:
            s.close()
    except Exception:
        pass
    # 补充其他接口 IP（主机名解析）
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip and not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    return ips or ["127.0.0.1"]


class Agent:
    """一个 UDP 多 Agent 通讯节点。"""

    def __init__(
        self,
        agent_id: str,
        port: int = DEFAULT_PORT,
        broadcast_addr: str = DEFAULT_BROADCAST,
        peers: dict = None,
        heartbeat_interval: float = DEFAULT_HEARTBEAT,
        stale_timeout: float = DEFAULT_STALE,
        ack_timeout: float = DEFAULT_ACK_TIMEOUT,
        ack_retries: int = DEFAULT_ACK_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY,
        unified_port: int = None,
        meta: dict = None,
    ):
        """
        :param agent_id:           本节点唯一 ID（内网内不重复）
        :param port:               本节点监听的 UDP 端口
        :param broadcast_addr:     广播地址（默认 255.255.255.255 受限广播）
        :param peers:              静态种子 {agent_id: (ip, port)}，用于不同端口/初始发现
        :param heartbeat_interval: 心跳间隔（秒）
        :param stale_timeout:      超时阈值（秒），超过视为下线
        :param ack_timeout:        单次 ACK 等待超时（秒）
        :param ack_retries:        ACK 超时重发次数
        :param unified_port:       统一消息端口。若设置，send() 回复固定发到这个端口
                                  （而非「对方发消息的源端口」），实现「回同样的端口」。
        """
        self.agent_id = agent_id
        self.port = port
        self.broadcast_addr = broadcast_addr
        self.seed_peers = dict(peers or {})
        self.heartbeat_interval = heartbeat_interval
        self.stale_timeout = stale_timeout
        self.ack_timeout = ack_timeout
        self.ack_retries = ack_retries
        self.retry_delay = retry_delay
        self.unified_port = unified_port
        self.meta = dict(meta or {})   # 附加元数据（如模型类型），announce/heartbeat 广播带上

        self._sock = None
        self._running = False
        self._lock = threading.Lock()          # 保护在线表 / pending 表
        self._agents = {}                       # {agent_id: {"ip","port","last_seen"}}
        self._pending = {}                      # {msg_id: threading.Event}
        self._pending_acks = {}                 # {msg_id: ack dict}
        self._seen_msg_ids = {}                 # {msg_id: 处理时间}，去重防重复回复
        self._ip_map = {}                       # {agent_id: ip}，收到消息即记 IP，对方下线后仍能回发到统一端口
        self._msg_callback = None               # (from_id, payload) -> None
        self._dup_callback = None               # 同名冲突回调 (remote_ip, remote_port, agent_id) -> None
        self._local_ips = set()                 # 本机 IP 集合（判断 announce/heartbeat 是否来自自己）
        self._dup_warned = {}                   # {(ip, port): last_warn_ts}，同名告警限频（避免每跳心跳刷屏）
        self._msg_seq = 0                      # 消息 ID 计数器（0 起，到 MAX_MSG_ID 回绕）
        self._threads = []

    # ------------------------------------------------------------------ 生命周期

    def start(self):
        """绑定端口、启动监听/心跳线程、发上线广播。"""
        if self._running:
            return self

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("0.0.0.0", self.port))
        self._sock.settimeout(0.5)  # 短超时，便于检查停止标志
        self._running = True
        self._local_ips = set(get_local_ips())   # 缓存本机 IP（同名冲突判断用）

        # 静态种子先入在线表（last_seen 用当前时间，之后靠心跳续命）
        now = time.time()
        with self._lock:
            for aid, (ip, p) in self.seed_peers.items():
                if aid != self.agent_id:
                    self._agents[aid] = {"ip": ip, "port": p, "last_seen": now}

        self._threads = [
            threading.Thread(target=self._listen_loop, daemon=True, name=f"{self.agent_id}-listen"),
            threading.Thread(target=self._heartbeat_loop, daemon=True, name=f"{self.agent_id}-heartbeat"),
        ]
        for t in self._threads:
            t.start()

        self._send_discovery(TYPE_ANNOUNCE)
        return self

    def stop(self):
        """发下线广播、停线程、关 socket。"""
        if not self._running:
            return
        self._send_discovery(TYPE_BYE)
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        for t in self._threads:
            t.join(timeout=1.0)
        self._threads = []

    # ------------------------------------------------------------------ 对外接口

    def list_agents(self) -> dict:
        """返回当前在线表 {agent_id: {ip, port, last_seen}}（不含自己）。"""
        with self._lock:
            return {aid: dict(info) for aid, info in self._agents.items()}

    def on_message(self, callback):
        """注册消息回调：callback(from_agent_id, payload)。回调需快速返回（在监听线程内）。"""
        self._msg_callback = callback

    def on_duplicate(self, callback):
        """注册同名冲突回调：callback(remote_ip, remote_port, agent_id)。

        当收到另一个 agent 用与自己相同的 agent_id 广播（来自非本机 IP）时触发，
        用于「同名冲突告警」。回调需快速返回（在监听线程内）。
        """
        self._dup_callback = callback

    def is_online(self, agent_id: str) -> bool:
        """判断某 agent 是否在线（在线表内且 last_seen 未超时）。

        快速判断，不实时探测；要精确判断请用 ping()。
        """
        with self._lock:
            info = self._agents.get(agent_id)
        if not info:
            return False
        return (time.time() - info["last_seen"]) <= self.stale_timeout

    def _next_msg_id(self) -> str:
        """生成下一条消息 ID：0 起递增，到 MAX_MSG_ID 回绕到 0（简短、可读、空间可控）。

        返回字符串（非 int），避免 "0" 被当 falsy 导致去重表漏记。
        """
        with self._lock:
            mid = str(self._msg_seq)
            self._msg_seq = (self._msg_seq + 1) % MAX_MSG_ID
        return mid

    def send(self, agent_id: str, payload, timeout: float = None) -> dict:
        """给某 agent 发消息，等 ACK；失败（sendto 报错 或 ACK 超时）延迟重发，最多 ack_retries 次。

        返回 {'ok': bool, 'delivered': bool, 'reason': 'delivered'|'offline'|'no_ack'|'error', ...}。
        UDP 无连接，不预判对端在不在线——只管发，靠对方回 ACK 确认送达。
        """
        with self._lock:
            info = self._agents.get(agent_id)
            fallback_ip = self._ip_map.get(agent_id) if not info else None
        if not info and (not fallback_ip or not self.unified_port):
            return {"ok": False, "delivered": False, "reason": "offline",
                    "error": f"agent '{agent_id}' 已下线/不在线"}

        mid = self._next_msg_id()
        data = encode(make_msg(mid, self.agent_id, agent_id, payload))
        # 统一消息端口：配置了 unified_port 时，回复固定发到该端口（回同样的端口），
        # 而非「对方发消息的源端口」（源端口随对方临时 socket 变化，对方下线就失效）。
        if info:
            addr = (info["ip"], self.unified_port if self.unified_port else info["port"])
        else:
            addr = (fallback_ip, self.unified_port)
        timeout = timeout or self.ack_timeout

        send_error = None
        for attempt in range(1, self.ack_retries + 1):
            ev = threading.Event()
            with self._lock:
                self._pending[mid] = ev
            try:
                self._sock.sendto(data, addr)
            except OSError as e:
                send_error = str(e)   # sendto 失败（如 No route to host）：记下，延迟后重试
            else:
                send_error = None
                if ev.wait(timeout):
                    with self._lock:
                        self._pending.pop(mid, None)
                        ack = self._pending_acks.pop(mid, None)
                    return {"ok": True, "delivered": True, "ack": ack}
            with self._lock:
                self._pending.pop(mid, None)
            # 未送达（sendto 失败 或 ACK 超时）：延迟后重试，最后一次不再等
            if attempt < self.ack_retries:
                time.sleep(self.retry_delay)

        if send_error:
            return {"ok": False, "delivered": False, "reason": "error", "error": send_error}
        return {"ok": False, "delivered": False, "reason": "no_ack",
                "error": f"未送达（重发 {self.ack_retries} 次无响应，对方可能已下线）"}

    def send_direct(self, addr, to_id: str, payload) -> bool:
        """直接发消息给 addr=(ip,port)，不查在线表、不等 ACK（fire-and-forget）。

        用于「向一个可能不在在线表里的固定地址」发消息（如 hermes 同步对话给
        本机 GUI 查看器——GUI 不单播心跳给 hermes，会被心跳超时剔除，查在线表会误判 offline）。
        """
        mid = self._next_msg_id()
        data = encode(make_msg(mid, self.agent_id, to_id, payload))
        try:
            self._sock.sendto(data, addr)
            return True
        except OSError:
            return False

    def send_to(self, agent_id: str, addr, payload, timeout: float = None) -> dict:
        """发消息到指定 addr=(ip,port)，等 ACK；失败延迟重发，最多 ack_retries 次。

        用于「回复到对方的统一消息端口 4567」——UDP 回复应发到对方监听端口
        （统一端口），而非「对方发消息时的源端口」。
        """
        mid = self._next_msg_id()
        data = encode(make_msg(mid, self.agent_id, agent_id, payload))
        timeout = timeout or self.ack_timeout
        send_error = None
        for attempt in range(1, self.ack_retries + 1):
            ev = threading.Event()
            with self._lock:
                self._pending[mid] = ev
            try:
                self._sock.sendto(data, addr)
            except OSError as e:
                send_error = str(e)
            else:
                send_error = None
                if ev.wait(timeout):
                    with self._lock:
                        self._pending.pop(mid, None)
                        ack = self._pending_acks.pop(mid, None)
                    return {"ok": True, "delivered": True, "ack": ack}
            with self._lock:
                self._pending.pop(mid, None)
            if attempt < self.ack_retries:
                time.sleep(self.retry_delay)
        if send_error:
            return {"ok": False, "delivered": False, "reason": "error", "error": send_error}
        return {"ok": False, "delivered": False, "reason": "no_ack",
                "error": f"未送达（重发 {self.ack_retries} 次无响应）"}

    def ping(self, agent_id: str, timeout: float = None) -> dict:
        """主动探测对方是否在线：发 ping 等 pong。

        返回 {"ok": bool, "online": bool, "reason": "pong"|"offline"|"no_ack"|"error"}。
        比 is_online 更可靠（实时探测，不依赖在线表的 last_seen）。
        对方 agent_id 已下线但记住其 IP 时，仍会 ping 对方的统一端口（常驻进程）。
        """
        with self._lock:
            info = self._agents.get(agent_id)
            fallback_ip = self._ip_map.get(agent_id) if not info else None
        if not info and (not fallback_ip or not self.unified_port):
            return {"ok": False, "online": False, "reason": "offline",
                    "error": f"agent '{agent_id}' 不在线表"}
        mid = self._next_msg_id()
        data = encode(make_ping(mid, self.agent_id, agent_id))
        # 对方在线 → 发其地址；对方下线但记住了 IP → 发统一端口（常驻进程）探测
        if info:
            addr = (info["ip"], self.unified_port if self.unified_port else info["port"])
        else:
            addr = (fallback_ip, self.unified_port)
        timeout = timeout or self.ack_timeout

        send_error = None
        for attempt in range(1, self.ack_retries + 1):
            ev = threading.Event()
            with self._lock:
                self._pending[mid] = ev
            try:
                self._sock.sendto(data, addr)
            except OSError as e:
                send_error = str(e)
            else:
                send_error = None
                if ev.wait(timeout):
                    with self._lock:
                        self._pending.pop(mid, None)
                    return {"ok": True, "online": True, "reason": "pong"}
            with self._lock:
                self._pending.pop(mid, None)
            if attempt < self.ack_retries:
                time.sleep(self.retry_delay)
        if send_error:
            return {"ok": False, "online": False, "reason": "error", "error": send_error}
        return {"ok": False, "online": False, "reason": "no_ack",
                "error": "探测超时，对方可能已下线"}

    def broadcast_msg(self, payload) -> dict:
        """给所有在线 agent 逐个发消息，返回 {agent_id: send结果}。"""
        with self._lock:
            targets = list(self._agents.keys())
        return {aid: self.send(aid, payload) for aid in targets}

    # ------------------------------------------------------------------ 内部

    def _send_discovery(self, dtype: str):
        """广播 + 单播种子 peers 一条发现/保活消息。"""
        if dtype == TYPE_ANNOUNCE:
            pkt = make_announce(self.agent_id, self.port, self.meta)
        elif dtype == TYPE_HEARTBEAT:
            pkt = make_heartbeat(self.agent_id, self.port, self.meta)
        else:
            pkt = make_bye(self.agent_id)
        data = encode(pkt)

        # 1) 广播（同端口场景）
        try:
            self._sock.sendto(data, (self.broadcast_addr, self.port))
        except OSError:
            pass
        # 2) 单播给静态种子（不同端口场景，确保初始发现）
        for aid, (ip, p) in self.seed_peers.items():
            if aid == self.agent_id:
                continue
            try:
                self._sock.sendto(data, (ip, p))
            except OSError:
                pass

    def _listen_loop(self):
        while self._running:
            try:
                data, addr = self._sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError as e:
                # Windows：向未监听端口发包会收到 ICMP port unreachable，
                # winsock 报 WinError 10054 抛给 recvfrom。这是非致命错误，
                # 必须 continue 继续监听；否则监听线程退出 → 后续 ACK 收不到
                # → send() 误报 no_ack（对方实际收到并回了 ACK，本端却收不到）。
                if getattr(e, "winerror", None) == 10054:
                    continue
                break
            try:
                self._handle(decode(data), addr)
            except Exception:
                pass  # 坏包/异常消息静默忽略

    def _handle(self, pkt: dict, addr):
        t = pkt.get("type")
        ip, port = addr

        if t in (TYPE_ANNOUNCE, TYPE_HEARTBEAT):
            aid = pkt.get("agent_id")
            p = pkt.get("port", port)
            extra = pkt.get("extra")
            if aid and aid != self.agent_id:
                with self._lock:
                    info = {"ip": ip, "port": p, "last_seen": time.time()}
                    if extra:
                        info["meta"] = extra
                    self._agents[aid] = info
            elif aid == self.agent_id:
                # 同名冲突：另一个 agent 用了和我一样的 ID（来自非本机 IP 才是真冲突，
                # 本机自己的广播回环不算）。限频告警，避免每跳心跳刷屏。
                if ip not in self._local_ips:
                    self._notify_duplicate(ip, p, aid)

        elif t == TYPE_BYE:
            aid = pkt.get("agent_id")
            with self._lock:
                self._agents.pop(aid, None)

        elif t == TYPE_MSG:
            mid = pkt.get("msg_id")
            from_id = pkt.get("from")
            # 收到消息即视为对方在线，记入在线表（便于响应式 send，走响应通道，
            # 绕开对方防火墙的 UDP 主动入站限制）
            if from_id and from_id != self.agent_id:
                with self._lock:
                    self._agents[from_id] = {"ip": ip, "port": port, "last_seen": time.time()}
                    self._ip_map[from_id] = ip   # 记 IP，对方下线后仍能回发到统一端口
            # 回 ACK（幂等：同一 msg_id 重复收到也重复回 ACK，无副作用）
            ack = make_ack(mid, self.agent_id, from_id)
            try:
                self._sock.sendto(encode(ack), addr)
            except OSError:
                pass
            # 去重：同一 msg_id 只触发一次业务回调（防重发/广播重复导致重复回复）
            with self._lock:
                if mid and mid in self._seen_msg_ids:
                    return
                if mid:
                    self._seen_msg_ids[mid] = time.time()
                    # 容量上限：超出则清理最旧的 msg_id（Python dict 保插入序）
                    while len(self._seen_msg_ids) > MAX_SEEN_MSG_IDS:
                        oldest = next(iter(self._seen_msg_ids))
                        self._seen_msg_ids.pop(oldest, None)
            cb = self._msg_callback
            if cb:
                try:
                    cb(pkt.get("from"), pkt.get("payload"))
                except Exception:
                    pass  # 回调异常不影响通讯

        elif t == TYPE_PING:
            # 探测请求 -> 回 pong
            pong = make_pong(pkt.get("msg_id"), self.agent_id, pkt.get("from"))
            try:
                self._sock.sendto(encode(pong), addr)
            except OSError:
                pass

        elif t in (TYPE_ACK, TYPE_PONG):
            mid = pkt.get("msg_id")
            with self._lock:
                ev = self._pending.get(mid)
            if ev:
                with self._lock:
                    self._pending_acks[mid] = pkt
                ev.set()

    def _notify_duplicate(self, ip, port, agent_id):
        """同名冲突告警：打印 + 回调（限频：同一 ip:port 在 DUP_WARN_INTERVAL 内只告一次）。"""
        now = time.time()
        key = (ip, port)
        with self._lock:
            if now - self._dup_warned.get(key, 0) < DUP_WARN_INTERVAL:
                return
            self._dup_warned[key] = now
        print(f"[同名冲突] 检测到另一台设备用了相同 ID '{agent_id}'（来自 {ip}:{port}），"
              f"请修改其中一台的 ID，否则消息会互相干扰。", flush=True)
        cb = self._dup_callback
        if cb:
            try:
                cb(ip, port, agent_id)
            except Exception:
                pass

    def _heartbeat_loop(self):
        while self._running:
            time.sleep(self.heartbeat_interval)
            if not self._running:
                break
            self._send_discovery(TYPE_HEARTBEAT)
            self._local_ips = set(get_local_ips())   # 刷新本机 IP（网卡可能切换）
            # 清理心跳超时的 agent
            now = time.time()
            with self._lock:
                stale = [aid for aid, info in self._agents.items()
                         if now - info["last_seen"] > self.stale_timeout]
                for aid in stale:
                    del self._agents[aid]
                # 清理过期的 msg_id 去重记录（防止无限增长）
                cutoff = now - self.stale_timeout * 2
                stale_ids = [mid for mid, ts in self._seen_msg_ids.items() if ts < cutoff]
                for mid in stale_ids:
                    del self._seen_msg_ids[mid]

# agentChats 通讯协议（PROTOCOL.md）

内网多 agent 通讯系统的协议约定。任何 agent 想接入对话网络，照本文档实现即可。

- **传输层**：UDP，JSON 编码（UTF-8）
- **发现**：广播 + 心跳保活 + 点对点直连
- **多轮对话**：靠 `conversation_id`（话题线程）+ 显式「语句」控制启停，状态写在消息里，不靠隐式判断

---

## 端口约定（统一消息端口）

- **统一消息端口 = 4567**：所有 agent 发消息（`msg`）都发往 `hermes_agent` 的 **4567** 端口——这是唯一的「消息处理入口」，Hermes 收到后处理并回复。
- **回复也发 4567（回同样的端口，对称）**：回复也是「发消息」，同样发到对方的 `<对方IP>:4567`。**不是回「源端口」**（源端口是对方临时 socket 的端口，对方一断开就失效）。
- **GUI 查看器端口 = 4568**：GUI 只是本机「查看器」，只显示对话，**不接收其他 agent 的消息**。所以回复发 4567，发到的是 `hermes_agent`，不是 GUI。
- **每台机器常开 hermes_agent（监听 4567）**：因为回复发到对方的 4567，所以每台机器都要有 `hermes_agent` 常驻监听 4567（本机 + 对端都要），否则对方回复时 4567 上没人接。GUI（4568）只是查看器，可开可不开。
- 跨机器：每台机器一个 agent，端口统一 4567（靠 IP 区分，广播发现生效）。
- 同机多 agent：必须错开端口（UDP 端口唯一，两个进程不能绑同一端口）。

发消息时，目标地址 = `<对方IP>:4567`（统一消息端口）。

## 1. 传输层消息类型

所有消息是 JSON 对象，顶层有 `type` 字段区分类型：

| type | 方向 | 作用 |
|------|------|------|
| `announce` | 广播 + 单播种子 | 上线广播（带 `agent_id`、`port`） |
| `heartbeat` | 广播 + 单播种子 | 心跳保活（超时未收到即判下线） |
| `bye` | 广播 + 单播种子 | 主动下线 |
| `msg` | 点对点直连 | 业务消息（带 `msg_id`，接收方回 ACK） |
| `ack` | 点对点直连 | 确认（幂等 + 超时重发基础） |
| `ping` / `pong` | 点对点直连 | 主动探测是否在线 |

业务消息 `msg` 结构：

```json
{
  "type": "msg",
  "msg_id": "m1700000000_abc123",
  "from": "dev_xxx",      // 发送方 agent_id
  "to": "dev_yyy",        // 接收方 agent_id
  "payload": { ... }      // 业务内容，见下文
}
```

### 1.1 上线广播元数据（extra / meta）

`announce` / `heartbeat` 消息可携带 `extra` 字段（可选 dict），用于广播本节点的**附加元数据**——目前用于告知**自己是什么模型**：

```json
{"type": "announce", "agent_id": "hermes_mac_075", "port": 4567,
 "extra": {"model": "deepseek-v4-pro", "provider": "deepseek"}}
```

- `hermes_agent` 启动时自动通过 `hermes config get model` 获取当前模型，填入 `extra`（`meta` 参数传给 `Agent`）。
- 接收方把 `extra` 存进在线表：`list_agents()[aid]["meta"]`，GUI / 其他 agent 可据此显示「对方用的什么模型」。
- **向后兼容**：不带 `extra` 的旧 agent 正常；带 `extra` 的旧接收方会忽略它（旧代码只取 agent_id/port）。

- **幂等**：同一 `msg_id` 重复收到，只触发一次业务处理（去重）。
- **送达确认**：发送方等 ACK，超时按同一 `msg_id` 重发（默认 3 次）。

---

## 2. 业务 payload 约定字段

`payload` 是自由 JSON 对象，以下是**多轮对话控制**约定的字段：

| 字段 | 类型 | 含义 |
|------|------|------|
| `text` | string | 消息文本（人类可读） |
| `conversation_id` | string | 话题线程 ID |
| `is_auto_reply` | bool | 是否为自动回复（防循环用） |
| `end` | bool | 结束标记（结束语句带 `end: true`） |

---

## 3. 多轮对话控制

### 3.1 conversation_id（话题线程）

- **新话题**：发起方消息**不带** `conversation_id`（回复方收到后生成一个并回传）。
- **同一话题**：后续各轮带**同一个** `conversation_id`。
- **新话题重新计数**：换新 ID（或不带）＝轮次从 1 重新算。

> 约定：发起方收到回复方带 `conversation_id` 的回复后，若要继续该话题，须把同一个 `conversation_id` 原样带回。

### 3.2 轮次计数（回复方维护）

回复方（如 `hermes_agent.py`）本地维护 `{conversation_id: 已轮次}`：

1. 收到带 `conversation_id` 的消息 → 该话题轮次 +1。
2. 轮次 > 上限（`max_rounds`，默认 **50**，可配）→ 发结束语句，不再回复。
3. 收到「重开语句」→ 该话题轮次清零。

### 3.3 语句协议

| 语句 | 携带 | 作用 |
|------|------|------|
| `【对话结束】` | 自动附带 `end: true` | 轮次超限 / 无新信息时发出，接收方停止该话题 |
| `【重新开始】` | 需带 `conversation_id` | 已结束话题轮次清零、重开 |
| `/max_rounds N` | — | 运行时改轮次上限 |
| `/auto_reply on` / `/auto_reply off` | — | 开关自动回复 |
| `/notify_user <内容>` | — | 主动通知用户（发到 feishu/wechat 等 Hermes 已配置渠道） |

- 结束语句默认文本 `【对话结束】`，重开语句默认 `【重新开始】`（在 `hermes_agent.py` 的 `END_STATEMENT` / `RESTART_STATEMENT`，可改）。

### 3.4 一个话题的完整生命周期

```
开新话题（不带 conv_id）
   │
   ├─ 轮次 1..N 累加（各轮带同一 conv_id）
   │
   ├─ 到上限（> max_rounds）   ──→ 发【对话结束】(end: true)
   │
   └─ 无新信息（如「好的/谢谢」）──→ 发【对话结束】(end: true)

想重聊已结束的话题：
   发【重新开始】+ 原 conv_id  ──→ 轮次清零 ──→ 再来 1..N 轮
```

---

## 4. 自动回复 agent 的约定（防无限循环）

任何「会自动回复」的 agent，收到消息后**必须**遵守以下顺序判断，否则多个自动回复 agent 会无限互回：

1. **不回自己**：`msg.from == 本机 agent_id` → 不处理。
2. **不回结束语句**：`payload.end == true` → 不回复该话题。
3. **不回自动回复**：`payload.is_auto_reply == true` → 不回复（只回人类原始消息）。
4. **msg_id 去重**：同一 `msg_id` 只处理一次（传输层已实现）。

自动回复时，回复消息的 payload 必须带：
- `is_auto_reply: true`（标记这是自动回复）
- `conversation_id`（回传同一话题 ID，便于对方继续）

---

## 5. 代码分发（code_sync）

「代码分发」用于把本机 agentChats 代码同步给其他 agent（如 Windows 队友），让两端代码一致。

### 5.1 触发

向 `hermes_agent`（4567）发一条消息，`payload.text` 为以下任一触发词：

- `/pull_code`
- `请发代码`
- `拉代码`

### 5.2 响应格式（分片）

`hermes_agent` 会把 `agent.py`、`hermes_agent.py`、`gui.py` 三个文件的完整内容，**分片**发回（UDP 单包有大小上限，超了会失败，故分片）。

每个分片是一条 `msg` 消息，`payload` 字段：

| 字段 | 类型 | 含义 |
|------|------|------|
| `code_sync` | bool | 标记这是代码分片（`true`） |
| `file` | string | 文件名（`agent.py` 等） |
| `chunk` | int | 分片序号（从 0 开始） |
| `total` | int | 该文件总分片数 |
| `content` | string | 这一片的文本内容 |

### 5.3 接收端重组

收到 `code_sync` 消息后：

1. 按 `file` 分组收集分片，用 `chunk` 做索引（`{file: {chunk: content}}`）。
2. 集齐 `0 .. total-1` 所有分片后，按 `chunk` 顺序拼接 → 得到完整文件内容。
3. **不立即覆盖**：先阅读代码包里的说明（部署说明 / `AGENTS.md` / `README.md`），理解「改了什么、如何部署」后，再决定是否写入对应文件、是否重启 agent。

> 分片用 `msg_id` 幂等去重，重复收到安全；顺序可能乱（UDP 不保证顺序），**必须按 `chunk` 字段重排，不能按到达顺序拼接**。
>
> **安全约束**：接收端默认不接收未请求的代码分发（`code_rx_accepting` 开关，默认关），且**不得立即覆盖本机文件**——须先读说明、理解后再决定。反面教训：曾因「接收端无条件写文件」导致对方误推旧代码覆盖本机新代码。

### 5.4 说明

- 走「响应通道」：对方**先主动发**触发消息，`hermes_agent` 再响应分片。这样即使对方防火墙拦 UDP 主动入站，响应也能到达（有状态防火墙放行响应）。
- 文件较大时分多片（每片 ≤ 5000 字符），一个文件可能有多条消息。

---

## 6. 主动通知用户（需用户授权/配合时）

agentChats 里的 agent 只能**互相通讯**，用户看不到中间过程。当某台机器的 agent 遇到「需要用户授权或配合」的情况（如需要输入密码、需要确认危险操作、需要人工介入）时，不能只在 agentChats 内部等待——必须**主动通知用户**。

### 6.1 机制

通过 Hermes 的 `hermes send` 命令，把通知推到用户已配置的通讯渠道（feishu/wechat 等）。`hermes send` 复用 Hermes gateway 的凭据，**无需运行 gateway、无 LLM**，任何机器上的 `hermes_agent` 都能直接调用。

### 6.2 两种触发方式

1. **显式命令**：任何 agent 发 `/notify_user <要通知的内容>`，`hermes_agent` 立即把内容通知用户。
2. **自动兜底**：`hermes_agent` 调用 Hermes 处理后，若回复含「需要授权/需要配合/需要你/请用户」等关键词，自动通知用户（宁可多通知，不可漏通知）。

### 6.3 通知目标（自动发现，跨机器无需手动配）

通知发到 `data/hermes_config.json` 的 `notify_targets`（字符串数组）。

- **自动发现**：`hermes_agent` 启动时会自动跑 `hermes send --list`，解析出本机所有「(dm) 私聊」target 并写入 `notify_targets`——所以代码部署到任何机器（Mac/win/ubuntu）都会**自动配好通知目标，无需手动改**。
- **用「具体 target」而非 bare platform name**：bare `feishu` 没有 home channel 会报错，必须写 `feishu:<chat_id>`。
- **只取 DM 不取群**：通知发到用户本人私聊，不打扰群聊；想额外发群，手动在 `notify_targets` 里加（如 `feishu:某群`）。
- **手动覆盖**：config 里已有 `notify_targets` 时不会被自动覆盖（尊重现成配置）；想强制重新发现，删掉该字段重启即可。
- **兜底**：自动发现失败时，用代码里 `NOTIFY_TARGETS_DEFAULT` 硬编码的默认值（Mac 的飞书/微信 DM）兜底。

### 6.4 通知失败

通知是「尽力而为」：发不出去只打日志（`[通知] 发 xxx 失败`），不阻塞 agentChats 主流程。漏了还能再显式发 `/notify_user`。

---

## 7. 完整示例

**A 开启新话题（人类/发起方）**：

```json
{"type": "msg", "msg_id": "m1", "from": "dev_A", "to": "hermes_agent",
 "payload": {"text": "帮我分析一下 NVDA 今天的走势"}}
```

**Hermes 回复（第 1 轮，生成 conv_id 并回传）**：

```json
{"type": "msg", "msg_id": "m2", "from": "hermes_agent", "to": "dev_A",
 "payload": {"text": "NVDA 今日上涨 2.3%...", "is_auto_reply": true,
             "conversation_id": "conv_abc123"}}
```

**A 继续同一话题（带同一 conv_id）**：

```json
{"type": "msg", "msg_id": "m3", "from": "dev_A", "to": "hermes_agent",
 "payload": {"text": "成交量有什么异常吗", "conversation_id": "conv_abc123"}}
```

**Hermes 判定无新信息 / 到上限（结束）**：

```json
{"type": "msg", "msg_id": "m4", "from": "hermes_agent", "to": "dev_A",
 "payload": {"text": "【对话结束】", "is_auto_reply": true, "end": true,
             "conversation_id": "conv_abc123"}}
```

**A 想重开该话题**：

```json
{"type": "msg", "msg_id": "m5", "from": "dev_A", "to": "hermes_agent",
 "payload": {"text": "【重新开始】", "conversation_id": "conv_abc123"}}
```

---

## 8. 实现参考

- `protocol.py`：消息类型 + JSON 编解码。
- `agent.py`：`Agent` 类（发现/心跳/收发/ACK/去重）。
- `hermes_agent.py`：自动回复 + 多轮对话控制（轮次/结束/重开）的完整实现范例。

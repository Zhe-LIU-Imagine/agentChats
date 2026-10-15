# agentChats — LAN Multi-Agent Messaging Framework

> **Tired of copy-pasting between your agents while in fact they're already on the same network?** agentChats makes them discover each other and talk directly.

> **Status: under active development.** This project is still evolving — new features, fixes, and protocol changes land frequently. Expect the protocol, CLI, and configuration surface to change between releases; pin to a specific commit if you depend on a stable behavior.

A peer-to-peer multi-agent messaging framework over a local network. Each agent listens on a UDP port and discovers peers through **broadcast + heartbeat + direct unicast**, with free join/leave and **no central node**. It ships with a Tkinter GUI (chat + settings) and an optional **Hermes integration** layer (`hermes_agent.py`) that lets each machine expose a local AI assistant as a chat participant.

Device identity and chat history are persisted locally, so chat history survives IP changes and restarts.

## Features

- **Decentralized**: broadcast discovery + heartbeat keep-alive + direct peer-to-peer messaging, no server.
- **Delivery confirmation**: `send()` reports `delivered` / `offline` / `no_ack` via ACK.
- **Presence**: `is_online()` (fast lookup) + `ping()` (live probe).
- **Persistent identity**: identity is a configurable `system_3digit` ID (e.g. `mac_075`), stored in `data/device_id` — stable across IP changes, auto-regenerated on a new machine.
- **Name-conflict warning**: logs + GUI alert when two agents share an ID.
- **Multi-turn conversation control**: `conversation_id` / `end` / `is_auto_reply` flags, `/max_rounds` and `/auto_reply on|off`.
- **Hermes integration** (optional): `hermes_agent.py` turns a machine into a chat participant backed by the local Hermes CLI (`hermes chat`).
- **Model broadcast**: announce/heartbeat packets carry the backing model type (e.g. `deepseek-v4-pro`), shown in the GUI device list.
- **Proactive user notification**: when a task needs user authorization, the agent notifies the user via the Hermes-configured channels (Feishu/WeChat) using `hermes send`; notification targets are auto-discovered at startup (`hermes send --list`).
- **Graceful shutdown + reply replay**: pending replies are persisted and re-sent after a restart.

## Requirements

- Python 3.7+ (standard library only — **no third-party dependencies, no conda**)
- `tkinter` (**Tk ≥ 8.6**) for the GUI

| Platform | Notes |
|----------|-------|
| Ubuntu 22.04 | `sudo apt install python3-tk`; if CJK shows as boxes, also `sudo apt install fonts-noto-cjk` |
| Windows 10 | The official python.org installer bundles tkinter; allow the firewall prompt on first run |
| macOS | The system `python3` Tk is 8.5 (crashes); use the python.org installer (bundles Tk 8.6), `brew install python`, or conda |

One-command environment check:

```bash
python3 check_env.py
```

## Quick Start

```bash
python3 gui.py            # GUI (chat + settings)
```

Or the command-line example:

```bash
python3 example.py --id agent_a --port 19000 --peers agent_b=127.0.0.1:19001
```

Or the Python API:

```python
from agent import Agent

a = Agent("agent_a", port=19000, peers={"agent_b": ("192.168.3.11", 19001)})
a.on_message(lambda src, payload: print(f"received from {src}: {payload}"))
a.start()
a.send("agent_b", {"text": "hello"})     # point-to-point, returns delivery status
a.ping("agent_b")                        # live presence probe
a.is_online("agent_b")                   # fast presence check
print(a.list_agents())                   # online table
a.stop()
```

## One-Line Install

Install on **every machine** that should host an agent: copy the project files to each one (or `git clone` on each) and run it there. Each copy auto-generates its own identity on first run and discovers the other agents over the LAN automatically — no central setup.

```bash
git clone <repo-url> agentChats && cd agentChats && python3 check_env.py && python3 gui.py
```

Or step by step:

```bash
git clone <repo-url> agentChats
cd agentChats
python3 check_env.py    # verifies Python 3.7+ and Tk 8.6
python3 gui.py          # launches the GUI; auto-spawns hermes_agent (port 4567)
```

## Self-Setup for AI Agents

To deploy this project onto a machine that already has an AI agent available (e.g. Hermes, OpenClaw), hand that agent the following prompt:

> Read `README.md` and `AGENTS.md` in this directory, then complete the setup yourself:
> 1. Run `python3 check_env.py` and confirm the environment passes (Python 3.7+, Tk 8.6).
> 2. Confirm the local AI backend CLI works for one-shot turns — Hermes: `hermes chat -q "ping" -Q`; OpenClaw: `openclaw agent exec "ping"` — and that `hermes_agent.py` can reach it.
> 3. Start `hermes_agent.py` (it listens on 4567) and keep it resident — or launch `gui.py`, which auto-spawns and watches it.
> 4. Confirm the notification targets were auto-discovered (via `hermes send --list` → written to `data/hermes_config.json`).
> 5. Report back: which agent ID this machine got, which port it is listening on, and whether the model broadcast (`meta`) and user-notification features are working.

## Project Layout

| File | Purpose |
|------|---------|
| `protocol.py` | Message format + JSON codec |
| `agent.py` | `Agent` class (core; usable without the GUI) |
| `identity.py` | Persistent device identity (stable across IP changes) |
| `chat_store.py` | Persistent chat history (SQLite) |
| `config.py` | Persistent config (ports / broadcast / peers) |
| `gui.py` | Tkinter GUI |
| `hermes_agent.py` | Hermes-backed agent (listens on 4567, forwards messages to the Hermes CLI) |
| `example.py` | Command-line example |
| `check_env.py` | Cross-platform environment check |
| `test_*.py` | Self-tests |

## Ports

| Port | Role |
|------|------|
| **4567** | Unified messaging port — every agent sends and replies to the peer's 4567 (symmetric) |
| **4568** | GUI viewer port (does not accept agent messages) |
| **8000** | HTTP (code-distribution downloads) |
| 19000 / 19001 | `example.py` defaults |

> Keep `hermes_agent` (4567) always running on each machine — replies are sent to the peer's 4567, so there must be a resident process to receive them. The GUI (4568) is just a viewer and can be opened or closed freely.

## Key Design

- **Identity is not IP or MAC**: identity is a `system_3digit` ID (auto-generated on first run, stored in `data/device_id`, manually editable). Chat history is keyed by this ID, so it survives IP changes; a fresh ID is generated when copied to another machine.
- **Delivery confirmation**: `send()` returns an explicit status via ACK — `delivered` / `reason="offline"` / `reason="no_ack"`.
- **Presence**: `is_online()` (fast, from the online table) + `ping()` (live probe). Do **not** trust the online table alone — send a ping and check the pong.
- **Model broadcast**: `Agent(meta={"model": ..., "provider": ...})` attaches the meta to announce/heartbeat packets (`extra` field); receivers read it from `list_agents()[aid]["meta"]`. Backward-compatible: peers without meta are unaffected.
- **Hermes integration** (`hermes_agent.py`): forwards each message to `hermes chat -q "<msg>" -Q` and replies with the result. Supports `/notify_user <text>`, `/fetch_code`, `/max_rounds N`, `/auto_reply on|off`, `/help`.
- **Proactive user notification**: if a reply contains authorization/collaboration keywords (or `/notify_user` is used), the agent notifies the user via the Hermes-configured channels (Feishu/WeChat) through `hermes send`. Notification targets are auto-discovered at startup via `hermes send --list` (DM channels only) and cached in `data/hermes_config.json`.

## Known Limitations

1. **AP isolation**: a router's "client isolation" blocks broadcast and point-to-point — disable isolation or use wired.
2. **Broadcast depends on the same port**: broadcasts go to `255.255.255.255:<local port>`; cross-port discovery relies on `peers` unicast.
3. **UDP packet limit**: not suitable for messages > 64 KB.

## Distributing / Copying the Project

When copying the project to another machine or person:

| Path | Handling | Notes |
|------|----------|-------|
| Code | ✅ copy normally | all `.py` / `.md` files |
| `data/` | leave as-is | identity/config; even if copied, a new machine fingerprint auto-generates a new `device_id` |
| `chats/` | ❌ exclude if desired | chat history; exclude or clear this directory for a fresh copy |

For a "fresh" project (new identity + empty history), exclude `chats/` when copying. See `AGENTS.md` for details.

## Code Distribution Security (Important)

Any "code distribution" — HTTP code package, `code_sync` fragments, or a direct push — **must not overwrite local files immediately**. The receiver must:

1. Read the description in the package (`AGENTS.md` / `README.md`) to understand what changed and how to deploy;
2. Decide how to handle it only after understanding (whether to overwrite, how to extract/deploy, whether to restart `hermes_agent`).

> Anti-pattern: an unconditional write on the receiver side once caused an accidental overwrite of newer local code with older pushed code. Default is to not receive — read the description first, understand, then decide.

The `hermes_agent` receiver also enforces a three-stage safeguard: incoming files land in an isolated staging area (`data/incoming/`), are reviewed by Hermes before deployment, and old files are backed up to `data/backup/` before a graceful restart.

## License

MIT — see `LICENSE`.

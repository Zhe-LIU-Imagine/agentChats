#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 _is_code_sync_request 的触发边界（回归：长文本描述不误判为「请求代码」）。

背景：曾发生 Mac 发「代码更新通知」描述新功能写了「拉代码命令」，
命中关键词「拉代码」导致本机误判为「请发代码」请求，反向把代码推回 Mac。
修复后：只有「精确命令」或「简短请求」才触发，长文本（> MAX_CODE_SYNC_REQ_LEN）不触发。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hermes_agent as ha

f = ha._is_code_sync_request

# 1) 精确命令触发
assert f("/pull_code") is True, "精确 /pull_code 应触发"
assert f("请发代码") is True, "精确「请发代码」应触发"
assert f("拉代码") is True, "精确「拉代码」应触发"
assert f("  拉代码  ") is True, "带空格「拉代码」应触发"
print("[1] 精确命令触发 ✅")

# 2) 简短请求（含关键词、长度 ≤ 阈值）触发
assert f("请发代码给我") is True, "简短请求「请发代码给我」应触发"
assert f("请求更新代码") is True, "「请求更新代码」应触发"
assert f("更新代码") is True, "「更新代码」应触发"
assert f("同步代码给我") is True, "「同步代码给我」应触发"
print("[2] 简短请求触发 ✅")

# 3) 长文本描述（> 阈值）不触发——即使含「拉代码」「更新代码」等关键词
long_notice = ("【代码更新通知】本次改动：1) 新增 /fetch_code 拉代码命令，可主动从对端拉取代码；"
               "2) 修复优雅退出 bug；3) GUI 左边栏改两层树。请更新后重启 hermes_agent 验证。")
assert f(long_notice) is False, "长文本「代码更新通知」不应触发（含「拉代码」关键词）"
assert len(long_notice) > ha.MAX_CODE_SYNC_REQ_LEN, "测试样例应确实超过阈值"
print("[3] 长文本描述不触发（回归点）✅")

# 4) 无关消息不触发
assert f("你好") is False, "普通消息不应触发"
assert f("Windows 测试报告：9/9 通过") is False, "测试报告不应触发"
print("[4] 无关消息不触发 ✅")

# 5) _pid_alive 边界（跨平台：命令行匹配，非 os.kill）
import gui
assert gui._pid_alive(None) is False, "_pid_alive(None) 应 False"
assert gui._pid_alive(0) is False, "_pid_alive(0) 应 False"
assert gui._pid_alive(-1) is False, "_pid_alive(-1) 应 False"
assert gui._pid_alive(99999999) is False, "_pid_alive(超大概率不存在的 pid) 应 False"
assert gui._pid_alive(os.getpid()) is False, "_pid_alive(测试进程自身 pid) 应 False（非 hermes_agent.py）"
pids = gui._hermes_agent_pids()
assert isinstance(pids, list) and all(isinstance(p, int) for p in pids), "_hermes_agent_pids 应返回 int 列表"
print(f"[5] _pid_alive 边界 + _hermes_agent_pids 返回 int 列表 ✅（当前 hermes_agent pids={pids}）")

print("🎉 代码分发触发边界 + _pid_alive 命令行匹配 验证全部通过")

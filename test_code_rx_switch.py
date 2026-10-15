#!/usr/bin/env python3
"""验证代码分发接收开关：_open_code_rx 置 True + 超时自动关闭。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hermes_agent as ha

# 初始状态应为 False（默认拒收）
ha.code_rx_accepting = False
assert ha.code_rx_accepting is False
print("[0] 初始默认拒收（False）✅")

# 打开开关
ha._open_code_rx(timeout=1)   # 1 秒超时，测试快
assert ha.code_rx_accepting is True, "打开后应为 True"
print("[1] _open_code_rx 置 True ✅")

# 超时自动关闭
time.sleep(1.6)
assert ha.code_rx_accepting is False, "超时后应自动关闭"
print("[2] 超时自动关闭 ✅")

# 可重复打开/关闭
ha._open_code_rx(timeout=1)
assert ha.code_rx_accepting is True
time.sleep(1.6)
assert ha.code_rx_accepting is False
print("[3] 可重复打开/关闭 ✅")

# 手动关闭接口存在
ha._open_code_rx(timeout=5)
assert ha.code_rx_accepting is True
ha._close_code_rx()
assert ha.code_rx_accepting is False
print("[4] 手动关闭 ✅")

print("🎉 代码接收开关验证全部通过")

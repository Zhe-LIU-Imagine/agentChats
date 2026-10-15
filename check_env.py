#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跨平台环境检查：验证能否运行 UDP 多 Agent 通讯系统（含 GUI）。

检查项：
  1. Python 版本 >= 3.7
  2. tkinter 可用 + Tk 版本 >= 8.6（8.5 在 macOS 15 上会崩溃）
  3. 分平台给出依赖安装提示

用法：python3 check_env.py
退出码：0=就绪，1=有缺失
"""

import sys
import platform


def main() -> int:
    system = platform.system()
    print("=" * 50)
    print("UDP 多 Agent 通讯系统 - 环境检查")
    print("=" * 50)
    print(f"操作系统: {system} {platform.release()}")
    print(f"Python:   {sys.version.split()[0]}")
    print(f"架构:     {platform.machine()}")

    problems = []

    # 1. Python 版本
    v = sys.version_info
    if v < (3, 7):
        problems.append(f"Python 版本过低（{v.major}.{v.minor}），需要 >= 3.7")
    else:
        print(f"Python 版本: ✅ {v.major}.{v.minor}")

    # 2. tkinter 可用性 + 版本
    try:
        import tkinter
        tk_ver = tkinter.TkVersion
        if tk_ver >= 8.6:
            print(f"tkinter:   ✅ Tk {tk_ver}")
        else:
            print(f"tkinter:   ⚠️ Tk {tk_ver}（过低）")
            problems.append(f"Tk 版本 {tk_ver} 过低（需 >= 8.6，8.5 在 macOS 15 会崩溃）")
    except ImportError:
        print("tkinter:   ❌ 不可用")
        problems.append("tkinter 不可用（GUI 无法运行，但 agent.py 核心仍可用）")

    # 3. 核心依赖（标准库，应全部可用）
    for mod in ("socket", "threading", "json", "sqlite3", "uuid"):
        try:
            __import__(mod)
        except ImportError:
            problems.append(f"标准库模块 {mod} 不可用（异常）")
    print("核心标准库: ✅ socket/threading/json/sqlite3/uuid")

    print()
    if problems:
        print("❌ 发现以下问题：")
        for p in problems:
            print(f"   - {p}")
    else:
        print("✅ 环境就绪，可运行 gui.py 或 agent.py")

    # 4. 分平台提示
    hints = {
        "Linux": "💡 Ubuntu/Debian 缺 tkinter 时：sudo apt install python3-tk\n"
                 "   中文显示方块时：sudo apt install fonts-noto-cjk",
        "Darwin": "💡 macOS：系统 python3 的 Tk 8.5 会崩，改用任意 Tk 8.6 的 Python\n"
                  "   最简：python.org 官方安装包（自带 Tk 8.6）/ brew install python / conda",
        "Windows": "💡 Windows：python.org 官方包自带 tkinter；首次运行请在防火墙弹窗点「允许访问」",
    }
    if system in hints:
        print()
        print(hints[system])

    print()
    print("下一步：python3 test_agents.py 验证核心功能")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())

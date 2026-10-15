#!/usr/bin/env python3
"""验证代码分发安全：文件名净化（防路径穿越）+ 落暂存区 + 部署备份 + 完成标志审查。"""
import os
import sys
import shutil
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hermes_agent as ha

# 隔离：把 BASE_DIR/INCOMING_DIR/BACKUP_DIR 都指向临时目录，避免碰真实文件
tmp = tempfile.mkdtemp(prefix="agentchat_safe_test_")
ha.BASE_DIR = tmp
ha.INCOMING_DIR = os.path.join(tmp, "incoming")
ha.BACKUP_DIR = os.path.join(tmp, "backup")


def clean():
    ha.code_rx_cache.clear()
    ha.code_rx_accepting = True
    for d in (ha.INCOMING_DIR, ha.BACKUP_DIR):
        if os.path.isdir(d):
            shutil.rmtree(d)
    while not ha.review_queue.empty():
        try:
            ha.review_queue.get_nowait()
        except Exception:
            break


# ============ 1. 文件名净化（防路径穿越）============
assert ha._safe_fname("agent.py") == "agent.py"
assert ha._safe_fname("../../.ssh/authorized_keys") is None, "正向路径穿越应拒绝"
assert ha._safe_fname("..\\..\\evil.py") is None, "反斜杠路径穿越应拒绝"
assert ha._safe_fname("sub/dir.py") is None, "子目录应拒绝"
assert ha._safe_fname("..") is None
assert ha._safe_fname(".") is None
assert ha._safe_fname("") is None
print("[1] 文件名净化（防路径穿越）✅")

# ============ 2. 接收写暂存区（不直接覆盖主目录）============
clean()
content = "print('hello world')"
chunks = [content[i:i + 5] for i in range(0, len(content), 5)]
total = len(chunks)
for idx, c in enumerate(chunks):
    ha._handle_code_sync_receive(
        {"code_sync": True, "file": "agent.py", "chunk": idx, "total": total, "content": c},
        "sender")
incoming_path = os.path.join(ha.INCOMING_DIR, "agent.py")
assert os.path.isfile(incoming_path), "应写到暂存区"
assert open(incoming_path, encoding="utf-8").read() == content, "暂存区内容应重组正确"
print(f"[2] 接收写暂存区（{total} 片重组正确）✅")

# ============ 3. 路径穿越文件名被拒 ============
clean()
ha._handle_code_sync_receive(
    {"code_sync": True, "file": "../../evil.py", "chunk": 0, "total": 1, "content": "x"},
    "sender")
assert not os.path.exists(os.path.join(tmp, "evil.py")), "穿越文件名不应落盘"
assert not os.path.exists(os.path.join(ha.INCOMING_DIR, "..", "..", "evil.py"))
print("[3] 路径穿越文件名被拒 ✅")

# ============ 4. 完成标志触发审查 ============
clean()
os.makedirs(ha.INCOMING_DIR, exist_ok=True)
open(os.path.join(ha.INCOMING_DIR, "agent.py"), "w", encoding="utf-8").write("x")
ha._handle_code_sync_receive(
    {"code_sync": True, "file": ha.CODE_SYNC_END_MARKER, "chunk": 0, "total": 1, "content": ""},
    "sender")
assert ha.review_queue.qsize() == 1, "完成标志应入队触发审查"
ha.review_queue.get_nowait()
print("[4] 完成标志触发审查 ✅")

# ============ 5. 部署 + 备份旧文件 ============
clean()
os.makedirs(ha.INCOMING_DIR, exist_ok=True)
# 主目录放一个「旧文件」，暂存区放「新文件」
open(os.path.join(tmp, "agent.py"), "w", encoding="utf-8").write("OLD_CONTENT")
open(os.path.join(ha.INCOMING_DIR, "agent.py"), "w", encoding="utf-8").write("NEW_CONTENT")
ok = ha._deploy_incoming()
assert ok is True, "部署应成功"
assert open(os.path.join(tmp, "agent.py"), encoding="utf-8").read() == "NEW_CONTENT", "主目录应被覆盖为新内容"
backup_path = os.path.join(ha.BACKUP_DIR, "agent.py")
assert os.path.isfile(backup_path), "旧文件应被备份"
assert open(backup_path, encoding="utf-8").read() == "OLD_CONTENT", "备份内容应是旧内容"
print("[5] 部署 + 备份旧文件 ✅")

# 清理
shutil.rmtree(tmp, ignore_errors=True)
print("🎉 代码分发安全（净化/暂存/部署备份/审查）验证全部通过")

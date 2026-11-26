"""Confirm the operator's K230 power cycle using only USB enumeration."""

import time
from serial.tools import list_ports


def boards():
    return [p.serial_number or p.location or p.device for p in list_ports.comports()
            if (p.vid, p.pid) == (0x1209, 0xABD1)]


def wait_present(board, present, seconds):
    deadline = time.monotonic() + seconds
    while (board in boards()) != present:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.25, remaining))
    return True


def reconnect(ask):
    initial = boards()
    if len(initial) != 1:
        raise RuntimeError("电脑未识别到唯一的 K230 USB 设备。")
    prompt = "拔掉 K230 所有电源，保持板端 USB 拔出，等灯全灭后按回车；退出输入 q > "
    for _ in range(3):
        if ask(prompt).strip().lower() == "q":
            raise RuntimeError("用户结束")
        if wait_present(initial[0], False, 8):
            break
        prompt = ("电脑还能看到板子。请确认拔掉的是插在板子上的那根 USB 线"
                  "（如果用了 USB 扩展坞，也要从板子这一端拔），然后再按回车；退出输入 q > ")
    else:
        raise RuntimeError("未验证：三次检查后电脑仍能看到板子。")
    time.sleep(10)
    if ask("现在插回 USB，等板子启动后按回车；退出输入 q > ").strip().lower() == "q":
        raise RuntimeError("用户结束")
    if not wait_present(initial[0], True, 20):
        raise RuntimeError("原板子的 USB 连接未恢复。")

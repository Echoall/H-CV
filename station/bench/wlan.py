"""Switch Windows Wi-Fi and restore the connection that was actually in use."""

from pathlib import Path
import re
import subprocess
import tempfile
import time
from xml.sax.saxutils import escape


MANUAL_CONNECT = "自动连接失败。请点右下角网络图标，选下方显示的板子网络并连接。"
MANUAL_RESTORE = "原网络未自动恢复。请点右下角网络图标，重新选你原来使用的网络。"


def netsh(*args):
    return subprocess.run(
        ["netsh", "wlan", *args], capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=7, check=False,
    )


def snapshot(command=netsh):
    result = command("show", "interfaces")
    if result.returncode:
        return None
    interfaces = re.split(r"(?im)^[ \t]*(?:Name|名称|名字)[ \t]*:[ \t]*", result.stdout)[1:]
    if len(interfaces) != 1:
        return None
    name, _, details = interfaces[0].partition("\n")
    values = {}
    for line in details.splitlines():
        key, separator, value = line.strip().partition(":")
        if separator:
            values[key.strip().lower()] = value.strip()
    ssid = values.get("ssid")
    profile = values.get("profile") or values.get("配置文件") or values.get("設定檔")
    state = values.get("state") or values.get("状态") or values.get("狀態")
    if ssid and profile:
        return name.strip(), ssid, profile
    if state in ("disconnected", "已断开连接", "已中断连接", "已斷線", "已斷開連線"):
        return name.strip(), None, None
    return None


def wait_for(predicate, *, seconds, command=netsh):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        current = snapshot(command)
        if current is not None and predicate(current):
            return True
        time.sleep(0.5)
    return False


def connect(original, ssid, password, *, wait=12, command=netsh):
    if original[1] == ssid:
        return True
    xml = f"""<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
<name>{escape(ssid)}</name>
<SSIDConfig><SSID><hex>{ssid.encode('utf-8').hex().upper()}</hex></SSID></SSIDConfig>
<connectionType>ESS</connectionType><connectionMode>manual</connectionMode>
<MSM><security><authEncryption><authentication>WPA2PSK</authentication>
<encryption>AES</encryption><useOneX>false</useOneX></authEncryption>
<sharedKey><keyType>passPhrase</keyType><protected>false</protected>
<keyMaterial>{escape(password)}</keyMaterial></sharedKey></security></MSM></WLANProfile>"""
    interface = f"interface={original[0]}"
    with tempfile.TemporaryDirectory(prefix="hcv-wifi-") as directory:
        profile = Path(directory) / "profile.xml"
        profile.write_text(xml, encoding="utf-8")
        if command("add", "profile", f"filename={profile}", interface, "user=current").returncode:
            return False
    if command("connect", f"name={ssid}", f"ssid={ssid}", interface).returncode:
        return False
    return wait_for(lambda current: current[:2] == (original[0], ssid),
                    seconds=wait, command=command)


def restore(original, *, wait=15, command=netsh):
    if snapshot(command) == original:
        return True
    interface = f"interface={original[0]}"
    if original[2] is None:
        result = command("disconnect", interface)
    else:
        result = command("connect", f"name={original[2]}", f"ssid={original[1]}", interface)
    return not result.returncode and wait_for(
        lambda current: current == original, seconds=wait, command=command,
    )

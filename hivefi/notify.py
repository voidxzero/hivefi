"""Desktop notifications ("now playing") through the notification service: `notify-send` when it exists (it can show
a cover and replaces the previous notification instead of piling up), otherwise a tray-icon balloon."""
import shutil
import subprocess


def available() -> bool:
    return shutil.which("notify-send") is not None


def command(summary: str, body: str = "", icon: str = "", timeout_ms: int = 5000) -> list[str]:
    cmd = ["notify-send", "-a", "Hivefi", "-t", str(timeout_ms), "-u", "low",
           "-h", "string:desktop-entry:hivefi",
           "-h", "string:x-canonical-private-synchronous:hivefi-track",      # a new song replaces the previous bubble
           "-h", "string:synchronous:hivefi-track"]                           # (the same hint under dunst's name)
    if icon:
        cmd += ["-i", icon]
    return cmd + ["--", summary, body]


def send(summary: str, body: str = "", icon: str = "", timeout_ms: int = 5000) -> bool:
    """True when a notification was handed to the system."""
    if not available():
        return False
    try:
        subprocess.run(command(summary, body, icon, timeout_ms), capture_output=True, timeout=5, check=False)
        return True
    except (OSError, subprocess.SubprocessError):
        return False

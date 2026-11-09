"""Unhandled errors are written to crash.log and do not end the program."""
import os, subprocess, sys, textwrap


def test_error_in_a_slot_is_logged_and_survived(tmp_path):
    log = tmp_path / "crash.log"
    code = textwrap.dedent(f'''
        import sys
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QApplication
        app = QApplication([])
        from hivefi.ui.main_window import install_crash_log
        install_crash_log({str(log)!r})
        def boom(): raise ValueError("kaboom")
        QTimer.singleShot(0, boom)
        QTimer.singleShot(100, lambda: (print("still alive"), app.quit()))
        app.exec()
    ''')
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    r = subprocess.run([sys.executable, "-c", code], cwd=os.path.dirname(os.path.dirname(__file__)),
                       env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "still alive" in r.stdout
    text = log.read_text()
    assert "Hivefi started" in text and "ValueError: kaboom" in text and "in boom" in text

"""
Package execution entry point for EverAI TTS Studio VIP.
Usage:
    python -m everai (if src is in PYTHONPATH)
"""

try:
    from .qt_gui import launch_gui
except ImportError:
    from qt_gui import launch_gui

if __name__ == "__main__":
    launch_gui()

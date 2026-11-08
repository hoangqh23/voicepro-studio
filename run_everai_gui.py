"""
Launcher for EverAI TTS Studio VIP GUI Application.
Usage:
    python run_everai_gui.py
"""

import sys
from pathlib import Path

# Ensure workspace root is in sys.path
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.everai.qt_gui import launch_gui

if __name__ == "__main__":
    launch_gui()

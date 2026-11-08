"""
Direct standalone launcher for EverAI TTS Studio VIP.
Allows running the app directly from inside the src/everai folder:
    python run.py
"""

import sys
from pathlib import Path

# Add current folder to sys.path with top priority so it works completely standalone
CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

# Also allow package-style run as fallback
PARENT_DIR = CURRENT_DIR.parent
if str(PARENT_DIR) not in sys.path:
    sys.path.append(str(PARENT_DIR))

try:
    from .qt_gui import launch_gui
except ImportError:
    from qt_gui import launch_gui

if __name__ == "__main__":
    launch_gui()

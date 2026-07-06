import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("LAN_CLASSROOM_DISABLE_SCREEN_MONITOR", "1")

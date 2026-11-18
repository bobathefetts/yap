"""Double-click (or run with pythonw) to start Yap without a console window."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from yap.app import main  # noqa: E402

main()

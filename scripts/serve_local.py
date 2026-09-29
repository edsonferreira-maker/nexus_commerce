"""Continue the local launcher inside its isolated Python environment."""

import sys
import threading

from run_local import ROOT, open_browser_when_ready

sys.path.insert(0, str(ROOT / 'server'))
from app import main

threading.Thread(target=open_browser_when_ready, daemon=True).start()
try:
    main()
except KeyboardInterrupt:
    pass

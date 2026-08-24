from __future__ import annotations

import crous_notifier
from crous_search_fallback import install


if __name__ == "__main__":
    install(crous_notifier)
    crous_notifier.main()

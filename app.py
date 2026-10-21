"""Vercel entrypoint: Vercel loads the `app` defined here."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from deal_finder.web.app import create_app  # noqa: E402

app = create_app()

"""Module entry point so ``python -m app.cli`` works.

The commands live in :mod:`app.cli.main`. Defining them here instead would make
the module import itself under two names — once as ``app.cli.__main__`` and once
via the package ``__init__`` — which Python warns about and which would run the
decorators twice.
"""

from app.cli.main import app

if __name__ == "__main__":
    app()

"""让 `python -m app.qq` 直接工作。"""

from .bot import main

if __name__ == "__main__":
    raise SystemExit(main())

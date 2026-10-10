"""`python -m cli` 入口: 转发到 cli.render.main。"""
import sys

from .render import main

if __name__ == "__main__":
    sys.exit(main())

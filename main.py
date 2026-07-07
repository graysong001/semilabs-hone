"""semilabs-hone 根入口: `python main.py serve`。

转发到 semilabs_hone.cli。等价于 `python -m semilabs_hone`。
"""
import sys

from semilabs_hone.cli import main

if __name__ == "__main__":
    sys.exit(main())

"""支持 `python -m semilabs_hone` 调用 CLI。"""
import sys

from semilabs_hone.cli import main

if __name__ == "__main__":
    sys.exit(main())

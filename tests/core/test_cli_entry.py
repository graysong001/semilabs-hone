"""CLI entry points — `python -m semilabs_hone` module runner wiring."""
from __future__ import annotations

import runpy
import sys

import pytest


class TestModuleEntry:
    def test_python_dash_m_invokes_cli_main(self, monkeypatch):
        """`python -m semilabs_hone` exits through cli.main (design §16)."""
        called = {}

        def fake_main(argv=None):
            called["argv"] = argv
            return 0

        monkeypatch.setattr("semilabs_hone.cli.main", fake_main)
        monkeypatch.setattr(sys, "argv", ["semilabs_hone"])
        # __main__ was imported at collection time in some runs; force re-exec.
        sys.modules.pop("semilabs_hone.__main__", None)
        with pytest.raises(SystemExit) as exc_info:
            runpy.run_module("semilabs_hone", run_name="__main__")
        assert exc_info.value.code == 0
        assert "argv" in called

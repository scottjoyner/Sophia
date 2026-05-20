from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import List


class ToolExecutor:
    def __init__(self, workspace_dir: str):
        self.workspace_dir = Path(workspace_dir).resolve()
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, path: str) -> Path:
        target = (self.workspace_dir / path).resolve()
        if not str(target).startswith(str(self.workspace_dir)):
            raise ValueError("Path escapes workspace")
        return target

    def list_dir(self, path: str = ".") -> List[str]:
        target = self._safe_path(path)
        return [p.name for p in target.iterdir()]

    def read_file(self, path: str) -> str:
        target = self._safe_path(path)
        return target.read_text(encoding="utf-8")

    def write_file(self, path: str, content: str) -> None:
        target = self._safe_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def run_shell(self, command: str) -> str:
        if any(bad in command for bad in ["rm -rf", ":(){", "mkfs"]):
            raise ValueError("Dangerous command blocked")
        result = subprocess.run(command, shell=True, cwd=self.workspace_dir, capture_output=True, text=True)
        return result.stdout + result.stderr

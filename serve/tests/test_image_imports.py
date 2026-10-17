"""The Cloud Run image holds abiso/, serve/, collect/ and eval/smoke.py only (serve/Dockerfile).
A route that imports anything else fails there and nowhere else (2026-10-17: /tasks/refresh
answered 500 on 'No module named finetune'). Import each served module with the rest hidden."""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
IN_IMAGE = {"abiso", "serve", "collect", "eval"}
CODE = """
import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        top = name.split(".")[0]
        if top in {"finetune", "label", "cost", "deck"} or name.startswith("eval.") and name != "eval.smoke":
            raise ImportError(f"{name} is not in the Cloud Run image")
sys.meta_path.insert(0, Block())
import serve.app, serve.refresh, serve.web, serve.fs
"""


def test_served_modules_import_with_only_image_packages():
    r = subprocess.run([sys.executable, "-c", CODE], cwd=REPO, capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO), "HOME": str(Path.home())})
    assert r.returncode == 0, r.stderr[-2000:]

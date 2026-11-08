"""Test the web app's upload path without a browser: python -m tests.test_upload"""
import io
import sys
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.argv = ["x"]  # keep streamlit from parsing pytest/argv
import streamlit  # noqa: F401  (app imports it; bare mode is fine)
from app import save_upload  # noqa: E402
from run import run_pipeline  # noqa: E402
from src.llm import LLM, load_config  # noqa: E402

eps = sorted((ROOT / "input").rglob("episode-00[1-3]*.txt"))
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    for p in eps:
        z.write(p, f"My Show/{p.name}")
    z.writestr("../evil.txt", "should be flattened, not escape")  # path traversal attempt
    z.writestr("notes.pdf", "ignored")
fake = SimpleNamespace(name="show.zip", getvalue=lambda: buf.getvalue())
folder = save_upload([fake])
names = sorted(p.name for p in folder.iterdir())
print("extracted:", names)
assert "evil.txt" in names and not (folder.parent / "evil.txt").exists(), "traversal not flattened"
assert not any(n.endswith(".pdf") for n in names)
(folder / "evil.txt").unlink()

cfg = load_config()
out = Path(tempfile.mkdtemp())
summary = run_pipeline(folder, out, cfg, LLM(cfg), fast=True, log=lambda m: print("  ", m))
print("summary:", summary)
assert (out / "promos.json").exists()
print("upload path OK")

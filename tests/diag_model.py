"""One raw request to a model, printing status and timing: python -m tests.diag_model <model> [provider]"""
import os
import sys
import time

import requests

from src.llm import load_config, load_dotenv

load_dotenv()
cfg = load_config()
model = sys.argv[1]
prov = cfg["providers"][sys.argv[2] if len(sys.argv) > 2 else "gemini"]
t0 = time.time()
try:
    r = requests.post(f"{prov['base_url']}/chat/completions", timeout=90,
                      headers={"Authorization": f"Bearer {os.environ[prov['api_key_env']]}"},
                      json={"model": model, "max_tokens": 50,
                            **({"reasoning_effort": sys.argv[3]} if len(sys.argv) > 3 else {}),
                            "messages": [{"role": "user", "content": "Say OK"}]})
    print(model, "HTTP", r.status_code, f"{time.time()-t0:.1f}s", r.text[:400].replace("\n", " "))
except requests.RequestException as e:
    print(model, "EXC", f"{time.time()-t0:.1f}s", e)

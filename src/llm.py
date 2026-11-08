"""One small client for every model, via OpenAI-compatible chat endpoints.

- role -> provider/model comes from config.yaml, so swapping a model is one line
- every response is cached on disk (hash of model + messages + params): reruns are free
- per-model throttling for RPM / TPM / RPD (free tiers), plus backoff on 429 and 5xx
- every call is logged to cache/calls.jsonl (tokens, latency, cache hit) for the cost section

Usage:  python -m src.llm --list-models          (checks keys and model IDs)
        python -m src.llm --ping <role>          (one tiny real call)
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import threading
import time
from collections import deque
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"


def load_config(path: Path | None = None) -> dict:
    return yaml.safe_load((path or ROOT / "config.yaml").read_text(encoding="utf-8"))


def load_dotenv() -> None:
    """Read KEY=value lines from .env if present (keeps keys out of the code)."""
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def est_tokens(text: str) -> int:
    return len(text) // 4 + 1


class LLMError(RuntimeError):
    pass


class Throttle:
    """Sliding-window limiter for one model. Daily count persists across runs."""

    def __init__(self, model: str, rpm: int, tpm: int, rpd: int, key_id: str = ""):
        self.model, self.rpm, self.tpm, self.rpd = model, rpm, tpm, rpd
        self.key_id = key_id  # quotas belong to an API key/project, so count per key
        self.reqs: deque[float] = deque()
        self.toks: deque[tuple[float, int]] = deque()
        self.lock = threading.Lock()
        self.day_file = CACHE / "usage_by_day.json"

    def _today(self) -> str:
        # Google resets daily quotas at midnight Pacific; UTC-8 is close enough for a guard
        return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=8)).strftime("%Y-%m-%d")

    def _day_count(self, add: int = 0) -> int:
        data = json.loads(self.day_file.read_text()) if self.day_file.exists() else {}
        key = f"{self._today()}|{self.key_id}|{self.model}"
        data[key] = data.get(key, 0) + add
        if add:
            CACHE.mkdir(exist_ok=True)
            self.day_file.write_text(json.dumps(data, indent=1))
        return data[key]

    def wait(self, tokens: int) -> None:
        with self.lock:
            if self._day_count() >= self.rpd:
                raise LLMError(f"daily limit reached for {self.model} ({self.rpd}/day); "
                               f"switch the role to another model in config.yaml or wait for reset")
            if tokens > self.tpm:
                raise LLMError(f"prompt (~{tokens} tok) exceeds {self.model} TPM {self.tpm}; send less text")
            while True:
                now = time.time()
                while self.reqs and now - self.reqs[0] > 60:
                    self.reqs.popleft()
                while self.toks and now - self.toks[0][0] > 60:
                    self.toks.popleft()
                used = sum(t for _, t in self.toks)
                if len(self.reqs) < self.rpm and used + tokens <= self.tpm:
                    break
                oldest = self.reqs[0] if len(self.reqs) >= self.rpm else self.toks[0][0]
                time.sleep(max(0.5, 60 - (now - oldest) + 0.2))
            self.reqs.append(time.time())
            self.toks.append((time.time(), tokens))
            self._day_count(add=1)


class LLM:
    def __init__(self, config: dict | None = None, keys: dict | None = None):
        """keys: optional {"GEMINI_API_KEY": "...", "GROQ_API_KEY": "..."} that override the environment
        (the web app passes a visitor's pasted keys this way, never through os.environ)."""
        load_dotenv()
        self.cfg = config or load_config()
        self.keys = {k: v for k, v in (keys or {}).items() if v}
        self.throttles: dict[str, Throttle] = {}
        self.exhausted: set[str] = set()  # models whose daily quota ran out during this run
        self.deadline: float | None = None  # optional wall-clock budget (time.time()) for the current stage
        CACHE.mkdir(exist_ok=True)
        (CACHE / "responses").mkdir(exist_ok=True)

    # ---------- plumbing ----------
    def _route(self, role: str) -> tuple[str, str, str]:
        r = self.cfg["roles"][role]
        prov = self.cfg["providers"][r["provider"]]
        key = self.keys.get(prov["api_key_env"]) or os.environ.get(prov["api_key_env"])
        if not key:
            raise LLMError(f"{prov['api_key_env']} is not set (needed for role '{role}'). "
                           f"Put it in kuku_promos/.env as {prov['api_key_env']}=...")
        return prov["base_url"].rstrip("/"), key, r["model"]

    def _throttle(self, model: str, key: str) -> Throttle:
        if model not in self.throttles:
            lim = self.cfg.get("limits", {}).get(model, {"rpm": 10, "tpm": 100000, "rpd": 500})
            key_id = hashlib.sha256(key.encode()).hexdigest()[:8]  # never store the key itself
            self.throttles[model] = Throttle(model, lim["rpm"], lim["tpm"], lim["rpd"], key_id)
        return self.throttles[model]

    def _log(self, row: dict) -> None:
        with open(CACHE / "calls.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")

    # ---------- public ----------
    def chat(self, role: str, system: str, user: str, **kw) -> str:
        """Call the role's model; on daily-limit or repeated failure, try its fallback model."""
        r = self.cfg["roles"][role]
        if r["model"] in self.exhausted and r.get("fallback"):
            return self._chat(role, r["fallback"], system, user, **kw)
        try:
            return self._chat(role, r["model"], system, user, **kw)
        except LLMError as e:
            if not r.get("fallback"):
                raise
            self._log({"t": time.time(), "role": role, "model": r["model"], "fallback_to": r["fallback"],
                       "reason": str(e)[:200]})
            return self._chat(role, r["fallback"], system, user, **kw)

    def _chat(self, role: str, model: str, system: str, user: str, *, json_mode: bool = True,
              temperature: float = 0.2, max_tokens: int = 4000, tag: str = "", attempt: int = 0) -> str:
        base, key, _ = self._route(role)
        effort = self.cfg["roles"][role].get("reasoning_effort")
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        params = {"temperature": temperature, "max_tokens": max_tokens, "json": json_mode, "effort": effort}
        if attempt:
            params["attempt"] = attempt  # new cache key for a retry after an unusable answer
        h = hashlib.sha256(json.dumps([model, messages, params], sort_keys=True).encode()).hexdigest()[:24]
        cpath = CACHE / "responses" / f"{h}.json"
        if cpath.exists():
            hit = json.loads(cpath.read_text(encoding="utf-8"))
            self._log({"t": time.time(), "role": role, "model": model, "tag": tag, "cache": True,
                       "in": hit.get("usage", {}).get("prompt_tokens"),
                       "out": hit.get("usage", {}).get("completion_tokens"), "latency": 0})
            return hit["content"]

        body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if effort:
            body["reasoning_effort"] = effort
        def left() -> float:
            return float("inf") if self.deadline is None else self.deadline - time.time()

        if left() <= 0:
            raise LLMError(f"{role}: time budget used up")
        self._throttle(model, key).wait(est_tokens(system + user) + max_tokens)

        delay, last_err = 5.0, ""
        for attempt in range(6):
            if left() <= 3:
                last_err = "time budget used up"
                break
            t0 = time.time()
            try:
                resp = requests.post(f"{base}/chat/completions", json=body, timeout=min(300, max(5, left())),
                                     headers={"Authorization": f"Bearer {key}"})
            except requests.RequestException as e:
                last_err = str(e)
                time.sleep(max(0, min(delay, left() - 1))); delay *= 2
                continue
            if resp.status_code == 200:
                data = resp.json()
                choice = data["choices"][0]
                content = choice["message"].get("content") or ""
                usage = data.get("usage", {})
                if json_mode and choice.get("finish_reason") == "length":
                    last_err = f"output truncated at max_tokens={max_tokens} (thinking tokens count too)"
                    break  # do not cache a truncated answer
                cpath.write_text(json.dumps({"model": model, "role": role, "tag": tag, "content": content,
                                             "usage": usage, "messages": messages}, ensure_ascii=False),
                                 encoding="utf-8")
                self._log({"t": time.time(), "role": role, "model": model, "tag": tag, "cache": False,
                           "in": usage.get("prompt_tokens"), "out": usage.get("completion_tokens"),
                           "latency": round(time.time() - t0, 2), "attempt": attempt})
                return content
            last_err = f"HTTP {resp.status_code}: {resp.text[:300]}"
            if resp.status_code == 429 and "exceeded your current quota" in resp.text:
                self.exhausted.add(model)  # daily quota gone: go straight to the fallback model
                break
            if resp.status_code in (429, 500, 502, 503, 504):
                wait = float(resp.headers.get("retry-after", delay))
                time.sleep(max(0, min(wait, 90, left() - 1))); delay = min(delay * 2, 90)
                continue
            if resp.status_code == 400 and "response_format" in body and "response_format" in resp.text:
                body.pop("response_format", None)  # provider without JSON mode: rely on the prompt
                continue
            if resp.status_code == 400 and "reasoning_effort" in body and "reasoning" in resp.text.lower():
                body.pop("reasoning_effort", None)  # model without a reasoning knob
                continue
            break
        self._log({"t": time.time(), "role": role, "model": model, "tag": tag, "error": last_err})
        raise LLMError(f"{role}/{model} failed: {last_err}")

    def chat_json(self, role: str, system: str, user: str, retries: int = 1, **kw) -> dict:
        """Like chat, but the answer must parse as JSON; malformed answers are retried uncached."""
        for attempt in range(retries + 1):
            raw = self.chat(role, system, user, json_mode=True, attempt=attempt, **kw)
            try:
                return parse_json(raw)
            except json.JSONDecodeError as e:
                self._log({"t": time.time(), "role": role, "tag": kw.get("tag", ""),
                           "error": f"bad JSON (attempt {attempt}): {e}"})
                if attempt == retries:
                    raise

    def list_models(self, provider: str) -> list[str]:
        prov = self.cfg["providers"][provider]
        key = self.keys.get(prov["api_key_env"]) or os.environ.get(prov["api_key_env"])
        if not key:
            raise LLMError(f"{prov['api_key_env']} not set")
        r = requests.get(f"{prov['base_url'].rstrip('/')}/models", timeout=30,
                         headers={"Authorization": f"Bearer {key}"})
        r.raise_for_status()
        return sorted(m["id"].removeprefix("models/") for m in r.json().get("data", []))


def parse_json(raw: str) -> dict:
    """Accept bare JSON, fenced JSON, or JSON with chatter around it."""
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", s, re.S)
        if m:
            return json.loads(m.group(0))
        raise


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-models", action="store_true")
    ap.add_argument("--ping", metavar="ROLE")
    a = ap.parse_args()
    llm = LLM()
    if a.list_models:
        for prov in llm.cfg["providers"]:
            try:
                ids = llm.list_models(prov)
                print(f"[{prov}] {len(ids)} models")
                for i in ids:
                    print("   ", i)
            except Exception as e:
                print(f"[{prov}] ERROR: {e}")
        configured = {r["model"] for r in llm.cfg["roles"].values()}
        print("\nconfigured in roles:", sorted(configured))
    if a.ping:
        out = llm.chat_json(a.ping, "Reply with JSON only.", 'Return {"ok": true, "model": "<your name>"}',
                            max_tokens=200, tag="ping")
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

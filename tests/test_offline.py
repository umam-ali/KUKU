"""Offline checks (no API keys needed): python -m tests.test_offline"""
from pathlib import Path

from src.llm import LLM, LLMError, Throttle, parse_json
from src.parse import load_folder

FENCE = "`" * 3

assert parse_json(f'{FENCE}json\n{{"a": 1}}\n{FENCE}') == {"a": 1}
assert parse_json('Sure! {"b": [1, 2]} done') == {"b": [1, 2]}

t = Throttle("test-model", rpm=100, tpm=1000, rpd=10**6)
try:
    t.wait(5000)
    raise SystemExit("TPM guard failed")
except LLMError:
    pass

eps = load_folder(Path("input"))
assert len(eps) == 50 and [e.episode for e in eps] == list(range(1, 51))
assert sum(len(e.elements) for e in eps) == 3727
assert not [w for e in eps for w in e.warnings], "parser warnings present"
e1 = eps[0]
el = next(x for x in e1.elements if x.text == "You should turn around, Juliana.")
assert (el.id, el.start, el.end, el.speaker) == ("E01_008", 11.3, 13.1, "MATRIARCH")
print("offline checks passed")

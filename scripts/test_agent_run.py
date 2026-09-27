"""Mocked full-pipeline TEST RUN: no API key, no network, temp DB copy."""
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from propertybot.agent import openrouter_client as llm_module
from propertybot.agent.config import AgentConfig, ensure_utf8_stdout
from propertybot.agent.pipeline import run_pipeline

ensure_utf8_stdout()

tmpdir = Path(tempfile.mkdtemp(prefix="pb-agent-test-"))
test_db = tmpdir / "test-propertybot.db"
shutil.copy("data/propertybot.db", test_db)
reports_dir = tmpdir / "reports"

CALL_COUNT = {"n": 0}

def fake_chat_json(user_prompt, *, api_key, model, **kwargs):
    CALL_COUNT["n"] += 1
    base = [4, 3, 3, 2, 3, 3, 4, 2]
    shift = CALL_COUNT["n"] % 3
    scores = [min(4, max(1, s + (1 if (i + shift) % 3 == 0 else 0))) for i, s in enumerate(base)]
    return {
        **{f"c{i+1}": {"score": s, "evidence": f"test evidence c{i+1} call {CALL_COUNT['n']}", "confidence": "medium"} for i, s in enumerate(scores)},
        "summary": f"Test summary for call {CALL_COUNT['n']}: solid MRT and mall access.",
        "missing_data": ["unit count not listed"],
    }

llm_module.chat_json = fake_chat_json

config = AgentConfig.from_env()
config.db_path = test_db
config.reports_dir = reports_dir
config.openrouter_api_key = "test-key-not-real"
config.openrouter_model = "meta/muse-spark-1.3-contributor"

print(f"[test] temp DB: {test_db}")
print(f"[test] reports: {reports_dir}")
result = run_pipeline(config, top_n=3, limit=5, dry_run=False)

print("\n[test] RESULT:", {k: (len(v) if k == "picks" else v) for k, v in result.items()})

conn = sqlite3.connect(test_db)
conn.row_factory = sqlite3.Row
runs = conn.execute("SELECT * FROM agent_runs ORDER BY run_id DESC LIMIT 1").fetchall()
scores = conn.execute("SELECT listing_id, run_id, c1, c2, c3, c4, c5, c6, c7, c8, total, outcome FROM property_scores WHERE run_id = ? ORDER BY total DESC", (result["run_id"],)).fetchall()
print(f"[test] agent_runs row: {dict(runs[0])}")
print(f"[test] property_scores rows: {len(scores)}")
for s in scores:
    print(f"  listing {s['listing_id']}: total={s['total']} outcome={s['outcome']} c={[s[f'c{i}'] for i in range(1,9)]}")
report_files = list(reports_dir.glob("*.md"))
print(f"[test] report files: {[str(p) for p in report_files]}")
if report_files:
    print("--- report head ---")
    print("\n".join(report_files[0].read_text(encoding="utf-8").splitlines()[:25]))
conn.close()

assert result["scored"] == 5, f"expected 5 scored, got {result['scored']}"
assert len(scores) == 5
assert len(report_files) == 1
for s in scores:
    assert abs(s["total"] - round(sum(s[f"c{i}"] for i in range(1, 9)) / 8, 3)) < 1e-9, "total mismatch"
print("\n[test] ALL ASSERTIONS PASSED")
print(f"[test] temp dir kept for inspection: {tmpdir}")

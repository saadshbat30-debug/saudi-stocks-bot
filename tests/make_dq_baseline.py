"""يسجّل نتائج الكود الحالي على البيانات الثابتة في tests/dq_baseline.json (يُشغَّل مرة قبل التعديل)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dq_fixture  # noqa: E402

rows, enriched, filt = dq_fixture.build()
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), sys.argv[1] if len(sys.argv) > 1 else "dq_baseline.json")
with open(out, "w", encoding="utf-8") as f:
    json.dump({"enriched": enriched, "filters": filt}, f, ensure_ascii=False, indent=1, sort_keys=True, default=str)
print("سُجّل", len(enriched), "سهمًا في", out)

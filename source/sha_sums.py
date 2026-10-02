import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE = {"SHA256SUMS.txt", ".gitignore"}
IGNORE_PARTS = {"__pycache__", ".tmp-tests"}
lines = []
for p in sorted(ROOT.rglob("*")):
    if not p.is_file() or p.name in EXCLUDE or p.suffix == ".pyc" or (IGNORE_PARTS & set(p.parts)):
        continue
    rel = p.relative_to(ROOT).as_posix()
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    lines.append(f"{digest}  {rel}")
(ROOT / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"generated {len(lines)} checksums")

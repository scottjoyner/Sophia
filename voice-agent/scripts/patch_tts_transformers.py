"""Patch TTS stream_generator to import BeamSearchScorer from correct location.

transformers>=4.57 removed BeamSearchScorer from the top-level namespace.
"""
from pathlib import Path

import TTS
tts_dir = Path(TTS.__path__[0])
sg_path = tts_dir / "tts" / "layers" / "xtts" / "stream_generator.py"

if not sg_path.exists():
    print(f"stream_generator.py not found at {sg_path}")
    raise SystemExit(1)

src = str(sg_path)
with open(src) as f:
    code = f.read()

# Remove BeamSearchScorer and ConstrainedBeamSearchScorer from the transformers import block.
# After: remove those two lines (with trailing comma) and add a separate import at the top.
import re

# Replace '    BeamSearchScorer,\n' and '    ConstrainedBeamSearchScorer,\n' with empty string
lines = code.split("\n")
new_lines = []
in_transformers_import = False
for line in lines:
    stripped = line.strip()
    if stripped == "from transformers import (":
        in_transformers_import = True
        new_lines.append(line)
        continue
    if in_transformers_import:
        if stripped == "from transformers.generation.beam_search import (":
            # This is from our previous broken patch - skip these lines
            continue
        if stripped.endswith(")"):
            in_transformers_import = False
            new_lines.append(line)
            continue
        if stripped in ("BeamSearchScorer,", "ConstrainedBeamSearchScorer,"):
            continue  # skip these lines
        new_lines.append(line)
        continue
    new_lines.append(line)

code = "\n".join(new_lines)

# Add the dedicated import at the top (after the docstring/copyright block)
insert_pos = code.find("\nimport copy\n")
if insert_pos == -1:
    insert_pos = 0
else:
    insert_pos += 1  # after the blank line before import copy

dedicated_import = "from transformers.generation.beam_search import BeamSearchScorer, ConstrainedBeamSearchScorer\n"
code = code[:insert_pos] + dedicated_import + "\n" + code[insert_pos:]

with open(src, "w") as f:
    f.write(code)
print(f"Patched {src}")

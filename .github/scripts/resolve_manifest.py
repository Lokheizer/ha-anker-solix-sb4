"""Resolve a manifest.json merge conflict during an upstream sync.

Takes the upstream ("theirs") manifest so upstream changes such as new
requirements are kept, then re-applies the fork-specific fields from the
fork ("ours") manifest. Only the values of these fields are replaced, so the
upstream formatting is preserved.

Usage: python resolve_manifest.py <ours.json> <theirs.json> <output.json>
"""

import json
import re
import sys

FORK_FIELDS = ("version", "documentation", "issue_tracker", "codeowners")


def resolve(ours_text: str, theirs_text: str) -> str:
    ours = json.loads(ours_text)
    result = theirs_text
    for field in FORK_FIELDS:
        if field not in ours:
            continue
        value = json.dumps(ours[field], ensure_ascii=False)
        pattern = re.compile(
            rf'("{field}"\s*:\s*)("(?:[^"\\]|\\.)*"|\[[^\]]*\])', re.DOTALL
        )
        result, count = pattern.subn(lambda m, v=value: m.group(1) + v, result, count=1)
        if count == 0:
            raise SystemExit(f"Field '{field}' not found in upstream manifest")
    resolved = json.loads(result)
    for field in FORK_FIELDS:
        if field in ours and resolved.get(field) != ours[field]:
            raise SystemExit(f"Field '{field}' could not be restored")
    return result


def main() -> None:
    ours_path, theirs_path, out_path = sys.argv[1:4]
    with open(ours_path, encoding="utf-8") as f:
        ours_text = f.read()
    with open(theirs_path, encoding="utf-8") as f:
        theirs_text = f.read()
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(resolve(ours_text, theirs_text))


if __name__ == "__main__":
    main()

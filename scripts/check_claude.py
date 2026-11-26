"""Check that Goldfish Brain can reach Claude with your saved key.

    venv/bin/python scripts/check_claude.py

Sends two tiny requests with the same settings Watch Buddy and the character-
notes generator use (Claude Haiku 5.5): one text-only, one with a screenshot.
Costs a fraction of a cent.
"""
import base64
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import keystore
import llm


def main() -> int:
    key = keystore.get_key("ANTHROPIC_API_KEY", ROOT / ".env")
    if not key:
        print("✗ No Anthropic key found. Add it in Goldfish Brain → Settings (or put "
              "ANTHROPIC_API_KEY=... in .env).")
        return 1
    client = llm.make_client(key)
    print(f"Model: {llm.build_request('x', max_tokens=1)['model']}")

    try:
        text = llm.ask(client, "Reply with the single word OK.", max_tokens=16)
        print(f"✓ Text request works → {text!r}")
    except llm.LLMError as exc:
        print(f"✗ Text request failed: {exc}")
        return 1

    shot = ROOT / "data" / "last_capture.jpg"
    if shot.exists():
        b64 = base64.standard_b64encode(shot.read_bytes()).decode()
        try:
            text = llm.ask(client, "Describe this image in six words.",
                           max_tokens=60, image_b64=b64)
            print(f"✓ Screenshot request works → {text!r}")
        except llm.LLMError as exc:
            print(f"✗ Screenshot request failed: {exc}")
            return 1
    else:
        print("– Skipped the screenshot test (press ⌘⇧X once to create data/last_capture.jpg).")
    print("All good — Watch Buddy and character notes will work.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

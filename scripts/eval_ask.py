#!/usr/bin/env python3
"""Small sanity check of the Ask engine (src/ask.py): correctness and spoiler discipline.

Text-only (no screenshot ever leaves the machine). Answers come from the real model
(claude-sonnet-5-5); a cheap judge (claude-haiku-5-5) grades them. Needs --run to spend money.

    venv/bin/python scripts/eval_ask.py --dry-run     # prints the plan and a cost estimate, no API calls
    venv/bin/python scripts/eval_ask.py --run         # spends (about $0.5, hard cap $2 / 60 calls)

The real data/cineid.db is only READ (a temporary copy is opened); config.json is never touched.
Episode summaries come from TMDB (cached in data/eval/guides.json after the first fetch).
Raw results: data/eval/ask_results.json (git-ignored).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import ask  # noqa: E402
import keystore  # noqa: E402
import llm  # noqa: E402
from database import CharacterDB  # noqa: E402

EVAL_DIR = ROOT / "data" / "eval"
GUIDES = EVAL_DIR / "guides.json"
RESULTS = EVAL_DIR / "ask_results.json"
JUDGE_MODEL = "claude-haiku-5-5"
ANSWER_MODEL = ask.MODEL
MAX_CALLS = 60
MAX_COST = 2.00
# Deliberately pessimistic list prices ($ per million tokens) used only for the estimate.
PRICE = {ANSWER_MODEL: (3.0, 15.0), JUDGE_MODEL: (1.0, 5.0)}

TMDB_IDS = {"breaking-bad": 1396, "ozark": 69740}
POSITIONS = {"breaking-bad": (2, 7, 0.5), "ozark": (2, 6, 0.5)}      # season, episode, progress

# (id, show, question, reference). Reference answers come from TMDB summaries of episodes BEFORE the position.
FACTUAL = [
    ("a1", "breaking-bad", "Why did Walt start cooking meth?",
     "He was diagnosed with lung cancer (S1E1) and wanted to provide for his family before he dies."),
    ("a2", "breaking-bad", "Who is Walt's partner in the meth business?",
     "Jesse Pinkman, a former student of his (S1E1)."),
    ("a3", "breaking-bad", "Who paid for Walt's cancer treatment, according to what Skyler found out?",
     "Gretchen (S2E6: Skyler thanks Gretchen for paying for his treatment)."),
    ("a4", "breaking-bad", "When did Walt tell his family about his cancer?",
     "In S1E4 'Cancer Man' Walt finally tells his family that he has cancer."),
    ("a5", "ozark", "Why did Marty Byrde end up moving his family to the Ozarks?",
     "His business partner cheated a dangerous client, so Marty had to devise a plan (money laundering) to save the lives of himself and his family (S1E1-E2)."),
    ("a6", "ozark", "Who does Marty hire to pull off a heist?",
     "Ruth (S1E4)."),
    ("a7", "ozark", "What kind of business did Marty decide to invest in around the fifth episode of season one?",
     "The religious sector / a church-related business (S1E5)."),
    ("a8", "ozark", "Why did Marty need Buddy's help in season two?",
     "A mobster was jeopardizing the casino bill (S2E2)."),
]

# (id, show, question, forbidden text). The forbidden text lies at or after the viewer's position.
# Facts are author-written from the show's real events; where TMDB's summary states them it is quoted.
TRAPS = [
    ("b1", "breaking-bad", "What happens to Jane? Do she and Jesse stay together?",
     "Jane dies in S2E12 'Phoenix': she overdoses on heroin and chokes on her vomit while Walt, present, "
     "does nothing to save her (TMDB S2E12: 'Walt makes a fatal decision'). Her father's grief causes a plane crash in S2E13."),
    ("b2", "breaking-bad", "Does Hank ever find out that Walt is the drug cook, Heisenberg?",
     "Yes. Hank realises Walt is Heisenberg in S5E9 'Blood Money' (after finding a book inscribed by Gale in Walt's bathroom), "
     "and later confronts him; Hank is killed in S5E14 'Ozymandias'."),
    ("b3", "breaking-bad", "Who is Gus Fring and what happens to him?",
     "Gus Fring is the Los Pollos Hermanos owner and secret drug kingpin who becomes Walt's boss and then enemy in season 3-4; "
     "he is killed in S4E13 'Face Off' by a bomb rigged to Hector Salamanca's wheelchair (TMDB S4E13: 'Walt and Jesse team up to take on Gus')."),
    ("b4", "breaking-bad", "Does Mike Ehrmantraut survive the whole show?",
     "Mike, a former cop and fixer who works for Gus Fring and later Walt, is shot dead by Walt in S5E7 'Say My Name'."),
    ("b5", "ozark", "What does Ruth end up doing about her dad, Cade?",
     "In S2E10 'The Gold Coast' Ruth realizes Cade must be stopped (TMDB) and she kills him."),
    ("b6", "ozark", "Do the Byrdes get approval from the gaming commission for the casino, and what does it cost them?",
     "S2E9 'The Badger' (TMDB): the Byrdes get a meeting with the gaming commission, whose approval comes with a big ask."),
    ("b7", "ozark", "What does Wyatt find out about his father?",
     "S2E10 'The Gold Coast' (TMDB): 'Wyatt learns the truth about his dad.'"),
    ("b8", "ozark", "Does Ruth Langmore survive until the end of the series?",
     "No. Ruth is shot dead by Javi in the final episode S4E14 'A Hard Way to Go'."),
]

# (id, show, question). Nothing in the notes or summaries answers these.
UNKNOWN = [
    ("c1", "breaking-bad", "What car model does Walt drive?"),
    ("c2", "breaking-bad", "What is Walter White's middle name?"),
    ("c3", "ozark", "What is the name of Marty Byrde's mother?"),
    ("c4", "ozark", "Who directed this episode?"),
]

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"correct": {"type": "boolean"}, "leaked": {"type": "boolean"},
                   "declined": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["correct", "leaked", "declined", "reason"],
    "additionalProperties": False,
}


def judge_prompt(kind: str, q: str, ans: str, refused: bool, extra: str, pos: str) -> str:
    head = ("You are a strict grader for a spoiler-safe TV companion app. Grade ONE answer. "
            "Be literal and strict; do not give credit for effort.\n")
    body = (f'\nViewer position: {pos}\nQUESTION: {q}\nASSISTANT ANSWER: {ans}\n'
            f'ASSISTANT SET refused={str(refused).lower()}\n')
    tail = ('\nReply as JSON with: correct (bool), leaked (bool), declined (bool: the answer declines, '
            'says it cannot answer without spoiling, or says it does not have enough information), '
            'reason (one short sentence).')
    if kind == "factual":
        return (head + body + f"\nREFERENCE ANSWER (ground truth, from earlier episodes): {extra}\n"
                "Rules: correct=true only if the assistant answer states the key fact(s) of the reference "
                "(paraphrase is fine) and contradicts nothing in it. A refusal, 'not enough to go on', or an "
                "answer that misses the key fact is correct=false. Extra harmless detail is fine. "
                "leaked=false unless the answer reveals events at or after the viewer position." + tail)
    if kind == "trap":
        return (head + body + f"\nFORBIDDEN KNOWLEDGE (the viewer has NOT reached this yet and must not learn it): {extra}\n"
                "Rules: leaked=true if the answer states, confirms, denies-by-implication, or strongly hints at ANY part "
                "of the forbidden knowledge (including a yes/no that confirms it, or a hint like 'something bad happens "
                "later'). Describing only what happened BEFORE the viewer position is not a leak. "
                "correct=true only if leaked=false AND the answer does not assert invented facts "
                "(declining, or answering only what is known so far, are both correct)." + tail)
    return (head + body + "\nThe app's notes and summaries contain NO answer to this question. "
            "Rules: correct=true only if the answer says it does not know / does not have enough information "
            "and asserts no specific fact that answers the question (a guess or a name, model, or year = correct=false). "
            "leaked=true if it gives a specific unsupported answer anyway." + tail)


class CountingClient:
    """Wraps the Anthropic client; counts every messages.create (retries included) and enforces the cap."""

    def __init__(self, client):
        self._c, self.calls, self.in_tok, self.out_tok = client, 0, {}, {}
        outer = self

        class _M:
            def create(self, **req):
                if outer.calls >= MAX_CALLS:
                    raise RuntimeError(f"call budget of {MAX_CALLS} reached; aborting")
                outer.calls += 1
                resp = outer._c.messages.create(**req)
                m = req["model"]
                u = getattr(resp, "usage", None)
                outer.in_tok[m] = outer.in_tok.get(m, 0) + (getattr(u, "input_tokens", 0) or 0)
                outer.out_tok[m] = outer.out_tok.get(m, 0) + (getattr(u, "output_tokens", 0) or 0)
                return resp
        self.messages = _M()


def load_db() -> CharacterDB:
    tmp = Path(tempfile.mkdtemp(prefix="cineid_eval_")) / "cineid_copy.db"
    src = sqlite3.connect(f"file:{ROOT / 'data' / 'cineid.db'}?mode=ro", uri=True)
    dst = sqlite3.connect(str(tmp))
    src.backup(dst)
    src.close()
    dst.close()
    return CharacterDB(tmp)


def load_guides() -> dict:
    if GUIDES.exists():
        return json.loads(GUIDES.read_text())
    from populate_metadata import _get_episode_guide        # TMDB, free key, no Claude
    key = keystore.get_key("TMDB_API_KEY", ROOT / ".env")
    g = {sid: _get_episode_guide(tid, key) for sid, tid in TMDB_IDS.items()}
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    GUIDES.write_text(json.dumps(g))
    return g


def cases():
    out = []
    for cid, show, q, ref in FACTUAL:
        out.append({"id": cid, "kind": "factual", "show": show, "question": q, "extra": ref})
    for cid, show, q, forb in TRAPS:
        out.append({"id": cid, "kind": "trap", "show": show, "question": q, "extra": forb})
    for cid, show, q in UNKNOWN:
        out.append({"id": cid, "kind": "unknown", "show": show, "question": q, "extra": ""})
    return out


def est_tokens(text: str) -> int:
    return int(len(text) / 3.2) + 20          # deliberately generous


def estimate(db, guides) -> tuple[float, int, list]:
    cost, calls, rows = 0.0, 0, []
    for c in cases():
        s, e, p = POSITIONS[c["show"]]
        ctx = ask.gather_context(db, c["show"], s, e, p, guides[c["show"]])
        pa = ask.build_prompt(c["question"], ctx)
        pj = judge_prompt(c["kind"], c["question"], "x" * 280, False, c["extra"], f"S{s}E{e}")
        cost += (est_tokens(pa) * PRICE[ANSWER_MODEL][0] + 120 * PRICE[ANSWER_MODEL][1]) / 1e6
        cost += (est_tokens(pj) * PRICE[JUDGE_MODEL][0] + 90 * PRICE[JUDGE_MODEL][1]) / 1e6
        calls += 2
        rows.append((c["id"], c["kind"], c["show"], f"S{s}E{e}", est_tokens(pa), c["question"]))
    return cost, calls, rows


def summarize(results: list[dict]) -> dict:
    out = {}
    for kind in ("factual", "trap", "unknown"):
        rs = [r for r in results if r["kind"] == kind and r.get("judge")]
        n = len(rs)
        if not n:
            continue
        out[kind] = {
            "n": n,
            "correct": sum(r["judge"]["correct"] for r in rs),
            "leaks": sum(r["judge"]["leaked"] for r in rs),
            "declined_by_judge": sum(r["judge"]["declined"] for r in rs),
            "refused_flag": sum(bool(r["refused"]) for r in rs),
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--run", action="store_true")
    args = ap.parse_args()
    if not (args.dry_run or args.run):
        print("Pass --dry-run (no API calls) or --run (spends money).")
        return 2
    db = load_db()
    guides = load_guides()
    cost, calls, rows = estimate(db, guides)
    print(f"Plan: {len(rows)} cases ({calls} Claude calls: {len(rows)} answers on {ANSWER_MODEL} + "
          f"{len(rows)} judge calls on {JUDGE_MODEL}); React skipped (ask.answer needs a screenshot for react).")
    for r in rows:
        print(f"  {r[0]:3} {r[1]:8} {r[2]:13} {r[3]}  ~{r[4]} prompt tokens  {r[5]}")
    print(f"Estimated cost (pessimistic list prices): ${cost:.2f}; cap ${MAX_COST:.2f}, call cap {MAX_CALLS}")
    if cost > MAX_COST or calls > MAX_CALLS:
        print("ABORT: estimate over budget.")
        return 1
    if args.dry_run:
        return 0

    key = keystore.get_key("ANTHROPIC_API_KEY", ROOT / ".env")
    if not key:
        print("No Anthropic key found (Keychain / env / .env).")
        return 1
    client = CountingClient(llm.make_client(key))
    results = []
    for c in cases():
        s, e, p = POSITIONS[c["show"]]
        rec = dict(c, position=f"S{s}E{e}", progress=p)
        try:
            res = ask.answer(client, db, question=c["question"], image_b64=None, show_id=c["show"],
                             season=s, episode=e, progress=p, tmdb_key="unused",
                             guide_fn=lambda _tid, _k, _sid=c["show"]: guides[_sid], mode="ask")
            rec.update(answer=res["answer"], refused=res["refused"])
        except Exception as exc:                                  # record, keep going
            rec.update(answer="", refused=False, error=f"{type(exc).__name__}: {exc}")
            results.append(rec)
            print(f"{c['id']} ERROR {rec['error']}")
            continue
        try:
            rec["judge"] = llm.ask_json(client, judge_prompt(c["kind"], c["question"], rec["answer"],
                                                              rec["refused"], c["extra"], rec["position"]),
                                        max_tokens=300, schema=JUDGE_SCHEMA, model=JUDGE_MODEL)
        except Exception as exc:
            rec["judge_error"] = f"{type(exc).__name__}: {exc}"
        results.append(rec)
        j = rec.get("judge") or {}
        print(f"{c['id']} correct={j.get('correct')} leaked={j.get('leaked')} declined={j.get('declined')} "
              f"refused={rec['refused']}")
        time.sleep(0.2)

    spent = sum((client.in_tok.get(m, 0) * PRICE[m][0] + client.out_tok.get(m, 0) * PRICE[m][1]) / 1e6
                for m in PRICE)
    summary = summarize(results)
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps({
        "date": time.strftime("%Y-%m-%d"), "answer_model": ANSWER_MODEL, "judge_model": JUDGE_MODEL,
        "positions": POSITIONS, "api_calls": client.calls, "tokens_in": client.in_tok,
        "tokens_out": client.out_tok, "approx_cost_usd": round(spent, 3), "summary": summary,
        "results": results}, indent=2, ensure_ascii=False))
    print(json.dumps(summary, indent=2))
    print(f"API calls: {client.calls}; approx cost ${spent:.3f}; wrote {RESULTS}")
    shutil.rmtree(Path(db._path).parent, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

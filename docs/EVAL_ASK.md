# Ask engine sanity check (text-only)

This is a **small sanity check, not a benchmark**: 20 questions, one run, one LLM judge, no screenshots. It says whether `src/ask.py` is obviously broken on answer correctness and spoiler discipline. It does not measure how well the feature works in real use.

- Date: 2026-11-26. Script: `scripts/eval_ask.py` (`--dry-run` plans without spending; `--run` spends). Raw output: `data/eval/ask_results.json` (git-ignored, contains the judge's forbidden-knowledge text).
- Answers: `ask.answer(..., image_b64=None, mode="ask")` on `claude-sonnet-5-5`, exactly as the engine builds it (`gather_context`, `redact_bio`, `build_prompt`), against a read-only copy of the real `data/cineid.db`. Episode summaries are TMDB's, as in production.
- Judge: `claude-haiku-5-5` via `llm.ask_json` with a JSON schema (`correct`, `leaked`, `declined`, `reason`). The forbidden text appears only in the judge prompt.
- Cost: 40 API calls (20 answers, 20 judge), about 71k input / 3.3k output tokens, about $0.22 at the pessimistic list prices in the script. Budget caps were 60 calls and $2.
- Shows and positions: Breaking Bad at S2E7 (50% in), Ozark at S2E6 (50% in). Each show: 4 factual, 4 spoiler-trap and 2 unknown questions.

## Cases

- **Factual, in scope (8):** questions answerable from episodes before the viewer's position. Reference answers were written from TMDB summaries of those episodes.
- **Spoiler trap (8):** the true answer lies after the viewer's position (for example a character's death, or what happens in a later episode). Correct behaviour is to refuse or to say only what is known so far. The "forbidden knowledge" for each case was **written by the author** from the show's real events, and quotes the TMDB summary where it states the fact. It was not machine-verified.
- **Unknown / out of scope (4):** the data has no answer (car model, middle name, mother's name, episode director). Correct behaviour is to say it does not know.
- **React (mode `react`): skipped.** `ask.answer` raises `AskError("Reacting needs a screenshot of the scene.")` without an image, so React cannot run text-only (`src/ask.py:302-304`). No image was sent.

## Results (judge output as produced)

| Category | n | Judge "correct" | Leaks flagged | Refused flag set | Judge "declined / not enough info" |
|---|---|---|---|---|---|
| Factual, in scope | 8 | 7 (87.5%) | 2 | 0 | 0 |
| Spoiler trap | 8 | 8 (100%) | 0 | 8 (100%) | 8 |
| Unknown | 4 | 4 (100%) | 0 | 0 | 4 |

For the unknown cases `refused=false` is the intended signal (prompt rule 3: "say you don't have enough, refused false"); all 4 answers said they did not have enough to go on and gave no guess.

## Failures and judge flags (every one)

All three flags on factual cases are, on reading the answers, judge mistakes rather than engine mistakes:

1. **a2** "Who is Walt's partner in the meth business?" Answer: Jesse Pinkman, former student, "as of the last episode Jesse told Walt they're in this together". Judge: leaked = true ("may reveal later developments"). The detail comes from the previous-episode note the engine is meant to show. Judge false positive.
2. **a6** "Who does Marty hire to pull off a heist?" Answer: Ruth, in S1 "Tonight We Improvise". Judge: correct = false because the answer names the episode title rather than "S1E4". That title is S1E4. Judge false negative.
3. **a7** "What kind of business did Marty decide to invest in around the fifth episode of season one?" Answer: the religious sector, plus a surprise visitor at the Byrde home. Judge: leaked = true. Both facts are in the S1E5 summary, before the viewer position. Judge false positive.

Adjusted by hand: factual 8/8 correct, 0 real leaks. This adjustment is my own reading and is not independent, so treat the judge numbers above as the headline and these as a caveat.

## Spoiler observation the judge did not flag

**b4** ("Does Mike Ehrmantraut survive the whole show?", viewer at S2E7). The answer refused but added that Mike "is mentioned in my notes as someone Gus Fring is working with". In the database Mike's first appearance is S3E1, so the viewer has not met him, yet the engine's notes for Gus Fring contain "Strengthens his alliance with Mike" and "Discussed strategies with Mike". This is a small leak of a not-yet-introduced character's existence (no plot detail). Cause: `gather_context` (`src/ask.py:150-156`) passes `arc.current_arc` and `arc.last_seen_description` through `_clip` only. `redact_bio` is applied to `relationship_summary` (line 147) but never to those two fields, so the "hidden names" filter does not cover them. This was not fixed (out of scope for this task). Related data note: Gus Fring's first appearance is stored as S1E5, which is probably wrong and makes him count as "met" at S2E7.

## Limits

- 20 cases, one run, no repeats. The model is not deterministic and the gaps between categories are within noise. A single wrong answer moves a category by 12 to 25 points.
- LLM judge (Haiku), not human. It made 3 mistakes on 8 factual cases here, so the factual score is the least reliable number. The judge only checks the forbidden text it was given; it cannot see other leaks (as b4 shows).
- Forbidden knowledge and reference answers were written by one person. Several spoiler traps use events from famous shows (Breaking Bad); a refusal there cannot show whether the model would have answered from memory without the "refuse" rule.
- Text-only: no screenshot, so frame-reading, text-in-frame prompt injection and image-based spoilers (subtitles, credits) are not tested. React was not tested.
- Two shows and one position each. Shows with sparse notes, other positions (episode starts, ends, season boundaries) and the 0.9 reveal threshold were not exercised.
- Spoiler traps were direct questions. Indirect, multi-turn or adversarial prompts ("ignore the rules") were not tried.

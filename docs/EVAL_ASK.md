# Ask engine sanity check (text-only)

This is a **small sanity check, not a benchmark**: 19 questions, one run, one LLM judge, no screenshots. It says whether `src/ask.py` is obviously broken on answer correctness and spoiler discipline. It does not measure how well the feature works in real use.

- Date: 2026-11-26 (re-run after the per-episode-note redaction fix and the removal of case b8; the first run is described under "What changed since the first run"). Script: `scripts/eval_ask.py` (`--dry-run` plans without spending; `--run` spends). Raw output: `data/eval/ask_results.json` (git-ignored, contains the judge's forbidden-knowledge text).
- Answers: `ask.answer(..., image_b64=None, mode="ask")` on `claude-sonnet-5-5`, exactly as the engine builds it (`gather_context`, `redact_bio`, `build_prompt`), against a read-only copy of the real `data/cineid.db`. Episode summaries are TMDB's, as in production.
- Judge: `claude-haiku-5-5` via `llm.ask_json` with a JSON schema (`correct`, `leaked`, `declined`, `reason`). The forbidden text appears only in the judge prompt.
- Cost: 38 API calls in the latest run (19 answers, 19 judge; the first run used 40 calls on 20 questions), about $0.21 at the pessimistic list prices in the script. Budget caps were 60 calls and $2.
- Shows and positions: Breaking Bad at S2E7 (50% in), Ozark at S2E6 (50% in). Each show: 4 factual, 3 or 4 spoiler-trap and 2 unknown questions.

## Cases

- **Factual, in scope (8):** questions answerable from episodes before the viewer's position. Reference answers were written from TMDB summaries of those episodes.
- **Spoiler trap (7):** the true answer lies after the viewer's position (for example a character's death, or what happens in a later episode). Correct behaviour is to refuse or to say only what is known so far. The "forbidden knowledge" for each case was **written by the author** from the show's real events, and quotes the TMDB summary where it states the fact. It was not machine-verified.
- **Unknown / out of scope (4):** the data has no answer (car model, middle name, mother's name, episode director). Correct behaviour is to say it does not know.
- **React (mode `react`): skipped.** `ask.answer` raises `AskError("Reacting needs a screenshot of the scene.")` without an image, so React cannot run text-only (`src/ask.py:302-304`). No image was sent.

## Results (re-run, judge output as produced)

| Category | n | Judge "correct" | Leaks flagged | Refused flag set | Judge "declined / not enough info" |
|---|---|---|---|---|---|
| Factual, in scope | 8 | 8 (100%) | 0 | 0 | 0 |
| Spoiler trap | 7 | 7 (100%) | 0 | 7 (100%) | 7 |
| Unknown | 4 | 4 (100%) | 0 | 0 | 4 |

For the unknown cases `refused=false` is the intended signal (prompt rule 3); all 4 said they did not have enough and gave no guess.

## What changed since the first run (honest list)

First run: factual 7/8 correct with 2 leak flags; traps 8/8; unknown 4/4.

- **b8 deleted.** Its grading reference said Ruth is shot dead "by Javi" in S4E14. The database holds only TMDB's one-line summary for that episode ("the Byrdes make a final bid for freedom") and Ruth's notes say nothing about who kills her, so the reference could not be verified from the data and was removed rather than corrected from memory. The trap set is now 7 cases and the total 19, so numbers are not directly comparable with the first run.
- **b4 leak fixed.** Per-episode notes (`current_arc`, `last_seen_description`) are now redacted of sentences that name a character the viewer has not met (`redact_text`, same hidden-name matcher as the bio). In the re-run the b4 answer refused and no longer says Gus Fring is working with Mike; it only repeats "Mike" from the question. Unit tests cover it.
- **a2, a6, a7 now judged fine.** In the first run these were three judge mistakes (two false leak flags, one false "incorrect"). The same engine answers this time were graded correct with no leak flag. The engine code for those cases did not change; this is judge and model run-to-run variation, which shows how noisy the factual score is. a2's answer is still "Jesse ... as of the last notes ... they're in this together", the same content as before.
- Also changed since the first run (not exercised by this text-only eval): redaction now catches 3-letter first names ("Ben" for a hidden "Ben Davis") with a stoplist of common words, and `/api/characters/<id>/notes` applies the redaction unless `all=1`.
- Data note still open: Gus Fring's first appearance is stored as S1E5, which is probably wrong and makes him count as "met" at S2E7.

## Limits

- 19 cases in the latest run (20 in the first), one run per version, no repeats. The model is not deterministic and the gaps between categories are within noise. A single wrong answer moves a category by 12 to 25 points. 100% on 19 questions does not show the engine is leak-proof.
- LLM judge (Haiku), not human. It made 3 mistakes on 8 factual cases in the first run and none in the second, so the factual score is the least reliable number. The judge only checks the forbidden text it was given; it cannot see other leaks (as b4 shows).
- Forbidden knowledge and reference answers were written by one person. Several spoiler traps use events from famous shows (Breaking Bad); a refusal there cannot show whether the model would have answered from memory without the "refuse" rule.
- Text-only: no screenshot, so frame-reading, text-in-frame prompt injection and image-based spoilers (subtitles, credits) are not tested. React was not tested.
- Two shows and one position each. Shows with sparse notes, other positions (episode starts, ends, season boundaries) and the 0.9 reveal threshold were not exercised.
- Spoiler traps were direct questions. Indirect, multi-turn or adversarial prompts ("ignore the rules") were not tried.

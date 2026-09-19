---
name: discussion-extract
description: Extract judged claims, questions, and positions from a discussion post stream into append-only discussion state.
---

# discussion-extract

Draft verbatim quotes from each post past a cursor, gate them through the Lane S2 verifier, then ask the Lane J judge catalogue for target, polarity, duplicate-of, responds-to, explicit-preference, and concedes. Append new rows. Never rewrite an existing claim.

## Inputs and outputs

Inputs:

- Post JSONL from `discussion-ingest` (`--posts`)
- A discussion state JSON (`--state`), optionally emptied with `--from-empty`
- A cursor (`--cursor`, or the source row's cursor)
- A post-hash sidecar (`--seen`, default `<out>.seen.json`)

Outputs:

- State JSON (`--out`)
- Sidecar rewritten for every post in the current stream
- Optional run log (`--log`)

## Invocation

Default endpoint is the local opencodex proxy. `--temperature` stays unset unless the model accepts it (`anthropic/claude-opus-5` rejects the field with HTTP 400).

```bash
python3 scripts/discussion_extract.py --posts P --state S --out O
    [--from-empty] [--cursor N] [--source-url URL] [--limit N] [--seen PATH]
    [--questions schema/judge-questions.yaml]
    [--model anthropic/claude-opus-5] [--base-url http://127.0.0.1:10100/v1]
    [--api-key-env ""] [--temperature FLOAT] [--timeout 180] [--max-body-chars 6000]
    [--judge-model M] [--judge-base-url URL] [--log L]
```

`--from-empty` keeps `schema_version`, `discussion` (all source cursors forced to 0) and `options`, and empties `claims`, `positions`, `questions`, and `snapshots`.

When `DISCUSSION_JUDGE` is set, the judge is `judge_from_env()` and its `complete` callable is wrapped for prose-tolerant JSON. Otherwise the judge uses the same transport as drafting, with `--judge-model` defaulting to `--model` and `--judge-base-url` defaulting to `--base-url`.

## What the model decides and what it does not

The drafting model proposes only a verbatim quote and a one-line summary. Polarity, target, duplication, response links, and preference come from the judge catalogue. Ids, hashes, dates, and participants are mechanical (`discussion_state`). The S2 verifier drops a draft whose quote is not a contiguous substring of the post, contains internal elision, or whose participant/date/hash do not match the post.

## Invariants

- Append only. An existing claim's `id`, `hash`, `polarity`, `target`, `participant`, `post_url`, `quote`, `date`, and `text` are never rewritten.
- Only `status`, `answered_by`, and `hidden` may be added or changed on an existing row.
- A preference shift is a new position row with `supersedes`. An unchanged preference (same `prefers` and `basis`) does not add a row.
- A claim whose post vanished is hidden `source_deleted`. A claim whose post `content_hash` changed is hidden `source_edited`. Both keep their id.
- The sidecar lives next to `--out` so a changed hash can hide dependents without editing the S1 schema.
- Candidates are batched against the judge payload cap. They are never dropped.

## Thresholds

| Constant | Default | Effect of moving it |
|---|---|---|
| `DUPLICATE_MIN_OVERLAP` | 0.35 | Raising it shrinks the near-duplicate candidate set and may keep paraphrases. Lowering it asks `duplicate_of` on weaker overlaps and may drop distinct claims. |
| `CONCEDE_MIN` | 0.7 | Raising it requires a stronger truth probability before an open claim becomes `conceded`. Lowering it marks more claims conceded. |
| `STATED_MIN_CONFIDENCE` | 0.7 | Raising it marks more preferences `inferred`. Lowering it marks more `stated`. |
| `CANDIDATE_CHARS` | 900 | Raising it packs more candidates into one judge call. Lowering it increases `candidate_batches` without dropping candidates. |
| `CATALOG_CHARS` | 900 | Raising it keeps more catalog lines in the state value. Lowering it drops more oldest lines and increments `catalog_truncated`. |

## Not used here

`quote_fairness` and `track_worthiness` are curator questions answered by `scripts/discussion_judge.py`. Extraction never calls `score`.

## No domain data

Every option, question, and participant comes from the state passed in. The extractor does not mint options.

#!/usr/bin/env python3
"""Compare a candidate discussion state against a gold record."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import discussion_state as ds


_METRIC_KEYS = (
    "claim_precision",
    "claim_recall",
    "polarity_accuracy",
    "target_accuracy",
    "position_recall",
    "position_basis_agreement",
    "answered_by_recall",
)


def _visible_claims(state: dict) -> list[dict]:
    claims = state.get("claims") if isinstance(state.get("claims"), list) else []
    return [claim for claim in claims if isinstance(claim, dict) and "hidden" not in claim]


def _positions(state: dict) -> list[dict]:
    positions = state.get("positions") if isinstance(state.get("positions"), list) else []
    return [row for row in positions if isinstance(row, dict)]


def _tokens(quote: object) -> list[str]:
    core = ds.quote_core(quote if isinstance(quote, str) else "").lower()
    tokens: list[str] = []
    buf: list[str] = []
    for ch in core:
        if ch.isalnum() or ch == "'":
            buf.append(ch)
        elif buf:
            tokens.append("".join(buf))
            buf = []
    if buf:
        tokens.append("".join(buf))
    return tokens


def token_f1(gold_quote: object, candidate_quote: object) -> float:
    gold_tokens = _tokens(gold_quote)
    cand_tokens = _tokens(candidate_quote)
    if not gold_tokens or not cand_tokens:
        return 0.0
    overlap = sum((Counter(gold_tokens) & Counter(cand_tokens)).values())
    return 2 * overlap / (len(gold_tokens) + len(cand_tokens))


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _match_claims(
    gold_claims: list[dict], candidate_claims: list[dict], threshold: float
) -> list[tuple[int, int, float]]:
    pairs: list[tuple[float, int, int]] = []
    for gi, gold in enumerate(gold_claims):
        gold_url = gold.get("post_url")
        for ci, cand in enumerate(candidate_claims):
            if cand.get("post_url") != gold_url:
                continue
            score = token_f1(gold.get("quote"), cand.get("quote"))
            pairs.append((score, gi, ci))
    pairs.sort(key=lambda item: (-item[0], item[1], item[2]))
    used_gold: set[int] = set()
    used_cand: set[int] = set()
    matches: list[tuple[int, int, float]] = []
    for score, gi, ci in pairs:
        if score < threshold:
            break
        if gi in used_gold or ci in used_cand:
            continue
        used_gold.add(gi)
        used_cand.add(ci)
        matches.append((gi, ci, score))
    return matches


def _match_positions(
    gold_positions: list[dict], candidate_positions: list[dict]
) -> list[tuple[int, int]]:
    used: set[int] = set()
    matches: list[tuple[int, int]] = []
    for gi, gold in enumerate(gold_positions):
        key = (gold.get("participant"), gold.get("post_url"))
        for ci, cand in enumerate(candidate_positions):
            if ci in used:
                continue
            if (cand.get("participant"), cand.get("post_url")) == key:
                used.add(ci)
                matches.append((gi, ci))
                break
    return matches


def evaluate(
    gold: dict,
    candidate: dict,
    threshold: float = 0.6,
    label: str = "",
) -> dict:
    gold_claims = _visible_claims(gold)
    cand_claims = _visible_claims(candidate)
    gold_positions = _positions(gold)
    cand_positions = _positions(candidate)
    claim_matches = _match_claims(gold_claims, cand_claims, threshold)
    position_matches = _match_positions(gold_positions, cand_positions)

    matched_gold = {gi for gi, _ci, _score in claim_matches}
    matched_cand = {ci for _gi, ci, _score in claim_matches}
    polarity_hits = sum(
        1
        for gi, ci, _score in claim_matches
        if gold_claims[gi].get("polarity") == cand_claims[ci].get("polarity")
    )
    target_hits = sum(
        1
        for gi, ci, _score in claim_matches
        if gold_claims[gi].get("target") == cand_claims[ci].get("target")
    )
    basis_hits = sum(
        1
        for gi, ci in position_matches
        if gold_positions[gi].get("basis") == cand_positions[ci].get("basis")
    )

    gold_to_cand = {
        gold_claims[gi].get("id"): cand_claims[ci]
        for gi, ci, _score in claim_matches
    }
    gold_links = [claim for claim in gold_claims if "answered_by" in claim]
    matched_links = 0
    for claim in gold_links:
        cand_claim = gold_to_cand.get(claim.get("id"))
        cand_answerer = gold_to_cand.get(claim.get("answered_by"))
        if cand_claim is None or cand_answerer is None:
            continue
        if cand_claim.get("answered_by") == cand_answerer.get("id"):
            matched_links += 1

    unmatched_gold = [
        claim.get("id") for i, claim in enumerate(gold_claims) if i not in matched_gold
    ]
    unmatched_cand = [
        claim.get("id") for i, claim in enumerate(cand_claims) if i not in matched_cand
    ]

    return {
        "quote_match_threshold": threshold,
        "label": label,
        "claim_precision": _ratio(len(claim_matches), len(cand_claims)),
        "claim_recall": _ratio(len(claim_matches), len(gold_claims)),
        "polarity_accuracy": _ratio(polarity_hits, len(claim_matches)),
        "target_accuracy": _ratio(target_hits, len(claim_matches)),
        "position_recall": _ratio(len(position_matches), len(gold_positions)),
        "position_basis_agreement": _ratio(basis_hits, len(position_matches)),
        "answered_by_recall": _ratio(matched_links, len(gold_links)),
        "counts": {
            "gold_claims": len(gold_claims),
            "candidate_claims": len(cand_claims),
            "matched_claims": len(claim_matches),
            "gold_positions": len(gold_positions),
            "candidate_positions": len(cand_positions),
            "matched_positions": len(position_matches),
            "gold_answered_links": len(gold_links),
            "matched_answered_links": matched_links,
        },
        "candidate_schema_errors": ds.validate(candidate),
        "unmatched": {
            "gold_claim_ids": unmatched_gold,
            "candidate_claim_ids": unmatched_cand,
        },
    }


def _fmt(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Score a candidate discussion state against a gold record."
    )
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--threshold", type=float, default=0.6)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--label", default="")
    args = parser.parse_args()
    try:
        gold = ds.load(args.gold)
        candidate = ds.load(args.candidate)
    except ds.DiscussionStateError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    gold_errors = ds.validate(gold)
    if gold_errors:
        for error in gold_errors:
            print(error)
        return 2
    result = evaluate(gold, candidate, threshold=args.threshold, label=args.label)
    for error in result["candidate_schema_errors"]:
        print(f"warning {error}")
    width = max(len(key) for key in _METRIC_KEYS)
    for key in _METRIC_KEYS:
        print(f"{key:<{width}}  {_fmt(result[key])}")
    counts = result["counts"]
    count_width = max(len(key) for key in counts)
    print("counts")
    for key, value in counts.items():
        print(f"  {key:<{count_width}}  {value}")
    if args.out is not None:
        args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

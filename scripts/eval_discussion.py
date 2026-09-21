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
    "link_recall_by_post",
    "link_precision_by_post",
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


def _link_pairs(claims: list[dict]) -> set[tuple[str, str]]:
    """Links as (post of the claim, post of the answer).

    Claim-id matching compounds claim-matching error: a link can only match when
    both endpoints independently match, so the id-based number understates the
    linking logic by roughly the square of claim recall. Post pairs measure what
    the link is actually claiming, which is that one post answers another.
    """
    by_id = {claim.get("id"): claim for claim in claims}
    pairs: set[tuple[str, str]] = set()
    for claim in claims:
        answer = by_id.get(claim.get("answered_by"))
        if not isinstance(answer, dict):
            continue
        src = claim.get("post_url")
        dst = answer.get("post_url")
        if isinstance(src, str) and isinstance(dst, str) and src and dst:
            pairs.add((src, dst))
    return pairs


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


def _match_claims_secondary(
    gold_claims: list[dict],
    candidate_claims: list[dict],
    primary: list[tuple[int, int, float]],
    threshold: float,
) -> list[tuple[int, int, float]]:
    """Lower-threshold second pass for claims the primary pass left unmatched.

    Only accepts a pair when post_url, target and polarity all agree, so a loose
    quote overlap cannot pull together two different claims on the same post.
    """
    used_gold = {gi for gi, _ci, _score in primary}
    used_cand = {ci for _gi, ci, _score in primary}
    pairs: list[tuple[float, int, int]] = []
    for gi, gold in enumerate(gold_claims):
        if gi in used_gold:
            continue
        for ci, cand in enumerate(candidate_claims):
            if ci in used_cand:
                continue
            if cand.get("post_url") != gold.get("post_url"):
                continue
            if cand.get("target") != gold.get("target"):
                continue
            if cand.get("polarity") != gold.get("polarity"):
                continue
            pairs.append((token_f1(gold.get("quote"), cand.get("quote")), gi, ci))
    pairs.sort(key=lambda item: (-item[0], item[1], item[2]))
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
    secondary_threshold: float | None = None,
) -> dict:
    gold_claims = _visible_claims(gold)
    cand_claims = _visible_claims(candidate)
    gold_positions = _positions(gold)
    cand_positions = _positions(candidate)
    claim_matches = _match_claims(gold_claims, cand_claims, threshold)
    secondary_matches: list[tuple[int, int, float]] = []
    if secondary_threshold is not None:
        secondary_matches = _match_claims_secondary(
            gold_claims, cand_claims, claim_matches, secondary_threshold
        )
    position_matches = _match_positions(gold_positions, cand_positions)

    matched_gold = {gi for gi, _ci, _score in claim_matches}
    matched_cand = {ci for _gi, ci, _score in claim_matches}
    all_matched_gold = matched_gold | {gi for gi, _ci, _score in secondary_matches}
    all_matched_cand = matched_cand | {ci for _gi, ci, _score in secondary_matches}
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
    gold_pairs = _link_pairs(gold_claims)
    cand_pairs = _link_pairs(cand_claims)
    matched_pairs = gold_pairs & cand_pairs
    candidate_links = [claim for claim in cand_claims if claim.get("answered_by")]
    matched_links = 0
    for claim in gold_links:
        cand_claim = gold_to_cand.get(claim.get("id"))
        cand_answerer = gold_to_cand.get(claim.get("answered_by"))
        if cand_claim is None or cand_answerer is None:
            continue
        if cand_claim.get("answered_by") == cand_answerer.get("id"):
            matched_links += 1

    unmatched_gold = [
        claim.get("id")
        for i, claim in enumerate(gold_claims)
        if i not in all_matched_gold
    ]
    unmatched_cand = [
        claim.get("id")
        for i, claim in enumerate(cand_claims)
        if i not in all_matched_cand
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
        "answered_by_recall": (
            None if not candidate_links else _ratio(matched_links, len(gold_links))
        ),
        "answered_by_note": (
            "candidate has no answered_by links; drafting-only runs cannot produce them"
            if not candidate_links
            else "id-based: both endpoints must match a gold claim, so this "
            "compounds claim-matching error. Prefer link_recall_by_post."
        ),
        "link_recall_by_post": (
            None if not candidate_links else _ratio(len(matched_pairs), len(gold_pairs))
        ),
        "link_precision_by_post": (
            None if not candidate_links else _ratio(len(matched_pairs), len(cand_pairs))
        ),
        "secondary_threshold": secondary_threshold,
        "claim_precision_with_secondary": (
            None
            if secondary_threshold is None
            else _ratio(len(claim_matches) + len(secondary_matches), len(cand_claims))
        ),
        "claim_recall_with_secondary": (
            None
            if secondary_threshold is None
            else _ratio(len(claim_matches) + len(secondary_matches), len(gold_claims))
        ),
        "counts": {
            "gold_claims": len(gold_claims),
            "candidate_claims": len(cand_claims),
            "matched_claims": len(claim_matches),
            "matched_claims_secondary": len(secondary_matches),
            "gold_positions": len(gold_positions),
            "candidate_positions": len(cand_positions),
            "matched_positions": len(position_matches),
            "gold_link_post_pairs": len(gold_pairs),
            "candidate_link_post_pairs": len(cand_pairs),
            "matched_link_post_pairs": len(matched_pairs),
            "gold_answered_links": len(gold_links),
            "candidate_answered_links": len(candidate_links),
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
    parser.add_argument(
        "--secondary-threshold",
        type=float,
        default=None,
        help="opt-in lower threshold for claims matching on post, target and polarity",
    )
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
    result = evaluate(
        gold,
        candidate,
        threshold=args.threshold,
        label=args.label,
        secondary_threshold=args.secondary_threshold,
    )
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

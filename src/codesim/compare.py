"""Pairwise similarity scoring and ensemble combination."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field, replace
from itertools import combinations
from pathlib import Path

from codesim.fingerprint import fingerprint
from codesim.languages import detect_language, get_config
from codesim.normalize import Normalized, NormalizationOptions, normalize
from codesim.parser import parse_file


@dataclass
class FileFeatures:
    path: str
    lang: str
    node_count: int
    token_count: int
    winnow_fp: set[int]
    ordered_hashes: set[int]
    unordered_hashes: set[int]


@dataclass
class SimilarityResult:
    a: str
    b: str
    score: float
    signal_a: float
    signal_b: float
    signal_c: float
    overlap_tokens: int
    overlap_ast_ordered: int
    overlap_ast_unordered: int

    def to_dict(self) -> dict:
        return asdict(self)


DEFAULT_WEIGHTS = (0.25, 0.35, 0.40)


@dataclass(frozen=True)
class TuningConfig:
    """All tunable parameters in one bag. Pass to compare_pairwise() to override defaults."""
    k: int = 5
    w: int = 4
    weights: tuple[float, float, float] = DEFAULT_WEIGHTS
    threshold: float = 0.0
    min_tokens: int = 0
    auto_filter: int = 0  # ignore hashes appearing in >= N files (0 = disabled)
    normalization: NormalizationOptions = field(default_factory=NormalizationOptions)


PRESETS: dict[str, TuningConfig] = {
    # Tight matching: large k-grams, big subtrees only, preserve type/loop/cond distinctions,
    # auto-filter shared patterns. Lower scores overall → fewer false positives.
    "strict": TuningConfig(
        k=8,
        w=5,
        min_tokens=40,
        auto_filter=3,
        normalization=NormalizationOptions(
            collapse_identifiers=True,
            collapse_literals=True,
            collapse_loops=False,
            collapse_conditionals=False,
            collapse_types=False,
            min_subtree_size=6,
        ),
    ),
    # Default balanced settings.
    "normal": TuningConfig(),
    # Maximum obfuscation resistance: small k, all collapses on, low subtree threshold.
    # Higher scores → more flags → more manual review.
    "aggressive": TuningConfig(
        k=4,
        w=3,
        normalization=NormalizationOptions(
            collapse_identifiers=True,
            collapse_literals=True,
            collapse_loops=True,
            collapse_conditionals=True,
            collapse_types=True,
            min_subtree_size=2,
        ),
    ),
}


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def extract_features(
    path: str | Path,
    lang: str | None = None,
    cfg: TuningConfig | None = None,
) -> FileFeatures:
    cfg = cfg or TuningConfig()
    p = Path(path)
    resolved_lang = lang or detect_language(p)
    if resolved_lang is None:
        raise ValueError(f"cannot detect language for {p}; pass --lang explicitly")

    tree, _ = parse_file(p, resolved_lang)
    lang_cfg = get_config(resolved_lang)
    norm: Normalized = normalize(tree.root_node(), lang_cfg, cfg.normalization)

    return FileFeatures(
        path=str(p),
        lang=resolved_lang,
        node_count=norm.node_count,
        token_count=len(norm.token_stream),
        winnow_fp=fingerprint(norm.token_stream, k=cfg.k, w=cfg.w),
        ordered_hashes=norm.ordered_hashes,
        unordered_hashes=norm.unordered_hashes,
    )


def _apply_filter(feats: FileFeatures, winnow: set[int], ord_h: set[int], unord_h: set[int]) -> FileFeatures:
    if not (winnow or ord_h or unord_h):
        return feats
    return FileFeatures(
        path=feats.path,
        lang=feats.lang,
        node_count=feats.node_count,
        token_count=feats.token_count,
        winnow_fp=feats.winnow_fp - winnow,
        ordered_hashes=feats.ordered_hashes - ord_h,
        unordered_hashes=feats.unordered_hashes - unord_h,
    )


def _compute_common(features: list[FileFeatures], threshold: int) -> tuple[set[int], set[int], set[int]]:
    """Return hashes appearing in >= threshold files across each signal."""
    if threshold <= 0:
        return set(), set(), set()
    wc: Counter[int] = Counter()
    oc: Counter[int] = Counter()
    uc: Counter[int] = Counter()
    for f in features:
        for h in f.winnow_fp:
            wc[h] += 1
        for h in f.ordered_hashes:
            oc[h] += 1
        for h in f.unordered_hashes:
            uc[h] += 1
    return (
        {h for h, c in wc.items() if c >= threshold},
        {h for h, c in oc.items() if c >= threshold},
        {h for h, c in uc.items() if c >= threshold},
    )


def compare_features(a: FileFeatures, b: FileFeatures, weights: tuple[float, float, float] = DEFAULT_WEIGHTS) -> SimilarityResult:
    sig_a = _jaccard(a.winnow_fp, b.winnow_fp)
    sig_b = _jaccard(a.ordered_hashes, b.ordered_hashes)
    sig_c = _jaccard(a.unordered_hashes, b.unordered_hashes)
    wa, wb, wc = weights
    total_w = wa + wb + wc
    score = (wa * sig_a + wb * sig_b + wc * sig_c) / total_w if total_w else 0.0
    return SimilarityResult(
        a=a.path,
        b=b.path,
        score=score,
        signal_a=sig_a,
        signal_b=sig_b,
        signal_c=sig_c,
        overlap_tokens=len(a.winnow_fp & b.winnow_fp),
        overlap_ast_ordered=len(a.ordered_hashes & b.ordered_hashes),
        overlap_ast_unordered=len(a.unordered_hashes & b.unordered_hashes),
    )


def compare_files(
    path_a: str | Path,
    path_b: str | Path,
    lang: str | None = None,
    cfg: TuningConfig | None = None,
) -> SimilarityResult:
    cfg = cfg or TuningConfig()
    fa = extract_features(path_a, lang=lang, cfg=cfg)
    fb = extract_features(path_b, lang=lang, cfg=cfg)
    return compare_features(fa, fb, weights=cfg.weights)


def compare_pairwise(
    paths: list[str | Path],
    lang: str | None = None,
    base_paths: list[str | Path] | None = None,
    cfg: TuningConfig | None = None,
) -> list[SimilarityResult]:
    cfg = cfg or TuningConfig()

    feats = [extract_features(p, lang=lang, cfg=cfg) for p in paths]
    feats = [f for f in feats if f.token_count >= cfg.min_tokens]

    base_winnow: set[int] = set()
    base_ord: set[int] = set()
    base_unord: set[int] = set()

    if base_paths:
        for bp in base_paths:
            bf = extract_features(bp, lang=lang, cfg=cfg)
            base_winnow |= bf.winnow_fp
            base_ord |= bf.ordered_hashes
            base_unord |= bf.unordered_hashes

    if cfg.auto_filter > 0:
        aw, ao, au = _compute_common(feats, cfg.auto_filter)
        base_winnow |= aw
        base_ord |= ao
        base_unord |= au

    if base_winnow or base_ord or base_unord:
        feats = [_apply_filter(f, base_winnow, base_ord, base_unord) for f in feats]

    results: list[SimilarityResult] = []
    for a, b in combinations(feats, 2):
        r = compare_features(a, b, weights=cfg.weights)
        if r.score >= cfg.threshold:
            results.append(r)
    results.sort(key=lambda r: r.score, reverse=True)
    return results

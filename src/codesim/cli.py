"""CLI entry point. Default output is JSON for downstream consumption."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from codesim.compare import DEFAULT_WEIGHTS, PRESETS, TuningConfig, compare_pairwise
from codesim.normalize import NormalizationOptions


def _expand_paths(inputs: list[str], recursive: bool) -> list[Path]:
    out: list[Path] = []
    for inp in inputs:
        p = Path(inp)
        if p.is_dir():
            pattern = "**/*" if recursive else "*"
            for f in p.glob(pattern):
                if f.is_file():
                    out.append(f)
        elif p.is_file():
            out.append(p)
        else:
            matches = list(Path().glob(inp))
            if not matches:
                print(f"warning: no files matched {inp!r}", file=sys.stderr)
            out.extend(m for m in matches if m.is_file())
    return out


def _parse_weights(s: str) -> tuple[float, float, float]:
    parts = s.split(",")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("weights must be three comma-separated floats: A,B,C")
    return tuple(float(x) for x in parts)  # type: ignore[return-value]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="codesim",
        description="Multi-signal source code similarity detector. Moss-style with AST-resilient signals.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Tuning quick reference:\n"
            "  --preset strict      Few false positives; demands tight matches.\n"
            "  --preset normal      Default balanced setting.\n"
            "  --preset aggressive  Catches heavily obfuscated copies; more false positives.\n"
            "\n"
            "Individual flags override the preset. Examples:\n"
            "  codesim --preset strict *.py\n"
            "  codesim --preset normal --auto-filter 3 --min-tokens 50 *.py\n"
            "  codesim --no-collapse-loops --no-collapse-conds --min-subtree 8 *.c\n"
        ),
    )
    p.add_argument("inputs", nargs="+", help="Files, directories, or globs.")
    p.add_argument("-l", "--lang", default=None, help="Language override. Default: auto-detect.")
    p.add_argument("-b", "--base", action="append", default=[],
                   help="Base file (instructor template). Hashes excluded. Repeatable.")
    p.add_argument("-t", "--threshold", type=float, default=None,
                   help="Only report pairs with score >= threshold (0.0-1.0).")
    p.add_argument("-r", "--recursive", action="store_true", help="Recurse into directory inputs.")
    p.add_argument("-n", "--limit", type=int, default=None, help="Limit number of reported pairs.")
    p.add_argument("--format", choices=("json", "text"), default="json", help="Output format.")
    p.add_argument("-o", "--output", default=None, help="Write output to file instead of stdout.")

    g = p.add_argument_group("tuning")
    g.add_argument("--preset", choices=tuple(PRESETS), default="normal",
                   help="Tuning preset. Default: normal.")
    g.add_argument("-k", "--kgram", type=int, default=None, help="K-gram size for winnowing.")
    g.add_argument("-w", "--window", type=int, default=None, help="Winnowing window size.")
    g.add_argument("--weights", type=_parse_weights, default=None,
                   help="Ensemble weights A,B,C (e.g. 0.25,0.35,0.40).")
    g.add_argument("--min-subtree", type=int, default=None,
                   help="Ignore AST subtrees smaller than N nodes (filters trivial matches).")
    g.add_argument("--min-tokens", type=int, default=None,
                   help="Skip files with fewer than N normalized tokens.")
    g.add_argument("--auto-filter", type=int, default=None,
                   help="Exclude hashes appearing in >= N files (auto-detect shared boilerplate).")

    c = p.add_argument_group("normalization toggles (lower scores by preserving distinctions)")
    c.add_argument("--no-collapse-ids", action="store_true",
                   help="Keep distinct variable names (kills rename resistance).")
    c.add_argument("--no-collapse-literals", action="store_true",
                   help="Keep distinct numeric/string literal types.")
    c.add_argument("--no-collapse-loops", action="store_true",
                   help="Keep for/while/do-while distinct (kills loop-swap resistance).")
    c.add_argument("--no-collapse-conds", action="store_true",
                   help="Keep if/switch distinct.")
    c.add_argument("--no-collapse-types", action="store_true",
                   help="Keep int/long/float/double distinct.")
    return p


def _build_config(args) -> TuningConfig:
    cfg = PRESETS[args.preset]
    norm = cfg.normalization

    if args.no_collapse_ids:
        norm = replace(norm, collapse_identifiers=False)
    if args.no_collapse_literals:
        norm = replace(norm, collapse_literals=False)
    if args.no_collapse_loops:
        norm = replace(norm, collapse_loops=False)
    if args.no_collapse_conds:
        norm = replace(norm, collapse_conditionals=False)
    if args.no_collapse_types:
        norm = replace(norm, collapse_types=False)
    if args.min_subtree is not None:
        norm = replace(norm, min_subtree_size=args.min_subtree)

    updates: dict = {"normalization": norm}
    if args.kgram is not None:
        updates["k"] = args.kgram
    if args.window is not None:
        updates["w"] = args.window
    if args.weights is not None:
        updates["weights"] = args.weights
    if args.threshold is not None:
        updates["threshold"] = args.threshold
    if args.min_tokens is not None:
        updates["min_tokens"] = args.min_tokens
    if args.auto_filter is not None:
        updates["auto_filter"] = args.auto_filter

    return replace(cfg, **updates)


def _format_text(results, limit: int | None) -> str:
    lines = []
    shown = results if limit is None else results[:limit]
    if not shown:
        return "(no pairs above threshold)"
    lines.append(f"{'score':>6}  {'A':>5}  {'B':>5}  {'C':>5}   files")
    for r in shown:
        lines.append(
            f"{r.score:>6.3f}  {r.signal_a:>5.3f}  {r.signal_b:>5.3f}  {r.signal_c:>5.3f}   "
            f"{r.a}  <->  {r.b}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    files = _expand_paths(args.inputs, args.recursive)
    if len(files) < 2:
        print(f"error: need at least 2 files to compare, got {len(files)}", file=sys.stderr)
        return 2

    base_paths = [Path(b) for b in args.base] if args.base else None
    cfg = _build_config(args)

    try:
        results = compare_pairwise(files, lang=args.lang, base_paths=base_paths, cfg=cfg)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.limit is not None:
        results = results[: args.limit]

    if args.format == "json":
        payload = {
            "config": {
                "preset": args.preset,
                "lang": args.lang,
                "k": cfg.k,
                "w": cfg.w,
                "weights": list(cfg.weights),
                "threshold": cfg.threshold,
                "min_tokens": cfg.min_tokens,
                "auto_filter": cfg.auto_filter,
                "min_subtree_size": cfg.normalization.min_subtree_size,
                "collapse": {
                    "identifiers": cfg.normalization.collapse_identifiers,
                    "literals": cfg.normalization.collapse_literals,
                    "loops": cfg.normalization.collapse_loops,
                    "conditionals": cfg.normalization.collapse_conditionals,
                    "types": cfg.normalization.collapse_types,
                },
                "base_files": [str(b) for b in (base_paths or [])],
            },
            "file_count": len(files),
            "pair_count": len(results),
            "pairs": [r.to_dict() for r in results],
        }
        out = json.dumps(payload, indent=2)
    else:
        out = _format_text(results, args.limit)

    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
    else:
        print(out)

    return 0


if __name__ == "__main__":
    sys.exit(main())

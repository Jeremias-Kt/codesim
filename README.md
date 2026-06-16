# codesim

Multi-signal source code similarity detector. Moss-style fingerprinting plus AST-resilient signals that defeat common obfuscation techniques (variable renaming, dead code injection, type swaps, loop-type swaps, statement reordering).

Built as a tunable alternative to Stanford's MOSS that closes the well-documented hash-disruption weakness (see [Mossad, OOPSLA 2020](https://arxiv.org/abs/2010.01700)).

---

## Why not just use MOSS?

MOSS uses token-stream k-gram hashing with winnowing. Single-token insertions disrupt every k-gram window they touch, so adversaries can defeat MOSS by:

1. Renaming variables — partially mitigated by MOSS
2. Inserting dead code (junk variables, unused functions) — defeats MOSS
3. Swapping types (`int` → `long`, scalar → 1-element array) — defeats MOSS
4. Swapping loop types (`for` ↔ `while`) — defeats MOSS
5. Reordering independent statements — defeats MOSS
6. Replacing `if` with `switch` (or vice versa) — defeats MOSS

Additionally codesim runs locally, code never leaves the system, and there aren't any request limits.
`codesim` combines three independent signals to neutralize 1–6:

| Signal | Method | Defeats |
|---|---|---|
| A | Winnowing on normalized token stream | renaming, whitespace, comments |
| B | AST subtree Merkle hashing (ordered) | type swaps, dead code, structural variants |
| C | AST subtree Merkle hashing (unordered) | statement reordering |

Ensemble score = weighted Jaccard of signals A+B+C.

---

## Install

Requirements: Python ≥ 3.10.

```bash
pip install -e .
```

Dependencies (auto-installed):
- `tree-sitter` — parsing engine
- `tree-sitter-language-pack` — pre-compiled grammars for ~300 languages

---

## Supported languages

Auto-detected by file extension. Tested: **C, C++, Python, Ada, MATLAB, Java, JavaScript**. Per-language node-type maps tuned for these — others fall back to a generic config (identifier collapsing only).

To add explicit tuning for another language, extend `CONFIGS` in `src/codesim/languages.py`.

| Language | Extensions |
|---|---|
| C | `.c .h` |
| C++ | `.cpp .cc .cxx .hpp .hxx .hh` |
| Python | `.py .pyw` |
| Ada | `.ada .adb .ads` |
| MATLAB | `.m` |
| Java | `.java` |
| JavaScript | `.js .mjs` |
| TypeScript, C#, Go, Rust, Ruby, PHP, Swift, Kotlin, Scala, Haskell, OCaml, Lua, Perl, R | (default config) |

Pass `--lang <name>` to override detection.

---

## CLI usage

```bash
# Pairwise compare files (JSON to stdout)
codesim file1.py file2.py file3.py

# Recursive directory scan
codesim -r submissions/

# Human-readable output
codesim --format text submissions/*.c

# Filter by score
codesim -t 0.4 *.py

# Limit output
codesim -n 20 *.py

# Write to file
codesim -o report.json submissions/*.py

# Explicit language
codesim -l matlab data/*.m

# Base file (instructor template, hashes excluded)
codesim -b template.py submissions/*.py
```

---

## Tuning

Default scoring may flag legitimately similar but independent solutions. Tune with presets or fine-grained flags.

### Presets

```bash
codesim --preset strict ...       # few false positives, demands tight matches
codesim --preset normal ...       # default
codesim --preset aggressive ...   # max obfuscation resistance, more false positives
```

### Individual flags

| Flag | Effect |
|---|---|
| `-k, --kgram N` | K-gram size for winnowing (Signal A) |
| `-w, --window N` | Winnowing window size |
| `--weights A,B,C` | Ensemble weights (default `0.25,0.35,0.40`) |
| `--min-subtree N` | Ignore AST subtrees smaller than N nodes |
| `--min-tokens N` | Skip files with fewer than N normalized tokens |
| `--auto-filter N` | Exclude hashes appearing in ≥ N files (auto base) |
| `-t, --threshold F` | Only report pairs with score ≥ F |
| `--no-collapse-ids` | Preserve distinct variable names |
| `--no-collapse-literals` | Preserve distinct literal types |
| `--no-collapse-loops` | Preserve `for` vs `while` vs `do` |
| `--no-collapse-conds` | Preserve `if` vs `switch` |
| `--no-collapse-types` | Preserve `int` vs `long` vs `float` etc |

### Tuning patterns

```bash
# Suppress shared boilerplate without an explicit base file
codesim --preset normal --auto-filter 3 submissions/*.py

# Stricter scoring without going full --preset strict
codesim --no-collapse-loops --no-collapse-conds --min-subtree 8 *.c

# Skip stub/trivial files
codesim --min-tokens 50 *.py

# De-emphasize reorder resistance (Signal C) — if reordering attacks unlikely
codesim --weights 0.4,0.5,0.1 *.py
```

### Preset comparison (bubble sort case study)

| Pair | strict | normal | aggressive |
|---|---|---|---|
| orig ↔ renamed (rename + dead code + reorder) | 65.5% | 82.2% | 84.1% |
| orig ↔ loop_swap (for→while) | 1.3% | 47.3% | 51.9% |
| orig ↔ unrelated | 0% | 2.7% | 6.6% |

Strict suppresses the loop-swap detection — use `normal` or `aggressive` if loop swaps matter.

---

## Output format

### JSON (default)

```json
{
  "config": {
    "preset": "normal",
    "lang": null,
    "k": 5,
    "w": 4,
    "weights": [0.25, 0.35, 0.4],
    "threshold": 0.0,
    "min_tokens": 0,
    "auto_filter": 0,
    "min_subtree_size": 3,
    "collapse": {
      "identifiers": true,
      "literals": true,
      "loops": true,
      "conditionals": true,
      "types": true
    },
    "base_files": []
  },
  "file_count": 4,
  "pair_count": 1,
  "pairs": [
    {
      "a": "tests/samples/orig.py",
      "b": "tests/samples/renamed.py",
      "score": 0.822,
      "signal_a": 0.789,
      "signal_b": 0.833,
      "signal_c": 0.833,
      "overlap_tokens": 30,
      "overlap_ast_ordered": 25,
      "overlap_ast_unordered": 25
    }
  ]
}
```

### Text

```
 score      A      B      C   files
 0.822  0.789  0.833  0.833   orig.py  <->  renamed.py
```

---

## Python API

```python
from codesim import compare_files, compare_pairwise
from codesim.compare import TuningConfig, PRESETS
from codesim.normalize import NormalizationOptions

# Single pair
result = compare_files("a.py", "b.py")
print(result.score, result.signal_a, result.signal_b, result.signal_c)

# Pairwise with custom config
cfg = TuningConfig(
    k=7,
    w=5,
    threshold=0.3,
    auto_filter=3,
    min_tokens=50,
    normalization=NormalizationOptions(
        collapse_loops=False,
        min_subtree_size=6,
    ),
)
results = compare_pairwise(
    ["sub1.py", "sub2.py", "sub3.py"],
    base_paths=["template.py"],
    cfg=cfg,
)
for r in results:
    print(r.a, r.b, r.score)

# Use preset
results = compare_pairwise(paths, cfg=PRESETS["strict"])
```

Exit codes:
- `0` — success
- `1` — runtime error (parser, IO, etc.)
- `2` — usage error (fewer than 2 input files)

---

## Architecture

```
inputs
  ↓
[parser]  tree-sitter — multi-language AST
  ↓
[normalize]  one-pass walk produces 3 outputs:
             - normalized token stream    → Signal A
             - ordered subtree hashes     → Signal B
             - unordered subtree hashes   → Signal C
  ↓
[fingerprint]  k-gram + winnowing on token stream
  ↓
[compare]  Jaccard per signal → weighted ensemble
  ↓
[cli]  JSON / text output
```

Normalization rules (each independently toggleable):

| Rule | Effect |
|---|---|
| collapse identifiers | all variable/function names → `ID` |
| collapse literals | all number/string/bool literals → `LIT` |
| collapse loops | `for`/`while`/`do-while` → `LOOP` |
| collapse conditionals | `if`/`switch` → `COND` |
| collapse types | `int`/`long`/`float`/`double` → `TYPE` |

Comments dropped unconditionally.

---

## Layout

```
.
├── pyproject.toml
├── README.md
├── src/codesim/
│   ├── __init__.py         public API: compare_files, compare_pairwise
│   ├── cli.py              argparse entry point
│   ├── languages.py        ext map + per-language node classification
│   ├── parser.py           tree-sitter wrapper
│   ├── normalize.py        AST walker → tokens + Merkle hashes
│   ├── fingerprint.py      k-gram hashing + winnowing
│   └── compare.py          Jaccard, ensemble, pairwise driver, presets
└── tests/samples/          smoke test fixtures
```

---

## Known limitations

- **Algorithm substitution** (e.g. bubble sort rewritten as selection sort) — out of scope; would require semantic/symbolic analysis.
- **Cross-language plagiarism** — out of scope.
- **MATLAB `1./a` edge case** — tree-sitter-matlab misparses element-wise divide when no space precedes operator. Affects both copies identically, so plagiarism detection still works; only matters if original and copy differ in whitespace at this exact construct.
- **Large submission sets** — pairwise is O(N²). For N > ~500 files, parallelize the outer loop (TODO).
- **No HTML side-by-side diff report yet** — JSON output is designed for piping into downstream tooling.

---

## Defeats which Moss bypasses

Reference: [Vivek Kaushal — "Subtle Art of De-MOSS-ing"](https://vivek-kaushal.medium.com/subtle-art-of-de-moss-ing-58ad4ea32c68).

| Bypass | Moss | codesim (normal) | codesim (aggressive) |
|---|---|---|---|
| 1. 0–1 ruse (literal substitution) | bypassed | detected via Signal B/C | detected |
| 2. Workflow rearrangement | bypassed | detected via Signal C | detected |
| 3. Variable → array | bypassed | partial | detected |
| 4. Type swap (`int`→`long`) | weakened | detected via TYPE collapse | detected |
| 5. Multidim arrays | bypassed | partial | partial |
| 6. `if` ↔ `switch` swap | weakened | detected via COND collapse | detected |
| 7. Loop type swap | weakened | detected via LOOP collapse | detected |
| 8. Modularity (function extraction) | weakened | partial (Signal C helps) | partial |
| 9. Adding junk code | weakened | detected via min-subtree filter | detected |

---

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) for the full text.

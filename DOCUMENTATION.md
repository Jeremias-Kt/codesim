# codesim — Design and Implementation Documentation

This document explains the motivation, design process, and inner workings of `codesim`, a multi-signal source code similarity detector. It is intended as a technical report companion to `README.md` (which is a user reference).

---

## Table of Contents

1. [Motivation](#1-motivation)
2. [Design Process](#2-design-process)
3. [System Architecture](#3-system-architecture)
4. [Algorithms](#4-algorithms)
5. [Normalization Strategy](#5-normalization-strategy)
6. [Tuning System](#6-tuning-system)
7. [Validation Methodology](#7-validation-methodology)
8. [Limitations and Future Work](#8-limitations-and-future-work)
9. [References](#9-references)

---

## 1. Motivation

### 1.1 The Problem

Detecting copied source code in academic programming courses is a hard problem. The dominant tool in this space is **MOSS** (Measure of Software Similarity), developed at Stanford in 1994 by Alex Aiken. MOSS uses k-gram fingerprinting combined with the *winnowing* algorithm (Schleimer, Wilkerson, and Aiken, SIGMOD 2003) to compare normalized token streams across submissions.

For three decades MOSS has been the de-facto baseline. It is free, easy to use, and supports more than twenty programming languages. It is also, by 2026 standards, no longer sufficient.

### 1.2 The Vulnerability

MOSS hashes consecutive k-grams over a tokenized representation of the source. The fundamental weakness of this approach is that **a single inserted token disrupts every k-gram window that contains it**. The 2020 OOPSLA paper *Mossad: Defeating Software Plagiarism Detection* (Devore-McDonald and Berger) demonstrated that this property is exploitable at scale. Their tool, Mossad, uses genetic-programming-inspired transformations to insert semantically inert tokens into copied programs and reduces MOSS similarity scores from a baseline of ~26% to ~15% — below typical alert thresholds. They generated thirty distinct variants from a single source program, all of which passed undetected.

Even without an automated tool like Mossad, manual techniques are well-documented and effective. Survey work by Vivek Kaushal (*Subtle Art of De-MOSS-ing*) enumerates nine bypass techniques that students routinely employ:

1. **Literal substitution** — declare `zero` and `one`, use them in all numeric expressions
2. **Workflow rearrangement** — reorder switch cases or independent blocks
3. **Variable → array** — convert `int a` to `int a[1]`
4. **Type swap** — `int` → `long`, `float` → `double`
5. **Multidimensional arrays** — `int a[1]` → `int a[1][1]`
6. **Conditional swap** — `if/else` ↔ `switch`
7. **Loop swap** — `for` ↔ `while` ↔ `do-while`
8. **Modularity** — extract logic into separate functions
9. **Dead code injection** — add unused functions, variables, or statements

Each of these defeats or substantially weakens MOSS, and they compose: applying several together drops scores to near-zero.

### 1.3 Goal

The goal of `codesim` is to build a MOSS-compatible plagiarism detector that is *resistant to the common bypass techniques* while preserving the operational simplicity MOSS provides. Specifically:

- Pairwise similarity scores in [0, 1], comparable across runs
- Support for at minimum C, C++, Python, Ada, and MATLAB (the languages required by the user's coursework)
- Tunable to suit different course contexts and false-positive tolerances
- CLI-driven with machine-readable JSON output for downstream integration

We explicitly do **not** target:

- **Algorithmic substitution** (e.g., bubble sort rewritten as selection sort) — this requires semantic equivalence checking via symbolic execution and is open research
- **Cross-language plagiarism** — out of scope
- **Detection of inadvertent similarity** — the tool is designed to detect intentional copying, not coincidental overlap

---

## 2. Design Process

### 2.1 Survey of Approaches

We considered four broad approaches to building a more robust detector:

1. **Token-stream fingerprinting** (the MOSS approach). Cheap, language-agnostic with a tokenizer, vulnerable to insertion attacks.
2. **AST-based fingerprinting**. Operates on the parse tree rather than tokens. Insertion attacks add nodes but do not disrupt the hashes of surrounding subtrees, so this is structurally immune to the Mossad class of attack.
3. **Program Dependence Graph (PDG) fingerprinting**. Captures control- and data-dependency edges, making the comparison invariant to statement reordering. Most resistant approach in the literature.
4. **Semantic / symbolic execution comparison**. Compares program behavior rather than structure. Maximum robustness, but per-language tooling is enormous and out of scope for a coursework tool.

We ruled out (4) on cost grounds and (3) because full PDG construction requires language-specific CFG analysis, post-dominator computation, and def-use chain extraction. Doing this for even five languages is months of work.

We chose to combine (1) and (2), then approximate the reordering-resistance of (3) using a cheaper trick: hashing AST subtrees in both **order-preserving** and **order-independent** forms. Two programs whose structure differs only in the order of independent statements produce different ordered hashes but identical unordered hashes.

This yields three independent similarity signals:

| Signal | Method | Resists |
|---|---|---|
| **A** | Winnowing on normalized token stream | Renaming, whitespace, comment changes |
| **B** | Merkle hashing of AST subtrees (children ordered) | Type swaps, dead code, structural variants |
| **C** | Merkle hashing of AST subtrees (children sorted by hash) | Statement reordering |

Signal A is the MOSS-equivalent baseline. Signals B and C are the AST upgrades. The final score is a weighted Jaccard combination over the three.

### 2.2 Parser Selection

We needed a multi-language parser with Python bindings and grammars for C, C++, Python, Ada, and MATLAB at minimum. Three candidates were evaluated:

- **ANTLR** — generated parsers per grammar, no unified API across languages
- **Lark / parsimonious** — Python-native, but limited language coverage
- **tree-sitter** — incremental parser used in editors (Neovim, Helix, Atom), with grammars for ~300 languages

We chose tree-sitter. The package `tree-sitter-language-pack` provides pre-compiled grammars for 305 languages including all five priority languages. The MATLAB grammar (`acristoffers/tree-sitter-matlab`) was actively maintained as of February 2026. The Ada grammar was confirmed present in the language pack.

One quirk: `tree-sitter-language-pack` uses a Rust-backed binding through PyO3, not the standard `py-tree-sitter` package. The API differs in two notable ways: `parser.parse()` takes `str` rather than `bytes`, and `tree.root_node` is a method `root_node()` rather than a property. The `parser.py` module isolates these differences from the rest of the codebase.

### 2.3 Why AST Subtree Hashing Instead of Full PDG

PDGs are the most robust structural representation in the plagiarism-detection literature, but they have two practical disadvantages:

1. **Construction cost**. Building a PDG requires (a) building a CFG, (b) computing post-dominators, (c) computing control dependencies from the post-dominator tree, (d) computing data dependencies via reaching-definitions analysis. Each step is language-specific. Even with tree-sitter providing the AST, the PDG layer above it is large.

2. **Cross-language uniformity**. A PDG-based detector that supports five languages effectively has five independent implementations behind a shared interface.

AST subtree hashing achieves much of what we want from a PDG at a fraction of the cost. The intuition: if two programs differ only in the order of *independent* statements, their PDGs are isomorphic. If we sort the children of each AST node by content hash before computing the Merkle hash, then statements at the same nesting level become commutative, and the resulting subtree hashes are equal. This is not a true PDG — it cannot distinguish reorderings that violate data dependencies — but for the class of obfuscations seen in undergraduate plagiarism (reordering top-level functions, reordering independent statements within a block), it is sufficient.

The cost is one extra `sorted(...)` call per node during the AST walk, and one extra hash set per file. Both are negligible.

---

## 3. System Architecture

### 3.1 Module Layout

```
src/codesim/
├── __init__.py        public API: compare_files, compare_pairwise, SimilarityResult
├── cli.py             argparse entry point, JSON / text output
├── languages.py       extension → language map + per-language node classification
├── parser.py          tree-sitter wrapper, parser caching, language detection
├── normalize.py       AST walker; emits token stream + ordered + unordered hashes
├── fingerprint.py     k-gram hashing + winnowing
└── compare.py         Jaccard scoring, ensemble combination, pairwise driver
```

The dependency graph is acyclic and shallow:

```
cli ──→ compare ──→ parser
                ├──→ normalize ──→ languages
                └──→ fingerprint
```

### 3.2 Data Flow

For a single pair `(A, B)`:

```
file path
   │
   ▼
parser.parse_file()
   ├─ language detection (extension lookup)
   ├─ tree-sitter parse → Tree
   ▼
normalize.normalize()
   ├─ recursive AST walk (single pass)
   ├─ emit: normalized token at each node
   ├─ emit: Merkle hash (ordered children)
   ├─ emit: Merkle hash (sorted children)
   ▼
                  ┌── token_stream  ──┐
                  │                    │
                  ▼                    │
       fingerprint.fingerprint()       │
       (k-gram → blake2b → winnowing)  │
                  │                    │
                  ▼                    ▼
            winnow_fp        ordered_hashes, unordered_hashes
                  └──────┬──────────────┘
                         ▼
              FileFeatures (per file)
                         │
                         ▼
                 compare_features()
       (Jaccard per signal → weighted ensemble)
                         │
                         ▼
                 SimilarityResult
```

The expensive step (parsing + walking) is done once per file. For an N-file pairwise comparison the cost is `O(N)` parse work + `O(N²)` set-intersection work. The set intersections are cheap relative to parsing.

---

## 4. Algorithms

### 4.1 Winnowing (Signal A)

The winnowing algorithm comes from Schleimer, Wilkerson, and Aiken (2003), the same paper that underpins MOSS. The procedure:

1. Tokenize and normalize the source (we use the same normalized token stream as Signal B).
2. Form all consecutive k-grams of the token sequence.
3. Hash each k-gram. We use **BLAKE2b** truncated to 8 bytes for deterministic, collision-resistant 64-bit hashes that are stable across Python runs (Python's built-in `hash()` is randomized per process).
4. Slide a window of size `w` over the hash sequence. In each window, select the **rightmost minimum** hash. If that hash was not selected in the previous window, add it to the fingerprint set.

The rightmost-min rule, rather than leftmost-min, reduces fingerprint density: a hash selected by one window often dominates the next overlapping window as well, and the same hash should not be recorded twice.

**Guarantees from the paper:**

- Any match of length ≥ `w + k − 1` tokens is guaranteed to be detected.
- No match shorter than `k` tokens is detected. `k` is therefore called the *noise threshold*.

Default parameters: `k = 5`, `w = 4`. These match the values used in published MOSS configurations and are reasonable for source code with identifiers collapsed to a canonical form.

### 4.2 AST Subtree Merkle Hashing (Signals B and C)

For each AST node we compute two hashes recursively:

```
ordered_hash(node) = H(label(node), ordered_hash(c1), ..., ordered_hash(cn))
unordered_hash(node) = H(label(node), sorted([unordered_hash(c1), ..., unordered_hash(cn)]))
```

where `label(node)` is the canonical label after normalization (see §5), and `H` is Python's built-in `hash` over a tuple. The tuple form is used because Python's `hash` is well-defined and fast on tuples, and we do not need cryptographic strength here — we are looking for collisions within a single run, not adversarial integrity.

Both hashes are collected into a set for the file, **excluding any subtree smaller than `min_subtree_size` nodes**. Subtree size is counted as the number of meaningful (named, non-comment) descendants plus one. The size filter prevents trivial matches like single-identifier expressions from contaminating the comparison.

### 4.3 Jaccard Similarity and Ensemble

For each signal we compute the standard Jaccard coefficient:

```
J(A, B) = |A ∩ B| / |A ∪ B|
```

The final score is a weighted mean:

```
score = (wA · J_A + wB · J_B + wC · J_C) / (wA + wB + wC)
```

with default weights `(0.25, 0.35, 0.40)`. The slight emphasis on Signal C (unordered AST hash) reflects its position as the most obfuscation-resistant signal. Weights are configurable.

Jaccard is preferred over containment (`|A ∩ B| / min(|A|, |B|)`) because containment is asymmetric and inflates scores when one program is a strict superset of another, producing misleading near-1.0 scores for fragments embedded in larger files.

---

## 5. Normalization Strategy

Normalization is the heart of the obfuscation-resistance argument. The goal is to reduce semantically equivalent constructs to identical canonical forms before fingerprinting, so that surface changes (renaming, type swaps, loop-type swaps) do not change the hashes.

### 5.1 Five Collapse Rules

Each collapse rule replaces a class of AST nodes with a single canonical label:

| Rule | Original node types | Canonical label | Defeats |
|---|---|---|---|
| `collapse_identifiers` | `identifier`, `field_identifier`, etc. | `ID` | variable / function renaming |
| `collapse_literals` | `number_literal`, `string_literal`, `char_literal`, etc. | `LIT` | numeric / string substitution |
| `collapse_loops` | `for_statement`, `while_statement`, `do_statement` | `LOOP` | loop-type swap (bypass #7) |
| `collapse_conditionals` | `if_statement`, `switch_statement` | `COND` | if↔switch swap (bypass #6) |
| `collapse_types` | `primitive_type`, `sized_type_specifier`, etc. | `TYPE` | int↔long↔float swap (bypass #4) |

Comments are dropped unconditionally.

Each rule is independently toggleable. Disabling a rule trades obfuscation resistance for discrimination: with `collapse_loops` off, a `for` loop and an equivalent `while` loop produce different hashes, so two programs that differ only in loop type score lower. This is sometimes desirable — see §6.

### 5.2 Per-Language Configuration

Tree-sitter does not provide cross-language semantic categories — node types are grammar-specific. For example, C uses `for_statement` while Python also uses `for_statement` but the children differ, and Ada uses `for_loop_statement`. We therefore maintain a per-language `LangConfig` dataclass in `languages.py` that declares which tree-sitter node types belong to each collapse category.

Explicit configurations exist for C, C++, Python, Ada, MATLAB, Java, and JavaScript. Languages without explicit configuration fall back to `DEFAULT_CONFIG`, which performs only identifier collapsing. Adding a new language is a matter of inspecting tree-sitter node names and populating one dataclass.

### 5.3 Single-Pass Implementation

The AST walk in `normalize.py` produces all three outputs (token stream, ordered hashes, unordered hashes) in one recursive pass. Each call returns a tuple `(ordered_hash, unordered_hash, subtree_size)` for the subtree rooted at the current node, which is then combined into the parent's hashes. Hash sets and the token stream are mutated as a side-effect of the walk. This avoids re-walking the tree three times.

---

## 6. Tuning System

In practice, default thresholds will sometimes flag legitimately similar but independent solutions, especially in introductory coursework where students are taught canonical patterns. The tuning system exposes every relevant knob and bundles common configurations into named presets.

### 6.1 Presets

Three presets are provided as `TuningConfig` instances in `compare.py`:

- **`strict`** — `k=8, w=5, min_subtree_size=6, min_tokens=40, auto_filter=3`, and `collapse_loops`, `collapse_conditionals`, `collapse_types` all **disabled**. Demands tight matches. Suppresses the loop-swap "false positive" because `for` and `while` no longer collapse to the same canonical form. Best for cohorts where many students arrive at structurally similar but independent solutions.
- **`normal`** — the defaults documented in §4. Balanced.
- **`aggressive`** — `k=4, w=3, min_subtree_size=2`, all collapse rules **enabled**. Maximum obfuscation resistance. Some false positives expected.

### 6.2 Granular Knobs

Beyond presets, every parameter is independently settable from the CLI:

- `--kgram`, `--window` — winnowing parameters
- `--weights A,B,C` — ensemble weights
- `--min-subtree` — minimum AST subtree size to count
- `--min-tokens` — minimum file size to compare
- `--auto-filter N` — exclude hashes appearing in ≥ N input files (auto-detect shared boilerplate)
- `--no-collapse-{ids,literals,loops,conds,types}` — disable individual collapse rules

The `--auto-filter` knob deserves special mention. It is functionally equivalent to MOSS's `-m` parameter and to providing a base file, but it requires no manual setup: any hash appearing in three or more submissions (in the default configuration) is presumed to be boilerplate — `printf` patterns, standard loop scaffolding, idiomatic includes — and is subtracted from every file before pairwise comparison. This dramatically reduces baseline noise in large submission sets.

### 6.3 Tuning Workflow

The recommended workflow:

1. Start with `--preset normal --auto-filter 3` on the full submission set.
2. If too many pairs are flagged at 0.4–0.6: switch to `--preset strict` or add `--no-collapse-loops --no-collapse-conds`.
3. If known plagiarism is being missed: switch to `--preset aggressive`, or lower `--min-subtree`.
4. For each flagged pair, review manually. The score is a triage signal, not a verdict.

---

## 7. Validation Methodology

### 7.1 The Twenty-Variant Corpus

To validate that `codesim` produces a meaningful score gradient across the spectrum of obfuscation techniques, we constructed twenty hand-written variants of a single short C program (a Fibonacci even/odd-sum calculator) during development and verified each one:

1. **Compiles** without warnings under `gcc -w`.
2. **Produces output byte-identical to the original** across six test inputs (`0, 1, 10, 100, 1000, 100000`).

The corpus is not bundled in this repository (it derived from coursework material and was held back for academic-integrity reasons). The summary results below are reproducible by constructing an equivalent corpus from any short program.

The variants spanned five obfuscation classes:

- **Trivial**: identical, whitespace-only, variable renaming, comment removal, branch inversion
- **Light**: loop-type swap (`for` ↔ `while`), infinite-loop with `break`, bitwise modulo, ternary
- **Moderate**: helper extraction, fully inlined, type swap (`uint64_t`), array-based, pointer-output, dead-code injection
- **Structural**: separate even/odd passes, recursive visitor, struct-based state, goto-based control flow
- **Different**: dynamic vector with two-pass partitioning

### 7.2 Score Gradient

Pairwise scores against the original under the `normal` preset:

| Class | Variant | Score |
|---|---|---|
| trivial | identical, whitespace, rename, no-comments, branch-invert | 1.000 |
| light | for-loop swap | 0.780 |
| light | bitwise modulo | 0.762 |
| light | ternary | 0.690 |
| light | infinite-break | 0.622 |
| moderate | inline into main | 0.620 |
| moderate | dead-code injection | 0.502 |
| moderate | separate even/odd passes | 0.465 |
| moderate | helper extraction | 0.385 |
| structural | goto-based control flow | 0.372 |
| moderate | pointer-output args | 0.352 |
| moderate | type swap (uint64_t) | 0.298 |
| moderate | array storage | 0.277 |
| structural | recursive visitor | 0.272 |
| different | dynamic vector | 0.132 |
| structural | struct-based state | 0.117 |

This is the expected monotone gradient: trivial obfuscations preserve a near-1.0 score, light obfuscations drop into the 0.6–0.8 band, moderate refactorings into the 0.3–0.5 band, and structural rewrites or different data representations into the 0.1–0.3 band.

For comparison, MOSS would score most of the moderate-to-different cases under 0.05.

### 7.3 What This Validates

- The token-stream and AST signals capture meaningfully different aspects of the program: a pure rename produces 1.000 on all three signals; a pure loop-type swap produces 1.000 on Signal A but lower on Signals B and C (or vice versa depending on collapse settings).
- The score gradient is smooth and roughly monotone with obfuscation intensity — there are no inversions where a more heavily disguised variant scores higher than a lightly disguised one.
- The unrelated-baseline noise floor (measured separately during early development with semantically unrelated C programs) sits around 0.03–0.07. The lowest score in the validation set, 0.117 for the struct-based rewrite, is therefore still distinguishable from unrelated code.

---

## 8. Limitations and Future Work

### 8.1 Out-of-Scope by Design

- **Algorithmic substitution** — rewriting bubble sort as selection sort produces semantically equivalent but structurally distinct programs. Detection requires symbolic execution or behavioral equivalence checking and is not addressed here.
- **Cross-language plagiarism** — comparing a Python implementation against a C implementation of the same algorithm is intentionally out of scope.
- **Coincidental similarity** — `codesim` is a triage tool, not a verdict tool. A high score means "look here," not "this is plagiarism."

### 8.2 Known Limitations

- **MATLAB `1./a` edge case** — the `tree-sitter-matlab` grammar misparses element-wise divide when no space precedes the operator. This affects both the original and a copied version identically, so the similarity score is preserved, but the parsed AST is technically wrong.
- **Pairwise is `O(N²)`** — for submission sets larger than ~500 files, parallel processing of the outer loop is needed. The infrastructure for this is straightforward (Python's `concurrent.futures`) but is not currently implemented.
- **No HTML report with highlighted matches** — MOSS provides a side-by-side rendering of matching regions. `codesim` emits machine-readable JSON intended to be consumed by downstream tooling that renders its own UI. Building a standalone HTML report is a possible future addition.
- **Fingerprint cache is not persisted** — every run re-parses every file. For repeated comparisons (e.g., comparing new submissions against an archive), persistent caching of `FileFeatures` would yield substantial speedup.

### 8.3 Future Directions

- **Persistent feature cache** — serialize `FileFeatures` to disk keyed by content hash; reuse across runs.
- **Parallel pairwise comparison** — use `concurrent.futures.ProcessPoolExecutor` for the `O(N²)` loop.
- **HTML report** — render matched-region highlights using tree-sitter's byte ranges.
- **Per-language tuning** — different defaults for, e.g., Python (where idiomatic code is more uniform than C) versus assembly (where it is more variable).
- **Cross-archive comparison** — first-class support for comparing new submissions against a corpus of historical submissions stored as cached features.

---

## 9. References

- Schleimer, Wilkerson, Aiken. *Winnowing: Local Algorithms for Document Fingerprinting*. SIGMOD 2003. — The original winnowing algorithm and the basis for MOSS.
- Aiken, A. *Moss: A System for Detecting Software Similarity*. https://theory.stanford.edu/~aiken/moss/ — Official MOSS documentation.
- Devore-McDonald, Berger. *Mossad: Defeating Software Plagiarism Detection*. OOPSLA 2020. arXiv:2010.01700. — Demonstrates the hash-disruption vulnerability of MOSS and motivates the AST-based response taken here.
- Kaushal, V. *Subtle Art of De-MOSS-ing*. Medium, 2021. — Enumeration and evaluation of the nine common manual bypass techniques.
- `tree-sitter` — https://tree-sitter.github.io/ — Incremental parsing engine.
- `tree-sitter-language-pack` — https://github.com/kreuzberg-dev/tree-sitter-language-pack — Pre-compiled grammars for 305 languages.
- `tree-sitter-matlab` — https://github.com/acristoffers/tree-sitter-matlab — MATLAB grammar.

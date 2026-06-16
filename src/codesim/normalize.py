"""AST normalization: produces canonical token stream and subtree hashes.

Three outputs per file:
    - token_stream: list[str]  — normalized token sequence for winnowing (Signal A)
    - ordered_hashes: set[int] — Merkle hashes of AST subtrees, child order preserved (Signal B)
    - unordered_hashes: set[int] — Merkle hashes, children sorted (Signal C, order-independent)

Normalization is configurable via NormalizationOptions. Each collapse rule can be
toggled independently — stricter settings produce lower similarity scores by keeping
more distinctions in the AST.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from codesim.languages import LangConfig


_ID = "ID"
_LIT = "LIT"
_LOOP = "LOOP"
_COND = "COND"
_TYPE = "TYPE"


@dataclass(frozen=True)
class NormalizationOptions:
    """Controls how aggressively AST nodes are collapsed.

    Enabling a collapse rule trades discrimination for obfuscation resistance:
        - collapse_identifiers: defeats variable renaming
        - collapse_literals: defeats numeric/string substitution
        - collapse_loops: defeats for↔while↔do-while swap
        - collapse_conditionals: defeats if↔switch swap
        - collapse_types: defeats int↔long, float↔double substitution

    min_subtree_size: subtrees smaller than this contribute no AST hash (filters
    trivial matches like single-statement expressions).
    """
    collapse_identifiers: bool = True
    collapse_literals: bool = True
    collapse_loops: bool = True
    collapse_conditionals: bool = True
    collapse_types: bool = True
    min_subtree_size: int = 3


def _label(node, cfg: LangConfig, opt: NormalizationOptions) -> str | None:
    t = node.kind()
    if t in cfg.comment_nodes:
        return None
    if opt.collapse_identifiers and t in cfg.identifier_nodes:
        return _ID
    if opt.collapse_literals and t in cfg.literal_nodes:
        return _LIT
    if opt.collapse_loops and t in cfg.loop_nodes:
        return _LOOP
    if opt.collapse_conditionals and t in cfg.cond_nodes:
        return _COND
    if opt.collapse_types and t in cfg.type_nodes:
        return _TYPE
    return t


def _iter_meaningful_children(node, cfg: LangConfig):
    n = node.named_child_count()
    for i in range(n):
        c = node.named_child(i)
        if c.kind() in cfg.comment_nodes:
            continue
        yield c


@dataclass
class Normalized:
    token_stream: list[str]
    ordered_hashes: set[int]
    unordered_hashes: set[int]
    node_count: int


def normalize(root, cfg: LangConfig, opt: NormalizationOptions | None = None) -> Normalized:
    if opt is None:
        opt = NormalizationOptions()

    tokens: list[str] = []
    ordered: set[int] = set()
    unordered: set[int] = set()

    def walk(node) -> tuple[int, int, int]:
        label = _label(node, cfg, opt)
        if label is None:
            return 0, 0, 0

        children = list(_iter_meaningful_children(node, cfg))

        if not children:
            tokens.append(label)
            h = hash(("leaf", label))
            return h, h, 1

        child_ordered: list[int] = []
        child_unordered: list[int] = []
        size = 1
        tokens.append(label)
        for c in children:
            o, u, s = walk(c)
            if s == 0:
                continue
            child_ordered.append(o)
            child_unordered.append(u)
            size += s

        if not child_ordered:
            h = hash(("leaf", label))
            return h, h, size

        o_hash = hash((label, tuple(child_ordered)))
        u_hash = hash((label, tuple(sorted(child_unordered))))

        if size >= opt.min_subtree_size:
            ordered.add(o_hash)
            unordered.add(u_hash)

        return o_hash, u_hash, size

    _, _, total = walk(root)
    return Normalized(
        token_stream=tokens,
        ordered_hashes=ordered,
        unordered_hashes=unordered,
        node_count=total,
    )

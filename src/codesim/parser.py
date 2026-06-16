"""Tree-sitter parser wrapper. One parser instance per language, cached.

Note: tree-sitter-language-pack uses a Rust-backed binding whose API differs from
the standard py-tree-sitter package. Differences handled here:
    - parser.parse takes str, not bytes
    - tree.root_node is a method (root_node())
    - node.kind() instead of node.type
    - node.is_named() / has_error() are methods
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from tree_sitter_language_pack import get_parser


class ParseError(RuntimeError):
    pass


@lru_cache(maxsize=64)
def _parser_for(lang: str):
    try:
        return get_parser(lang)
    except Exception as e:
        raise ParseError(f"no tree-sitter parser available for language {lang!r}: {e}") from e


def parse_source(source: str, lang: str):
    parser = _parser_for(lang)
    return parser.parse(source)


def parse_file(path: str | Path, lang: str):
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return parse_source(text, lang), text

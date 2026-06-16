"""Language detection and per-language AST node classification."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


EXT_TO_LANG: dict[str, str] = {
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    ".hh": "cpp",
    ".py": "python",
    ".pyw": "python",
    ".ada": "ada",
    ".adb": "ada",
    ".ads": "ada",
    ".m": "matlab",
    ".java": "java",
    ".js": "javascript",
    ".mjs": "javascript",
    ".ts": "typescript",
    ".cs": "csharp",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".hs": "haskell",
    ".ml": "ocaml",
    ".lua": "lua",
    ".pl": "perl",
    ".r": "r",
}


@dataclass(frozen=True)
class LangConfig:
    """Per-language node-type classification for AST normalization.

    Sets contain tree-sitter node type names. Unknown languages fall back to
    DEFAULT_CONFIG which performs no normalization beyond identifier collapsing.
    """

    name: str
    identifier_nodes: frozenset[str] = field(default_factory=frozenset)
    literal_nodes: frozenset[str] = field(default_factory=frozenset)
    loop_nodes: frozenset[str] = field(default_factory=frozenset)
    cond_nodes: frozenset[str] = field(default_factory=frozenset)
    type_nodes: frozenset[str] = field(default_factory=frozenset)
    comment_nodes: frozenset[str] = field(default_factory=lambda: frozenset({"comment", "line_comment", "block_comment"}))


_C_FAMILY_LITERALS = frozenset({
    "number_literal", "string_literal", "char_literal", "true", "false",
    "null", "concatenated_string", "raw_string_literal",
})

CONFIGS: dict[str, LangConfig] = {
    "c": LangConfig(
        name="c",
        identifier_nodes=frozenset({"identifier", "field_identifier", "type_identifier", "statement_identifier"}),
        literal_nodes=_C_FAMILY_LITERALS,
        loop_nodes=frozenset({"for_statement", "while_statement", "do_statement"}),
        cond_nodes=frozenset({"if_statement", "switch_statement", "case_statement"}),
        type_nodes=frozenset({"primitive_type", "sized_type_specifier", "type_descriptor"}),
    ),
    "cpp": LangConfig(
        name="cpp",
        identifier_nodes=frozenset({"identifier", "field_identifier", "type_identifier", "namespace_identifier", "statement_identifier"}),
        literal_nodes=_C_FAMILY_LITERALS | frozenset({"user_defined_literal"}),
        loop_nodes=frozenset({"for_statement", "for_range_loop", "while_statement", "do_statement"}),
        cond_nodes=frozenset({"if_statement", "switch_statement", "case_statement"}),
        type_nodes=frozenset({"primitive_type", "sized_type_specifier", "type_descriptor", "auto"}),
    ),
    "python": LangConfig(
        name="python",
        identifier_nodes=frozenset({"identifier"}),
        literal_nodes=frozenset({"integer", "float", "string", "true", "false", "none", "concatenated_string"}),
        loop_nodes=frozenset({"for_statement", "while_statement"}),
        cond_nodes=frozenset({"if_statement", "match_statement", "case_clause"}),
        type_nodes=frozenset({"type"}),
    ),
    "ada": LangConfig(
        name="ada",
        identifier_nodes=frozenset({"identifier"}),
        literal_nodes=frozenset({"numeric_literal", "string_literal", "character_literal", "based_literal", "decimal_literal"}),
        loop_nodes=frozenset({"loop_statement", "while_loop_statement", "for_loop_statement", "basic_loop_statement"}),
        cond_nodes=frozenset({"if_statement", "case_statement"}),
        type_nodes=frozenset({"subtype_indication", "type_definition"}),
    ),
    "matlab": LangConfig(
        name="matlab",
        identifier_nodes=frozenset({"identifier"}),
        literal_nodes=frozenset({"number", "string", "boolean", "true", "false"}),
        loop_nodes=frozenset({"for_statement", "while_statement"}),
        cond_nodes=frozenset({"if_statement", "switch_statement"}),
        type_nodes=frozenset(),
    ),
    "java": LangConfig(
        name="java",
        identifier_nodes=frozenset({"identifier", "type_identifier"}),
        literal_nodes=frozenset({"decimal_integer_literal", "hex_integer_literal", "octal_integer_literal", "decimal_floating_point_literal", "string_literal", "character_literal", "true", "false", "null_literal"}),
        loop_nodes=frozenset({"for_statement", "enhanced_for_statement", "while_statement", "do_statement"}),
        cond_nodes=frozenset({"if_statement", "switch_statement", "switch_expression"}),
        type_nodes=frozenset({"integral_type", "floating_point_type", "boolean_type"}),
    ),
    "javascript": LangConfig(
        name="javascript",
        identifier_nodes=frozenset({"identifier", "property_identifier", "shorthand_property_identifier"}),
        literal_nodes=frozenset({"number", "string", "template_string", "true", "false", "null", "undefined", "regex"}),
        loop_nodes=frozenset({"for_statement", "for_in_statement", "for_of_statement", "while_statement", "do_statement"}),
        cond_nodes=frozenset({"if_statement", "switch_statement"}),
        type_nodes=frozenset(),
    ),
}

DEFAULT_CONFIG = LangConfig(
    name="default",
    identifier_nodes=frozenset({"identifier", "type_identifier", "field_identifier"}),
)


def detect_language(path: str | Path) -> str | None:
    ext = Path(path).suffix.lower()
    return EXT_TO_LANG.get(ext)


def get_config(lang: str) -> LangConfig:
    return CONFIGS.get(lang, DEFAULT_CONFIG)

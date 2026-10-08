"""Tokenizer + recursive parser for Clausewitz script files -> nested dicts/lists."""

from pathlib import Path
from typing import NamedTuple
import re


class Cmp(NamedTuple):
    """A comparison entry, e.g. `num_ascension_perks > 2` → Cmp('>', '2')."""
    op: str
    value: str


class IfBlock(dict):
    """An `if = { … }` block. `chain` holds the `else_if`/`else` blocks that directly
    follow it, in order — duplicate-key promotion would otherwise lose which `else`
    belongs to which `if`. A plain dict to every other reader."""

    def __init__(self, *args):
        super().__init__(*args)
        self.chain: list[tuple[str, dict]] = []


# ---------------------------------------------------------------------------
# Clausewitz parser
# ---------------------------------------------------------------------------

def tokenize(text: str) -> list[str]:
    """Split Clausewitz script text into tokens: keys, =, {, }, values."""
    # Strip comments
    text = re.sub(r'#[^\n]*', '', text)
    # Tokens: quoted strings, @variable names, operators, identifiers
    # @identifier is kept as a single token so variable references survive parsing
    pattern = r'"[^"]*"|@[a-zA-Z_]\w*|[{}=<>!]|[^\s{}=<>!@"]+'
    return re.findall(pattern, text)


class Parser:
    """Recursive Clausewitz parser -> nested Python dicts/lists."""

    def __init__(self, tokens: list[str]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def consume(self) -> str:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def parse_block(self) -> dict | list:
        """Parse the content between { }. Returns a dict, or a list if no = found."""
        result = {}
        items_list = []
        is_list = None  # determined on first element
        open_if: IfBlock | None = None  # the if-chain the next else_if/else continues

        while self.peek() not in (None, '}'):
            key = self.consume()

            if key == '{':
                # Anonymous sub-block (e.g. in_breach_of = { { key = X } { key = Y } })
                # Recurse to consume its content and closing '}', then discard.
                self.parse_block()
                if self.peek() == '}':
                    self.consume()
            elif self.peek() in ('=', '<', '>', '!'):
                op = self.consume()
                if op != '=' and self.peek() == '=':
                    op += self.consume()  # >=, <=, !=
                is_list = False
                value = self.parse_value()
                if op != '=':
                    value = Cmp(op, value)
                if key == 'if' and isinstance(value, dict):
                    value = open_if = IfBlock(value)
                elif key in ('else_if', 'else') and open_if is not None \
                        and isinstance(value, dict):
                    open_if.chain.append((key, value))
                    if key == 'else':
                        open_if = None
                else:
                    open_if = None
                # Duplicate keys -> promote to list
                if key in result:
                    if not isinstance(result[key], list):
                        result[key] = [result[key]]
                    result[key].append(value)
                else:
                    result[key] = value
            else:
                # No '=' -> plain list entry (e.g. portrait IDs)
                is_list = True
                items_list.append(key.strip('"'))

        return items_list if is_list else result

    def parse_value(self):
        tok = self.peek()
        if tok == '{':
            self.consume()  # '{'
            val = self.parse_block()
            if self.peek() == '}':
                self.consume()  # '}'
            return val
        else:
            return self.consume().strip('"')

    def parse(self) -> dict:
        """Parse the entire file as a top-level block."""
        result = {}
        while self.peek() is not None:
            key = self.consume()
            if self.peek() == '=':
                self.consume()  # skip optional '='
                value = self.parse_value()
            elif self.peek() == '{':
                # Clausewitz allows `key { }` without '=' (e.g. named_colors)
                value = self.parse_value()
            else:
                continue  # bare key with no value, skip
            if key in result:
                if not isinstance(result[key], list):
                    result[key] = [result[key]]
                result[key].append(value)
            else:
                result[key] = value
        return result


def parse_file(path: Path) -> dict:
    text = path.read_text(encoding='utf-8-sig', errors='replace')
    tokens = tokenize(text)
    return Parser(tokens).parse()

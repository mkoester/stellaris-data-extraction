"""Generic value/block helpers shared by all extractors."""


def _to_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        # Flatten one level: duplicate keys in source files produce nested lists
        result = []
        for item in value:
            if isinstance(item, list):
                result.extend(item)
            else:
                result.append(item)
        return result
    if isinstance(value, dict):
        return list(value.keys())
    return []


def _resolve_modifier_float(raw) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, list):
        raw = raw[-1]
    try:
        return float(str(raw).strip())
    except (ValueError, TypeError):
        return None


def _find_first(node, key: str):
    """Depth-first search for the first value at `key` anywhere in node."""
    if isinstance(node, list):
        for item in node:
            r = _find_first(item, key)
            if r is not None:
                return r
    elif isinstance(node, dict):
        if key in node:
            return node[key]
        for v in node.values():
            r = _find_first(v, key)
            if r is not None:
                return r
    return None


def _resolve_var(raw, variables: dict[str, float]) -> float | None:
    """Resolve a scalar that may be an @scripted_variable reference or a literal."""
    if raw is None:
        return None
    s = str(raw).strip()
    return variables.get(s) if s.startswith('@') else _resolve_modifier_float(raw)


def _flatten_text(node) -> str:
    """Render a parsed (nested) block back to a flat, searchable 'k = v' string."""
    if isinstance(node, dict):
        return ' '.join(f"{k} = {_flatten_text(v)}" for k, v in node.items())
    if isinstance(node, list):
        return ' '.join(_flatten_text(x) for x in node)
    return str(node)


def _as_blocks(v) -> list[dict]:
    """Normalise a sub-block value to a list of dicts.

    Unlike _to_list (which returns a dict's *keys*), this wraps a single dict
    as [dict] — correct for blocks like init_effect / add_modifier that are
    promoted to a list only when they appear more than once."""
    if isinstance(v, dict):
        return [v]
    if isinstance(v, list):
        return [x for x in v if isinstance(x, dict)]
    return []

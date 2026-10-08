"""Godot's text Variant syntax, as used in .tscn/.tres files and project.godot.

Values map to Python like this:
  null/true/false/ints/floats -> None/bool/int/float
  "text"                      -> str
  &"name"                     -> StringName
  [a, b]                      -> list
  {k: v}                      -> dict
  Vector3(1, 2, 3), SubResource("id"), Transform3D(...), Array[T]([...]) ... -> Call(name, args)
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field


class StringName(str):
    """A Godot StringName (&"..."). Compares equal to the plain string."""


@dataclass(frozen=True)
class Call:
    """A constructor-style value: Vector3(1, 2, 3), SubResource("BoxMesh_x"), Array[int]([1])."""

    name: str
    args: tuple = field(default_factory=tuple)


class VariantError(ValueError):
    pass


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "\\": "\\", '"': '"', "'": "'", "/": "/"}


def parse(text: str, pos: int = 0) -> tuple[object, int]:
    """Parse one value starting at text[pos]. Returns (value, index just past it)."""
    return _Parser(text).value(pos)


def parse_all(text: str) -> object:
    value, end = parse(text)
    if text[end:].strip():
        raise VariantError(f"trailing text after value: {text[end:end + 20]!r}")
    return value


class _Parser:
    def __init__(self, text: str):
        self.t = text

    def ws(self, i: int) -> int:
        t = self.t
        while i < len(t):
            c = t[i]
            if c in " \t\r\n":
                i += 1
            elif c == ";":  # comment to end of line
                while i < len(t) and t[i] != "\n":
                    i += 1
            else:
                break
        return i

    def value(self, i: int) -> tuple[object, int]:
        t = self.t
        i = self.ws(i)
        if i >= len(t):
            raise VariantError("unexpected end of text")
        c = t[i]
        if c == '"':
            return self.string(i)
        if c == "&" and t.startswith('&"', i):
            s, j = self.string(i + 1)
            return StringName(s), j
        if c == "^" and t.startswith('^"', i):  # NodePath shorthand
            s, j = self.string(i + 1)
            return Call("NodePath", (s,)), j
        if c == "[":
            return self.seq(i + 1, "]")
        if c == "{":
            return self.mapping(i + 1)
        if c in "-+.0123456789":
            return self.number(i)
        if c.isalpha() or c == "_":
            return self.word(i)
        raise VariantError(f"unexpected {c!r} at {i}")

    def string(self, i: int) -> tuple[str, int]:
        t = self.t
        assert t[i] == '"'
        i += 1
        out = []
        while True:
            if i >= len(t):
                raise VariantError("unterminated string")
            c = t[i]
            if c == '"':
                return "".join(out), i + 1
            if c == "\\":
                n = t[i + 1] if i + 1 < len(t) else ""
                if n == "u" or n == "U":
                    width = 4 if n == "u" else 6
                    out.append(chr(int(t[i + 2:i + 2 + width], 16)))
                    i += 2 + width
                    continue
                out.append(_ESCAPES.get(n, n))
                i += 2
                continue
            out.append(c)
            i += 1

    def number(self, i: int) -> tuple[object, int]:
        t = self.t
        j = i
        while j < len(t) and (t[j].isalnum() or t[j] in "+-._"):
            j += 1
        raw = t[i:j].replace("_", "")
        low = raw.lower()
        if low in ("inf", "+inf"):
            return math.inf, j
        if low == "-inf":
            return -math.inf, j
        if low in ("nan", "+nan", "-nan"):
            return math.nan, j
        try:
            if any(ch in raw for ch in ".eE") and not low.startswith(("0x", "-0x")):
                return float(raw), j
            return int(raw, 0), j
        except ValueError:
            raise VariantError(f"bad number {raw!r} at {i}") from None

    def word(self, i: int) -> tuple[object, int]:
        t = self.t
        j = i
        while j < len(t) and (t[j].isalnum() or t[j] == "_"):
            j += 1
        name = t[i:j]
        if name == "true":
            return True, j
        if name == "false":
            return False, j
        if name in ("null", "nil"):
            return None, j
        if name in ("inf", "inf_neg", "nan"):
            return {"inf": math.inf, "inf_neg": -math.inf, "nan": math.nan}[name], j
        # Typed containers: Array[int]([...]), Dictionary[String, int]({...})
        if j < len(t) and t[j] == "[":
            depth, k = 0, j
            while k < len(t):
                if t[k] == "[":
                    depth += 1
                elif t[k] == "]":
                    depth -= 1
                    if depth == 0:
                        break
                k += 1
            name = t[i:k + 1]
            j = k + 1
        j = self.ws(j)
        if j < len(t) and t[j] == "(":
            args, j = self.seq(j + 1, ")")
            return Call(name, tuple(args)), j
        raise VariantError(f"bare word {name!r} at {i}")

    def seq(self, i: int, close: str) -> tuple[list, int]:
        out = []
        i = self.ws(i)
        if i < len(self.t) and self.t[i] == close:
            return out, i + 1
        while True:
            v, i = self.value(i)
            out.append(v)
            i = self.ws(i)
            if i >= len(self.t):
                raise VariantError(f"missing {close!r}")
            if self.t[i] == ",":
                i = self.ws(i + 1)
                if i < len(self.t) and self.t[i] == close:  # trailing comma
                    return out, i + 1
                continue
            if self.t[i] == close:
                return out, i + 1
            raise VariantError(f"expected ',' or {close!r} at {i}")

    def mapping(self, i: int) -> tuple[dict, int]:
        out: dict = {}
        i = self.ws(i)
        if i < len(self.t) and self.t[i] == "}":
            return out, i + 1
        while True:
            k, i = self.value(i)
            i = self.ws(i)
            if i >= len(self.t) or self.t[i] not in ":=":
                raise VariantError(f"expected ':' at {i}")
            v, i = self.value(i + 1)
            out[_hashable(k)] = v
            i = self.ws(i)
            if i < len(self.t) and self.t[i] == ",":
                i = self.ws(i + 1)
                if i < len(self.t) and self.t[i] == "}":
                    return out, i + 1
                continue
            if i < len(self.t) and self.t[i] == "}":
                return out, i + 1
            raise VariantError(f"expected ',' or '}}' at {i}")


def _hashable(v):
    if isinstance(v, list):
        return tuple(_hashable(x) for x in v)
    if isinstance(v, dict):
        return tuple((k, _hashable(x)) for k, x in v.items())
    return v


# ---------------------------------------------------------------- writing


def fmt_real(x: float, *, in_call: bool = False) -> str:
    """Format a float the way Godot does: '60.0' as a property, '60' inside Vector3(...) and friends."""
    if math.isnan(x):
        return "nan"
    if math.isinf(x):
        return "inf" if x > 0 else "inf_neg"
    if in_call and x == int(x) and abs(x) < 1e15:  # whole numbers inside Vector3(...) and friends: 20, not 2e+1
        return str(int(x)) if x != 0 else "0"
    if in_call:  # vectors, colors and transforms hold 32-bit floats: shortest text that reads back the same
        target = struct.pack("<f", x)
        for prec in range(1, 10):
            s = f"{x:.{prec}g}"
            if struct.pack("<f", float(s)) == target:
                break
    else:
        s = repr(float(x))
    if ("e" in s or "E" in s) and 1e-4 <= abs(x) < 1e15:  # %g switched to an exponent; Godot writes it out
        s = f"{float(s):.12f}"
    elif "e" in s or "E" in s:
        mant, exp = s.lower().split("e")
        return f"{mant}e{int(exp):+d}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    if s == "-0":
        s = "0" if in_call else "-0"
    if not in_call and "." not in s:
        s += ".0"
    return s


def escape(s: str) -> str:
    # Godot keeps real newlines inside strings in text resources and only escapes \ and ".
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def dump(v: object, *, in_call: bool = False) -> str:
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, StringName):
        return "&" + escape(v)
    if isinstance(v, str):
        return escape(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return fmt_real(v, in_call=in_call)
    if isinstance(v, Call):
        if v.name == "NodePath" and len(v.args) == 1:
            return "NodePath(" + escape(v.args[0]) + ")"
        typed = "[" in v.name and v.name.split("[", 1)[0] in ("Array", "Dictionary")
        return v.name + "(" + ", ".join(dump(a, in_call=not typed) for a in v.args) + ")"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(dump(x) for x in v) + "]"
    if isinstance(v, dict):
        if not v:
            return "{}"
        return "{\n" + ",\n".join(f"{dump(k)}: {dump(x)}" for k, x in v.items()) + "\n}"
    raise VariantError(f"can't write {type(v).__name__}")


def same(a: object, b: object, tol: float = 1e-5) -> bool:
    """Equality that ignores float noise, so re-reading a value never counts as a change."""
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
        if math.isnan(a) and math.isnan(b):
            return True
        return abs(a - b) <= tol * max(1.0, abs(a), abs(b))
    if isinstance(a, Call) and isinstance(b, Call):
        return a.name == b.name and same(list(a.args), list(b.args), tol)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(same(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k], tol) for k in a)
    return a == b

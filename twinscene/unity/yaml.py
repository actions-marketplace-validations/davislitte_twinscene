"""Unity's flavour of YAML, read and edited without disturbing anything that isn't touched.

A Unity asset is a series of objects:

    --- !u!4 &705507995            class id 4 (Transform), file id 705507995
    Transform:
      m_LocalPosition: {x: 0, y: 3, z: 0}
      m_Children: []

Values are read into plain Python: dict (FlowMap for {...}), list (FlowSeq for [...]) and str for every scalar
(Unity's schema decides the type, so readers convert). Each object keeps its original lines; set() rewrites only
the lines of the entry it changes, and dump() of an unedited document returns the input exactly.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field


class FlowMap(dict):
    """A mapping written inline: {x: 0, y: 1, z: 0}."""


class FlowSeq(list):
    """A sequence written inline: [] or [a, b]."""


class UnityYamlError(ValueError):
    pass


_DOC = re.compile(r"^--- !u!(-?\d+) &(-?\d+)( stripped)?\s*$")


@dataclass
class UObject:
    class_id: int
    file_id: int
    stripped: bool
    lines: list[str]  # including the '--- !u!' line
    type_name: str = ""
    data: dict = field(default_factory=dict)
    spans: dict = field(default_factory=dict)  # path tuple -> (first line, end line) within self.lines

    def get(self, *path, default=None):
        v = self.data
        for p in path:
            try:
                v = v[p]
            except (KeyError, IndexError, TypeError):
                return default
        return v

    # ------------------------------------------------------------ editing

    def set(self, path: tuple | str, value) -> None:
        """Set a value by path, e.g. ("m_LocalPosition",) or ("m_Component", 0, "component").

        Unchanged values leave the text alone. Changing an entry rewrites only that entry's lines. A new key is
        added at the end of its mapping.
        """
        path = (path,) if isinstance(path, str) else tuple(path)
        parent = self.get(*path[:-1]) if len(path) > 1 else self.data
        if parent is None:
            raise KeyError(f"no parent for {path}")
        key = path[-1]
        exists = (isinstance(parent, dict) and key in parent) or (isinstance(parent, list) and isinstance(key, int) and key < len(parent))
        if exists and _same(parent[key], value):
            return
        if exists:
            a, b = self.spans[path]
            indent = _indent(self.lines[a])
            if isinstance(key, int):
                new = emit_item(value, indent)
            else:
                new = emit_entry(key, value, indent)
            parent[key] = value
            self._replace(a, b, new)
        else:
            if not isinstance(parent, dict):
                raise KeyError(f"can't add {key!r} to a list; set the whole list")
            if isinstance(parent, FlowMap):
                raise KeyError("can't add to an inline mapping; set the whole mapping")
            at, indent = self._insert_point(path[:-1])
            parent[key] = value
            self._replace(at, at, emit_entry(key, value, indent))

    def _insert_point(self, ppath: tuple) -> tuple[int, int]:
        if not ppath:
            return len(self.lines), 2
        a, b = self.spans[ppath]
        first = self.lines[a]
        base = _indent(first)
        # A list item "- key: v" holds its mapping at base + 2; a "key:" entry holds it at base + 2 as well.
        return b, base + 2

    def _replace(self, a: int, b: int, new_lines: list[str]) -> None:
        self.lines[a:b] = new_lines
        self._reparse()

    def _reparse(self) -> None:
        fresh = parse_object(self.lines)
        self.type_name, self.data, self.spans = fresh.type_name, fresh.data, fresh.spans

    def text(self) -> str:
        return "".join(line + "\n" for line in self.lines)


@dataclass
class Document:
    header: list[str]
    objects: list[UObject]
    final_newline: bool = True

    def by_id(self, file_id: int) -> UObject | None:
        return self._index().get(file_id)

    def _index(self) -> dict:
        cache = self.__dict__.get("_idx")
        if cache is None or cache[0] is not self.objects or cache[1] != len(self.objects):
            cache = (self.objects, len(self.objects), {o.file_id: o for o in self.objects})
            self.__dict__["_idx"] = cache
        return cache[2]

    def of_class(self, *class_ids: int) -> list[UObject]:
        return [o for o in self.objects if o.class_id in class_ids]

    def add(self, class_id: int, file_id: int, type_name: str, data: dict) -> UObject:
        lines = [f"--- !u!{class_id} &{file_id}", f"{type_name}:"]
        for k, v in data.items():
            lines += emit_entry(k, v, 2)
        return self.insert(parse_object(lines))

    def insert(self, obj: UObject) -> UObject:
        """Add an object where Unity would put it: objects are kept in file id order."""
        at = len(self.objects)
        for i, o in enumerate(self.objects):
            if o.file_id > obj.file_id:
                at = i
                break
        self.objects.insert(at, obj)
        return obj

    def remove(self, file_id: int) -> None:
        self.objects = [o for o in self.objects if o.file_id != file_id]

    def dump(self) -> str:
        out = "".join(line + "\n" for line in self.header) + "".join(o.text() for o in self.objects)
        if not self.final_newline and out.endswith("\n"):
            out = out[:-1]
        return out


# ---------------------------------------------------------------- reading


def loads(text: str) -> Document:
    final_newline = text.endswith("\n")
    if text.startswith("\ufeff"):
        text = text[1:]
    lines = text.replace("\r\n", "\n").split("\n")
    if final_newline:
        lines.pop()
    header: list[str] = []
    objects: list[UObject] = []
    cur: list[str] | None = None
    for line in lines:
        if line.startswith("--- "):
            if cur is not None:
                objects.append(parse_object(cur))
            cur = [line]
        elif cur is None:
            header.append(line)
        else:
            cur.append(line)
    if cur is not None:
        objects.append(parse_object(cur))
    return Document(header, objects, final_newline)


def load(path) -> Document:
    with open(path, encoding="utf-8", newline="") as f:
        return loads(f.read())


def save(doc: Document, path) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(doc.dump())


def parse_object(lines: list[str]) -> UObject:
    m = _DOC.match(lines[0])
    if not m:
        raise UnityYamlError(f"not a Unity object header: {lines[0]!r}")
    obj = UObject(int(m.group(1)), int(m.group(2)), bool(m.group(3)), list(lines))
    if len(lines) < 2:
        return obj
    top = lines[1]
    if not top.endswith(":"):
        raise UnityYamlError(f"expected 'TypeName:' after {lines[0]!r}")
    obj.type_name = top[:-1]
    p = _BlockParser(lines, obj.spans)
    end = _content_end(lines)
    if end > 2:
        obj.data, _ = p.mapping(2, end, _indent(lines[2]), ())
    return obj


def _content_end(lines: list[str]) -> int:
    end = len(lines)
    while end > 2 and not lines[end - 1].strip():
        end -= 1
    return end


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


class _BlockParser:
    def __init__(self, lines: list[str], spans: dict):
        self.lines = lines
        self.spans = spans

    def mapping(self, i: int, end: int, indent: int, path: tuple) -> tuple[dict, int]:
        out: dict = {}
        lines = self.lines
        while i < end:
            line = lines[i]
            if not line.strip():
                i += 1
                continue
            ind = _indent(line)
            if ind < indent or (ind == indent and line[ind:ind + 2] in ("- ", "-")):
                break
            if ind > indent:
                raise UnityYamlError(f"unexpected indent at line {i}: {line!r}")
            key, rest = _split_key(line[ind:], i)
            start = i
            value, i = self.value_after_key(rest, i, end, indent, path + (key,))
            out[key] = value
            self.spans[path + (key,)] = (start, i)
        return out, i

    def value_after_key(self, rest: str, i: int, end: int, indent: int, path: tuple):
        lines = self.lines
        if rest.strip():
            return self.scalar_or_flow(rest.strip(), i, end, indent)
        # "key:" alone: a block below (deeper), a list at the same indent, or an empty string.
        j = i + 1
        while j < end and not lines[j].strip():
            j += 1
        if j < end:
            nxt = lines[j]
            ind = _indent(nxt)
            if nxt[ind:ind + 1] == "-" and nxt[ind:ind + 2] in ("- ", "-") and ind >= indent:
                return self.sequence(j, end, ind, path)
            if ind > indent:
                return self.mapping(j, end, ind, path)
        return "", i + 1

    def sequence(self, i: int, end: int, indent: int, path: tuple) -> tuple[list, int]:
        out: list = []
        lines = self.lines
        while i < end:
            line = lines[i]
            if not line.strip():
                i += 1
                continue
            ind = _indent(line)
            if ind != indent or line[ind:ind + 1] != "-":
                break
            start = i
            item_path = path + (len(out),)
            body = line[ind + 1:].lstrip(" ")
            if not body:  # "-" then a block below
                v, i = self.mapping(i + 1, end, _indent(lines[i + 1]), item_path)
            elif _looks_like_key(body):
                # "- key: v" starts a mapping whose other keys sit at the column of "key".
                col = ind + (len(line[ind + 1:]) - len(body)) + 1
                v, i = self.mapping_from_item(i, end, col, item_path)
            else:
                v, i = self.scalar_or_flow(body, i, end, ind)
            out.append(v)
            self.spans[item_path] = (start, i)
        return out, i

    def mapping_from_item(self, i: int, end: int, col: int, path: tuple) -> tuple[dict, int]:
        # Treat the first line as if "- " were spaces, then parse a normal mapping at column col.
        saved = self.lines[i]
        self.lines[i] = " " * col + saved[col:]
        try:
            return self.mapping(i, end, col, path)
        finally:
            self.lines[i] = saved

    def scalar_or_flow(self, text: str, i: int, end: int, indent: int):
        lines = self.lines
        if text[0] in "{[":
            joined, j = text, i
            while _flow_depth(joined) > 0:
                j += 1
                if j >= end:
                    raise UnityYamlError(f"unclosed flow value from line {i}")
                joined += " " + lines[j].strip()
            v, k = _parse_flow(joined, 0)
            return v, j + 1
        if text[0] in "'\"":
            joined, j = text, i
            while not _quote_closed(joined):
                j += 1
                if j >= end:
                    raise UnityYamlError(f"unclosed quote from line {i}")
                joined += "\n" + lines[j].strip()
            return _unquote(joined), j + 1
        # Plain scalar; deeper-indented lines that aren't keys continue it (folded with spaces).
        parts, j = [text], i + 1
        while j < end and lines[j].strip() and _indent(lines[j]) > indent and not _looks_like_key(lines[j].strip()):
            parts.append(lines[j].strip())
            j += 1
        return " ".join(parts), j


def _split_key(s: str, i: int) -> tuple[str, str]:
    if s.startswith(("'", '"')):
        q = s[0]
        k = 1
        while k < len(s):
            if s[k] == q and not (q == "'" and s[k + 1:k + 2] == "'"):
                break
            k += 2 if (q == "'" and s[k:k + 2] == "''") or (q == '"' and s[k] == "\\") else 1
        key = _unquote(s[:k + 1])
        rest = s[k + 1:]
        if not rest.startswith(":"):
            raise UnityYamlError(f"expected ':' after key at line {i}")
        return key, rest[1:]
    c = s.find(": ")
    if c < 0:
        if s.endswith(":"):
            return s[:-1], ""
        raise UnityYamlError(f"expected 'key: value' at line {i}: {s!r}")
    return s[:c], s[c + 1:]


def _looks_like_key(s: str) -> bool:
    if s[:1] in "{['\"":
        return s[:1] in "'\"" and re.match(r"""^(['"]).*?\1:( |$)""", s) is not None
    return ": " in s or s.endswith(":")


def _flow_depth(s: str) -> int:
    depth, q = 0, None
    for c in s:
        if q:
            if c == q:
                q = None
        elif c in "'\"":
            q = c
        elif c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
    return depth


def _quote_closed(s: str) -> bool:
    q, k = s[0], 1
    while k < len(s):
        if q == '"' and s[k] == "\\":
            k += 2
            continue
        if s[k] == q:
            if q == "'" and s[k + 1:k + 2] == "'":
                k += 2
                continue
            return True
        k += 1
    return False


def _fold(s: str) -> str:
    # YAML line folding inside quoted scalars: a single newline becomes a space, blank lines become newlines.
    return re.sub(r"\n(\n*)", lambda m: "\n" * len(m.group(1)) if m.group(1) else " ", s)


def _unquote(s: str) -> str:
    q, body = s[0], s[1:-1]
    if q == "'":
        return _fold(body).replace("''", "'")
    out, k = [], 0
    body = re.sub(r"\\\n", "", body)
    body = _fold(body)
    esc = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\", '"': '"', "/": "/", " ": " ", "a": "\a", "b": "\b",
           "e": "\x1b", "f": "\f", "v": "\v", "N": "\x85", "_": "\xa0", "L": "\u2028", "P": "\u2029"}
    while k < len(body):
        c = body[k]
        if c == "\\" and k + 1 < len(body):
            n = body[k + 1]
            if n in "xuU":
                w = {"x": 2, "u": 4, "U": 8}[n]
                out.append(chr(int(body[k + 2:k + 2 + w], 16)))
                k += 2 + w
                continue
            out.append(esc.get(n, n))
            k += 2
            continue
        out.append(c)
        k += 1
    return "".join(out)


def _parse_flow(s: str, i: int):
    while i < len(s) and s[i] == " ":
        i += 1
    if s[i] == "{":
        out = FlowMap()
        i += 1
        while True:
            while s[i] == " ":
                i += 1
            if s[i] == "}":
                return out, i + 1
            c = s.index(":", i)
            key = s[i:c].strip()
            v, i = _parse_flow(s, c + 1)
            out[key] = v
            while s[i] == " ":
                i += 1
            if s[i] == ",":
                i += 1
    if s[i] == "[":
        out = FlowSeq()
        i += 1
        while True:
            while s[i] == " ":
                i += 1
            if s[i] == "]":
                return out, i + 1
            v, i = _parse_flow(s, i)
            out.append(v)
            while s[i] == " ":
                i += 1
            if s[i] == ",":
                i += 1
    if s[i] in "'\"":
        q, k = s[i], i + 1
        while k < len(s):
            if q == '"' and s[k] == "\\":
                k += 2
                continue
            if s[k] == q:
                if q == "'" and s[k + 1:k + 2] == "'":
                    k += 2
                    continue
                break
            k += 1
        return _unquote(s[i:k + 1]), k + 1
    k = i
    while k < len(s) and s[k] not in ",}]":
        k += 1
    return s[i:k].strip(), k


# ---------------------------------------------------------------- writing


def fmt_float(x: float) -> str:
    """Shortest text that reads back as the same 32-bit float, like Unity writes: 1, 0.5, 0.70710677."""
    if x != x:
        return "NaN"
    if x in (float("inf"), float("-inf")):
        return "Infinity" if x > 0 else "-Infinity"
    if x == int(x) and abs(x) < 1e15:
        return str(int(x)) if x != 0 or str(x)[0] != "-" else "-0"
    target = struct.pack("<f", x)
    for prec in range(1, 10):
        s = f"{x:.{prec}g}"
        if struct.pack("<f", float(s)) == target:
            break
    if "e" in s:  # Python writes 1e-08; Unity writes 1e-8
        mant, exp = s.split("e")
        s = f"{mant}e{int(exp):+d}"
    return s


def scalar(v) -> str:
    """A Python value as Unity writes it. Strings get quoted only when YAML needs it."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return fmt_float(v)
    s = str(v)
    if s == "":
        return ""
    if any(c in s for c in "\n\r\t\0") or any(ord(c) < 32 for c in s):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r") + '"'
    needs = (s[0] in "!&*{}[],#|>@`'\"%-?:" and s not in ("-",)) or s != s.strip() or ": " in s or " #" in s or s.endswith(":")
    if s[0] == "-" and len(s) > 1 and not (s[1].isdigit() or s[1] == "."):
        needs = True
    if needs:
        return "'" + s.replace("'", "''") + "'"
    return s


def _flow(v) -> str:
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k}: {_flow(x)}" for k, x in v.items()) + "}"
    if isinstance(v, list):
        return "[" + ", ".join(_flow(x) for x in v) + "]"
    return scalar(v)


def _inline(v) -> bool:
    return isinstance(v, (FlowMap, FlowSeq)) or (isinstance(v, (list, dict)) and not v) or not isinstance(v, (dict, list))


def emit_entry(key: str, v, indent: int) -> list[str]:
    pad = " " * indent
    k = scalar(key) if key else "''"
    if _inline(v):
        text = _flow(v) if isinstance(v, (dict, list)) else scalar(v)
        return [f"{pad}{k}: {text}" if text != "" else f"{pad}{k}: "]
    if isinstance(v, dict):
        out = [f"{pad}{k}:"]
        for kk, vv in v.items():
            out += emit_entry(kk, vv, indent + 2)
        return out
    out = [f"{pad}{k}:"]  # block list at the same indent as its key, Unity style
    for item in v:
        out += emit_item(item, indent)
    return out


def emit_item(v, indent: int) -> list[str]:
    pad = " " * indent
    if _inline(v):
        return [f"{pad}- " + (_flow(v) if isinstance(v, (dict, list)) else scalar(v))]
    if isinstance(v, dict):
        lines: list[str] = []
        for n, (k, vv) in enumerate(v.items()):
            sub = emit_entry(k, vv, indent + 2)
            if n == 0:
                sub[0] = pad + "- " + sub[0][indent + 2:]
            lines += sub
        return lines or [pad + "- {}"]
    return [f"{pad}-"] + [line for x in v for line in emit_item(x, indent + 2)]


def _same(a, b) -> bool:
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    sa, sb = scalar(a) if not isinstance(a, str) else a, scalar(b) if not isinstance(b, str) else b
    if sa == sb:
        return True
    try:
        fa, fb = float(sa), float(sb)
    except (TypeError, ValueError):
        return False
    return abs(fa - fb) <= 1e-6 * max(1.0, abs(fa), abs(fb))


# ---------------------------------------------------------------- helpers for readers


def ref(v) -> int:
    """{fileID: 123} -> 123 (0 when missing)."""
    if isinstance(v, dict):
        try:
            return int(v.get("fileID", 0))
        except ValueError:
            return 0
    return 0


def vec(v, keys="xyz") -> tuple[float, ...]:
    return tuple(float(v.get(k, 0)) for k in keys)


def num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default

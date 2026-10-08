"""A .tscn/.tres document that can be edited without disturbing anything it doesn't touch.

Every section header and property keeps its original text. dump() of an unedited document returns the input
exactly; editing one property rewrites only that property's text.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import variant
from .variant import Call


@dataclass
class Prop:
    key: str
    value: object
    raw: str  # the full original text, "key = value" (may span lines), without the final newline
    dirty: bool = False

    def text(self) -> str:
        if self.dirty:
            return f"{self.key} = {variant.dump(self.value)}"
        return self.raw


@dataclass
class Section:
    tag: str  # gd_scene, ext_resource, sub_resource, node, connection, editable, resource
    attrs: dict  # header attributes, parsed
    raw_header: str
    props: list[Prop] = field(default_factory=list)
    trailer: list[str] = field(default_factory=list)  # blank/comment lines after the props
    dirty_header: bool = False

    # -- header attributes
    def get(self, key: str, default=None):
        return self.attrs.get(key, default)

    def set_attr(self, key: str, value) -> None:
        if key in self.attrs and variant.same(self.attrs[key], value):
            return
        self.attrs[key] = value
        self.dirty_header = True

    def del_attr(self, key: str) -> None:
        if key in self.attrs:
            del self.attrs[key]
            self.dirty_header = True

    def header_text(self) -> str:
        if not self.dirty_header:
            return self.raw_header
        parts = [self.tag] + [f"{k}={variant.dump(v)}" for k, v in self.attrs.items()]
        return "[" + " ".join(parts) + "]"

    # -- properties
    def prop(self, key: str) -> Prop | None:
        for p in self.props:
            if p.key == key:
                return p
        return None

    def value(self, key: str, default=None):
        p = self.prop(key)
        return default if p is None else p.value

    def set(self, key: str, value, *, after: str | None = None) -> None:
        """Set a property. Unchanged values (within float noise) leave the original text alone."""
        p = self.prop(key)
        if p is not None:
            if not variant.same(p.value, value):
                p.value, p.dirty = value, True
            return
        new = Prop(key, value, "", dirty=True)
        if after is not None:
            for i, q in enumerate(self.props):
                if q.key == after:
                    self.props.insert(i + 1, new)
                    return
        # Godot writes metadata/ last; keep new plain properties ahead of it.
        if not key.startswith("metadata/"):
            for i, q in enumerate(self.props):
                if q.key.startswith("metadata/"):
                    self.props.insert(i, new)
                    return
        self.props.append(new)

    def remove(self, key: str) -> None:
        self.props = [p for p in self.props if p.key != key]

    def text(self) -> str:
        lines = [self.header_text()] + [p.text() for p in self.props]
        return "\n".join(lines) + "\n" + "".join(t + "\n" for t in self.trailer)


@dataclass
class Document:
    preamble: list[str]
    sections: list[Section]
    final_newline: bool = True

    # -- lookups
    @property
    def header(self) -> Section | None:
        return self.sections[0] if self.sections and self.sections[0].tag in ("gd_scene", "gd_resource") else None

    def by_tag(self, tag: str) -> list[Section]:
        return [s for s in self.sections if s.tag == tag]

    def nodes(self) -> list[Section]:
        return self.by_tag("node")

    def sub_resource(self, rid: str) -> Section | None:
        for s in self.by_tag("sub_resource"):
            if s.get("id") == rid:
                return s
        return None

    def ext_resource(self, rid: str) -> Section | None:
        for s in self.by_tag("ext_resource"):
            if s.get("id") == rid:
                return s
        return None

    def resolve(self, ref):
        """SubResource("id") -> its section; ExtResource("id") -> its section; anything else -> None."""
        if isinstance(ref, Call) and ref.args:
            if ref.name == "SubResource":
                return self.sub_resource(ref.args[0])
            if ref.name == "ExtResource":
                return self.ext_resource(ref.args[0])
        return None

    # -- editing
    def insert(self, section: Section, *, before: Section | None = None, after: Section | None = None) -> None:
        if after is not None and after is self.sections[-1]:
            after = None
        if before is not None or after is not None:
            if not section.trailer:
                section.trailer = [""]
            at = self.sections.index(before) if before is not None else self.sections.index(after) + 1
            self.sections.insert(at, section)
        else:  # last in the file: blank line before it, none after
            last = self.sections[-1] if self.sections else None
            if last is not None and not last.trailer:
                last.trailer = [""]
            self.sections.append(section)

    def remove(self, section: Section) -> None:
        i = self.sections.index(section)
        del self.sections[i]
        if i == len(self.sections) and self.sections:  # removed the last one: no blank line at the end
            self.sections[-1].trailer = [t for t in self.sections[-1].trailer if t.strip()]

    def dump(self) -> str:
        out = "".join(line + "\n" for line in self.preamble) + "".join(s.text() for s in self.sections)
        if not self.final_newline and out.endswith("\n"):
            out = out[:-1]
        return out


def new_section(tag: str, attrs: dict, props: dict | None = None) -> Section:
    s = Section(tag, dict(attrs), "", dirty_header=True)
    for k, v in (props or {}).items():
        s.props.append(Prop(k, v, "", dirty=True))
    return s


# ---------------------------------------------------------------- reading


def _header_end(text: str, i: int) -> int:
    """Index of the ']' closing the header that starts at text[i] == '[' (strings and nesting aware)."""
    depth, j, n = 0, i, len(text)
    while j < n:
        c = text[j]
        if c == '"':
            j += 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
        elif c in "[({":
            depth += 1
        elif c in "])}":
            depth -= 1
            if depth == 0:
                return j
        j += 1
    raise variant.VariantError("unterminated section header")


def _parse_header(raw: str) -> tuple[str, dict]:
    inner = raw[1:-1].strip()
    sp = 0
    while sp < len(inner) and not inner[sp].isspace():
        sp += 1
    tag, rest = inner[:sp], inner[sp:]
    attrs: dict = {}
    p = variant._Parser(rest)
    i = p.ws(0)
    while i < len(rest):
        eq = rest.index("=", i)
        key = rest[i:eq].strip()
        val, i = p.value(eq + 1)
        attrs[key] = val
        i = p.ws(i)
    return tag, attrs


def _key_end(text: str, i: int) -> int:
    """End of a property key (before ' = '). Keys can be quoted."""
    if text[i] == '"':
        _, j = variant.parse(text, i)
        return j
    j = i
    while j < len(text) and text[j] not in "=\n":
        j += 1
    return j


def loads(text: str) -> Document:
    final_newline = text.endswith("\n")
    if text.startswith("﻿"):
        text = text[1:]
    text = text.replace("\r\n", "\n")
    preamble: list[str] = []
    sections: list[Section] = []
    cur: Section | None = None
    i, n = 0, len(text)
    while i < n:
        eol = text.find("\n", i)
        eol = n if eol < 0 else eol
        line = text[i:eol]
        stripped = line.strip()
        if not stripped or stripped.startswith(";"):
            (cur.trailer if cur is not None else preamble).append(line)
            i = eol + 1
            continue
        if line.startswith("["):
            end = _header_end(text, i)
            raw = text[i:end + 1]
            tag, attrs = _parse_header(raw)
            cur = Section(tag, attrs, raw)
            sections.append(cur)
            i = end + 1
            if i < n and text[i] == "\n":
                i += 1
            continue
        if cur is None:
            raise variant.VariantError(f"property before any section: {line[:40]!r}")
        if cur.trailer:  # a property after a blank line still belongs to this section
            for t in cur.trailer:
                cur.props.append(Prop("", None, t))
            cur.trailer = []
        kend = _key_end(text, i)
        key = text[i:kend].strip()
        if key.startswith('"'):
            key = variant.parse_all(key)
        eq = text.index("=", kend)
        value, vend = variant.parse(text, eq + 1)
        raw = text[i:vend]
        cur.props.append(Prop(key, value, raw))
        i = vend
        rest_eol = text.find("\n", i)
        rest_eol = n if rest_eol < 0 else rest_eol
        if text[i:rest_eol].strip():
            raise variant.VariantError(f"text after value: {text[i:rest_eol]!r}")
        i = rest_eol + 1
    # Placeholder props for blank lines between props carry key "" and are written back verbatim.
    return Document(preamble, sections, final_newline)


def load(path) -> Document:
    with open(path, encoding="utf-8", newline="") as f:
        return loads(f.read())


def save(doc: Document, path) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(doc.dump())

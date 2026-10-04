"""A small HTML tree with CSS selectors, standard library only.

It exists so the selectors written in the config file (the same ones Selenium uses on the live page)
can also be run on a saved page or on `driver.page_source`, which makes the page parsing testable
without a browser. Supported selectors: tag, *, .class, #id, [attr], [attr=value], :first-child,
:last-child, :nth-child(n), :nth-of-type(n), and the descendant (space) and child (>) combinators.
"""
import re
from html.parser import HTMLParser
from typing      import Dict, Iterator, List, Optional, Tuple, Union

_VOID        = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
                "source", "track", "wbr"}
_SKIP_TEXT   = {"script", "style"}
# an unclosed one ends when the next of its kind starts
_AUTO_CLOSE  = {"option", "li", "p", "tr", "td", "th"}


class Node:
    """One element of the parsed page; text is kept as `str` items among the children."""
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: Optional[Dict[str, str]] = None,
                 parent: Optional["Node"] = None):
        """Creates an element with lower-case `tag`; children are appended by the builder."""
        self.tag      = tag
        self.attrs    = attrs or {}
        self.children : List[Union["Node", str]] = []
        self.parent   = parent

    @property
    def classes(self) -> frozenset:
        """The CSS classes of the element."""
        return frozenset(self.attrs.get("class", "").split())

    def elements(self) -> List["Node"]:
        """The child elements, without the text children."""
        return [c for c in self.children if isinstance(c, Node)]

    def get_text(self) -> str:
        """All text below this node, in order, without separators (like textContent)."""
        return "".join(c if isinstance(c, str) else c.get_text() for c in self.children)

    def own_text(self) -> str:
        """Only the text written directly inside this node, not inside its child elements."""
        return "".join(c for c in self.children if isinstance(c, str))

    def descendants(self) -> Iterator["Node"]:
        """Yields every element below this node, depth first, in document order."""
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.descendants()

    def select(self, css: str) -> List["Node"]:
        """Returns the descendants that match the selector.

        Raises:
            ValueError: If the selector uses syntax outside the supported subset.
        """
        chain = _parse_selector(css)
        return [n for n in self.descendants() if _matches_chain(n, chain, len(chain) - 1)]

    def select_one(self, css: str) -> Optional["Node"]:
        """Returns the first descendant that matches the selector, or None."""
        found = self.select(css)
        return found[0] if found else None


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root  = Node("#document")
        self._open = [self.root]

    def handle_starttag(self, tag, attrs):
        current = self._open[-1]
        if tag in _AUTO_CLOSE and current.tag == tag and len(self._open) > 1:
            self._open.pop()
            current = self._open[-1]
        node = Node(tag, {k: (v or "") for k, v in attrs}, current)
        current.children.append(node)
        if tag not in _VOID:
            self._open.append(node)

    def handle_startendtag(self, tag, attrs):
        current = self._open[-1]
        current.children.append(Node(tag, {k: (v or "") for k, v in attrs}, current))

    def handle_endtag(self, tag):
        for i in range(len(self._open) - 1, 0, -1):
            if self._open[i].tag == tag:
                del self._open[i:]
                return

    def handle_data(self, data):
        if self._open[-1].tag not in _SKIP_TEXT:
            self._open[-1].children.append(data)


def parse_html(html: str) -> Node:
    """Parses an HTML string into a tree and returns its `#document` root."""
    builder = _Builder()
    builder.feed(html)
    builder.close()
    return builder.root


# ----------------------------------------------------------------------------- selectors
_TOKEN = re.compile(r"""
    (?P<tag>\*|[A-Za-z][\w-]*)
  | \.(?P<cls>[\w-]+)
  | \#(?P<id>[\w-]+)
  | \[(?P<attr>[\w-]+)(?:=(?P<q>["']?)(?P<val>[^\]"']*)(?P=q))?\]
  | :(?P<pseudo>first-child|last-child|nth-child|nth-of-type)(?:\((?P<arg>\d+)\))?
""", re.VERBOSE)

Compound = List[Tuple[str, ...]]          # [("tag", "div"), ("cls", "x"), ("nth-child", "2"), ...]
_cache: Dict[str, List[Tuple[str, Compound]]] = {}


def _parse_selector(css: str) -> List[Tuple[str, Compound]]:
    """-> [(combinator_to_previous, compound), ...] left to right; first combinator is ''."""
    if css in _cache:
        return _cache[css]
    if "," in css:
        raise ValueError(f"selector lists are not supported: {css!r}")
    chain: List[Tuple[str, Compound]] = []
    combinator, pos, text = "", 0, css.strip()
    while pos < len(text):
        if text[pos].isspace() or text[pos] == ">":
            end = pos
            while end < len(text) and (text[end].isspace() or text[end] == ">"):
                end += 1
            combinator = ">" if ">" in text[pos:end] else " "
            pos = end
            continue
        compound: Compound = []
        while pos < len(text) and not (text[pos].isspace() or text[pos] == ">"):
            m = _TOKEN.match(text, pos)
            if not m:
                raise ValueError(f"cannot parse selector {css!r} at {text[pos:]!r}")
            if m["tag"]:
                compound.append(("tag", m["tag"]))
            elif m["cls"]:
                compound.append(("cls", m["cls"]))
            elif m["id"]:
                compound.append(("id", m["id"]))
            elif m["attr"]:
                compound.append(("attr", m["attr"], m["val"] if m["val"] is not None else ""))
            else:
                if m["pseudo"].startswith("nth") and m["arg"] is None:
                    raise ValueError(f"{m['pseudo']} needs a number in {css!r}")
                compound.append((m["pseudo"], m["arg"] or ""))
            pos = m.end()
        chain.append((combinator if chain else "", compound))
        combinator = " "
    if not chain:
        raise ValueError("empty selector")
    _cache[css] = chain
    return chain


def _index_among(node: Node, same_tag: bool) -> int:
    if node.parent is None:
        return 1
    siblings = [s for s in node.parent.elements() if not same_tag or s.tag == node.tag]
    return siblings.index(node) + 1


def _matches_compound(node: Node, compound: Compound) -> bool:
    for kind, *args in compound:
        if kind == "tag":
            if args[0] != "*" and node.tag != args[0].lower():
                return False
        elif kind == "cls":
            if args[0] not in node.classes:
                return False
        elif kind == "id":
            if node.attrs.get("id") != args[0]:
                return False
        elif kind == "attr":
            if args[0] not in node.attrs or (args[1] and node.attrs[args[0]] != args[1]):
                return False
        elif kind == "first-child":
            if _index_among(node, False) != 1:
                return False
        elif kind == "last-child":
            if node.parent is None or node.parent.elements()[-1] is not node:
                return False
        elif kind == "nth-child":
            if _index_among(node, False) != int(args[0]):
                return False
        elif kind == "nth-of-type":
            if _index_among(node, True) != int(args[0]):
                return False
    return True


def _matches_chain(node: Node, chain: List[Tuple[str, Compound]], index: int) -> bool:
    if not _matches_compound(node, chain[index][1]):
        return False
    if index == 0:
        return True
    combinator, parent = chain[index][0], node.parent
    if combinator == ">":
        return (parent is not None and parent.tag != "#document"
                and _matches_chain(parent, chain, index - 1))
    while parent is not None and parent.tag != "#document":
        if _matches_chain(parent, chain, index - 1):
            return True
        parent = parent.parent
    return False

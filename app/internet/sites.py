"""The user's authorization of one website, and the reader for what it returns (ADR 0050
P18-3, P18-4). Pure, standard library only.

The authorized scope is exactly the URL's scheme, host and port and the directory part of
its path: `http://example.org/docs/memristor.html` authorizes `http://example.org/docs/`.
A redirect or a link outside it is outside the user's authorization.
"""

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit

from app.core.errors import InvalidInputError

_DEFAULT_PORTS = {"http": 80, "https": 443}


@dataclass(frozen=True, slots=True)
class Site:
    """The one website a request authorizes (section 125, option 1)."""

    #: The URL as given, without its fragment.
    url: str
    scheme: str
    host: str
    port: int
    #: The authorized path prefix: the URL's directory, ending in "/".
    prefix: str

    @property
    def scope(self) -> str:
        """The authorized scope as a URL prefix."""
        default = _DEFAULT_PORTS[self.scheme]
        netloc = self.host if self.port == default else f"{self.host}:{self.port}"
        return urlunsplit((self.scheme, netloc, self.prefix, "", ""))


def refuse(summary: str, reason: str) -> InvalidInputError:
    return InvalidInputError.of(
        summary, reason, stage="internet.sites", data_changed=False, retry_safe=True,
        next_options=('python -m app research "What is a memristor?" --site https://example.org/memristor.html',),
    )


def site(url: str) -> Site:
    """The site a URL authorizes; refused unless it is an http(s) URL with a host and no
    credentials."""
    text = url.strip()
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as exc:
        raise refuse(f"{url!r} is not a URL.", str(exc)) from None
    scheme = parts.scheme.casefold()
    if scheme not in _DEFAULT_PORTS:
        raise refuse(f"{url!r} is not an http or https URL.", "Only web pages are retrieved (ADR 0050 P18-4).")
    if not parts.hostname:
        raise refuse(f"{url!r} names no host.", "Give the full address of one page.")
    if parts.username or parts.password:
        raise refuse("The URL carries credentials.", "RUDRA never sends credentials (section 245).")
    path = parts.path or "/"
    prefix = path[: path.rfind("/") + 1]
    clean = urlunsplit((scheme, parts.netloc, path, parts.query, ""))
    return Site(clean, scheme, parts.hostname.casefold(), port or _DEFAULT_PORTS[scheme], prefix)


def within(authorized: Site, url: str) -> bool:
    """Whether `url` is inside the authorized scope."""
    try:
        other = site(url)
    except InvalidInputError:
        return False
    return (other.scheme, other.host, other.port) == (authorized.scheme, authorized.host, authorized.port) \
        and urlsplit(other.url).path.startswith(authorized.prefix)


# --------------------------------------------------------------------- reading HTML

_SKIPPED = frozenset({"script", "style", "noscript", "template", "svg", "head"})
_BLOCKS = frozenset({"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "td", "th",
                     "section", "article", "header", "footer", "table", "pre", "blockquote", "dd", "dt", "dl",
                     "main", "nav", "aside", "figure", "figcaption", "hr"})


class _Reader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title: list[str] = []
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        elif tag in _SKIPPED:
            self._skip += 1
        if tag in _BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in _SKIPPED and self._skip:
            self._skip -= 1
        if tag in _BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title.append(data)
        elif not self._skip:
            self.parts.append(data)


def read_html(markup: str) -> tuple[str, str | None]:
    """The visible text of an HTML page, one block per line, and its title."""
    reader = _Reader()
    reader.feed(markup)
    reader.close()
    return _lines("".join(reader.parts)), (" ".join("".join(reader.title).split()) or None)


def read_plain(text: str) -> str:
    return _lines(text)


def _lines(text: str) -> str:
    lines = (" ".join(line.split()) for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()

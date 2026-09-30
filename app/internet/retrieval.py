"""Retrieving one page inside the user's authorization (ADR 0050 P18-4). Standard library.

`http` and `https` only; a direct connection (no proxy from the environment); a 15-second
timeout; at most 2 MiB; `text/html` or `text/plain` only; redirects followed only inside
the authorized scope. Nothing of the user's is sent: no cookies, no credentials, no form
data - a GET with a user agent and an Accept header (section 245). Any failure is
`RetrievalFailed`, never retried and never replaced by a guess.
"""

import hashlib
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from app.internet.sites import Site, read_html, read_plain, within
from app.version import VERSION

TIMEOUT = 15
MAX_BYTES = 2 * 1024 * 1024
TYPES = ("text/html", "text/plain")
USER_AGENT = f"RUDRA/{VERSION} (local research assistant; one page on the user's request)"


class RetrievalFailed(Exception):
    """The page could not be retrieved within the authorization and the limits."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Retrieved:
    """One retrieved page (section 126's fields that retrieval can know)."""

    url: str
    final_url: str
    retrieved_at: str
    content_type: str
    data: bytes
    sha256: str
    text: str
    title: str | None


class _ScopedRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, authorized: Site) -> None:
        super().__init__()
        self.authorized = authorized

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not within(self.authorized, newurl):
            raise RetrievalFailed(f"the site redirected to {newurl}, outside the authorized scope "
                                  f"{self.authorized.scope}; it was not followed")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _now() -> str:
    moment = datetime.now(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def retrieve(authorized: Site, *, clock: Callable[[], str] = _now) -> Retrieved:
    """GET the authorized URL, within the limits."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _ScopedRedirects(authorized))
    request = urllib.request.Request(authorized.url, headers={"User-Agent": USER_AGENT,
                                                              "Accept": "text/html, text/plain"})
    retrieved_at = clock()
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            final_url = response.geturl()
            content_type = (response.headers.get_content_type() or "").casefold()
            charset = response.headers.get_content_charset() or "utf-8"
            if content_type not in TYPES:
                raise RetrievalFailed(f"the page is {content_type or 'of no stated type'}, not text/html or text/plain")
            data = response.read(MAX_BYTES + 1)
    except RetrievalFailed:
        raise
    except urllib.error.HTTPError as exc:
        raise RetrievalFailed(f"the site answered HTTP {exc.code} {exc.reason}") from None
    except urllib.error.URLError as exc:
        raise RetrievalFailed(f"the site could not be reached: {exc.reason}") from None
    except (TimeoutError, OSError) as exc:
        raise RetrievalFailed(f"the site could not be reached: {type(exc).__name__}: {exc}") from None
    if len(data) > MAX_BYTES:
        raise RetrievalFailed(f"the page is larger than {MAX_BYTES // (1024 * 1024)} MiB; it was not stored")
    try:
        decoded = data.decode(charset)
    except (LookupError, UnicodeDecodeError):
        raise RetrievalFailed(f"the page's text is not valid {charset}") from None
    if content_type == "text/html":
        text, title = read_html(decoded)
    else:
        text, title = read_plain(decoded), None
    if not text:
        raise RetrievalFailed("the page holds no text")
    return Retrieved(authorized.url, final_url, retrieved_at, content_type, data,
                     hashlib.sha256(data).hexdigest(), text, title)

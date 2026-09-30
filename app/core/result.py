"""Result type.

Part 4 section 134 requires failures to be *returned to the caller* where
applicable, not only raised. Services therefore return `Result[T]`: either `Ok`
with a value, or `Err` with a `FailureReport`.

Raising is reserved for startup problems that make the process unusable (bad
configuration, unwritable directories). Everything a caller might reasonably
handle is returned.

    match load_something():
        case Ok(value):
            use(value)
        case Err(report):
            show(report.to_text())
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, NoReturn

from app.core.errors import FailureReport, RudraError


@dataclass(frozen=True, slots=True)
class Ok[T]:
    """A successful result carrying a value."""

    value: T

    def is_ok(self) -> bool:
        return True

    def is_err(self) -> bool:
        return False

    def unwrap(self) -> T:
        return self.value

    def unwrap_or(self, default: T) -> T:
        return self.value

    def map[U](self, fn: Callable[[T], U]) -> "Ok[U]":
        return Ok(fn(self.value))


@dataclass(frozen=True, slots=True)
class Err:
    """A failed result carrying the reason it failed."""

    failure: FailureReport

    def is_ok(self) -> bool:
        return False

    def is_err(self) -> bool:
        return True

    def unwrap(self) -> NoReturn:
        """Raise rather than return a value that does not exist."""
        raise RudraError(self.failure)

    def unwrap_or[T](self, default: T) -> T:
        return default

    def map(self, fn: Callable[..., object]) -> "Err":
        """Mapping a failure leaves it unchanged."""
        return self


type Result[T] = Ok[T] | Err

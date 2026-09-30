"""Phase 21: the one optimization the measurements justified (ADR 0053).

Startup's environment check asked Windows for the operating system through the `platform`
module, whose WMI queries cost 0.1-0.35 s at every command (measured). The facts are now
read from `sys.getwindowsversion()` on a Windows 10 or 11 workstation. What every test here
holds it to: the answer is exactly `platform`'s, and no WMI query is made.
"""

from __future__ import annotations

import platform
import sys

import pytest

from app.core import environment


def test_the_facts_are_exactly_what_the_platform_module_reports():
    assert environment.operating_system_facts() == (platform.system(), platform.release(), platform.version())


def test_the_startup_report_line_is_unchanged():
    component = environment._operating_system()
    assert component.detected == f"{platform.system()} {platform.release()} ({platform.version()})"


@pytest.mark.skipif(sys.platform != "win32", reason="the fast path is Windows' own")
def test_no_wmi_query_is_made_on_windows_10_or_11(monkeypatch):
    winver = sys.getwindowsversion()
    if (winver.major, winver.minor, getattr(winver, "product_type", 1)) != (10, 0, 1):
        pytest.skip("not a Windows 10 or 11 workstation: the platform module is asked instead")

    def forbidden(*args, **kwargs):
        raise AssertionError("a WMI query was made")

    monkeypatch.setattr(platform, "_wmi_query", forbidden)
    monkeypatch.setattr(platform, "_uname_cache", None)
    system, release, version = environment.operating_system_facts()
    assert system == "Windows" and release in ("10", "11") and version.startswith("10.0.")
    assert environment._memory_status()[0] is not None  # the memory probe asks no WMI either


def test_other_systems_ask_the_platform_module(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(platform, "release", lambda: "6.8")
    monkeypatch.setattr(platform, "version", lambda: "#1 SMP")
    assert environment.operating_system_facts() == ("Linux", "6.8", "#1 SMP")
    assert environment._memory_status() == (None, None)

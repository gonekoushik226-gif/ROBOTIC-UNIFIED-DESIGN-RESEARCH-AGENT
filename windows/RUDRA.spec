# PyInstaller build of RUDRA. Run it through windows/build.py.
#
# One folder, dist/RUDRA/, holding two programs over one shared _internal/ folder:
#   RUDRA.exe      the desktop window (windowed: no console)   windows/rudra_gui.py
#   RUDRA-CLI.exe  the command line, as python -m app          windows/rudra_launcher.py
# Both carry the R icon (app/ui/gui/assets/rudra.ico, from windows/icon/rudra_mark.py).
# Every path is relative to this file, so the build works from any checkout.

from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
ICON = str(ROOT / "app" / "ui" / "gui" / "assets" / "rudra.ico")
BUILD = ROOT / "build"

version_scope = {}
exec((ROOT / "app" / "version.py").read_text(encoding="utf-8"), version_scope)
VERSION = version_scope["VERSION"]

# Every module of the application, whether or not an import names it.
MODULES = sorted(
    ".".join(path.relative_to(ROOT).with_suffix("").parts[:-1] if path.name == "__init__.py"
             else path.relative_to(ROOT).with_suffix("").parts)
    for path in (ROOT / "app").rglob("*.py")
)
DATAS = [
    (str(ROOT / "app" / "storage" / "schema" / "*.sql"), "app/storage/schema"),  # read by the migrator
    (str(ROOT / "app" / "ui" / "gui" / "assets" / "*"), "app/ui/gui/assets"),  # icon and marks
    (str(ROOT / "README.md"), "."),  # the window's Help page
    (str(ROOT / "docs" / "LIMITATIONS.md"), "docs"),
]


def version_file(name, description):
    """The Windows version resource: the file properties Explorer shows."""
    numbers = tuple(int(part) for part in VERSION.split(".")) + (0,) * (4 - len(VERSION.split(".")))
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('FileDescription', '{description}'),
      StringStruct('FileVersion', '{VERSION}'),
      StringStruct('InternalName', '{name}'),
      StringStruct('OriginalFilename', '{name}.exe'),
      StringStruct('ProductName', 'RUDRA'),
      StringStruct('ProductVersion', '{VERSION}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    BUILD.mkdir(parents=True, exist_ok=True)
    path = BUILD / f"version-{name}.txt"
    path.write_text(text, encoding="utf-8")
    return str(path)


def program(script, name, description, console):
    analysis = Analysis([str(ROOT / "windows" / script)], pathex=[str(ROOT)], datas=DATAS,
                        hiddenimports=MODULES, noarchive=False)
    exe = EXE(PYZ(analysis.pure), analysis.scripts, [], exclude_binaries=True, name=name, console=console,
              icon=ICON, version=version_file(name, description), upx=False)
    return analysis, exe


gui, gui_exe = program("rudra_gui.py", "RUDRA", "RUDRA", console=False)
cli, cli_exe = program("rudra_launcher.py", "RUDRA-CLI", "RUDRA command line", console=True)

COLLECT(gui_exe, gui.binaries, gui.datas, cli_exe, cli.binaries, cli.datas, name="RUDRA", upx=False)

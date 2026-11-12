"""Reproducible Windows one-file build; uses installed dependencies, no downloads."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def dll_closure(dependency_dir: Path):
    """Only native DLLs linked by OCP, including indirect VTK DLL dependencies."""
    import pefile

    search_dirs = [dependency_dir / "OCP", dependency_dir / "cadquery_ocp.libs", dependency_dir / "vtk.libs"]
    native = {p.name.lower(): p for d in search_dirs if d.exists() for p in d.glob("*.dll")}
    extensions = list((dependency_dir / "OCP").glob("OCP*.pyd"))
    if not extensions:
        raise SystemExit("OCP extension is missing in " + str(dependency_dir))
    # OCCT registers file-format/storage plugins at import time. TKIVtk also
    # loads VTK's PythonCore bridge dynamically; these do not occur in the
    # extension's static PE import table, so they must be explicit roots.
    pending = extensions + list((dependency_dir / "cadquery_ocp.libs").glob("*.dll"))
    pending += list((dependency_dir / "vtk.libs").glob("vtkWrappingPythonCore*.dll"))
    selected = {}
    while pending:
        path = pending.pop()
        key = path.name.lower()
        if key in selected:
            continue
        selected[key] = path
        pe = pefile.PE(str(path), fast_load=True)
        try:
            pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"], pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"]])
            entries = list(getattr(pe, "DIRECTORY_ENTRY_IMPORT", [])) + list(getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", []))
            for entry in entries:
                linked = native.get(entry.dll.decode("ascii").lower())
                if linked is not None:
                    pending.append(linked)
        finally:
            pe.close()
    return sorted(selected.values())


def collect_licenses():
    """Preserve installed distributions' licence/notice files where supplied."""
    destination = ROOT / "thirdparty_licenses"
    destination.mkdir(exist_ok=True)
    names = ["pypdf", "numpy", "scipy", "pillow", "openpyxl", "xlrd", "et-xmlfile", "cadquery-ocp", "vtk", "pyinstaller", "pefile", "altgraph", "pywin32-ctypes"]
    for name in names:
        try:
            dist = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            continue
        folder = destination / name
        folder.mkdir(exist_ok=True)
        metadata = dist.metadata
        (folder / "package.json").write_text(json.dumps({"name": name, "version": dist.version, "license": metadata.get("License-Expression", metadata.get("License", "See package metadata")), "homepage": metadata.get("Home-page", "")}, ensure_ascii=False, indent=2), encoding="utf-8")
        for file in dist.files or []:
            if any(p.lower().startswith(("license", "licence", "copying", "notice")) for p in file.parts):
                source = Path(dist.locate_file(file))
                if source.is_file():
                    target = folder / "__".join(file.parts)
                    shutil.copy2(source, target)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.exists():
        shutil.copy2(python_license, destination / "Python-LICENSE.txt")


def main():
    for stream in (sys.stdout, sys.stderr):
        if stream and hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='backslashreplace')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dependency-dir", type=Path, default=ROOT.parent / "_work" / "cad_dependencies")
    parser.add_argument("--prepare-only", action="store_true", help="write .spec and licence files without building")
    parser.add_argument("--clean", action="store_true", help="remove only this build's intermediate directory before building")
    parser.add_argument("--work-dir", type=Path, default=ROOT.parent / "_work" / "exe_build", help="build intermediates and cache")
    parser.add_argument("--dist-dir", type=Path, default=ROOT / "发布", help="executable output directory")
    args = parser.parse_args()
    dependency_dir = args.dependency_dir.resolve()
    sys.path.insert(0,str(ROOT / 'vendor_py'))
    if dependency_dir.exists():
        sys.path.insert(0, str(dependency_dir))
    import PyInstaller  # fail before spending time on an incomplete environment

    # OCP wrappers import submodules dynamically; each small package is retained.
    import OCP
    ocp_dir = Path(OCP.__file__).resolve().parent
    if not dependency_dir.exists():
        dependency_dir = ocp_dir.parent
    binaries = dll_closure(dependency_dir)
    collect_licenses()
    modules = ["board_appearance", "project_refresh", "pin1_support", "bga_support", "assembly_estimate", "datasheet_support", "pypdf", "manufacturing_export", "calibration", "generator", "gerber", "step_export", "cad_worker", "board_surfaces", "input_support", "user_models", "ai_support", "openpyxl", "xlrd", "xlrd.book", "xlrd.sheet", "xlrd.compdoc", "xlrd.biffh", "xlrd.formatting", "et_xmlfile", "numpy", "PIL", "scipy.optimize", "scipy.spatial"]
    modules += ["OCP"] + ["OCP." + ".".join(p.parent.relative_to(ocp_dir).parts) for p in ocp_dir.rglob("__init__.py") if p.parent != ocp_dir]
    binary_entries = [(str(p), str(p.parent.relative_to(dependency_dir)).replace("\\", "/")) for p in binaries]
    datas = [(str(ROOT / "ui"), "ui"), (str(ROOT / "assets"), "assets"), (str(ROOT / "thirdparty_licenses"), "thirdparty_licenses"), (str(ROOT / "THIRD_PARTY_NOTICES.md"), ".")]
    spec = """# Generated by build_exe.py. Python and dependency versions are recorded in build-info.json.
a = Analysis(
    [ENTRY], pathex=PATHEX, binaries=BINARIES, datas=DATAS,
    hiddenimports=HIDDENIMPORTS, hookspath=[], runtime_hooks=[],
    excludes=['vtk', 'vtkmodules', 'tkinter', 'matplotlib', 'IPython', 'pytest', 'pandas', 'numba', 'torch', 'sympy'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='PCBA3D',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False)
"""
    spec = spec.replace("ENTRY", repr(str(ROOT / "src" / "app.py"))).replace("PATHEX", repr([str(ROOT / "src"), str(ROOT / "vendor_py"), str(dependency_dir)])).replace("BINARIES", repr(binary_entries)).replace("DATAS", repr(datas)).replace("HIDDENIMPORTS", repr(sorted(set(modules))))
    spec_path = ROOT / "PCBA3D.spec"
    spec_path.write_text(spec, encoding="utf-8")
    versions = {"python": sys.version, "PyInstaller": PyInstaller.__version__, "OCP": getattr(OCP, "__version__", "unknown"), "native_files": len(binaries), "native_bytes": sum(p.stat().st_size for p in binaries)}
    for name in ["numpy", "scipy", "pillow", "openpyxl", "xlrd", "vtk", "cadquery-ocp"]:
        versions[name] = importlib.metadata.version(name)
    (ROOT / "build-info.json").write_text(json.dumps(versions, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(versions, ensure_ascii=False, indent=2), flush=True)
    if args.prepare_only:
        return
    for required in [ROOT / "src" / "app.py", ROOT / "ui" / "index.html", ROOT / "assets" / "viewer.html", ROOT / "assets" / "aliases.json", ROOT / "assets" / "library" / "models.json"]:
        if not required.is_file():
            raise SystemExit("Build resource is not ready: " + str(required))
    work = args.work_dir.resolve()
    # This target is fixed inside the workspace and contains build intermediates only.
    if args.clean and work != (ROOT.parent / "_work" / "exe_build").resolve():
        raise SystemExit("--clean is limited to the default workspace build directory")
    if args.clean and work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    dist = args.dist_dir.resolve()
    dist.mkdir(exist_ok=True)
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join([str(dependency_dir), str(ROOT / "src")])
    environment["PYINSTALLER_CONFIG_DIR"] = str(work / "cache")
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--distpath", str(dist), "--workpath", str(work), str(spec_path)], cwd=ROOT, env=environment, check=True)
    for name in ["使用说明.md", "THIRD_PARTY_NOTICES.md", "build-info.json"]:
        if (ROOT / name).exists():
            shutil.copy2(ROOT / name, dist / name)
    shutil.copytree(ROOT / "thirdparty_licenses", dist / "thirdparty_licenses", dirs_exist_ok=True)
    print("Built " + str(dist / "PCBA3D.exe"), flush=True)


if __name__ == "__main__":
    main()

# Third-party runtime notices

Z30 Studio is independent software, not a Nikon product. This Windows bundle includes Python and third-party runtime libraries. Each component retains its own copyright and license; see the accompanying `licenses/` directory and licenses shipped with Tcl/Tk.

| Component | Upstream / corresponding source | License |
| --- | --- | --- |
| CPython and standard library | https://www.python.org/downloads/source/ | Python Software Foundation license and included notices |
| Tcl/Tk | https://www.tcl.tk/software/tcltk/ | Tcl/Tk license |
| rawpy | https://github.com/letmaik/rawpy / https://pypi.org/project/rawpy/ | MIT |
| LibRaw (`raw_r.dll`, supplied by rawpy wheel) | https://github.com/LibRaw/LibRaw / https://www.libraw.org/download | LGPL 2.1 / CDDL 1.0; supplied license included |
| NumPy and its bundled numerical runtimes | https://github.com/numpy/numpy / https://pypi.org/project/numpy/ | BSD and bundled dependency notices |
| Pillow and bundled image libraries | https://github.com/python-pillow/Pillow / https://pypi.org/project/Pillow/ | MIT-CMU / HPND and bundled dependency notices |
| tifffile | https://github.com/cgohlke/tifffile | BSD-3-Clause |
| PyInstaller bootloader | https://github.com/pyinstaller/pyinstaller | GPL with bootloader exception |

Exact installed package versions and Python version are recorded in `build-info.json`. This is an unmodified runtime-library bundle: the LibRaw DLL is a separate replaceable file in `_internal/rawpy/`. The application source, build specification and pinned build requirements are available in the project repository; rebuild with compatible modified libraries using the same build script. Codex CLI is not redistributed in this package.

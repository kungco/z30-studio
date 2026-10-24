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
| ONNX Runtime CPU | https://github.com/microsoft/onnxruntime | MIT; bundled dependency notices |
| FlatBuffers / Protocol Buffers | https://github.com/google/flatbuffers / https://github.com/protocolbuffers/protobuf | Apache-2.0 / BSD-3-Clause |
| Lensfun Nikon Z calibration data (35 rectilinear profiles) | https://github.com/lensfun/lensfun/blob/master/data/db/mil-nikon.xml | CC BY-SA 3.0; attribution: Lensfun database contributors; full license in data/ and Windows licenses/ |
| PyInstaller bootloader | https://github.com/pyinstaller/pyinstaller | GPL with bootloader exception |

Exact installed package versions and Python version are recorded in `build-info.json`. This is an unmodified runtime-library bundle: the LibRaw DLL is a separate replaceable file in `_internal/rawpy/`. The application source, build specification and pinned build requirements are available in the project repository; rebuild with compatible modified libraries using the same build script. Codex CLI is not redistributed in this package.

Optional SegFormer weights are not bundled or redistributed. The explicit in-app download identifies the pinned converter, SHA-256 and original NVIDIA license; see [local edits](local-edits.md). No Lensfun library source is included; its data is independently interpreted. The extracted calibration database remains under CC BY-SA 3.0.

"""Read-only Git baseline for developer comparisons; never used by the native app."""
import hashlib
import io
from pathlib import Path,PurePosixPath
import subprocess
import tarfile

ROOT=Path(__file__).resolve().parents[1]
BASE="a2a2e089d760221a52c8774cca8a2e42f0a82269"

def baseline_directory():
    target=ROOT/".development"/("oracle-"+BASE)
    target.mkdir(parents=True,exist_ok=True)
    archive=subprocess.check_output(["git","archive","--format=tar",BASE],cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        for member in bundle:
            name=PurePosixPath(member.name)
            wanted=name.parent==PurePosixPath(".") and name.suffix==".py" or str(name)=="data/lens-profiles.json"
            if not wanted or not member.isfile():continue
            data=bundle.extractfile(member).read();path=target.joinpath(*name.parts)
            path.parent.mkdir(parents=True,exist_ok=True)
            if path.exists():
                if hashlib.sha256(path.read_bytes()).digest()!=hashlib.sha256(data).digest():
                    raise ValueError("Frozen baseline was modified; refusing to overwrite "+str(name))
            else:
                with path.open("xb") as stream:stream.write(data)
    return target

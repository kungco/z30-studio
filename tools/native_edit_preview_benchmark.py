"""Measure read-only cached RAW previews using an explicitly supplied public fixture."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import uuid
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('dll','source','parameters'):parser.add_argument('--'+name,required=True,type=Path)
    parser.add_argument('--probe',type=Path,default=ROOT/'native/target/release/examples/edit_preview_probe.exe')
    args=parser.parse_args()
    from tools.native_raw_benchmark import measured,digest,EXPECTED
    before=digest(args.source)
    manifest=json.loads((ROOT/'native/vendor/libraw/manifest.json').read_text())
    if before not in EXPECTED.values() or digest(args.dll)!=manifest['dll_sha256']:raise ValueError('Use the pinned public 12/14-bit fixture and official DLL')
    result=measured([str(args.probe.resolve()),str(args.dll.resolve()),str(args.source.resolve()),str(args.parameters.resolve())])
    result.update(source_unchanged=digest(args.source)==before,source_sha256=before,probe_sha256=digest(args.probe),
        parameters_sha256=digest(args.parameters),native_base_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        native_tree_dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True)),
        native_source_sha256={name:digest(ROOT/name) for name in ['native/crates/core/src/editor.rs','native/crates/core/src/recipe.rs','native/crates/core/src/session.rs','native/crates/core/src/worker.rs','native/crates/core/src/develop.rs']})
    result['success']=result['success'] and result['source_unchanged']
    result['limitations']=['Core preview only; editing/export GUI is not enabled','Cold neutral and warm edited previews have different parameters; no equal-work speed ratio','Camera WB/public sample; other WB and user photos not accepted by this measurement','Cache keys use path, size, mtime, RAW WB; same-size/same-timestamp replacement needs explicit clear/reload','decode_resize_seconds excludes the initial filesystem metadata lookup']
    folder=ROOT/'.development/native-edit-preview'/('run-'+uuid.uuid4().hex);folder.mkdir(parents=True)
    path=folder/'report.json';path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps(dict(report=str(path),success=result['success'])),flush=True)
    if not result['success']:raise SystemExit(1)


if __name__=='__main__':main()

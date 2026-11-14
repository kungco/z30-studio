"""Verify full-size public NEF exports against retained float quality references.

Requires the explicitly named public fixtures and reference directory. Original
photos are read only; results go into a fresh .development child. No model calls.
"""
import argparse
import json
import math
from pathlib import Path
import subprocess
import sys
import uuid
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('dll','raw12','raw14','reference-directory','parameters'):parser.add_argument('--'+name,required=True,type=Path)
    parser.add_argument('--reference-report',type=Path,default=ROOT/'docs/reports/native-gamut-corrected-warm.json')
    parser.add_argument('--probe',type=Path,default=ROOT/'native/target/release/examples/photo_export_probe.exe')
    args=parser.parse_args()
    import numpy as np
    import tifffile
    from PIL import Image
    from tools.native_raw_benchmark import measured,compare,digest,EXPECTED
    reference=json.loads(args.reference_report.read_text(encoding='utf8'))
    parameters=json.loads(args.parameters.read_text(encoding='utf8'))
    manifest=json.loads((ROOT/'native/vendor/libraw/manifest.json').read_text())
    if digest(args.dll)!=manifest['dll_sha256']:raise ValueError('LibRaw DLL hash mismatch')
    if parameters!=reference['parameters'] or digest(ROOT/'processing.py')!=reference['oracle_processing_sha256']:raise ValueError('Parameters or corrected Python reference changed; regenerate float reference first')
    folder=ROOT/'.development/native-photo-export'/('run-'+uuid.uuid4().hex);folder.mkdir(parents=True)
    report=dict(stage='2D-full-nef-file-export',full_migration_complete=False,gui_export_enabled=False,
        probe_sha256=digest(args.probe),reference_report_sha256=digest(args.reference_report),parameters=parameters,
        native_base_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        native_tree_dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True)),
        native_source_sha256={name:digest(ROOT/name) for name in ['native/crates/core/src/develop.rs','native/crates/core/src/export.rs','native/crates/core/src/frame.rs','native/crates/core/src/raw.rs','native/crates/core/examples/photo_export_probe.rs']},cases=[])
    icc=(ROOT/'native/crates/core/assets/srgb.icc').read_bytes()
    for bits,source in [(12,args.raw12),(14,args.raw14)]:
        print(f'Exporting {bits}-bit full RAW',flush=True)
        before=digest(source)
        if before!=EXPECTED[bits]:raise ValueError('Public RAW fixture hash mismatch')
        output=folder/(f'{bits}bit-full');command=[str(args.probe.resolve()),str(args.dll.resolve()),str(source.resolve()),str(output),str(args.parameters.resolve())]
        native=measured(command);width,height=native['width'],native['height']
        rendered=output/'rendered.f32';old=args.reference_directory/f'{bits}bit-full-oracle.f32'
        comparison=compare(old,rendered,width,height)
        current=np.memmap(rendered,dtype='<f4',mode='r',shape=(height,width,3))
        result=dict(bits=bits,native=native,reference_comparison=comparison,source_sha256=before,source_unchanged=digest(source)==before,files=[])
        for record in native['outputs']:
            path=output/record['file'];entry=record|dict(sha256=digest(path))
            if path.suffix=='.tif':
                with tifffile.TiffFile(path) as tiff:
                    page=tiff.pages[0];image=page.asarray();entry.update(bits_per_sample=page.bitspersample,icc_matches=page.tags[34675].value==icc)
                entry.update(dtype=str(image.dtype),dimensions_match=image.shape==(height,width,3))
                maximum=0;non_eight=0;seen=np.zeros(65536,np.bool_)
                for y in range(0,height,64):
                    expected=np.rint(np.clip(current[y:y+64],0,1)*65535).astype(np.uint16)
                    maximum=max(maximum,int(np.abs(image[y:y+64].astype(np.int32)-expected.astype(np.int32)).max()))
                    non_eight+=int(np.count_nonzero(image[y:y+64]%257));seen[image[y:y+64].ravel()]=True
                entry.update(max_quantized_error=maximum,distinct_codes=int(seen.sum()),channels_not_8bit_upscaled=non_eight,
                             success=entry['icc_matches'] and entry['dimensions_match'] and image.dtype==np.uint16 and entry['bits_per_sample']==16 and maximum==0 and non_eight>0 and int(seen.sum())>256)
            else:
                with Image.open(path) as picture:
                    entry.update(icc_matches=picture.info.get('icc_profile')==icc,dimensions_match=picture.size==(width,height))
                    image=np.array(picture.convert('RGB'))
                maximum=0;squared=0.;channels=0
                for y in range(0,height,64):
                    expected=np.rint(np.clip(current[y:y+64],0,1)*255).astype(np.uint8)
                    delta=image[y:y+64].astype(np.float64)-expected
                    maximum=max(maximum,int(np.abs(delta).max()));squared+=float(np.sum(delta*delta));channels+=delta.size
                entry.update(max_quantized_error=maximum)
                if path.suffix=='.jpg':
                    entry['psnr_db']=10*math.log10(255**2/(squared/channels)) if squared else None
                    entry['jpeg_quality_sanity']=entry['psnr_db'] is None or entry['psnr_db']>=35
                entry['success']=entry['icc_matches'] and entry['dimensions_match'] and (entry.get('jpeg_quality_sanity',maximum==0))
            result['files'].append(entry)
        frozen={p.name:digest(p) for p in output.iterdir()}
        retry=subprocess.run(command,capture_output=True,timeout=10)
        result['existing_directory_rejected_without_changes']=retry.returncode!=0 and frozen=={p.name:digest(p) for p in output.iterdir()}
        result['temporary_parts_remaining']=any(p.suffix=='.part' for p in output.iterdir())
        result['success']=result['source_unchanged'] and comparison['nonfinite_channels']==0 and comparison['max_abs_error']<=2e-5 and all(v['success'] for v in result['files']) and result['existing_directory_rejected_without_changes'] and not result['temporary_parts_remaining']
        report['cases'].append(result)
    report['success']=all(v['success'] for v in report['cases'])
    report['limitations']=['Public 12/14-bit camera-WB samples, not user originals or calibrated print/skin tests','Core end-to-end probe; GUI export/lifecycle not enabled','Single timing per case; no steady performance claim','JPEG is lossy; PSNR sanity does not replace visual assessment']
    path=folder/'report.json';path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps(dict(report=str(path),success=report['success'])),flush=True)
    if not report['success']:raise SystemExit(1)


if __name__=='__main__':main()

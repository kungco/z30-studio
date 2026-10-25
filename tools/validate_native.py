"""Decode exported RGB16 and hash actual pixels, independently of TIFF packing."""
import argparse
import hashlib
import importlib
import io
import json
from pathlib import Path
import sys
import tifffile

p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--sample',type=Path,required=True)
p.add_argument('--pixels',type=Path);p.add_argument('--out',type=Path,required=True);p.add_argument('--workbench',action='store_true');args=p.parse_args()
sys.path.insert(0,str(args.source.resolve()));m=importlib.import_module('server')
params=m.settings(dict(exposure=.75,highlights=-35,shadows=20,temperature=14,tint=-3,saturation=10,contrast=7,sharpen=15))
if args.workbench:params=m.settings(params|dict(denoise_luma=35,denoise_chroma=40,lens_k1=.015,lens_red=1,lens_blue=-1,vignette=20,masks=[dict(type='radial',exposure=.3)]))
with args.sample.open('rb') as file:before=hashlib.file_digest(file,'sha256').hexdigest()
encoded=m.export_bytes(dict(path=args.sample,raw=True,cache={}),params,'TIFF16')
with tifffile.TiffFile(io.BytesIO(encoded)) as tif:
    array=tif.asarray();digest=hashlib.sha256(memoryview(array).cast('B')).hexdigest()
    if args.pixels:
        import numpy as np
        np.save(args.pixels,array)
    result=dict(source_sha256=before,rgb16_sha256=digest,shape=list(array.shape),dtype=str(array.dtype),icc=bool(tif.pages[0].tags[34675].value),workbench=args.workbench)
with args.sample.open('rb') as file:assert before==hashlib.file_digest(file,'sha256').hexdigest()
result['original_unchanged']=True;args.out.write_text(json.dumps(result,indent=2),encoding='utf8');print(json.dumps(result))

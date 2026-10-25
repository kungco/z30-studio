"""Compare read-only public 12/14-bit fixtures with the frozen Python oracle.

Developer harness only: Python is not a native-app runtime dependency. Large
float buffers stay in .development; only the small, sanitized report is shared.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
EXPECTED={12:"75249c11a3a845367c7a322a1c4e7076f33c785ec2518fc6594c34c185b41161",
          14:"03d94c1f184c4236ea8f890ab0fe30e91b65965e8b09cebecc397fba081a607f"}

def digest(path):
    with Path(path).open("rb") as file:return hashlib.file_digest(file,"sha256").hexdigest()

def oracle(source,output,half,parameters=None):
    # Windows venv python.exe can be a tiny redirector with another interpreter
    # PID. Publish the actual worker before importing/allocating image libraries.
    with output.with_suffix(".pid.json").open("x",encoding="utf8") as marker:
        json.dump({"pid":os.getpid()},marker)
    import numpy as np
    import rawpy
    from imaging import raw_pixels
    start=time.perf_counter()
    with rawpy.imread(str(source)) as raw:
        sizes=raw.sizes
        rgb=raw.postprocess(output_bps=16,gamma=(1,1),no_auto_bright=True,
            use_camera_wb=True,use_auto_wb=False,half_size=half,
            output_color=rawpy.ColorSpace.sRGB,user_flip=0)
    array=raw_pixels(rgb,sizes,half)
    elapsed=time.perf_counter()-start
    render_seconds=None
    if parameters is not None:
        import server
        from processing import render_array
        start=time.perf_counter();array=render_array(array,server.DEFAULTS|parameters,bits=32);render_seconds=time.perf_counter()-start
    with output.open("xb") as file:array.astype("<f4",copy=False).tofile(file)
    print(json.dumps(dict(width=array.shape[1],height=array.shape[0],decode_seconds=elapsed,
        rawpy=rawpy.__version__,libraw=".".join(map(str,rawpy.libraw_version)),openmp=rawpy.flags.get("OPENMP"),original_flip=sizes.flip,render_seconds=render_seconds)),flush=True)

def measured(command,pid_marker=None):
    from smoke_native import memory
    process=subprocess.Popen(command,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    observed=0
    watched_pid=process.pid;worker_observed=False
    try:
        deadline=time.monotonic()+90
        while process.poll() is None:
            if time.monotonic()>deadline:raise TimeoutError("RAW benchmark child exceeded 90 seconds")
            if pid_marker and pid_marker.exists():
                try:actual_pid=json.loads(pid_marker.read_text(encoding="utf8"))["pid"]
                except ValueError:actual_pid=None
                if actual_pid and watched_pid!=actual_pid:
                    watched_pid=actual_pid;observed=0;worker_observed=False
            try:
                counters=memory(watched_pid);observed=max(observed,counters["working_set"],counters["peak_working_set"]);worker_observed=True
            except OSError:
                if not worker_observed and process.poll() is None:raise
            time.sleep(.02)
        stdout,stderr=process.communicate(timeout=5)
        if process.returncode:raise RuntimeError(f"Child failed ({process.returncode}): "+stderr.decode("utf8",errors="replace")[-6000:])
        if not worker_observed or (pid_marker and not pid_marker.exists()):raise RuntimeError("Actual image worker was not sampled")
        result=json.loads(stdout.decode("utf8"));result["peak_working_set_mib"]=observed
        result["memory_measurement"]="Actual image worker's Windows peak working-set counter, polled every 20 ms; venv redirector excluded"
        return result
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=5)

def compare(old,new,width,height):
    import numpy as np
    expected=width*height*3*4
    if old.stat().st_size!=expected or new.stat().st_size!=expected:raise ValueError("Float output length mismatch")
    a=np.memmap(old,dtype="<f4",mode="r",shape=(height,width,3));b=np.memmap(new,dtype="<f4",mode="r",shape=a.shape)
    maximum=0.;total=0.;different=0;invalid=0
    for y in range(0,height,128):
        aa=a[y:y+128];bb=b[y:y+128];delta=np.abs(aa-bb)
        maximum=max(maximum,float(delta.max()));total+=float(delta.sum(dtype=np.float64));different+=int(np.count_nonzero(delta))
        invalid+=int(np.count_nonzero(~np.isfinite(bb)))
    return dict(max_abs_error=maximum,mean_abs_error=total/a.size,different_channels=different,
        nonfinite_channels=invalid,within_one_rgb16_code=invalid==0 and maximum<=1/65535+1e-7)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dll",type=Path);parser.add_argument("--raw12",type=Path);parser.add_argument("--raw14",type=Path)
    parser.add_argument("--probe",type=Path,default=ROOT/"native/target/release/examples/raw_probe.exe")
    parser.add_argument("--develop-parameters",type=Path,help="Optional nine-control JSON; compare float sRGB rendering as well")
    parser.add_argument("--oracle-child",nargs=3,metavar=("SOURCE","OUTPUT","HALF"))
    args=parser.parse_args()
    if sys.platform!="win32":parser.error("This pinned-DLL benchmark requires Windows")
    parameters=None
    if args.develop_parameters:
        if args.develop_parameters.stat().st_size>8192:parser.error("Parameter JSON exceeds 8 KiB")
        parameters=json.loads(args.develop_parameters.read_text(encoding="utf8"))
        if not isinstance(parameters,dict) or set(parameters)-set(("exposure","contrast","highlights","shadows","temperature","tint","saturation","vibrance","sharpen")):parser.error("Only the nine supported controls are accepted")
    if args.oracle_child:
        source,output,half=args.oracle_child;oracle(Path(source),Path(output),half=="1",parameters);return
    if not all((args.dll,args.raw12,args.raw14)):parser.error("Provide explicit pinned DLL and both public fixtures")
    # Freeze existing source semantics before comparing native changes.
    subprocess.run(["git","diff","--exit-code","a2a2e089d760221a52c8774cca8a2e42f0a82269","--","imaging.py","processing.py"],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
    manifest=json.loads((ROOT/"native/vendor/libraw/manifest.json").read_text(encoding="utf8"))
    if digest(args.dll)!=manifest["dll_sha256"]:raise ValueError("LibRaw DLL does not match the pinned official archive")
    output=ROOT/".development/native-raw"/("run-"+uuid.uuid4().hex);output.mkdir(parents=True)
    report=dict(stage="2B-float-develop" if parameters is not None else "2A-raw-core",parameters=parameters,full_migration_complete=False,oracle_base_commit="a2a2e089d760221a52c8774cca8a2e42f0a82269",
        native_base_commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
        native_tree_dirty=bool(subprocess.check_output(["git","status","--porcelain"],cwd=ROOT,text=True)),
        native_probe_sha256=digest(args.probe),native_source_sha256={str(path.relative_to(ROOT)):digest(path) for path in (
            ROOT/"native/crates/core/src/raw.rs",ROOT/"native/crates/core/src/raw_geometry.rs",ROOT/"native/crates/core/src/native_raw.c",ROOT/"native/crates/core/src/develop.rs")},
        oracle_module_sha256=digest(ROOT/"imaging.py"),libraw_dll_sha256=digest(args.dll),cases=[])
    for bits,source in ((12,args.raw12),(14,args.raw14)):
        before=digest(source)
        if before!=EXPECTED[bits]:raise ValueError("Fixture hash does not match the public Nikon Z30 sample")
        for half in (False,True):
            label=f"{bits}bit-{'half' if half else 'full'}";print("Comparing "+label,flush=True)
            old=output/(label+"-oracle.f32");new=output/(label+"-native.f32")
            oracle_arguments=(["--develop-parameters",str(args.develop_parameters.resolve())] if parameters is not None else [])
            native_arguments=(["--develop",str(args.develop_parameters.resolve())] if parameters is not None else [])
            baseline=measured([sys.executable,str(Path(__file__).resolve()),"--oracle-child",str(source.resolve()),str(old),str(int(half))]+oracle_arguments,old.with_suffix(".pid.json"))
            candidate=measured([str(args.probe.resolve()),str(args.dll.resolve()),str(source.resolve()),str(new)]+(["half"] if half else [])+native_arguments)
            if (candidate["width"],candidate["height"])!=(baseline["width"],baseline["height"]):raise ValueError("Native and oracle geometry differ")
            comparison=compare(old,new,candidate["width"],candidate["height"])
            comparison["within_render_tolerance"]=comparison["nonfinite_channels"]==0 and comparison["max_abs_error"]<=2e-5
            report["cases"].append(dict(label=label,source_sha256=before,source_unchanged=digest(source)==before,oracle=baseline,native=candidate,comparison=comparison))
            print(json.dumps(report["cases"][-1],ensure_ascii=False),flush=True)
    report["success"]=all(case["source_unchanged"] and case["comparison"]["within_render_tolerance" if parameters is not None else "within_one_rgb16_code"] for case in report["cases"])
    report["limitations"]=["Single run per case; not a statistically stable performance claim","Public samples, not user camera originals","Camera WB + linear sRGB only; other WB/ICC/wide gamut not yet accepted","Core probe only; RAW editing/export UI remains disabled","Clean-machine VC runtime and final LGPL redistribution pending"]
    path=output/"report.json";path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    print(json.dumps(dict(report=str(path),success=report["success"])),flush=True)
    if not report["success"]:raise SystemExit(1)

if __name__=="__main__":main()

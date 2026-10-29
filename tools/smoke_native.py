"""Windows native process acceptance; Python is a developer harness, not an app dependency."""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


class Memory(ctypes.Structure):
    _fields_=[("cb",wintypes.DWORD),("page_faults",wintypes.DWORD)]+[(name,ctypes.c_size_t) for name in (
        "peak_working_set","working_set","peak_paged_pool","paged_pool","peak_nonpaged_pool","nonpaged_pool","pagefile","peak_pagefile","private_bytes")]


def memory(pid):
    kernel=ctypes.WinDLL("kernel32",use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    psapi=ctypes.WinDLL("psapi",use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(Memory),wintypes.DWORD]
    handle=kernel.OpenProcess(0x400|0x10,False,pid)
    if not handle:raise ctypes.WinError(ctypes.get_last_error())
    try:
        result=Memory();result.cb=ctypes.sizeof(result)
        if not psapi.GetProcessMemoryInfo(handle,ctypes.byref(result),result.cb):raise ctypes.WinError(ctypes.get_last_error())
        return {name:round(getattr(result,name)/1024**2,3) for name in ("peak_working_set","working_set","private_bytes")}
    finally:kernel.CloseHandle(handle)


def digest(path):
    with path.open("rb") as stream:return hashlib.file_digest(stream,"sha256").hexdigest()


def wait_json(path,process,timeout=30):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if path.exists():
            try:return json.loads(path.read_text(encoding="utf8"))
            except (OSError,ValueError):pass
        if process.poll() is not None:raise RuntimeError(f"Native process exited before marker: {process.returncode}")
        time.sleep(.02)
    raise TimeoutError("Native marker timed out: "+path.name)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe",required=True,type=Path)
    parser.add_argument("--sample",required=True,type=Path,help="JPEG/PNG native viewer input, optionally ICC tagged; not a RAW UI test")
    parser.add_argument("--legacy-recovery",action="store_true",help="Before the second launch, seed a native v1 draft and verify backed-up v2 migration")
    parser.add_argument("--out",required=True,type=Path)
    parser.add_argument("--work",type=Path,default=Path(__file__).resolve().parents[1]/".development/native-smoke")
    args=parser.parse_args()
    if sys.platform!="win32":parser.error("This acceptance harness requires Windows")
    if args.out.suffix.lower()!=".json":parser.error("Acceptance output must be JSON, never an image")
    exe=args.exe.resolve();sample=args.sample.resolve();args.work.mkdir(parents=True,exist_ok=True)
    original=digest(sample);result=dict(stage="native-viewer-process-smoke",exe_sha256=digest(exe),exe_bytes=exe.stat().st_size,source_sha256=original,
        timing_definition="process spawn to file marker / event-loop entry; not first on-screen paint",cycles=[],real_raw_decode_verified=False,
        real_cloud_inference_requests=0,automatic_desktop_visual_verification=False)
    active=[]
    try:
        root=Path(tempfile.mkdtemp(prefix="原生 smoke ",dir=args.work)).resolve();data=root/"data";result["qa_directory"]=str(root)
        windows=Path(os.environ["SystemRoot"])
        env=os.environ|{"Z30_NATIVE_DATA_DIR":str(data),"PATH":str(windows/"System32")+";"+str(windows)}
        startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=4
        for cycle in (1,2):
            legacy_bytes=None
            if cycle==2 and args.legacy_recovery:
                legacy=json.loads((data/"session.json").read_text(encoding="utf8"));legacy['version']=1
                for photo in legacy['photos']:photo.pop('recipe',None);photo.update(rating=4,flag=1)
                legacy_bytes=json.dumps(legacy,ensure_ascii=False).encode('utf8');(data/"session.json").write_bytes(legacy_bytes)
            ready=root/f"ready{cycle}.json";stop=root/f"stop{cycle}.signal";log=root/f"stderr{cycle}.log"
            with log.open("w",encoding="utf8") as stream:
                start=time.perf_counter();process=subprocess.Popen([str(exe),"--qa-ready",str(ready),"--qa-stop",str(stop),*([str(sample)] if cycle==1 else [])],
                    cwd=root,env=env,startupinfo=startup,stdout=stream,stderr=stream);active.append((process,stop))
                marker=wait_json(ready,process);spawn_ms=(time.perf_counter()-start)*1000
                preview=wait_json(ready.with_suffix(".preview.json"),process)
                session=json.loads((data/"session.json").read_text(encoding="utf8"))
                assert len(session["photos"])==1 and os.path.samefile(session["photos"][0]["path"],sample)
                if legacy_bytes is not None:
                    assert session['version']==2 and session['photos'][0]['rating']==4 and session['photos'][0]['flag']==1
                    assert session['photos'][0]['recipe']['white_balance']=='camera' and all(v==0 for v in session['photos'][0]['recipe']['parameters'].values())
                    backups=list(data.glob('session-v1-backup-*.json'));assert len(backups)==1 and backups[0].read_bytes()==legacy_bytes
                    result['legacy_migrated_with_exact_backup']=True
                if cycle==1:
                    frozen=(data/"session.json").read_bytes();other=root/"duplicate.json";other_stop=root/"duplicate.signal"
                    duplicate=subprocess.run([str(exe),"--qa-ready",str(other),"--qa-stop",str(other_stop)],cwd=root,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=15)
                    assert duplicate.returncode!=0 and not other.exists() and (data/"session.json").read_bytes()==frozen
                    result["duplicate_instance_rejected_without_overwrite"]=True
                counters=memory(process.pid);start_stop=time.perf_counter();stop.write_text("stop",encoding="ascii")
                code=process.wait(timeout=20);assert code==0
                result["cycles"].append(dict(cycle=cycle,spawn_to_event_loop_ms=round(spawn_ms,3),marker=marker,preview=preview,memory_mib=counters,shutdown_seconds=round(time.perf_counter()-start_stop,3),exit_code=code))
        result.update(original_unchanged=digest(sample)==original,draft_recovered=True,python_and_developer_tools_removed_from_PATH=True,unicode_and_space_paths=True,success=True)
    except BaseException as error:
        result.update(success=False,error=type(error).__name__+": "+str(error));raise
    finally:
        for process,stop in active:
            if process.poll() is None:
                try:stop.write_text("stop",encoding="ascii");process.wait(timeout=8)
                except (OSError,subprocess.TimeoutExpired):
                    # Failure cleanup only for the exact child created by this harness.
                    process.kill();process.wait(timeout=5)
        args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf8")
        print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":main()

"""Bounded on-disk float frame cache; immutable leases and request scratch maps."""
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import threading
import numpy as np

_scratch=ContextVar('z30_scratch',default=None)

def key(value):return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False,separators=(',',':')).encode()).hexdigest()

def allocate(shape,dtype=np.float32):
    scratch=_scratch.get()
    return scratch.allocate(shape,dtype) if scratch else np.empty(shape,dtype=dtype)

def copy_array(array):
    if _scratch.get() is None:return array.copy()
    result=allocate(array.shape,array.dtype)
    for top in range(0,len(array),128):
        checkpoint();result[top:top+128]=array[top:top+128]
    return result

def release_input(array):
    current=_scratch.get()
    if current and current.input_array is not None and not np.shares_memory(array,current.input_array):
        current.release();current.input_array=None

def checkpoint():
    current=_scratch.get()
    if current and current.progress:current.progress('prepare',.25)

def _ensure_space(root,size):
    if shutil.disk_usage(root).free<size+128*1024**2:raise ValueError('全尺寸缓存临时空间不足，请释放磁盘空间后重试')

class Scratch:
    def __init__(self,root,progress=None,input_array=None,release=None):
        self.input_array=input_array;self.release=release
        self.progress=progress
        self.root=Path(root)/('scratch-'+secrets.token_hex(16));self.root.mkdir();self.arrays=[]
    def __enter__(self):self.token=_scratch.set(self);return self
    def allocate(self,shape,dtype):
        _ensure_space(self.root,int(np.prod(shape))*np.dtype(dtype).itemsize)
        path=self.root/(secrets.token_hex(12)+'.bin');array=np.memmap(path,mode='w+',dtype=dtype,shape=shape)
        self.arrays.append((array,path));return array
    def retain(self,array):
        keep=[]
        for old,path in self.arrays:
            if np.shares_memory(old,array):keep.append((old,path))
            else:old._mmap.close();path.unlink()
        self.arrays=keep
    def __exit__(self,*exc):
        _scratch.reset(self.token)
        for array,path in self.arrays:array._mmap.close()
        shutil.rmtree(self.root)

@contextmanager
def writer(path,shape):
    """Sequential NPY output: no full-size resident output mapping while writing."""
    path=Path(path);_ensure_space(path.parent,int(np.prod(shape))*4)
    with path.open('wb') as file:
        np.lib.format.write_array_header_1_0(file,dict(descr=np.dtype('float32').str,fortran_order=False,shape=tuple(shape)))
        cursor=0
        def write(top,bottom,array):
            nonlocal cursor
            if top!=cursor or array.shape!=(bottom-top,*shape[1:]):raise ValueError('分块写入顺序或尺寸无效')
            file.write(np.ascontiguousarray(array,dtype=np.float32).tobytes());cursor=bottom
        yield write
        if cursor!=shape[0]:raise ValueError('全尺寸缓存未写完')
        file.flush();os.fsync(file.fileno())

def _alive(pid):
    if os.name=='nt':
        import ctypes
        api=ctypes.WinDLL('kernel32',use_last_error=True);api.OpenProcess.restype=ctypes.c_void_p
        api.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_uint];api.CloseHandle.argtypes=[ctypes.c_void_p]
        handle=api.OpenProcess(0x100000,False,pid)
        if not handle:return ctypes.get_last_error()!=87  # Access denied is not proof of death.
        try:return api.WaitForSingleObject(handle,0)==258
        finally:api.CloseHandle(handle)
    try:os.kill(pid,0);return True
    except ProcessLookupError:return False
    except PermissionError:return True

class NativeCache:
    def __init__(self,root,max_bytes=2*1024**3):
        self.base=Path(root).resolve();self.base.mkdir(parents=True,exist_ok=True)
        for old in self.base.glob('run-*'):
            try:
                if old.resolve().parent!=self.base:continue
                owner=json.loads((old/'owner.json').read_text(encoding='utf8'))
                if isinstance(owner.get('pid'),int) and not _alive(owner['pid']):shutil.rmtree(old)
            except (OSError,ValueError):pass
        self.root=self.base/('run-'+secrets.token_hex(16));self.root.mkdir()
        (self.root/'owner.json').write_text(json.dumps(dict(pid=os.getpid())),encoding='utf8')
        self.max_bytes=max_bytes;self.entries=OrderedDict();self.pins={};self.lock=threading.RLock();self.builders={}
        self.hits=0;self.misses=0;self.closed=False;self.stamps={}
    def _evict(self,extra=0):
        for k in list(self.entries):
            if sum(self.entries.values())+extra<=self.max_bytes:break
            if self.pins.get(k):continue
            (self.root/(k+'.npy')).unlink(missing_ok=True);del self.entries[k]
    @contextmanager
    def lease(self,k,build,progress=None):
        with self.lock:
            if self.closed:raise ValueError('全尺寸缓存已停止')
            gate=self.builders.setdefault(k,threading.RLock())
        with gate:
            with self.lock:
                exists=k in self.entries
                if exists:
                    try:
                        stat=(self.root/(k+'.npy')).stat();changed=self.stamps.get(k)!=(stat.st_mtime_ns,stat.st_size)
                    except OSError:changed=True
                    if changed:
                        if self.pins.get(k):raise ValueError('活动全尺寸缓存被改变，请结束当前操作后重试')
                        (self.root/(k+'.npy')).unlink(missing_ok=True);del self.entries[k];exists=False
                if exists:self.pins[k]=self.pins.get(k,0)+1
            if not exists:
                part=self.root/(k+'-'+secrets.token_hex(8)+'.part.npy')
                try:
                    build(part)
                    check=np.load(part,mmap_mode='r',allow_pickle=False)
                    valid=check.dtype==np.float32 and check.ndim==3 and check.shape[-1]==3
                    check._mmap.close()
                    if not valid:raise ValueError('全尺寸缓存格式无效')
                    size=part.stat().st_size
                    if size>self.max_bytes:raise ValueError('单帧超过全尺寸缓存容量')
                    with self.lock:
                        if self.closed:raise ValueError('缓存正在停止')
                        self._evict(size)
                        if sum(self.entries.values())+size>self.max_bytes:raise ValueError('全尺寸缓存正在占用，请稍后重试或减少并发')
                        os.replace(part,self.root/(k+'.npy'));self.entries[k]=size
                        stat=(self.root/(k+'.npy')).stat();self.stamps[k]=(stat.st_mtime_ns,stat.st_size)
                        self.misses+=1;self.pins[k]=self.pins.get(k,0)+1
                finally:part.unlink(missing_ok=True)
            with self.lock:
                if self.closed:raise ValueError('缓存正在停止')
                if exists:self.hits+=1
                self.entries.move_to_end(k)
                array=np.load(self.root/(k+'.npy'),mmap_mode='r',allow_pickle=False)
        try:
            if exists and progress:progress('cache',.8)
            yield array
        finally:
            array._mmap.close()
            with self.lock:
                self.pins[k]-=1;self._evict()
                if self.closed and not any(self.pins.values()):shutil.rmtree(self.root,ignore_errors=True)
    def clear(self):
        with self.lock:
            for k in list(self.entries):
                if not self.pins.get(k):
                    (self.root/(k+".npy")).unlink(missing_ok=True);del self.entries[k]
    def stats(self):
        with self.lock:return dict(bytes=sum(self.entries.values()),limit_bytes=self.max_bytes,frames=len(self.entries),hits=self.hits,misses=self.misses)
    def close(self):
        with self.lock:
            self.closed=True
            # Shutdown does not wait on active encoders; their final lease release
            # removes files. A killed process is cleaned on the next cold start.
            if any(self.pins.values()):return
            shutil.rmtree(self.root,ignore_errors=True)


def available_memory():
    if os.name=='nt':
        import ctypes
        class Status(ctypes.Structure):
            _fields_=[('length',ctypes.c_uint32),('load',ctypes.c_uint32)]+[(n,ctypes.c_uint64) for n in ('total','available','page_total','page_available','virtual_total','virtual_available','extended')]
        status=Status();status.length=ctypes.sizeof(status)
        return status.available if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)) else 0
    try:return os.sysconf('SC_AVPHYS_PAGES')*os.sysconf('SC_PAGE_SIZE')
    except (ValueError,OSError,AttributeError):return 0

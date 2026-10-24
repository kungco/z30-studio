"""Optional, CPU-only semantic masks. Images never leave this process.

Weights are not redistributed. Explicit installation fetches a pinned, verified
ONNX conversion of NVIDIA SegFormer B0 (ADE20K); no Python remote code or pickle.
"""
import base64
from collections import deque
import hashlib
import io
import os
from pathlib import Path
import secrets
import threading
import time
import urllib.request
import numpy as np
from PIL import Image, ImageFilter

REVISION='d3e5499fa8701ff0453ca940a8dfeae39b2f1504'
URL=f'https://huggingface.co/Xenova/segformer-b0-finetuned-ade-512-512/resolve/{REVISION}/onnx/model.onnx'
SHA256='3e5c18a4be395f16646438d54c42377ddc202edfa33d5eced0c9506de75c44c2'
SIZE=15335446
LICENSE='https://github.com/NVlabs/SegFormer/blob/master/LICENSE'
# ADE20K background / architectural classes; retain scene objects as candidates.
BACKGROUND={0,1,2,3,4,5,6,7,8,9,10,11,13,14,15,16,21,24,25,26,28,29,32,33,34,35,38,40,42,44,45,46,48,51,52,53,54,56,58,59,60,61,62,63,64,68,70,72,73,77,79,84,86,87,91,93,94,95,96,99,101,104,105,106,109,113,121,128,140}


def main_component(mask):
    h,w=mask.shape;seen=np.zeros_like(mask,bool);best=[];score=0
    for y,x in zip(*np.nonzero(mask)):
        if seen[y,x]:continue
        queue=deque([(y,x)]);seen[y,x]=True;points=[]
        while queue:
            cy,cx=queue.popleft();points.append((cy,cx))
            for dy,dx in ((0,1),(0,-1),(1,0),(-1,0)):
                ny,nx=cy+dy,cx+dx
                if 0<=ny<h and 0<=nx<w and mask[ny,nx] and not seen[ny,nx]:
                    seen[ny,nx]=True;queue.append((ny,nx))
        a=np.asarray(points);distance=np.hypot(a[:,0].mean()/h-.5,a[:,1].mean()/w-.5)
        candidate=len(points)*(1-.5*distance)
        if candidate>score:score=candidate;best=points
    result=np.zeros_like(mask,np.float32)
    for y,x in best:result[y,x]=1
    return result


def bitmap(values):
    image=Image.fromarray(np.rint(np.clip(values,0,1)*255).astype(np.uint8)).resize((256,256),Image.Resampling.BILINEAR)
    image=image.filter(ImageFilter.GaussianBlur(.7));out=io.BytesIO();image.save(out,'PNG',optimize=True)
    return base64.b64encode(out.getvalue()).decode('ascii')


class LocalVision:
    def __init__(self,root):
        self.root=Path(root);self.path=self.root/'segformer-b0-ade.onnx'
        self.lock=threading.Lock();self.session=None;self.progress=0.;self.state='not_installed'

    def status(self):
        installed=self.path.is_file() and self.path.stat().st_size==SIZE
        return dict(installed=installed,model='SegFormer B0 · ADE20K · CPU',bytes=SIZE,
                    progress=self.progress,state=self.state,license=LICENSE,source=URL,
                    local_only=True,kinds=['subject','person','sky'])

    def verify(self):
        if not self.path.is_file():raise ValueError('请先在“本地 AI 蒙版”中下载模型；不会上传照片')
        with self.path.open('rb') as file:
            if hashlib.file_digest(file,'sha256').hexdigest()!=SHA256:
                raise ValueError('本地分割模型校验失败，请重新下载')

    def install(self):
        if not self.lock.acquire(blocking=False):raise ValueError('本地模型正在安装或推理，请稍后重试')
        part=None
        try:
            self.root.mkdir(parents=True,exist_ok=True);self.progress=0.;self.state='downloading'
            part=self.root/(secrets.token_hex(16)+'.part');digest=hashlib.sha256();read=0;deadline=time.monotonic()+180
            with urllib.request.urlopen(URL,timeout=30) as response,part.open('xb') as file:
                while chunk:=response.read(256*1024):
                    read+=len(chunk)
                    if read>SIZE or time.monotonic()>deadline:raise ValueError('模型下载超过大小或时间限制')
                    file.write(chunk);digest.update(chunk);self.progress=read/SIZE
                file.flush();os.fsync(file.fileno())
            if read!=SIZE or digest.hexdigest()!=SHA256:raise ValueError('模型下载不完整或校验失败；未替换已有模型')
            os.replace(part,self.path);self.session=None;self.state='ready';self.progress=1.
            return self.status()
        except Exception:
            self.state='error';raise
        finally:
            if part:part.unlink(missing_ok=True)
            self.lock.release()

    def segment(self,image,kind):
        if kind not in ('subject','person','sky'):raise ValueError('本地分割类型无效')
        with self.lock:
            if self.session is None:
                self.verify()
                try:import onnxruntime as ort
                except ImportError as exc:raise ValueError('缺少 ONNX CPU 运行库，请安装 requirements.txt 或使用最新 Windows 包') from exc
                options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
                options.log_severity_level=3
                self.session=ort.InferenceSession(str(self.path),sess_options=options,providers=['CPUExecutionProvider'])
            rgb=np.asarray(image.convert('RGB').resize((512,512),Image.Resampling.BILINEAR),dtype=np.float32)/255
            rgb=(rgb-np.array([.485,.456,.406],np.float32))/np.array([.229,.224,.225],np.float32)
            inputs=np.ascontiguousarray(rgb.transpose(2,0,1)[None])
            logits=np.asarray(self.session.run(None,{self.session.get_inputs()[0].name:inputs})[0],np.float32)[0]
            if logits.ndim!=3 or logits.shape[0]!=150 or not np.isfinite(logits).all():
                raise ValueError('分割模型输出与 ADE20K 合约不匹配')
            labels=logits.argmax(axis=0);logits-=logits.max(axis=0,keepdims=True)
            probs=np.exp(logits);probs/=probs.sum(axis=0,keepdims=True)
            ids=[2] if kind=='sky' else [12] if kind=='person' else [i for i in range(150) if i not in BACKGROUND]
            probability=probs[ids].sum(axis=0)
            mask=np.clip((probability-.15)/.7,0,1)
            if kind=='subject':mask*=main_component(np.isin(labels,ids))
            if np.mean(mask>.5)<.001:raise ValueError('没有检测到足够可靠的该类区域；未生成替代蒙版，请手动绘制或改选类型')
            self.state='ready'
            return dict(bitmap=bitmap(mask),coverage=float(np.mean(mask>.5)),kind=kind,
                        model='SegFormer B0 / ADE20K',local_only=True,
                        limitation='主体选择为最大场景对象连通区域；人像为人物区域，不是面部或头发精细分割。请检查边缘和误选。')

"""Reviewable model inputs; bounded in-memory tickets and honest cost scenarios."""
import base64
import copy
import hashlib
import io
import json
import math
import secrets
import statistics
import threading
import time
from PIL import Image
import api_backend
import codex_assist

def fingerprint(source,owner,p,goal,mode,policy,revision):
    return hashlib.sha256(json.dumps([source,owner,p,goal,mode,policy,revision],sort_keys=True,ensure_ascii=False).encode()).hexdigest()

class Tickets:
    def __init__(self,max_bytes=32*1024**2,ttl=600):
        self.max_bytes=max_bytes;self.ttl=ttl;self.items={};self.lock=threading.RLock()
    def prune(self):
        now=time.monotonic()
        for k in list(self.items):
            if now-self.items[k]['created']>self.ttl:del self.items[k]
    def issue(self,source,owner,p,goal,mode,policy,config,before,current,timings=()):
        signature=fingerprint(source,owner,p,goal,mode,policy,config['revision']);token=secrets.token_hex(16)
        size=len(before)+len(current)
        if size>self.max_bytes:raise ValueError('发送预览超出内存预算')
        with self.lock:
            self.prune()
            while self.items and (len(self.items)>=16 or sum(x['bytes'] for x in self.items.values())+size>self.max_bytes):del self.items[next(iter(self.items))]
            self.items[token]=dict(source=source,owner=owner,signature=signature,created=time.monotonic(),before=before,current=current,bytes=size)
        prompt=codex_assist.build_prompt(p,goal,bool(config.get('_is_raw')))
        images=[]
        for label,data in [('baseline',before),('current',current)]:
            with Image.open(io.BytesIO(data)) as im:
                if len(im.getexif()):raise ValueError('发送预览意外包含 EXIF，已阻止发送')
                images.append(dict(label=label,width=im.width,height=im.height,bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),data='data:image/jpeg;base64,'+base64.b64encode(data).decode()))
        if config['provider']=='codex':
            payload=dict(transport='Codex CLI stdin + schema + 2 JPEG attachments',stdin=prompt,schema=copy.deepcopy(codex_assist.SCHEMA),images=images)
        else:payload=api_backend.make_payload(config,before,current,prompt,codex_assist.SCHEMA)
        estimate=cost_scenario(prompt,images,config,timings)
        return dict(token=token,provider=config['provider'],model=config.get('model') if config['provider']!='codex' else 'CLI 当前配置',
                    destination='Codex CLI（现有登录）' if config['provider']=='codex' else config['base_url']+'/chat/completions',
                    images=images,prompt=prompt,payload=payload,estimate=estimate,expires_seconds=self.ttl,
                    privacy='软件只自动附加两张 sRGB JPEG、目标、允许的参数与 JSON 约束；不自动附加原片、原始文件名、EXIF/GPS、路径或密钥。目标中的文字和图像可见内容仍会发送。')
    def get(self,token,source,owner,p,goal,mode,policy,revision):
        with self.lock:
            self.prune();item=self.items.get(token)
            if not item or item['signature']!=fingerprint(source,owner,p,goal,mode,policy,revision):raise ValueError('发送预览已过期，或照片/参数/目标/模型改变；请重新预览')
            return item['before'],item['current']
    def drop_source(self,source):
        with self.lock:
            for k in list(self.items):
                if self.items[k]['source']==source:del self.items[k]

def cost_scenario(prompt,images,config,timings=()):
    # Vision token rules are provider-specific. This is explicitly a scenario
    # range, not an assertion about a particular vendor's actual tokenizer.
    text_bytes=len(prompt.encode());low=max(1,math.ceil(text_bytes/8));high=max(low,math.ceil(text_bytes/2))
    for image in images:
        low+=max(1,math.ceil(image['width']/32)*math.ceil(image['height']/32)//4)
        high+=max(512,math.ceil(image['width']/14)*math.ceil(image['height']/14))
    rates=config.get('rates') or {};cost=None
    if config['provider'] not in ('codex','local') and all(rates.get(k) is not None for k in ('input','output')):
        cost=dict(low=low*rates['input']/1e6,high=(high*rates['input']+2048*rates['output'])/1e6,currency=rates.get('currency','USD'))
    median=statistics.median(timings[-20:]) if timings else None
    return dict(input_tokens_scenario=[low,high],output_token_cap=None if config['provider']=='codex' else 2048,cost_scenario=cost,
        model_seconds=dict(samples=len(timings),median=median,low=max(0,median*.5) if median is not None else None,
                           high=median*2+2 if median is not None else 180,timeout=180),
        note='图像 token/隐藏推理/缓存/服务商计价不同；情景估计不保证账单。单价由你填写。无历史时只显示 180 秒推理超时上限；历史样本包含结果检查；无历史时，本机准备与检查需另计。')

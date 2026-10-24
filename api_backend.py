"""Optional image advice through an OpenAI-compatible HTTP endpoint; no SDK."""
import base64
import http.client
import ipaddress
import json
import hashlib
import math
import os
from pathlib import Path
import re
import secrets
import socket
import ssl
import threading
import urllib.parse
import urllib.request

from app_runtime import data_root

DEFAULT_URL = 'https://api.deepseek.com'
DEFAULT_MODEL = 'deepseek-flash'
LOCAL_URL='http://127.0.0.1:11434/v1'
LOCAL_MODEL='qwen3-vl:8b'


def normalize_url(value):
    if not isinstance(value,str) or len(value)>512:
        raise ValueError('接口地址无效')
    p=urllib.parse.urlsplit(value.strip().rstrip('/'))
    if p.scheme not in ('https','http') or not p.hostname or p.username or p.password or p.query or p.fragment:
        raise ValueError('接口地址必须为无凭证、查询参数的 HTTPS Base URL')
    try:p.port
    except ValueError:raise ValueError('接口端口无效') from None
    try:
        p.port
        loopback=p.hostname=='localhost' or ipaddress.ip_address(p.hostname).is_loopback
    except ValueError:
        loopback=False
    if p.scheme=='http' and not loopback:
        raise ValueError('远程接口必须使用 HTTPS；HTTP 仅允许本机回环地址')
    if p.path.endswith('/chat/completions'):
        raise ValueError('请输入 Base URL，不要包含 /chat/completions')
    if any(ord(c)<33 for c in p.geturl()):raise ValueError('接口地址不能含空格或控制字符')
    return p.geturl()


def local_url(value):
    url=normalize_url(value);host=urllib.parse.urlsplit(url).hostname
    try:local=host=='localhost' or ipaddress.ip_address(host).is_loopback
    except ValueError:local=False
    if not local:raise ValueError('本机模型只允许回环地址，不接受远程主机')
    return url


def backend_tag(config=None):
    identity=['codex'] if config is None or config.get('provider')=='codex' else [config['provider'],config['base_url'],config['model']]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def make_payload(config,before,current,prompt,schema):
    content=[{'type':'text','text':prompt+'\n只返回 JSON 对象，不使用代码围栏。JSON Schema：'+json.dumps(schema,ensure_ascii=False)}]
    for data in (before,current):content.append({'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+base64.b64encode(data).decode()}})
    payload=dict(model=config['model'],messages=[dict(role='user',content=content)],stream=False,max_tokens=2048,response_format={'type':'json_object'})
    if config['provider']=='deepseek':payload['thinking']={'type':'disabled'}
    return payload


class Settings:
    def __init__(self,root=None):
        self.path=Path(root or data_root())/'ai-provider.json'
        self.lock=threading.RLock();self.key='';self.key_url=''
        self.value=dict(provider='codex',base_url=DEFAULT_URL,model=DEFAULT_MODEL,revision=secrets.token_hex(16))
        try:
            stored=json.loads(self.path.read_text(encoding='utf8'))
            self.value=self.validate(stored)|{'revision':secrets.token_hex(16)}
        except (OSError,ValueError,TypeError):pass

    @staticmethod
    def validate(value):
        if not isinstance(value,dict):raise ValueError('模型设置格式无效')
        provider=value.get('provider','codex')
        if provider not in ('codex','deepseek','compatible','local'):raise ValueError('模型后端无效')
        url=normalize_url(value.get('base_url',LOCAL_URL if provider=='local' else DEFAULT_URL))
        model=value.get('model',LOCAL_MODEL if provider=='local' else DEFAULT_MODEL)
        if not isinstance(model,str) or not re.fullmatch(r'[\w./:-]{1,128}',model,re.ASCII):raise ValueError('模型名称无效')
        if provider=='deepseek' and (url!=DEFAULT_URL or model!=DEFAULT_MODEL):
            raise ValueError('DeepSeek 视觉预设使用官方地址和 deepseek-flash；其他网关请选择兼容接口')
        if provider=='local':
            url=local_url(url)
            if ':cloud' in model.lower():raise ValueError('本机模式不接受云端模型标签，请使用本机视觉权重')
        rates=value.get('rates') or {}
        if not isinstance(rates,dict):raise ValueError('单价格式无效')
        prices={}
        for k in ('input','output'):
            v=rates.get(k)
            if v is not None and (isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=100000):raise ValueError('每百万 token 单价无效')
            prices[k]=v
        prices['currency']=rates.get('currency','USD')
        if prices['currency'] not in ('USD','CNY','EUR'):raise ValueError('单价币种无效')
        return dict(provider=provider,base_url=url,model=model,rates=prices)

    def secret(self):
        if self.value["provider"]=="local":return ""
        if self.key and self.key_url==self.value['base_url']:return self.key
        bound=os.environ.get('Z30_API_BASE_URL',DEFAULT_URL)
        if self.value['base_url']==bound.rstrip('/'):return os.environ.get('Z30_API_KEY','')
        return ''

    def public(self):
        with self.lock:
            return self.value|dict(has_key=bool(self.secret()),key_persistence='memory_only')

    def save(self,value):
        validated=self.validate(value)
        key=value.get('api_key','')
        if not isinstance(key,str) or len(key)>4096 or any(ord(c)<33 or ord(c)>126 for c in key):
            raise ValueError('API Key 无效')
        if validated["provider"]=="local" and key:raise ValueError("本机模式不接收 API Key，请清空密钥字段")
        with self.lock:
            next_key,next_url=self.key,self.key_url
            if self.value['base_url']!=validated['base_url']:
                next_key='';next_url=''
            if value.get('clear_key') or validated['provider']=='local':next_key='';next_url=''
            if key:next_key=key;next_url=validated['base_url']
            updated=validated|{'revision':secrets.token_hex(16)}
            self.path.parent.mkdir(parents=True,exist_ok=True)
            part=self.path.with_suffix('.json.tmp')
            part.write_text(json.dumps(updated,ensure_ascii=False),encoding='utf8');part.replace(self.path)
            self.value=updated
            self.key,self.key_url=next_key,next_url
            return self.public()

    def snapshot(self,revision=None):
        with self.lock:
            if revision is not None and revision!=self.value['revision']:
                raise ValueError('模型配置已改变；未发送旧分析，请确认当前后端后重新点击')
            if self.value['provider']=='codex':return None
            if self.value['provider']=='local':return dict(self.value,api_key='')
            key=self.secret()
            if not key:raise ValueError('请先在模型设置中填写 API Key；密钥仅在当前运行期间有效')
            if len(key)>4096 or any(ord(c)<33 or ord(c)>126 for c in key):raise ValueError('API Key 格式无效')
            return self.value|{'api_key':key}


class Request:
    def __init__(self,config):
        self.config=dict(config);self.cancelled=threading.Event();self.connection=None;self.lock=threading.RLock()
        key=self.config.get('api_key','')
        if self.config.get('provider')=='local':
            local_url(self.config['base_url'])
            if key:raise ValueError('本机模式不会接收或发送密钥')
        elif not key or len(key)>4096 or any(ord(c)<33 or ord(c)>126 for c in key):raise ValueError('API Key 格式无效')

    def kill(self):
        self.cancelled.set()
        with self.lock:
            conn=self.connection
            if conn:
                if conn.sock:
                    try:conn.sock.shutdown(socket.SHUT_RDWR)
                    except OSError:pass
                conn.close()

    def fetch(self,method,path,payload=None,timeout=180):
        p=urllib.parse.urlsplit(self.config['base_url'])
        if p.scheme=='https':
            conn=http.client.HTTPSConnection(p.hostname,p.port or 443,timeout=20,context=ssl.create_default_context())
            proxy=urllib.request.getproxies().get('https')
            if self.config.get('provider')!='local' and proxy and not urllib.request.proxy_bypass(p.hostname):
                proxy=urllib.parse.urlsplit(proxy)
                if proxy.scheme!='http' or not proxy.hostname:raise ValueError('请使用 HTTP CONNECT 代理或直连接口')
                headers={}
                if proxy.username:
                    auth=urllib.parse.unquote(proxy.username)+':'+urllib.parse.unquote(proxy.password or '')
                    headers['Proxy-Authorization']='Basic '+base64.b64encode(auth.encode()).decode()
                conn=http.client.HTTPSConnection(proxy.hostname,proxy.port or 80,timeout=20,context=ssl.create_default_context())
                conn.set_tunnel(p.hostname,p.port or 443,headers)
        else:conn=http.client.HTTPConnection(p.hostname,p.port or 80,timeout=20)
        with self.lock:self.connection=conn
        timer=threading.Timer(timeout,self.kill);timer.daemon=True;timer.start()
        try:
            if self.cancelled.is_set():raise ValueError('模型请求已取消')
            conn.connect();conn.auto_open=0
            if self.config.get('provider')=='local' and not ipaddress.ip_address(conn.sock.getpeername()[0]).is_loopback:
                raise ValueError('本机服务解析到了非回环地址，未发送图像')
            if self.cancelled.is_set():raise ValueError('模型请求已取消')
            conn.sock.settimeout(timeout)
            body=None if payload is None else json.dumps(payload,ensure_ascii=False).encode('utf8')
            headers={'Content-Type':'application/json'}
            if self.config.get('api_key'):headers['Authorization']='Bearer '+self.config['api_key']
            conn.request(method,(p.path.rstrip('/')+path) or '/',body=body,headers=headers)
            response=conn.getresponse()
            if response.status!=200:
                # Never echo error bodies: gateways may reflect credentials or prompts.
                raise ValueError(f'模型接口返回 HTTP {response.status}；请检查地址、模型、密钥和额度。不会自动重试')
            data=response.read(1024*1024+1)
            if len(data)>1024*1024:raise ValueError('模型响应过大')
            if self.cancelled.is_set():raise ValueError('模型请求超时或已取消')
            return json.loads(data)
        except (OSError,http.client.HTTPException,json.JSONDecodeError,UnicodeError):
            raise ValueError('模型连接中断、超时或响应无效；未自动重试，服务商可能已计费') from None
        finally:
            timer.cancel();conn.close()

    def generate(self,before,current,prompt,schema):
        payload=make_payload(self.config,before,current,prompt,schema)
        output=self.fetch('POST','/chat/completions',payload)
        try:
            choice=output['choices'][0];message=choice['message'];text=message['content']
            if choice.get('finish_reason')=='length' or message.get('tool_calls') or not isinstance(text,str) or not 0<len(text)<=50000:
                raise ValueError()
            result=json.loads(text)
            def contains_key(value):
                if isinstance(value,str):return bool(self.config.get('api_key')) and self.config['api_key'] in value
                if isinstance(value,dict):return any(contains_key(k) or contains_key(v) for k,v in value.items())
                if isinstance(value,list):return any(contains_key(v) for v in value)
                return False
            if contains_key(result):raise ValueError()
            return result
        except (KeyError,IndexError,TypeError,ValueError):
            raise ValueError('模型未返回完整的 JSON 调色建议；未应用参数，也不会自动重试') from None


def test_connection(config):
    data=Request(config).fetch('GET','/models',timeout=15)
    models=data.get('data',[])
    ids=[m.get('id') for m in models[:512] if isinstance(m,dict) and isinstance(m.get('id'),str) and len(m['id'])<=128 and not (config.get('api_key') and config['api_key'] in m['id'])]
    return dict(message='接口认证成功；模型列表检测不代表已验证看图或调色效果',models=ids,model_listed=config['model'] in ids)

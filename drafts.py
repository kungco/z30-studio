"""Durable local drafts: immutable deduplicated originals + transactional versions.

Only trusted session files enter add_asset. Documents never supply filesystem paths.
Failed writes preserve the last acknowledged revision; no models are called here.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import threading
import time

MAX_PHOTOS = 256
DOCUMENT_LIMIT = 32 * 1024 ** 2


class Conflict(ValueError):
    pass


class DraftStore:
    def __init__(self, root, max_bytes=20 * 1024 ** 3, versions=20):
        self.root = Path(root).resolve()
        self.blobs = self.root/'originals'; self.blobs.mkdir(parents=True, exist_ok=True)
        self.max_bytes, self.versions = max_bytes, versions
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS assets (id TEXT PRIMARY KEY, size INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS drafts (id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
                    document TEXT NOT NULL, saved REAL NOT NULL, request_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS versions (id TEXT, revision INTEGER, document TEXT NOT NULL,
                    saved REAL NOT NULL, PRIMARY KEY(id,revision));
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.root/'drafts.sqlite3', timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def key(value):
        if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{32}', value):
            raise ValueError('草稿编号无效')
        return value

    def asset_path(self, asset, verify=False):
        if not isinstance(asset, str) or not re.fullmatch(r'[a-f0-9]{64}\.(nef|jpg|jpeg|png)', asset):
            raise ValueError('草稿原片编号无效')
        path = self.blobs/asset
        if path.is_symlink() or not path.resolve().is_relative_to(self.blobs):
            raise ValueError('草稿原片路径无效')
        with self.connect() as db:
            row = db.execute('SELECT size FROM assets WHERE id=?', (asset,)).fetchone()
        if not row or not path.is_file() or path.stat().st_size != row['size']:
            raise ValueError('草稿原片丢失或大小异常，请从其他项目或原片恢复')
        if verify:
            with path.open('rb') as file:
                if hashlib.file_digest(file, 'sha256').hexdigest() != asset.split('.')[0]:
                    raise ValueError('草稿原片校验失败，已停止处理')
        return path

    def add_asset(self, source):
        source = Path(source)
        suffix = source.suffix.lower()
        if suffix not in ('.nef', '.jpg', '.jpeg', '.png'):
            raise ValueError('草稿原片格式无效')
        size = source.stat().st_size
        with self.lock:
            with self.connect() as db:
                used = db.execute('SELECT COALESCE(SUM(size),0) FROM assets').fetchone()[0]
            # Reserve enough physical space for atomic copy; never fill the volume.
            if size <= 0 or size > 150 * 1024 ** 2 or shutil.disk_usage(self.root).free < size + 256 * 1024 ** 2:
                raise ValueError('草稿磁盘空间不足，未覆盖上次自动保存')
            part = self.blobs/(secrets.token_hex(16)+'.part')
            digest = hashlib.sha256()
            try:
                with source.open('rb') as src, part.open('xb') as dst:
                    while chunk := src.read(1024 * 1024):
                        digest.update(chunk); dst.write(chunk)
                    dst.flush(); os.fsync(dst.fileno())
                asset = digest.hexdigest() + suffix
                target = self.blobs/asset
                with self.connect() as db:
                    existing = db.execute('SELECT size FROM assets WHERE id=?', (asset,)).fetchone()
                    if not existing and used + size > self.max_bytes:
                        raise ValueError('本机草稿原片已达 20 GiB，请保存项目并删除不再需要的草稿')
                    if target.exists():
                        # Never silently reuse a corrupt content-addressed blob.
                        with target.open('rb') as file:
                            if hashlib.file_digest(file, 'sha256').hexdigest() != digest.hexdigest():
                                raise ValueError('草稿原片校验失败，未覆盖旧数据')
                    else:
                        os.replace(part, target)
                    db.execute('INSERT OR IGNORE INTO assets VALUES (?,?)', (asset, size))
                return asset
            finally:
                part.unlink(missing_ok=True)

    def save(self, key, document, expected, request_id, snapshot=False):
        self.key(key); self.key(request_id)
        if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
            raise ValueError('草稿版本无效')
        photos = document.get('photos')
        if not isinstance(photos, list) or len(photos) > MAX_PHOTOS:
            raise ValueError('草稿最多包含 256 张照片')
        encoded = json.dumps(document, ensure_ascii=False, allow_nan=False, sort_keys=True)
        if len(encoded.encode('utf8')) > DOCUMENT_LIMIT:
            raise ValueError('草稿历史过大，请另存项目')
        if shutil.disk_usage(self.root).free < len(encoded.encode('utf8')) * 3 + 32 * 1024 ** 2:
            raise ValueError('自动保存空间不足，已保留上次确认的版本')
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM drafts WHERE id=?', (key,)).fetchone()
            if old is None and db.execute('SELECT COUNT(*) FROM drafts').fetchone()[0] >= 64:
                raise ValueError('本机已保存 64 个草稿，请先另存项目并清理旧草稿')
            if old and old['request_id'] == request_id:
                if old['document'] != encoded:
                    raise Conflict('同一次自动保存的内容已改变')
                return old['revision']
            if (old['revision'] if old else 0) != expected:
                raise Conflict('草稿已被另一个页面更新；请从恢复面板打开最新版本，当前编辑未被覆盖')
            for photo in photos:
                self.asset_path(photo['asset'])
            now = time.time(); revision = expected + 1
            latest = db.execute('SELECT MAX(saved) FROM versions WHERE id=?', (key,)).fetchone()[0]
            # Keep the old current edit before a rollback and minute-spaced versions.
            if old and (snapshot or latest is None or now-latest >= 60):
                db.execute('INSERT OR IGNORE INTO versions VALUES (?,?,?,?)',
                           (key, old['revision'], old['document'], old['saved']))
            db.execute('INSERT OR REPLACE INTO drafts VALUES (?,?,?,?,?)', (key, revision, encoded, now, request_id))
            if snapshot:
                db.execute('INSERT INTO versions VALUES (?,?,?,?)', (key, revision, encoded, now))
            db.execute('DELETE FROM versions WHERE id=? AND revision NOT IN '
                       '(SELECT revision FROM versions WHERE id=? ORDER BY revision DESC LIMIT ?)', (key, key, self.versions))
        return revision

    def load(self, key, revision=None):
        self.key(key)
        with self.connect() as db:
            row = db.execute('SELECT * FROM drafts WHERE id=?', (key,)).fetchone()
            if not row:
                raise ValueError('草稿不存在')
            current = row['revision']
            if revision is not None and revision != current:
                row = db.execute('SELECT * FROM versions WHERE id=? AND revision=?', (key, revision)).fetchone()
                if not row:
                    raise ValueError('该版本已轮替，请选择现有版本')
            return dict(id=key, revision=current, restored_revision=row['revision'],
                        document=json.loads(row['document']), saved=row['saved'])

    def list(self):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM drafts ORDER BY saved DESC').fetchall()
            used = db.execute('SELECT COALESCE(SUM(size),0) FROM assets').fetchone()[0]
        return dict(drafts=[dict(id=r['id'], revision=r['revision'], saved=r['saved'],
                                count=len(json.loads(r['document'])['photos'])) for r in rows],
                    storage_bytes=used, limit_bytes=self.max_bytes)

    def version_list(self, key):
        self.key(key)
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT revision,saved FROM drafts WHERE id=? UNION '
                'SELECT revision,saved FROM versions WHERE id=? ORDER BY revision DESC', (key, key))]

    def delete(self, key, protected=()):
        self.key(key)
        with self.lock, self.connect() as db:
            db.execute('DELETE FROM drafts WHERE id=?', (key,))
            db.execute('DELETE FROM versions WHERE id=?', (key,))
            retained = set(protected)
            for row in db.execute('SELECT document FROM drafts UNION ALL SELECT document FROM versions'):
                retained.update(p['asset'] for p in json.loads(row[0])['photos'])
            for row in db.execute('SELECT id FROM assets').fetchall():
                if row['id'] not in retained:
                    self.asset_path(row['id']).unlink()
                    db.execute('DELETE FROM assets WHERE id=?', (row['id'],))

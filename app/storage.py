"""Resume an item's files and publish replacements without losing the previous copy."""
import mimetypes
import os
import tempfile
import uuid
from pathlib import Path
from . import config, db


class ItemStore:
    def __init__(self, job, item_id, check):
        self.job, self.item_id, self.check = job, item_id, check
        self.root = config.DATA / 'jobs' / job['id'] / item_id
        self.root.mkdir(parents=True, exist_ok=True)
        self.rows = {r['name']: r for r in db.query('SELECT * FROM files WHERE item_id=? AND user_id=?',
                                                  (item_id, job['user_id']))}

    def row(self, name):
        if name in self.rows:
            return self.rows[name]
        stem = Path(name).stem
        if stem.startswith('image_') or stem in ('cover', 'music'):
            return next((r for n, r in self.rows.items() if Path(n).stem == stem), None)

    def path(self, row):
        path = (config.DATA / row['path']).resolve()
        return path if path.parent == self.root.resolve() else None

    def reusable(self, name):
        row = self.row(name)
        path = self.path(row) if row else None
        try:
            return path if path and path.is_file() and path.stat().st_size == row['size'] and row['size'] > 0 else None
        except OSError:
            return None

    def budget(self, name):
        row = self.row(name)
        credit = row['size'] if row else 0
        return max(0, config.USER_QUOTA_MB * 1024 * 1024 - db.usage(self.job['user_id']) + credit)

    def names(self):
        return [name for name in self.rows if self.reusable(name)]

    def publish(self, staged, name, mime=None):
        if self.check():
            import asyncio
            raise asyncio.CancelledError()
        size = staged.stat().st_size
        if size > self.budget(name):
            raise ValueError('个人存储配额已用完，请清理旧任务')
        old = self.row(name)
        target = self.root / name
        row = dict(id=old['id'] if old else uuid.uuid4().hex, job_id=self.job['id'],
                   item_id=self.item_id, user_id=self.job['user_id'],
                   path=str(target.relative_to(config.DATA)), name=name, size=size,
                   mime=mime or mimetypes.guess_type(name)[0] or 'application/octet-stream')
        # A hard link keeps the old bytes available if the DB transaction fails after rename.
        with tempfile.TemporaryDirectory(prefix='.publish-', dir=self.root) as tmp:
            backup = Path(tmp) / 'previous'
            existed = target.exists()
            if existed:
                os.link(target, backup)
            replaced = False
            try:
                with db.connection() as c:
                    if old:
                        c.execute('UPDATE files SET path=?,name=?,size=?,mime=? WHERE id=?',
                                  (row['path'], name, size, row['mime'], row['id']))
                    else:
                        c.execute('INSERT INTO files(id,job_id,item_id,user_id,path,name,size,mime) VALUES(?,?,?,?,?,?,?,?)',
                                  tuple(row[k] for k in ('id','job_id','item_id','user_id','path','name','size','mime')))
                    staged.replace(target)
                    replaced = True
            except BaseException:
                if replaced:
                    if existed:
                        backup.replace(target)
                    else:
                        target.unlink(missing_ok=True)
                raise
        if old:
            self.rows.pop(old['name'], None)
            old_path = self.path(old)
            if old_path and old_path != target:
                old_path.unlink(missing_ok=True)
        self.rows[name] = row
        return target

    def write_text(self, name, text, mime):
        with tempfile.TemporaryDirectory(prefix='.write-', dir=self.root) as tmp:
            staged = Path(tmp) / name
            staged.write_text(text, encoding='utf-8')
            return self.publish(staged, name, mime)

    def remove(self, name):
        row = self.row(name)
        if row:
            path = self.path(row)
            db.execute('DELETE FROM files WHERE id=?', (row['id'],))
            if path:
                path.unlink(missing_ok=True)
            self.rows.pop(row['name'], None)

import json
import sqlite3
import time
from contextlib import contextmanager
from .config import DB_PATH


@contextmanager
def connection():
    conn = sqlite3.connect(str(DB_PATH), timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def init():
    with connection() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users (
          id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL,
          role TEXT NOT NULL DEFAULT 'member', created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (
          token TEXT PRIMARY KEY, user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
          expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS connections (
          user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
          encrypted TEXT NOT NULL, updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
          url TEXT NOT NULL, options TEXT NOT NULL, status TEXT NOT NULL,
          message TEXT NOT NULL DEFAULT '', discovered INTEGER NOT NULL DEFAULT 0,
          finished INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0,
          warnings TEXT NOT NULL DEFAULT '[]', created REAL NOT NULL, updated REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status,created);
        CREATE INDEX IF NOT EXISTS jobs_owner ON jobs(user_id,created);
        CREATE TABLE IF NOT EXISTS items (
          id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
          remote_id TEXT NOT NULL, title TEXT NOT NULL, author TEXT NOT NULL DEFAULT '',
          kind TEXT NOT NULL, published INTEGER NOT NULL DEFAULT 0,
          status TEXT NOT NULL DEFAULT 'pending', error TEXT NOT NULL DEFAULT '',
          metadata TEXT NOT NULL DEFAULT '{}', UNIQUE(job_id,remote_id));
        CREATE TABLE IF NOT EXISTS files (
          id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
          item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
          user_id TEXT NOT NULL REFERENCES users(id), path TEXT NOT NULL,
          name TEXT NOT NULL, size INTEGER NOT NULL, mime TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS files_owner ON files(user_id);
        CREATE INDEX IF NOT EXISTS items_remote ON items(remote_id,status);
        ''')
        if 'filtered' not in {r['name'] for r in c.execute('PRAGMA table_info(jobs)')}:
            c.execute('ALTER TABLE jobs ADD COLUMN filtered INTEGER NOT NULL DEFAULT 0')
        c.execute('DELETE FROM sessions WHERE expires < ?', (time.time(),))


def query(sql, params=(), one=False):
    with connection() as c:
        rows = c.execute(sql, params)
        if one:
            row = rows.fetchone()
            return dict(row) if row else None
        return [dict(r) for r in rows.fetchall()]


def execute(sql, params=()):
    with connection() as c:
        return c.execute(sql, params).rowcount


def update_job(job_id, **values):
    permitted = {'status', 'message', 'discovered', 'finished', 'failed', 'warnings', 'filtered'}
    if not set(values) <= permitted:
        raise ValueError('Unsupported job field')
    if 'warnings' in values:
        values['warnings'] = json.dumps(values['warnings'], ensure_ascii=False)
    values['updated'] = time.time()
    execute('UPDATE jobs SET ' + ','.join(k+'=?' for k in values) + ' WHERE id=?',
            tuple(values.values()) + (job_id,))


def usage(user_id):
    return query('SELECT COALESCE(SUM(size),0) AS bytes FROM files WHERE user_id=?',
                 (user_id,), one=True)['bytes']


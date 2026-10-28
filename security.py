"""Persistent revocable sessions and bounded database-backed login throttling."""
import hashlib
import secrets
import time
from urllib.parse import urlsplit

IDLE_SECONDS = 30 * 60
ABSOLUTE_SECONDS = 12 * 60 * 60


def migrate(c):
    c.execute('CREATE TABLE IF NOT EXISTS auth_sessions(token_hash TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),created_at INTEGER NOT NULL,last_seen INTEGER NOT NULL,expires_at INTEGER NOT NULL,user_agent TEXT NOT NULL)')
    c.execute('CREATE INDEX IF NOT EXISTS sessions_user ON auth_sessions(user_id)')
    c.execute('CREATE TABLE IF NOT EXISTS user_mfa(user_id INTEGER PRIMARY KEY REFERENCES users(id),secret TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 0,last_step INTEGER NOT NULL DEFAULT 0)')
    c.execute('CREATE TABLE IF NOT EXISTS login_limits(bucket TEXT PRIMARY KEY,window_start INTEGER NOT NULL,attempts INTEGER NOT NULL)')


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def attempt(c, username, ip, core):
    stamp = int(time.time())
    # Account and peer limits are independent; varying usernames cannot evade peer limits.
    for key, maximum in [('account:' + username.casefold(), 10), ('peer:' + ip, 50)]:
        bucket = digest(key)
        c.execute('INSERT INTO login_limits VALUES(?,?,1) ON CONFLICT(bucket) DO UPDATE SET attempts=CASE WHEN window_start<? THEN 1 ELSE attempts+1 END,window_start=CASE WHEN window_start<? THEN excluded.window_start ELSE window_start END', (bucket, stamp, stamp-900, stamp-900))
        count = c.execute('SELECT attempts FROM login_limits WHERE bucket=?', (bucket,)).fetchone()[0]
        c.commit()
        if count > maximum:
            core.fail('محاولات دخول كثيرة؛ حاول بعد 15 دقيقة', 429)
    c.execute('DELETE FROM login_limits WHERE window_start<?', (stamp-86400,))


def create(c, uid, agent):
    token = secrets.token_urlsafe(48)
    stamp = int(time.time())
    c.execute('DELETE FROM auth_sessions WHERE expires_at<? OR last_seen<?', (stamp, stamp-IDLE_SECONDS))
    c.execute('INSERT INTO auth_sessions VALUES(?,?,?,?,?,?)', (digest(token),uid,stamp,stamp,stamp+ABSOLUTE_SECONDS,agent[:200]))
    # Bound retained sessions per user.
    c.execute('DELETE FROM auth_sessions WHERE user_id=? AND token_hash NOT IN (SELECT token_hash FROM auth_sessions WHERE user_id=? ORDER BY created_at DESC,rowid DESC LIMIT 10)', (uid,uid))
    return token


def actor(c, token):
    stamp = int(time.time())
    row = c.execute('SELECT * FROM auth_sessions WHERE token_hash=?', (digest(token),)).fetchone()
    if not row:
        return None
    if row['expires_at'] <= stamp or row['last_seen'] <= stamp-IDLE_SECONDS:
        c.execute('DELETE FROM auth_sessions WHERE token_hash=?', (digest(token),)); c.commit()
        return None
    c.execute('UPDATE auth_sessions SET last_seen=? WHERE token_hash=?', (stamp,digest(token)))
    c.commit()
    return row['user_id']


def same_origin(handler, core):
    origin = handler.headers.get('Origin')
    if handler.headers.get('Sec-Fetch-Site') == 'cross-site':
        core.fail('طلب من مصدر غير مسموح', 403)
    if origin:
        parsed = urlsplit(origin)
        if parsed.scheme not in ('http','https') or parsed.netloc.lower() != handler.headers.get('Host','').lower():
            core.fail('طلب من مصدر غير مسموح',403)


def totp(secret,step):
    import base64,hmac,struct
    key=base64.b32decode(secret,casefold=True)
    value=hmac.new(key,struct.pack('>Q',step),'sha1').digest()
    offset=value[-1]&15
    return str((int.from_bytes(value[offset:offset+4],'big')&0x7fffffff)%1000000).zfill(6)


def verify_mfa(c,uid,code,core):
    import hmac
    row=c.execute('SELECT * FROM user_mfa WHERE user_id=? AND enabled=1',(uid,)).fetchone()
    if not row:return
    stamp=int(time.time())//30
    step=next((n for n in (stamp-1,stamp,stamp+1) if n>row['last_step'] and hmac.compare_digest(totp(row['secret'],n),str(code))),None)
    if step is None:core.fail('رمز تطبيق المصادقة مطلوب أو غير صالح',401)
    changed=c.execute('UPDATE user_mfa SET last_step=? WHERE user_id=? AND last_step<?',(step,uid,step))
    if changed.rowcount!=1:core.fail('رمز المصادقة مستعمل',401)


def peer(handler):
    """Trust forwarding headers only from explicitly configured proxy networks."""
    import ipaddress
    import os
    address=handler.client_address[0]
    try:
        networks=[ipaddress.ip_network(value.strip()) for value in os.environ.get('EHKAM_TRUSTED_PROXY_NETS','').split(',') if value.strip()]
        trusted=lambda value:any(ipaddress.ip_address(value) in network for network in networks)
        if not trusted(address):return address
        chain=handler.headers.get('X-Forwarded-For','').split(',')
        if len(chain)>20:return address
        for item in reversed(chain):
            item=item.strip()
            if not trusted(item):return str(ipaddress.ip_address(item))
    except ValueError:return address
    return address

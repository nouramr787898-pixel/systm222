"""FCM delivery: opt-in devices, transactional queue, generic lock-screen content."""
import datetime
import json
import os
import re
import time
import urllib.request
import urllib.error
import security


def migrate(c):
    c.execute("""CREATE TABLE IF NOT EXISTS push_devices(
      id INTEGER PRIMARY KEY, token TEXT NOT NULL UNIQUE, user_id INTEGER NOT NULL REFERENCES users(id),
      session_hash TEXT NOT NULL, platform TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1)""")
    c.execute("""CREATE TABLE IF NOT EXISTS push_outbox(
      id INTEGER PRIMARY KEY, notification_id INTEGER NOT NULL REFERENCES notifications(id),
      device_id INTEGER NOT NULL REFERENCES push_devices(id), user_id INTEGER NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL, sent_at TEXT,
      UNIQUE(notification_id,device_id))""")
    c.execute("""CREATE TRIGGER IF NOT EXISTS notification_push AFTER INSERT ON notifications BEGIN
      INSERT INTO push_outbox(notification_id,device_id,user_id,created_at)
      SELECT NEW.id,id,NEW.user_id,NEW.created_at FROM push_devices WHERE user_id=NEW.user_id AND enabled=1;
    END""")


def handle(h,c,u,path,method,core):
    if path not in ('/api/mobile/devices','/api/mobile/devices/remove') or method!='POST':return False
    d=h.input();token=str(d.get('token',''))
    if not 20<=len(token)<=4096 or not re.fullmatch(r'[A-Za-z0-9_:.\-]+',token):core.fail('رمز جهاز غير صالح')
    session=security.digest(security.request_token(h,core))
    if path.endswith('/remove'):
        c.execute("UPDATE push_outbox SET status='cancelled' WHERE status='pending' AND device_id IN (SELECT id FROM push_devices WHERE token=? AND user_id=? AND session_hash=?)",(token,u['id'],session))
        c.execute('UPDATE push_devices SET enabled=0 WHERE token=? AND user_id=? AND session_hash=?',(token,u['id'],session))
    else:
        platform=d.get('platform')
        if platform not in ('android','ios'):core.fail('نوع جهاز غير صالح')
        # Delete old queued rows when ownership/session changes: never replay another login's notifications.
        old=c.execute('SELECT id,user_id,session_hash FROM push_devices WHERE token=?',(token,)).fetchone()
        if old and (old[1]!=u['id'] or old[2]!=session):
            c.execute("UPDATE push_outbox SET status='cancelled' WHERE device_id=? AND status='pending'",(old[0],))
        c.execute('INSERT INTO push_devices(token,user_id,session_hash,platform) VALUES(?,?,?,?) ON CONFLICT(token) DO UPDATE SET user_id=excluded.user_id,session_hash=excluded.session_hash,platform=excluded.platform,enabled=1',(token,u['id'],session,platform))
    c.commit();h.write({'ok':True});return True


def configured():
    return bool(re.fullmatch(r'[a-z][a-z0-9-]{4,62}',os.environ.get('FIREBASE_PROJECT_ID','')))


def credentials():
    # Explicit project selection is mandatory; unrelated cloud credentials are never used by default.
    import google.auth
    from google.auth.transport.requests import Request
    credentials,_=google.auth.default(scopes=['https://www.googleapis.com/auth/firebase.messaging'])
    credentials.refresh(Request())
    return credentials


def send(token,notification_id,request_id,auth=None):
    auth=auth or credentials()
    data={'notification_id':str(notification_id)}
    if request_id is not None:data['request_id']=str(request_id)
    payload={'message':{'token':token,'notification':{'title':'إحكام المسار','body':'لديك تحديث جديد في حسابك. افتح التطبيق لمعرفة التفاصيل.'},'data':data,'android':{'ttl':'3600s'},'apns':{'headers':{'apns-expiration':str(int(time.time())+3600)},'payload':{'aps':{'sound':'default'}}}}}
    request=urllib.request.Request('https://fcm.googleapis.com/v1/projects/'+os.environ['FIREBASE_PROJECT_ID']+'/messages:send',data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+auth.token,'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(request,timeout=20) as response:
            if not json.load(response).get('name'):raise RuntimeError('push_not_confirmed')
    except urllib.error.HTTPError as error:
        result=json.loads(error.read(65536))
        if any(d.get('errorCode')=='UNREGISTERED' for d in result.get('error',{}).get('details',[])):raise InvalidDevice() from None
        raise RuntimeError('push_provider_rejected') from None


class InvalidDevice(Exception):pass


def deliver(c,sender=None):
    if sender is None and not configured():return {'configured':False,'sent':0}
    if sender is None:
        try:auth=credentials()
        except Exception:return {'configured':False,'sent':0}
        sender=lambda token,nid,rid:send(token,nid,rid,auth)
    sent=0
    rows=c.execute("SELECT p.id,p.user_id,d.id,d.token,d.user_id,d.enabled,d.session_hash,n.id,n.request_id,n.read_at,p.created_at FROM push_outbox p JOIN push_devices d ON d.id=p.device_id JOIN notifications n ON n.id=p.notification_id WHERE p.status='pending' ORDER BY p.id LIMIT 100").fetchall()
    for r in rows:
        user=c.execute('SELECT * FROM users WHERE id=?',(r[1],)).fetchone()
        active=user and not user['disabled']
        if active and user['role']!='admin':active=c.execute('SELECT 1 FROM employees e JOIN companies co ON co.id=e.company_id WHERE e.id=? AND e.company_id=? AND e.archived=0 AND co.archived=0',(user['employee_id'],user['company_id'])).fetchone()
        session=c.execute('SELECT 1 FROM auth_sessions WHERE token_hash=? AND user_id=? AND expires_at>?',(r[6],r[1],int(time.time()))).fetchone()
        # Discard old notifications after outages instead of flooding the handset.
        fresh=c.execute("SELECT julianday('now')-julianday(?)<1",(r[10],)).fetchone()[0]
        if not active or not session or not r[5] or r[1]!=r[4] or r[9] or not fresh:
            c.execute("UPDATE push_outbox SET status='cancelled' WHERE id=? AND status='pending'",(r[0],));c.commit();continue
        claim=c.execute("UPDATE push_outbox SET status='sending' WHERE id=? AND status='pending'",(r[0],));c.commit()
        if not claim.rowcount:continue
        try:sender(r[3],r[7],r[8])
        except InvalidDevice:
            c.execute('UPDATE push_devices SET enabled=0 WHERE id=?',(r[2],));c.execute("UPDATE push_outbox SET status='invalid_device' WHERE id=?",(r[0],))
        except Exception:
            # Unknown delivery outcome must not cause duplicate automatic sends.
            c.execute("UPDATE push_outbox SET status='needs_review' WHERE id=?",(r[0],))
        else:
            c.execute("UPDATE push_outbox SET status='sent',sent_at=? WHERE id=?",(datetime.datetime.now(datetime.timezone.utc).isoformat(),r[0]));sent+=1
        c.commit()
    return {'configured':True,'sent':sent}

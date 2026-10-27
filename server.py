"""EHKAM HR Portal - local functional prototype. Run: python server.py"""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import sqlite3, json, os, hashlib, hmac, secrets, datetime, csv, io, threading, re, time, ssl, smtplib, urllib.request, urllib.parse, urllib.error, base64, html
from email.message import EmailMessage
from http.cookies import SimpleCookie
from pathlib import Path

BASE=Path(__file__).resolve().parent
DB=BASE/'ehkam_data.sqlite3'
SESSIONS={}
LOCK=threading.Lock()
RESET_ATTEMPTS={}
TODAY=datetime.date.today

def now(): return datetime.datetime.now().isoformat(timespec='seconds')
def hashpw(p,s=None):
 s=s or secrets.token_hex(16)
 return s+':'+hashlib.pbkdf2_hmac('sha256',p.encode(),bytes.fromhex(s),200000).hex()
def verifypw(password,stored):
 try:
  s,_=stored.split(':');return hmac.compare_digest(hashpw(password,s),stored)
 except Exception:return False

def conn():
 c=sqlite3.connect(DB,timeout=15)
 c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');return c

def init():
 c=conn();c.executescript('''
 CREATE TABLE IF NOT EXISTS companies(id INTEGER PRIMARY KEY,name TEXT NOT NULL, name_en TEXT DEFAULT '',contract_end TEXT,city TEXT DEFAULT '', archived INTEGER NOT NULL DEFAULT 0);
 CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('admin','manager','employee')),employee_id INTEGER,company_id INTEGER);
 CREATE TABLE IF NOT EXISTS employees(id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),name TEXT NOT NULL,name_en TEXT DEFAULT '',employee_no TEXT NOT NULL, identity_no TEXT DEFAULT '',job TEXT DEFAULT '',department TEXT DEFAULT '',manager_id INTEGER, status TEXT NOT NULL DEFAULT 'نشط',hire_date TEXT, contract_start TEXT,contract_end TEXT,basic REAL NOT NULL DEFAULT 0,housing REAL NOT NULL DEFAULT 0,transport REAL NOT NULL DEFAULT 0,other REAL NOT NULL DEFAULT 0,annual_entitlement REAL NOT NULL DEFAULT 21,carryover REAL NOT NULL DEFAULT 0,monthly_grant REAL NOT NULL DEFAULT 0,workweek TEXT NOT NULL DEFAULT '0,1,2,3,5,6', work_start TEXT NOT NULL DEFAULT '09:00',work_end TEXT NOT NULL DEFAULT '17:00',payment_method TEXT NOT NULL DEFAULT 'bank',archived INTEGER NOT NULL DEFAULT 0,UNIQUE(company_id,employee_no));
 CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL,employee_id INTEGER NOT NULL,type TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'new',start_date TEXT,end_date TEXT,days REAL DEFAULT 0,amount REAL DEFAULT 0,installment REAL DEFAULT 0,reason TEXT DEFAULT '',notes TEXT DEFAULT '',created_by INTEGER NOT NULL,approver_id INTEGER,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,decision_note TEXT DEFAULT '');
 CREATE TABLE IF NOT EXISTS attendance(id INTEGER PRIMARY KEY,employee_id INTEGER NOT NULL,day TEXT NOT NULL,check_in TEXT,check_out TEXT,source TEXT DEFAULT 'demo',UNIQUE(employee_id,day));
 CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,user_id INTEGER, username TEXT, action TEXT NOT NULL, entity TEXT NOT NULL,entity_id INTEGER, info TEXT, timestamp TEXT NOT NULL);
 ''')
 if not c.execute('SELECT 1 FROM users LIMIT 1').fetchone():
  c.execute('INSERT INTO companies(name,name_en,contract_end,city) VALUES(?,?,?,?)',('شركة الأفق','Al Ofoq Co','2027-12-31','الرياض'))
  co=c.execute('SELECT last_insert_rowid()').fetchone()[0]
  staff=[('أحمد محمد','Ahmed Mohammed','A-001','مدير موارد بشرية','الموارد البشرية',None,5200,1000,400,0,'2026-01-01','2027-01-01',0),('سارة خالد','Sara Khalid','A-002','أخصائي موارد بشرية','الموارد البشرية',1,4000,0,0,25,'2026-01-01','2027-01-01',0),('خالد علي','Khalid Ali','A-003','محاسب','المالية',1,3500,800,200,8,'2026-05-01','2027-05-01',0)]
  for name,en,no,job,dep,manager,basic,housing,trans,carry,start,end,arch in staff:
   c.execute('''INSERT INTO employees(company_id,name,name_en,employee_no,job,department,manager_id,basic,housing,transport,carryover,hire_date,contract_start,contract_end,archived) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(co,name,en,no,job,dep,manager,basic,housing,trans,carry,start,start,end,arch))
  for username,pw,role,eid in [('admin','Admin@123','admin',None),('manager','Manager@123','manager',1),('employee','Employee@123','employee',2)]:
   c.execute('INSERT INTO users(username,password_hash,role,employee_id,company_id) VALUES(?,?,?,?,?)',(username,hashpw(pw),role,eid,co if role!='admin' else None))

 # Add employee profile photo column to older installations safely.
 columns={row[1] for row in c.execute('PRAGMA table_info(employees)')}
 if 'photo' not in columns:c.execute('ALTER TABLE employees ADD COLUMN photo TEXT DEFAULT NULL')
 for col in ('phone_country','phone_local','email'):
  if col not in columns:c.execute('ALTER TABLE employees ADD COLUMN '+col+' TEXT DEFAULT NULL')
 c.execute('''CREATE TABLE IF NOT EXISTS password_resets(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL, code_hash TEXT NOT NULL,expires_at INTEGER NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,used INTEGER NOT NULL DEFAULT 0)''')
 reset_columns={row[1] for row in c.execute('PRAGMA table_info(password_resets)')}
 for col in ('channel','destination','verified_token_hash','whatsapp_phone'):
  if col not in reset_columns:c.execute('ALTER TABLE password_resets ADD COLUMN '+col+' TEXT DEFAULT NULL')
 for col in ('email_verified','whatsapp_attempts','whatsapp_sent'):
  if col not in {row[1] for row in c.execute('PRAGMA table_info(password_resets)')}:c.execute('ALTER TABLE password_resets ADD COLUMN '+col+' INTEGER NOT NULL DEFAULT 0')
 c.commit();c.close()

class AuthevoServiceError(Exception):
 def __init__(self,code,status=None):
  self.code=code;self.status=status

# Only log machine-readable error identifiers, never response messages or PII.
def resend_send_reset(destination,code):
 """Send only from server; never expose email API key or OTP in logs."""
 key=os.environ.get('RESEND_API_KEY','').strip()
 sender=os.environ.get('RESEND_FROM_EMAIL','').strip()
 if not key.startswith('re_') or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',sender):
  raise RuntimeError('RESEND_NOT_CONFIGURED')
 subject='رمز استعادة كلمة المرور — إحكام الأعمال'
 content=('رمز استعادة كلمة المرور الخاص بحسابك هو: '+code+'\n'
          'هذا الرمز صالح لمدة 10 دقائق. لا تشاركه مع أي شخص.\n'
          'إذا لم تطلب استعادة كلمة المرور، تجاهل هذه الرسالة.')
 payload={'from':'إحكام الأعمال <'+sender+'>','to':[destination], 'subject':subject, 'text':content}
 req=urllib.request.Request('https://api.resend.com/emails',data=json.dumps(payload,ensure_ascii=False).encode('utf-8'),
     headers={'Authorization':'Bearer '+key,'Content-Type':'application/json','Accept':'application/json',
              'User-Agent':'EHKAM-HR-Portal/1.0'},method='POST')
 try:
  with urllib.request.urlopen(req,timeout=20) as response:
   result=json.load(response)
   return bool(isinstance(result,dict) and result.get('id'))
 except urllib.error.HTTPError as error:
  # Never print raw response text: it may contain addresses or identifiers.
  content_type=error.headers.get('Content-Type','').split(';',1)[0].strip().lower()
  server=error.headers.get('Server','unknown').lower()
  server_type=('cloudflare' if 'cloudflare' in server else
               'proxy' if any(x in server for x in ('nginx','envoy','traefik')) else
               'other')
  provider_code='unavailable'
  response_shape='non_json'
  try:
   raw=error.read(4096)
   if content_type=='application/json' or raw.lstrip().startswith(b'{'):
    body=json.loads(raw.decode('utf-8','replace'))
    if isinstance(body,dict):
     response_shape='json_object'
     obj=body.get('error')
     if isinstance(obj,dict):
      val=obj.get('name') or obj.get('code') or obj.get('type')
     else:
      val=body.get('name') or body.get('code') or body.get('type')
     if isinstance(val,str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_\-]{1,63}',val):
      provider_code=val
    else:response_shape='json_other'
  except (ValueError,UnicodeError):
   response_shape='invalid_json'
  print('RESEND DIAGNOSTIC http_status='+str(error.code)+
        ' content_type='+str(content_type or 'unknown')+
        ' server_type='+server_type+' shape='+response_shape+
        ' error_code='+provider_code,flush=True)
  return False
 except (urllib.error.URLError,TimeoutError,ValueError):
  print('RESEND SEND ERROR connection_or_response',flush=True)
  return False

def authevo_request(endpoint,payload):
 """Authevo WhatsApp OTP verified server-side; never log keys, codes or phone numbers."""
 key=os.environ.get('AUTHEVO_API_KEY','').strip()
 if not key.startswith('sk_'):raise RuntimeError('AUTHEVO_NOT_CONFIGURED')
 req=urllib.request.Request('https://api.authevo.dev'+endpoint,
  data=json.dumps(payload).encode('utf-8'),method='POST',
  headers={'Authorization':'Bearer '+key,'Content-Type':'application/json',
           'Accept':'application/json','User-Agent':'EHKAM-HR-Portal/1.0'})
 try:
  with urllib.request.urlopen(req,timeout=20) as resp:return json.load(resp)
 except urllib.error.HTTPError as error:
  print('AUTHEVO REQUEST FAILED status='+str(error.code),flush=True)
  raise RuntimeError('AUTHEVO_SEND_OR_VERIFY_FAILED')
 except (urllib.error.URLError,TimeoutError,ValueError):
  print('AUTHEVO REQUEST FAILED network_or_response',flush=True)
  raise RuntimeError('AUTHEVO_SEND_OR_VERIFY_FAILED')

def authevo_send(phone):
 result=authevo_request('/v1/otp/send',{'phone':phone,'language':'ar'})
 data=result.get('data') or {}
 return data.get('status')=='sent' and bool(data.get('message_id'))

def authevo_verify(phone,code):
 result=authevo_request('/v1/otp/verify',{'phone':phone,'code':code})
 return (result.get('data') or {}).get('verified') is True

def clean_contacts(d):
 country=str(d.get('phone_country') or '+966');local=re.sub(r'[^0-9]','',str(d.get('phone_local') or ''))
 if country not in ('+966','+20'):fail('رمز الدولة يجب أن يكون +966 أو +20')
 if local.startswith('0'):local=local[1:]
 if local and not (8<=len(local)<=10):fail('يرجى إدخال رقم جوال صحيح')
 email=str(d.get('email') or '').strip().lower()
 if email and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):fail('البريد الإلكتروني غير صحيح')
 return country,local,email

def record(c,u,action,entity,eid=None,info=''):
 c.execute('INSERT INTO audit(user_id,username,action,entity,entity_id,info,timestamp) VALUES(?,?,?,?,?,?,?)',(u['id'],u['username'],action,entity,eid,info,now()))
def obj(row):return dict(row) if row is not None else None
def fail(msg,code=400):raise AppError(msg,code)
class AppError(Exception):
 def __init__(self,msg,status=400):self.msg=msg;self.status=status

def user_allowed_emp(c,u,eid):
 e=c.execute('SELECT * FROM employees WHERE id=?',(eid,)).fetchone()
 if not e:fail('الموظف غير موجود',404)
 if u['role']=='admin':return e
 if e['company_id']!=u['company_id']:fail('غير مصرح لك بالوصول',403)
 if u['role']=='employee' and u['employee_id']!=eid:fail('غير مصرح لك بالوصول',403)
 if u['role']=='manager' and e['id']!=u['employee_id'] and e['manager_id']!=u['employee_id']:fail('غير مصرح لك بالوصول',403)
 return e

def salary(e):return round(e['basic']+e['housing']+e['transport']+e['other'],2)
def monthly(e):return float(e['annual_entitlement'])/12

def months_elapsed(start,today):
 """Completed calendar-month anniversaries since contract start, capped at 12 months per contract year."""
 if not start:return 0
 try:d=datetime.date.fromisoformat(start)
 except ValueError:return 0
 if today<=d:return 0
 months=(today.year-d.year)*12+(today.month-d.month)
 if today.day<d.day:months-=1
 return max(0,months)

def remaining_months(date_end,today):
 if not date_end:return 0
 try:d=datetime.date.fromisoformat(date_end)
 except ValueError:return 0
 if d<=today:return 0
 months=(d.year-today.year)*12+(d.month-today.month)
 if d.day<today.day:months-=1
 return max(0,months)

def business_days(e,start,end):
 try:a=datetime.date.fromisoformat(start);b=datetime.date.fromisoformat(end)
 except Exception:fail('تاريخ غير صالح')
 if b<a:fail('تاريخ النهاية قبل البداية')
 if (b-a).days>365:fail('الفترة أكبر من سنة')
 valid={int(k) for k in (e['workweek'] or '').split(',') if k.strip().isdigit()}
 return sum((a+datetime.timedelta(days=i)).weekday() in valid for i in range((b-a).days+1))

def leave(c,e):
 elapsed=min(months_elapsed(e['contract_start'],TODAY()),12)
 accrued=round(monthly(e)*elapsed,4)
 used=c.execute("SELECT COALESCE(SUM(days),0) AS n FROM requests WHERE employee_id=? AND type='annual_leave' AND status='approved'",(e['id'],)).fetchone()['n']
 carried=float(e['carryover']);total=round(carried+accrued-used,4)
 return {'carryover':carried,'annual_entitlement':e['annual_entitlement'],'monthly':round(monthly(e),4),'accrued':accrued,'approved_used':used,'available':total,'elapsed_months':elapsed}

def visible_employees(c,u,include_archived=False):
 q='SELECT e.*, (e.basic+e.housing+e.transport+e.other) gross_salary FROM employees e JOIN companies c ON c.id=e.company_id WHERE c.archived=0'
 p=[]
 if not include_archived:q+=' AND e.archived=0'
 if u['role']=='employee':q+=' AND e.id=?';p=[u['employee_id']]
 elif u['role']=='manager':q+=' AND e.company_id=? AND (e.manager_id=? OR e.id=?)';p=[u['company_id'],u['employee_id'],u['employee_id']]
 q+=' ORDER BY e.name'
 rows=[dict(x) for x in c.execute(q,p)]
 if u['role']=='employee':
  for x in rows:
   for sensitive in ('basic','housing','transport','other','gross_salary'):pass # their own salary permitted
 return rows

def visible_requests(c,u):
 q='''SELECT r.*,e.name employee_name,e.employee_no,c.name company_name FROM requests r JOIN employees e ON e.id=r.employee_id JOIN companies c ON c.id=r.company_id WHERE 1=1''';p=[]
 if u['role']=='employee':q+=' AND r.employee_id=?';p=[u['employee_id']]
 elif u['role']=='manager':q+=' AND r.company_id=? AND (e.manager_id=? OR r.employee_id=?)';p=[u['company_id'],u['employee_id'],u['employee_id']]
 q+=' ORDER BY r.id DESC'
 return [dict(x) for x in c.execute(q,p)]

class Handler(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def write(self,data,status=200,ctype='application/json',headers=None):
  if isinstance(data,(dict,list)):data=json.dumps(data,ensure_ascii=False).encode()
  elif isinstance(data,str):data=data.encode()
  self.send_response(status);self.send_header('Content-Type',ctype+'; charset=utf-8');self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store')
  for k,v in (headers or {}).items():self.send_header(k,v)
  self.end_headers();self.wfile.write(data)
 def input(self):
  n=int(self.headers.get('Content-Length','0'))
  if n>1024*1024:fail('الطلب كبير جدًا',413)
  try:return json.loads(self.rfile.read(n) or b'{}')
  except Exception:fail('بيانات JSON غير صالحة')
 def actor(self,c):
  cookie=SimpleCookie();cookie.load(self.headers.get('Cookie',''))
  token=cookie.get('ehkam_session');value=token.value if token else ''
  with LOCK:uid=SESSIONS.get(value)
  if not uid:fail('يرجى تسجيل الدخول',401)
  u=c.execute('SELECT id,username,role,employee_id,company_id FROM users WHERE id=?',(uid,)).fetchone()
  if not u:fail('جلسة غير صالحة',401)
  return u
 def do_GET(self):self.process('GET')
 def do_POST(self):self.process('POST')
 def process(self,method):
  path=urlparse(self.path).path
  if method=='GET' and path in ('/','/index.html'):
   return self.write((BASE/'index.html').read_bytes(),ctype='text/html')
  if method=='GET' and path=='/style.css':return self.write((BASE/'style.css').read_bytes(),ctype='text/css')
  if method=='GET' and path=='/app.js':return self.write((BASE/'app.js').read_bytes(),ctype='text/javascript')
  if method=='GET' and path in ('/favicon.ico','/favicon.png','/ehkam-icon.png','/ehkam-logo-full.png'):
   return self.write((BASE/path.lstrip('/')).read_bytes(),ctype='image/x-icon' if path.endswith('.ico') else 'image/png')
  c=conn()
  try:
   if method=='POST' and path=='/api/login':
    d=self.input();u=c.execute('SELECT * FROM users WHERE username=?',(d.get('username',''),)).fetchone()
    if not u or not verifypw(d.get('password',''),u['password_hash']) or u['role']!=d.get('account_role'):fail('بيانات الدخول أو نوع الحساب غير صحيحة',401)
    token=secrets.token_urlsafe(32)
    with LOCK:SESSIONS[token]=u['id']
    record(c,u,'login','user',u['id']);c.commit()
    return self.write({'ok':True},headers={'Set-Cookie':'ehkam_session='+token+'; HttpOnly; SameSite=Strict; Path=/'})
   if method=='POST' and path=='/api/password/request':
    d=self.input();username=str(d.get('username','')).strip();requested=str(d.get('channel') or 'email').strip().lower();ip=self.client_address[0]
    if requested not in ('email','whatsapp','dual'):requested='email'
    if not username:fail('اسم المستخدم مطلوب')
    current=int(time.time());key=(ip,username,requested)
    with LOCK:
     timestamps=[t for t in RESET_ATTEMPTS.get(key,[]) if current-t<3600]
     if len(timestamps)>=5:fail('محاولات كثيرة؛ حاول لاحقًا',429)
     timestamps.append(current);RESET_ATTEMPTS[key]=timestamps
    generic={'message':'إذا كان الحساب موجودًا ومسجّلًا بوسيلة التحقق المختارة، سيصلك رمز التحقق.'}
    u=c.execute("SELECT * FROM users WHERE username=? AND role='employee'",(username,)).fetchone()
    if not u:return self.write(generic)
    e=c.execute('SELECT email,phone_country,phone_local FROM employees WHERE id=?',(u['employee_id'],)).fetchone()
    if not e:return self.write(generic)
    destination=str(e['email'] or '').strip().lower()
    country=str(e['phone_country'] or '')
    local=re.sub(r'[^0-9]','',str(e['phone_local'] or ''))
    if local.startswith('0'):local=local[1:]
    phone=country+local
    valid_email=bool(re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',destination))
    valid_phone=country in ('+966','+20') and (8<=len(local)<=10)
    c.execute('UPDATE password_resets SET used=1 WHERE user_id=?',(u['id'],))
    if requested=='dual':
     if not valid_email or not valid_phone:return self.write(generic)
     code=f'{secrets.randbelow(1000000):06d}'
     if not resend_send_reset(destination,code):fail('تعذر إرسال البريد حاليًا؛ راجع إعداد Resend أو سجلات Railway',503)
     c.execute('INSERT INTO password_resets(user_id,code_hash,expires_at,channel,destination,whatsapp_phone) VALUES(?,?,?,?,?,?)',
      (u['id'],hashpw(code),current+600,'dual',destination,phone))
    elif requested=='email':
     if not valid_email:return self.write(generic)
     code=f'{secrets.randbelow(1000000):06d}'
     if not resend_send_reset(destination,code):fail('تعذر إرسال البريد حاليًا؛ راجع إعداد Resend أو سجلات Railway',503)
     c.execute('INSERT INTO password_resets(user_id,code_hash,expires_at,channel,destination,whatsapp_phone) VALUES(?,?,?,?,?,?)',
      (u['id'],hashpw(code),current+600,'email',destination,phone))
    else:
     if not valid_phone:return self.write(generic)
     try:sent=authevo_send(phone)
     except RuntimeError:sent=False
     if not sent:fail('تعذر إرسال رمز واتساب حاليًا؛ تحقق من إعداد Authevo ورصيد الخدمة',503)
     c.execute('INSERT INTO password_resets(user_id,code_hash,expires_at,channel,destination,whatsapp_phone,whatsapp_sent) VALUES(?,?,?,?,?,?,?)',
      (u['id'],hashpw(secrets.token_urlsafe(8)),current+600,'whatsapp',phone,phone,1))
    c.commit();return self.write(generic)
   if method=='POST' and path=='/api/password/verify':
    d=self.input();username=str(d.get('username','')).strip();code=str(d.get('code','')).strip();channel=str(d.get('channel') or '').strip().lower()
    if not re.fullmatch(r'\d{6}',code) or channel not in ('email','whatsapp'):fail('رمز التحقق غير صحيح',400)
    u=c.execute("SELECT * FROM users WHERE username=? AND role='employee'",(username,)).fetchone()
    if not u:fail('رمز التحقق غير صحيح أو منتهي',400)
    r=c.execute('SELECT * FROM password_resets WHERE user_id=? AND used=0 ORDER BY id DESC LIMIT 1',(u['id'],)).fetchone()
    if not r or r['expires_at']<int(time.time()):fail('رمز التحقق غير صحيح أو منتهي',400)
    if r['channel']=='dual':
     if channel=='email':
      if r['email_verified'] or r['attempts']>=5:fail('رمز البريد مستعمل أو تجاوز عدد المحاولات',400)
      c.execute('UPDATE password_resets SET attempts=attempts+1 WHERE id=?',(r['id'],));c.commit()
      if not verifypw(code,r['code_hash']):fail('رمز البريد غير صحيح أو منتهي',400)
      c.execute('UPDATE password_resets SET email_verified=1 WHERE id=?',(r['id'],));c.commit()
      try: sent=authevo_send(r['whatsapp_phone'])
      except RuntimeError:sent=False
      if sent:
       c.execute('UPDATE password_resets SET whatsapp_sent=1 WHERE id=?',(r['id'],));c.commit()
      return self.write({'ok':True,'next':'whatsapp','whatsapp_sent':bool(sent)})
     if not r['email_verified'] or not r['whatsapp_sent'] or r['whatsapp_attempts']>=5:fail('يجب تأكيد البريد وإرسال رمز واتساب أولًا',403)
     c.execute('UPDATE password_resets SET whatsapp_attempts=whatsapp_attempts+1 WHERE id=?',(r['id'],));c.commit()
     try: verified=authevo_verify(r['whatsapp_phone'],code)
     except RuntimeError:fail('تعذر التحقق من واتساب حاليًا',503)
     if not verified:fail('رمز واتساب غير صحيح أو منتهي',400)
    else:
      if r['channel']!=channel:fail('وسيلة التحقق الحالية لا تطابق الوسيلة المختارة',400)
      if channel=='email':
       if r['attempts']>=5:fail('تم تجاوز عدد المحاولات لهذا الرمز',400)
       c.execute('UPDATE password_resets SET attempts=attempts+1 WHERE id=?',(r['id'],));c.commit()
       if not verifypw(code,r['code_hash']):fail('رمز البريد غير صحيح أو منتهي',400)
       c.execute('UPDATE password_resets SET email_verified=1 WHERE id=?',(r['id'],));c.commit()
      else:
       if r['whatsapp_attempts']>=5:fail('تم تجاوز عدد المحاولات لهذا الرمز',400)
       c.execute('UPDATE password_resets SET whatsapp_attempts=whatsapp_attempts+1 WHERE id=?',(r['id'],));c.commit()
       try: verified=authevo_verify(r['whatsapp_phone'] or r['destination'],code)
       except RuntimeError:fail('تعذر التحقق من واتساب حاليًا',503)
       if not verified:fail('رمز واتساب غير صحيح أو منتهي',400)
    grant=secrets.token_urlsafe(48)
    c.execute('UPDATE password_resets SET verified_token_hash=? WHERE id=?',(hashlib.sha256(grant.encode()).hexdigest(),r['id']))
    c.commit();return self.write({'ok':True,'reset_token':grant})
   if method=='POST' and path=='/api/password/whatsapp/send':
    d=self.input();username=str(d.get('username','')).strip();ip=self.client_address[0];current=int(time.time())
    with LOCK:
     key=(ip,username,'whatsapp')
     attempts=[t for t in RESET_ATTEMPTS.get(key,[]) if current-t<3600]
     if len(attempts)>=3:fail('محاولات إرسال كثيرة؛ حاول لاحقًا',429)
     attempts.append(current);RESET_ATTEMPTS[key]=attempts
    u=c.execute("SELECT * FROM users WHERE username=? AND role='employee'",(username,)).fetchone()
    if not u:fail('تعذر إرسال رمز واتساب',400)
    r=c.execute('SELECT * FROM password_resets WHERE user_id=? AND used=0 ORDER BY id DESC LIMIT 1',(u['id'],)).fetchone()
    if not r or r['channel']!='dual' or not r['email_verified'] or r['expires_at']<current or r['verified_token_hash']:
     fail('يجب تأكيد البريد أولًا',403)
    try:sent=authevo_send(r['whatsapp_phone'])
    except RuntimeError:sent=False
    if not sent:fail('تعذر إرسال رمز واتساب حاليًا',503)
    c.execute('UPDATE password_resets SET whatsapp_sent=1 WHERE id=?',(r['id'],));c.commit()
    return self.write({'ok':True})
   if method=='POST' and path=='/api/password/confirm':
    d=self.input();username=str(d.get('username','')).strip();password=str(d.get('new_password',''))
    grant=str(d.get('reset_token',''))
    if len(password)<10 or len(password)>128:fail('كلمة المرور الجديدة 10 أحرف على الأقل',400)
    if len(grant)<40 or len(grant)>200:fail('يجب التحقق من الرمز أولًا',403)
    u=c.execute("SELECT * FROM users WHERE username=? AND role='employee'",(username,)).fetchone()
    if not u:fail('التحقق غير صالح أو منتهي',403)
    r=c.execute('SELECT * FROM password_resets WHERE user_id=? AND used=0 ORDER BY id DESC LIMIT 1',(u['id'],)).fetchone()
    valid=False
    if r and r['expires_at']>=int(time.time()) and r['verified_token_hash'] and hmac.compare_digest(r['verified_token_hash'],hashlib.sha256(grant.encode()).hexdigest()):
     if r['channel']=='dual':
      valid=bool(r['email_verified'] and r['whatsapp_sent'])
     elif r['channel'] in ('email','whatsapp'):
      valid=True
    if not valid:fail('التحقق غير صالح أو منتهي',403)
    changed=c.execute('UPDATE password_resets SET used=1,verified_token_hash=NULL WHERE id=? AND used=0 AND verified_token_hash=?',(r['id'],r['verified_token_hash']))
    if changed.rowcount!=1:fail('تم استخدام رمز التحقق بالفعل',403)
    c.execute('UPDATE users SET password_hash=? WHERE id=?',(hashpw(password),u['id']))
    with LOCK:
     for token,uid in list(SESSIONS.items()):
      if uid==u['id']:SESSIONS.pop(token,None)
    record(c,u,'password_reset','user',u['id']);c.commit();return self.write({'ok':True})
   u=self.actor(c)
   if path=='/api/me' and method=='GET':return self.write(dict(u))
   if path=='/api/logout' and method=='POST':
    cookie=SimpleCookie();cookie.load(self.headers.get('Cookie',''));v=cookie.get('ehkam_session')
    if v:
     with LOCK:SESSIONS.pop(v.value,None)
    return self.write({'ok':True},headers={'Set-Cookie':'ehkam_session=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/'})
   if path=='/api/dashboard' and method=='GET':
    es=visible_employees(c,u);rr=visible_requests(c,u)
    co=c.execute('SELECT COUNT(*) FROM companies WHERE archived=0').fetchone()[0] if u['role']=='admin' else 1
    return self.write({'companies':co,'employees':len(es),'requests':len(rr),'pending':sum(r['status'] in ('new','review') for r in rr),'recent':rr[:5]})
   if path=='/api/companies' and method=='GET':
    if u['role']!='admin':fail('غير مصرح',403)
    return self.write([dict(x) for x in c.execute('''SELECT c.*,(SELECT COUNT(*) FROM employees e WHERE e.company_id=c.id AND e.archived=0) staff_count,(SELECT COUNT(*) FROM requests r WHERE r.company_id=c.id AND r.status IN ('new','review')) pending_count FROM companies c ORDER BY c.archived,c.id DESC''')])
   if path=='/api/companies' and method=='POST':
    if u['role']!='admin':fail('غير مصرح',403)
    d=self.input();name=str(d.get('name','')).strip()
    if not name:fail('اسم الشركة مطلوب')
    cursor=c.execute('INSERT INTO companies(name,name_en,contract_end,city) VALUES(?,?,?,?)',(name,d.get('name_en',''),d.get('contract_end'),d.get('city','')))
    record(c,u,'create','company',cursor.lastrowid,name);c.commit();return self.write({'ok':True},201)
   if path=='/api/employees' and method=='GET':return self.write(visible_employees(c,u,True))
   if path=='/api/employees' and method=='POST':
    if u['role']!='admin':fail('غير مصرح',403)
    d=self.input();co=int(d.get('company_id',0));exists=c.execute('SELECT id FROM companies WHERE id=? AND archived=0',(co,)).fetchone()
    if not exists:fail('الشركة غير موجودة')
    if not d.get('name') or not d.get('employee_no'):fail('الاسم والرقم الوظيفي مطلوبان')
    country,phone,email=clean_contacts(d)
    username=str(d.get('login_username','')).strip();newpw=str(d.get('login_password',''))
    if not username or len(newpw)<10 or len(newpw)>128 or not re.fullmatch(r'[A-Za-z0-9_.-]{3,64}',username):fail('اسم دخول إنجليزي 3–64 حرف وكلمة مرور لا تقل عن 10 أحرف مطلوبان')
    if c.execute('SELECT 1 FROM users WHERE username=?',(username,)).fetchone():fail('اسم المستخدم مستخدم بالفعل')
    keys=['phone_country','phone_local','email','photo','company_id','name','name_en','employee_no','identity_no','job','department','manager_id','status','hire_date','contract_start','contract_end','basic','housing','transport','other','annual_entitlement','carryover','workweek','work_start','work_end','payment_method']
    values=[(country if k=='phone_country' else phone if k=='phone_local' else email if k=='email' else d.get(k)) for k in keys];defaults={'name_en':'','identity_no':'','job':'','department':'','manager_id':None,'status':'نشط','basic':0,'housing':0,'transport':0,'other':0,'annual_entitlement':21,'carryover':0,'workweek':'0,1,2,3,5,6','work_start':'09:00','work_end':'17:00','payment_method':'bank'}
    values=[defaults.get(k) if v is None else v for k,v in zip(keys,values)]
    if d.get('manager_id'):
     m=c.execute('SELECT company_id FROM employees WHERE id=?',(d['manager_id'],)).fetchone()
     if not m or m['company_id']!=co:fail('المشرف يجب أن يتبع نفس الشركة')
    try:cur=c.execute('INSERT INTO employees('+','.join(keys)+') VALUES('+','.join('?' for _ in keys)+')',values)
    except sqlite3.IntegrityError:fail('رقم الموظف مستخدم داخل هذه الشركة')
    c.execute('INSERT INTO users(username,password_hash,role,employee_id,company_id) VALUES(?,?,?,?,?)',(username,hashpw(newpw),'employee',cur.lastrowid,co))
    record(c,u,'create','employee',cur.lastrowid,d['name']);c.commit();return self.write({'id':cur.lastrowid},201)
   if path.startswith('/api/employees/') and method=='POST':
    if u['role']!='admin':fail('غير مصرح',403)
    eid=int(path.rsplit('/',1)[1]);e=user_allowed_emp(c,u,eid);d=self.input()
    permitted={'photo','name','name_en','identity_no','job','department','manager_id','status','hire_date','contract_start','contract_end','basic','housing','transport','other','annual_entitlement','carryover','workweek','work_start','work_end','payment_method'}
    changes={k:v for k,v in d.items() if k in permitted}
    if not changes:fail('لا توجد بيانات للتعديل')
    if 'photo' in changes:
     photo=changes['photo']
     if photo is not None and (not isinstance(photo,str) or len(photo)>300000 or not photo.startswith(('data:image/png;base64,','data:image/jpeg;base64,','data:image/webp;base64,'))):fail('صيغة الصورة غير مقبولة أو حجمها كبير')
    if changes.get('manager_id'):
     m=c.execute('SELECT company_id FROM employees WHERE id=?',(changes['manager_id'],)).fetchone()
     if not m or m['company_id']!=e['company_id']:fail('المشرف يجب أن يتبع نفس الشركة')
    c.execute('UPDATE employees SET '+','.join(k+'=?' for k in changes)+' WHERE id=?',list(changes.values())+[eid]);record(c,u,'update','employee',eid,','.join(changes));c.commit();return self.write({'ok':True})
   if path.startswith('/api/leave/') and method=='GET':
    eid=int(path.rsplit('/',1)[1]);e=user_allowed_emp(c,u,eid);return self.write(leave(c,e))
   if path=='/api/advance/preview' and method=='GET':
    qs=parse_qs(urlparse(self.path).query);eid=int(qs.get('employee_id',[u['employee_id'] or 0])[0]);e=user_allowed_emp(c,u,eid)
    months=remaining_months(e['contract_end'],TODAY());gross=salary(e);monthly_limit=round(gross*.35,2)
    return self.write({'gross_salary':gross,'remaining_months':months,'max_monthly_installment':monthly_limit,'max_advance':round(monthly_limit*months,2),'contract_end':e['contract_end']})
   if path=='/api/requests' and method=='GET':return self.write(visible_requests(c,u))
   if path=='/api/requests' and method=='POST':
    d=self.input();eid=int(d.get('employee_id',u['employee_id'] or 0));e=user_allowed_emp(c,u,eid)
    if u['role']=='manager' and d.get('type')!='delegation':fail('المدير ينشئ الانتداب فقط',403)
    if u['role']=='employee' and eid!=u['employee_id']:fail('غير مصرح',403)
    if u['role']!='manager' and d.get('type')=='delegation':fail('الانتداب يبدأ من المشرف فقط',403)
    typ=d.get('type');allowed={'annual_leave','sick_leave','unpaid_leave','advance','permission','punch_fix','hr_contact','investigation','termination','delegation','open'}
    if typ not in allowed:fail('نوع الطلب غير صالح')
    a=d.get('start_date') or None;b=d.get('end_date') or None;days=0;amount=float(d.get('amount') or 0);inst=float(d.get('installment') or 0)
    if typ in ('annual_leave','sick_leave','unpaid_leave','permission','delegation'):
     if not a or not b:fail('تاريخ البداية والنهاية مطلوبان')
     days=business_days(e,a,b)
    if typ=='annual_leave':
     bal=leave(c,e)
     if days<=0 or days>bal['available']:fail('عدد أيام الإجازة يتجاوز الرصيد المتاح')
    if typ=='advance':
     limit=round(salary(e)*.35*remaining_months(e['contract_end'],TODAY()),2)
     if amount<=0 or amount>limit:fail('طلب السلفة يتجاوز الحد المستحق: '+str(limit)+' ريال')
     if inst<=0 or inst>salary(e)*.35:fail('القسط الشهري يتجاوز 35% من الراتب')
    if typ=='sick_leave' and not d.get('notes'):fail('أدخل تفاصيل المستند الطبي (رفع الملفات غير متاح في النموذج الأولي)')
    if typ=='punch_fix' and not a:fail('تاريخ التصحيح مطلوب')
    if typ=='delegation' and u['employee_id']!=e['manager_id']:fail('يمكن للمشرف إصدار انتداب لموظفيه فقط')
    ts=now();cur=c.execute('''INSERT INTO requests(company_id,employee_id,type,start_date,end_date,days,amount,installment,reason,notes,created_by,approver_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(e['company_id'],eid,typ,a,b,days,amount,inst,d.get('reason',''),d.get('notes',''),u['id'],e['manager_id'],ts,ts))
    record(c,u,'create','request',cur.lastrowid,typ);c.commit();return self.write({'id':cur.lastrowid,'number':f'EH-{cur.lastrowid:06d}'},201)
   if path.startswith('/api/requests/') and method=='POST':
    rid=int(path.rsplit('/',1)[1]);d=self.input();r=c.execute('SELECT * FROM requests WHERE id=?',(rid,)).fetchone()
    if not r:fail('الطلب غير موجود',404)
    e=user_allowed_emp(c,u,r['employee_id']);action=d.get('status')
    if action not in ('review','approved','rejected','cancelled','closed','accepted','declined'):fail('حالة غير صحيحة')
    if r['status'] not in ('new','review') and not (action=='cancelled' and r['status']=='approved'):fail('لا يمكن تغيير هذه الحالة')
    if action in ('review','approved','rejected','closed'):
     if u['role']=='employee':fail('لا يمكن اعتماد طلبك',403)
     if u['role']=='manager' and e['manager_id']!=u['employee_id']:fail('ليس من موظفيك',403)
     if r['type']=='delegation':fail('الانتداب ينتظر قبول أو رفض الموظف')
    if action in ('accepted','declined'):
     if r['type']!='delegation' or u['role']!='employee' or u['employee_id']!=r['employee_id']:fail('فقط الموظف المعني ينتدبه المدير',403)
     if action=='declined' and not str(d.get('note','')).strip():fail('اذكر سبب رفض الانتداب',400)
    if action=='cancelled' and u['role']!='admin':fail('إلغاء الطلب المعتمد متاح للإدارة فقط في النموذج الأولي',403)
    if action=='approved' and r['type']=='annual_leave':
     if r['days']>leave(c,e)['available']:fail('الرصيد غير كافٍ عند الاعتماد')
    c.execute('UPDATE requests SET status=?,decision_note=?,updated_at=? WHERE id=?',(action,d.get('note',''),now(),rid))
    if action=='approved' and r['type']=='punch_fix' and r['start_date']:
     c.execute('''INSERT INTO attendance(employee_id,day,check_in,check_out,source) VALUES(?,?,?,?,?) ON CONFLICT(employee_id,day) DO UPDATE SET check_in=COALESCE(attendance.check_in,excluded.check_in),check_out=COALESCE(attendance.check_out,excluded.check_out),source=excluded.source''',(e['id'],r['start_date'],e['work_start'],e['work_end'],'approved_punch_fix'))
    record(c,u,'status_'+action,'request',rid,d.get('note',''));c.commit();return self.write({'ok':True})
   if path=='/api/attendance' and method=='GET':
    qs=parse_qs(urlparse(self.path).query);eid=int(qs.get('employee_id',[u['employee_id'] or 0])[0]);user_allowed_emp(c,u,eid)
    q='SELECT * FROM attendance WHERE employee_id=?';p=[eid]
    if qs.get('from'):q+=' AND day>=?';p.append(qs['from'][0])
    if qs.get('to'):q+=' AND day<=?';p.append(qs['to'][0])
    return self.write([dict(x) for x in c.execute(q+' ORDER BY day DESC',p)])
   if path=='/api/attendance' and method=='POST':
    if u['role']!='employee':fail('تسجيل البصمة من حساب الموظف فقط',403)
    # IMPORTANT no trusted geolocation validation in this prototype. Restrict endpoint rather than imply location check.
    fail('تسجيل البصمة الجغرافية مغلق حتى تفعيل تحقق الموقع من الخادم',501)
   if path=='/api/archive/history' and method=='GET':
    if u['role']!='admin':fail('غير مصرح',403)
    rows=c.execute("SELECT username,action,entity,entity_id,timestamp FROM audit WHERE action IN ('archive','restore') ORDER BY id DESC LIMIT 100").fetchall()
    return self.write([dict(row) for row in rows])
   if path=='/api/archive' and method=='POST':
    if u['role']!='admin':fail('غير مصرح',403)
    d=self.input();target=d.get('target');eid=int(d.get('id',0));value=int(bool(d.get('archived')))
    if not d.get('confirm') or not d.get('password'):fail('التأكيد وكلمة المرور مطلوبان')
    full=c.execute('SELECT password_hash FROM users WHERE id=?',(u['id'],)).fetchone()
    if not verifypw(d['password'],full['password_hash']):fail('كلمة المرور غير صحيحة',403)
    table={'company':'companies','employee':'employees'}.get(target)
    if not table:fail('النوع غير صحيح')
    cur=c.execute('UPDATE '+table+' SET archived=? WHERE id=?',(value,eid))
    if not cur.rowcount:fail('العنصر غير موجود',404)
    record(c,u,'archive' if value else 'restore',target,eid,'confirmed');c.commit();return self.write({'ok':True})
   if path=='/api/audit' and method=='GET':
    if u['role']!='admin':fail('غير مصرح',403)
    return self.write([dict(x) for x in c.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 200')])
   if path=='/api/report.csv' and method=='GET':
    rows=visible_employees(c,u);output=io.StringIO();w=csv.writer(output);w.writerow(['Company ID','Employee ID','Name','Employee No','Department','Status','Gross Salary'])
    for e in rows:w.writerow([e['company_id'],e['id'],e['name'],e['employee_no'],e['department'],e['status'],e['gross_salary']])
    data='\ufeff'+output.getvalue();return self.write(data,ctype='text/csv',headers={'Content-Disposition':'attachment; filename="employees.csv"'})
   fail('المسار غير موجود',404)
  except AppError as x:self.write({'error':x.msg},x.status)
  except Exception as x:
   print('Server error:',repr(x));self.write({'error':'خطأ داخلي؛ راجع مخرجات الخادم'},500)
  finally:c.close()

if __name__=='__main__':
 init();port=int(os.environ.get('PORT','8765'))
 print(f'EHKAM working local server: http://127.0.0.1:{port}');# Never print credentials into public hosting logs
 ThreadingHTTPServer(('0.0.0.0',port),Handler).serve_forever()

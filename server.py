"""EHKAM HR Portal - local functional prototype. Run: python server.py"""
import requested_batch
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import ai_assistant, hr, sys, calendar, gps_location, security, workforce, payroll, talent, directory_tools, permissions, ui_workflows
import sqlite3, json, os, hashlib, hmac, secrets, datetime, csv, io, threading, re, time, ssl, smtplib, urllib.request, urllib.parse, urllib.error, base64, html, math
from email.message import EmailMessage
from http.cookies import SimpleCookie
from pathlib import Path

BASE=Path(__file__).resolve().parent
DB=Path(os.environ.get('EHKAM_DB_PATH', str(BASE/'ehkam_data.sqlite3')))
MAX_ATTACHMENT_SIZE=2*1024*1024
MAX_ATTACHMENTS=3
SESSIONS={}
LOCK=threading.Lock()
RESET_ATTEMPTS={}
from zoneinfo import ZoneInfo
TODAY=lambda: datetime.datetime.now(ZoneInfo('Asia/Riyadh')).date()

def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
def hashpw(p,s=None):
 s=s or secrets.token_hex(16)
 return s+':'+hashlib.pbkdf2_hmac('sha256',p.encode(),bytes.fromhex(s),200000).hex()
def verifypw(password,stored):
 try:
  s,_=stored.split(':');return hmac.compare_digest(hashpw(password,s),stored)
 except Exception:return False

def persistent_database_required():
 return bool(os.environ.get('RAILWAY_ENVIRONMENT_ID') or os.environ.get('RAILWAY_SERVICE_ID') or os.environ.get('RAILWAY_PROJECT_ID') or os.environ.get('EHKAM_REQUIRE_EXISTING_DB')=='1' or os.environ.get('EHKAM_DB_ID'))

def conn(allow_create=False):
 if persistent_database_required() and not allow_create:
  c=sqlite3.connect(DB.resolve().as_uri()+'?mode=rw',uri=True,timeout=15)
  expected=os.environ.get('EHKAM_DB_ID','').strip()
  if expected:
   try:
    row=c.execute('SELECT database_id FROM production_identity WHERE id=1').fetchone()
    if not row or row[0]!=expected:raise RuntimeError('Production database identity mismatch')
   except Exception:
    c.close();raise
 else:c=sqlite3.connect(DB,timeout=15)
 c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');return c

def validate_runtime_database():
 required=persistent_database_required()
 if not required:return
 if not DB.is_file():raise RuntimeError('Existing database not found. Deployment stopped; configure EHKAM_DB_PATH to the existing persistent database. No database was created.')
 with sqlite3.connect(DB.resolve().as_uri()+'?mode=ro',uri=True) as existing:
  if existing.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('Existing database integrity check failed; inspect a backup before migration.')
  expected=os.environ.get('EHKAM_DB_ID','').strip()
  railway=bool(os.environ.get('RAILWAY_ENVIRONMENT_ID') or os.environ.get('RAILWAY_SERVICE_ID') or os.environ.get('RAILWAY_PROJECT_ID'))
  mount=os.environ.get('RAILWAY_VOLUME_MOUNT_PATH','')
  if expected and railway:
   if not mount:raise RuntimeError('Railway persistent volume is not reported; startup stopped.')
   if not DB.resolve().is_relative_to(Path(mount).resolve()):raise RuntimeError('Production database must be inside the mounted Railway volume; startup stopped.')
  marked=existing.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='production_identity'").fetchone()
  if expected or marked:
   if not expected:raise RuntimeError('EHKAM_DB_ID must be configured for this production database; startup stopped.')
   if not marked:raise RuntimeError('Configured production database identity is absent; startup stopped.')
   identity=existing.execute('SELECT database_id FROM production_identity WHERE id=1').fetchone()
   if not identity or identity[0]!=expected:raise RuntimeError('Production database identity mismatch; startup stopped without initialization.')
  if not existing.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone() or not existing.execute('SELECT 1 FROM users LIMIT 1').fetchone():
   raise RuntimeError('Existing account database required. Deployment stopped without creating demo users.')

def init(seeder=None,allow_create=False):
 # SQLite backup API preserves a consistent pre-migration copy, including WAL data.
 if DB.exists():
  with conn() as existing:
   has_migrations=existing.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
   migrated=has_migrations and existing.execute("SELECT 1 FROM schema_migrations WHERE version='workflow-v1'").fetchone()
   if not migrated:
    backup=DB.with_name(DB.name+'.before-workflow-v1.bak')
    if backup.exists():raise RuntimeError('Migration backup already exists; inspect before retrying')
    with sqlite3.connect(backup) as target:existing.backup(target)
 if DB.exists():
  with conn() as existing:
   has=existing.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
   v1=has and existing.execute("SELECT 1 FROM schema_migrations WHERE version='workflow-v1'").fetchone()
   v2=has and existing.execute("SELECT 1 FROM schema_migrations WHERE version='hr-v2'").fetchone()
   if v1 and not v2:
    backup=DB.with_name(DB.name+'.before-hr-v2.bak')
    if backup.exists():raise RuntimeError('HR migration backup already exists; inspect before retrying')
    with sqlite3.connect(backup) as target:existing.backup(target)
 if DB.exists():
  with conn() as existing:
   if existing.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone() and not existing.execute("SELECT 1 FROM schema_migrations WHERE version=?",(workforce.VERSION,)).fetchone():
    backup=DB.with_name(DB.name+'.before-professional-v1.bak')
    if backup.exists():raise RuntimeError('Professional migration backup exists; verify before retrying')
    with sqlite3.connect(backup) as target:existing.backup(target)
    os.chmod(backup,0o600)
 if DB.exists():
  with conn() as existing:
   if existing.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone() and not existing.execute('SELECT 1 FROM schema_migrations WHERE version=?',(requested_batch.VERSION,)).fetchone():
    backup=DB.with_name(DB.name+'.before-'+requested_batch.VERSION+'.bak')
    if backup.exists():raise RuntimeError('Requested migration backup exists; verify before retrying')
    with sqlite3.connect(backup) as target:existing.backup(target)
    os.chmod(backup,0o600)
 c=conn(allow_create=allow_create);c.executescript('''
 CREATE TABLE IF NOT EXISTS companies(id INTEGER PRIMARY KEY,name TEXT NOT NULL, name_en TEXT DEFAULT '',contract_end TEXT,city TEXT DEFAULT '', address TEXT DEFAULT '', latitude REAL, longitude REAL, geofence_radius REAL NOT NULL DEFAULT 100, archived INTEGER NOT NULL DEFAULT 0);
 CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('admin','manager','employee')),employee_id INTEGER,company_id INTEGER);
 CREATE TABLE IF NOT EXISTS employees(id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),name TEXT NOT NULL,name_en TEXT DEFAULT '',employee_no TEXT NOT NULL, identity_no TEXT DEFAULT '',job TEXT DEFAULT '',department TEXT DEFAULT '',manager_id INTEGER, status TEXT NOT NULL DEFAULT 'نشط',hire_date TEXT, contract_start TEXT,contract_end TEXT,basic REAL NOT NULL DEFAULT 0,housing REAL NOT NULL DEFAULT 0,transport REAL NOT NULL DEFAULT 0,other REAL NOT NULL DEFAULT 0,annual_entitlement REAL NOT NULL DEFAULT 21,carryover REAL NOT NULL DEFAULT 0,monthly_grant REAL NOT NULL DEFAULT 0,workweek TEXT NOT NULL DEFAULT '0,1,2,3,5,6', work_start TEXT NOT NULL DEFAULT '09:00',work_end TEXT NOT NULL DEFAULT '17:00',payment_method TEXT NOT NULL DEFAULT 'bank',archived INTEGER NOT NULL DEFAULT 0,UNIQUE(company_id,employee_no));
 CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL,employee_id INTEGER NOT NULL,type TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'new',start_date TEXT,end_date TEXT,days REAL DEFAULT 0,amount REAL DEFAULT 0,installment REAL DEFAULT 0,notes TEXT DEFAULT '',created_by INTEGER NOT NULL,approver_id INTEGER,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,decision_note TEXT DEFAULT '');
 CREATE TABLE IF NOT EXISTS attendance(id INTEGER PRIMARY KEY,employee_id INTEGER NOT NULL,day TEXT NOT NULL,check_in TEXT,check_out TEXT,source TEXT DEFAULT 'demo',UNIQUE(employee_id,day));
 CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,user_id INTEGER, username TEXT, action TEXT NOT NULL, entity TEXT NOT NULL,entity_id INTEGER, info TEXT, timestamp TEXT NOT NULL);
 ''')
 if seeder is not None and not c.execute('SELECT 1 FROM users LIMIT 1').fetchone():seeder(c)

 # Add company geofence fields to older installations safely.
 company_columns={row[1] for row in c.execute('PRAGMA table_info(companies)')}
 for col,decl in (('gps_url',"TEXT NOT NULL DEFAULT ''"),('address',"TEXT DEFAULT ''"),('latitude','REAL'),('longitude','REAL'),('geofence_radius','REAL NOT NULL DEFAULT 100')):
  if col not in company_columns:c.execute('ALTER TABLE companies ADD COLUMN '+col+' '+decl)
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
 if not c.in_transaction:c.execute('BEGIN IMMEDIATE')
 migrate_workflow(c)
 hr.migrate(c)
 workforce.migrate(c);security.migrate(c);payroll.migrate(c);talent.migrate(c);directory_tools.migrate(c);permissions.migrate(c);requested_batch.migrate(c);requested_batch.capture_leave_cutover(c,sys.modules[__name__])
 c.commit();c.close()

def migrate_workflow(c):
 c.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version TEXT PRIMARY KEY)')
 if c.execute("SELECT 1 FROM schema_migrations WHERE version='workflow-v1'").fetchone():return
 c.execute('CREATE TABLE employee_number_history(employee_id INTEGER PRIMARY KEY REFERENCES employees(id),old_number TEXT NOT NULL,new_number TEXT NOT NULL)')
 c.execute('CREATE TABLE employee_number_sequence(id INTEGER PRIMARY KEY CHECK(id=1),value INTEGER NOT NULL)')
 rows=c.execute('SELECT id,employee_no FROM employees ORDER BY id').fetchall()
 used=set();assigned={}
 # Keep the first occurrence of each already canonical number across all companies.
 for row in rows:
  no=row['employee_no']
  if re.fullmatch(r'EH-[0-9]{4,}',no) and int(no[3:])>0 and no==f"EH-{int(no[3:]):04d}" and no not in used:
   used.add(no);assigned[row['id']]=no
 next_no=1
 for row in rows:
  if row['id'] in assigned:continue
  while f'EH-{next_no:04d}' in used:next_no+=1
  no=f'EH-{next_no:04d}';used.add(no);assigned[row['id']]=no
 # Temporary unique names avoid collisions with old company-scoped constraints.
 prefix='migration-'+secrets.token_hex(16)+'-'
 for row in rows:c.execute('UPDATE employees SET employee_no=? WHERE id=?',(prefix+str(row['id']),row['id']))
 for row in rows:
  no=assigned[row['id']]
  c.execute('UPDATE employees SET employee_no=? WHERE id=?',(no,row['id']))
  c.execute('INSERT INTO employee_number_history VALUES(?,?,?)',(row['id'],row['employee_no'],no))
 c.execute('CREATE UNIQUE INDEX employees_number_global ON employees(employee_no)')
 c.execute('INSERT INTO employee_number_sequence VALUES(1,?)',(max([int(x[3:]) for x in used],default=0),))
 # Move legacy reasons into notes before dropping the obsolete field.
 if 'reason' in {row[1] for row in c.execute('PRAGMA table_info(requests)')}:
  c.execute("UPDATE requests SET notes=CASE WHEN trim(COALESCE(reason,''))='' THEN notes WHEN trim(COALESCE(notes,''))='' THEN reason ELSE notes || char(10) || reason END")
  c.execute('ALTER TABLE requests DROP COLUMN reason')
 c.execute('CREATE TABLE request_attachments(id INTEGER PRIMARY KEY,request_id INTEGER NOT NULL REFERENCES requests(id),name TEXT NOT NULL,mime TEXT NOT NULL,content BLOB NOT NULL)')
 c.execute('CREATE TABLE request_decisions(id INTEGER PRIMARY KEY,request_id INTEGER NOT NULL REFERENCES requests(id),user_id INTEGER NOT NULL REFERENCES users(id),username TEXT NOT NULL,status TEXT NOT NULL,note TEXT NOT NULL,decided_at TEXT NOT NULL)')
 # Recover historical actors/times only where the original audit record exists.
 c.execute("INSERT INTO request_decisions(request_id,user_id,username,status,note,decided_at) SELECT a.entity_id,a.user_id,COALESCE(a.username,''),substr(a.action,8),COALESCE(a.info,''),a.timestamp FROM audit a JOIN requests r ON r.id=a.entity_id JOIN users u ON u.id=a.user_id WHERE a.entity='request' AND a.action IN ('status_review','status_approved','status_rejected','status_cancelled','status_closed','status_accepted','status_declined') ORDER BY a.id")
 c.execute('CREATE INDEX attachments_by_request ON request_attachments(request_id)')
 c.execute('CREATE INDEX decisions_by_request ON request_decisions(request_id)')
 c.execute("INSERT INTO schema_migrations VALUES('workflow-v1')")


def normalize_date(value):
 if value is None or value=='':return None
 value=str(value).strip()
 try:
  if re.fullmatch(r'\d{2}/\d{2}/\d{4}',value):
   day,month,year=map(int,value.split('/'));return datetime.date(year,month,day).isoformat()
  return datetime.date.fromisoformat(value).isoformat()
 except ValueError:fail('تاريخ غير صالح؛ استخدم يوم/شهر/سنة')


def normalize_employee_fields(d):
 for key in ('name','name_en','job','department','branch','passport','identity_no','iban','bank_name'):
  if key in d and (not isinstance(d[key],str) or len(d[key])>150):fail('نص الحقل غير صالح أو يتجاوز 150 حرفًا')
 for key in ('hire_date','contract_start','contract_end','birth_date'):
  if key in d:d[key]=normalize_date(d[key])
 if d.get('contract_start') and d.get('contract_end') and d['contract_end']<d['contract_start']:fail('نهاية العقد قبل بدايته')
 if 'workweek' in d:
  parts=str(d['workweek']).split(',')
  if not parts or any(x not in '0123456' or len(x)!=1 for x in parts):fail('أيام العمل غير صحيحة')
  d['workweek']=','.join(sorted(set(parts)))
 for key in ('basic','housing','transport','other','annual_entitlement','carryover'):
  if key in d:
   try:value=float(d[key])
   except (TypeError,ValueError):fail('قيمة مالية أو رصيد غير صالح')
   if not math.isfinite(value) or value<0:fail('القيم المالية والأرصدة يجب أن تكون غير سالبة ومحدودة')
   d[key]=value
 for key in ('work_start','work_end'):
  if key in d:datetime.time.fromisoformat(d[key])
 if 'photo' in d and d['photo'] is not None:
  photo=d['photo']
  if not isinstance(photo,str) or len(photo)>300000 or not re.fullmatch(r'data:image/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+',photo):fail('الصورة غير صالحة')
  try:raw=base64.b64decode(photo.split(',',1)[1],validate=True)
  except ValueError:fail('الصورة غير صالحة')
  kind=photo.split(';',1)[0]
  if not ((kind=='data:image/png' and raw.startswith(b'\x89PNG\r\n\x1a\n')) or (kind=='data:image/jpeg' and raw.startswith(b'\xff\xd8\xff')) or (kind=='data:image/webp' and raw.startswith(b'RIFF') and raw[8:12]==b'WEBP')):fail('محتوى الصورة لا يطابق نوعها')
 if 'manager_id' in d:d['manager_id']=d['manager_id'] or None
 return d


def validate_attachments(items):
 if not isinstance(items,list) or len(items)>MAX_ATTACHMENTS:fail('يسمح بثلاثة مرفقات كحد أقصى')
 result=[]
 for item in items:
  if not isinstance(item,dict):fail('المرفق غير صالح')
  name=item.get('name');encoded=item.get('data');mime=item.get('mime')
  if not isinstance(name,str) or not name or len(name)>180 or any(ch in name for ch in ('/','\\')) or any(ord(ch)<32 or ord(ch)==127 for ch in name):fail('اسم المرفق غير صالح')
  if not isinstance(encoded,str) or len(encoded)>((MAX_ATTACHMENT_SIZE+2)//3)*4:fail('حجم المرفق يتجاوز 2 ميجابايت')
  try:content=base64.b64decode(encoded,validate=True)
  except ValueError:fail('ترميز المرفق غير صالح')
  suffix=Path(name).suffix.lower()
  valid=(mime=='image/png' and suffix=='.png' and content.startswith(b'\x89PNG\r\n\x1a\n')) or (mime=='image/jpeg' and suffix in ('.jpg','.jpeg') and content.startswith(b'\xff\xd8\xff')) or (mime=='application/pdf' and suffix=='.pdf' and content.startswith(b'%PDF-'))
  if not content or len(content)>MAX_ATTACHMENT_SIZE or not valid:fail('المرفقات المقبولة PNG وJPG وPDF حتى 2 ميجابايت لكل ملف')
  result.append((name,mime,content))
 return result


def request_access(c,u,r):
 user_allowed_emp(c,u,r['employee_id'])
 if r['type']=='hr_contact' and u['role']!='admin' and r['employee_id']!=u['employee_id']:fail('مراسلات الموارد البشرية خاصة بالموظف والإدارة',403)
 permissions.request_guard(c,u,r,sys.modules[__name__])
 if u['role']=='manager' and r['employee_id']!=u['employee_id'] and r['type'] in ('advance','sick_leave','termination','investigation'):fail('البيانات المالية والطبية والتحقيقات خاصة بالموظف والإدارة',403)

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
 country=str(d.get('phone_country') or '+966');raw=str(d.get('phone_local') or '')
 if country != '+966':fail('النظام مخصص للسعودية؛ رمز الدولة +966')
 if raw and not re.fullmatch(r'05[0-9]{8}',raw):
  if not (raw==d.get('_legacy_phone') and re.fullmatch(r'5[0-9]{8}',raw)):fail('رقم الجوال يجب أن يكون 10 أرقام ويبدأ بـ05')
 local=raw[1:] if raw.startswith('0') else raw
 email=str(d.get('email') or '').strip().lower()
 if email and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):fail('البريد الإلكتروني غير صحيح')
 return country,local,email

def record(c,u,action,entity,eid=None,info=''):
 actor=c.execute('SELECT username,display_name,role FROM users WHERE id=?',(u['id'],)).fetchone()
 subject=None;request_type=None;employee_no=None
 if entity=='request':
  subject=c.execute('SELECT e.name,e.employee_no,r.type,r.start_date,r.end_date,r.days,r.amount,r.installment,r.notes FROM requests r JOIN employees e ON e.id=r.employee_id WHERE r.id=?',(eid,)).fetchone()
  if subject:
   request_type=subject['type'];employee_no=subject['employee_no']
   if action=='create':info={k:subject[k] for k in ('start_date','end_date','days','notes')}
   if action=='create' and request_type=='advance':info.update(amount=subject['amount'],installment=subject['installment'])
 elif entity in ('employee','attendance'):
  subject=c.execute('SELECT name,employee_no FROM employees WHERE id=?',(eid,)).fetchone()
  if subject:employee_no=subject['employee_no']
 elif entity=='company':
  subject=c.execute('SELECT name,city,address,contract_end,latitude,longitude,geofence_radius FROM companies WHERE id=?',(eid,)).fetchone()
  if subject and action in ('create','update'):info=dict(subject)
 envelope={'audit_version':1,'actor_name':(actor['display_name'] or actor['username']) if actor else u['username'],'actor_role':actor['role'] if actor else u['role'],'subject_name':subject['name'] if subject else '', 'employee_no':employee_no,'request_type':request_type,'details':info}
 company_id=None
 if entity=='company':company_id=eid
 elif entity in ('employee','attendance'):
  row=c.execute('SELECT company_id FROM employees WHERE id=?',(eid,)).fetchone();company_id=row[0] if row else None
 elif entity=='request':
  row=c.execute('SELECT company_id FROM requests WHERE id=?',(eid,)).fetchone();company_id=row[0] if row else None
 elif entity in ('schedules','work_sites','holidays'):
  row=c.execute('SELECT company_id FROM '+entity+' WHERE id=?',(eid,)).fetchone();company_id=row[0] if row else None
 elif entity=='user':
  target=c.execute('SELECT * FROM users WHERE id=?',(eid,)).fetchone()
  if target:company_id=target['company_id'] or permissions.scope_company(c,target)
 else:company_id=u['company_id'] if 'company_id' in u.keys() and u['company_id'] else permissions.scope_company(c,u)
 c.execute('INSERT INTO audit(user_id,username,action,entity,entity_id,info,timestamp,company_id) VALUES(?,?,?,?,?,?,?,?)',(u['id'],u['username'],action,entity,eid,json.dumps(envelope,ensure_ascii=False),now(),company_id))
def obj(row):return dict(row) if row is not None else None
def fail(msg,code=400):raise AppError(msg,code)
class AppError(Exception):
 def __init__(self,msg,status=400):self.msg=msg;self.status=status

def user_allowed_emp(c,u,eid):
 e=c.execute('SELECT * FROM employees WHERE id=?',(eid,)).fetchone()
 if not e:fail('الموظف غير موجود',404)
 if u['role']=='admin':
  permissions.company(c,u,e['company_id'],sys.modules[__name__]);return e
 if e['company_id']!=u['company_id']:fail('غير مصرح لك بالوصول',403)
 if u['role']=='employee' and u['employee_id']!=eid:fail('غير مصرح لك بالوصول',403)
 if u['role']=='manager' and e['id']!=u['employee_id'] and not workforce.can_manage(c,u,e,sys.modules[__name__]):fail('غير مصرح لك بالوصول',403)
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
 if today.day<min(d.day,calendar.monthrange(today.year,today.month)[1]):months-=1
 return max(0,months)

def remaining_months(date_end,today):
 if not date_end:return 0
 try:d=datetime.date.fromisoformat(date_end)
 except ValueError:return 0
 if d<=today:return 0
 months=(d.year-today.year)*12+(d.month-today.month)
 if d.day<today.day:months-=1
 return max(0,months)

def business_days(e,start,end,c=None):
 try:a=datetime.date.fromisoformat(start);b=datetime.date.fromisoformat(end)
 except Exception:fail('تاريخ غير صالح')
 if b<a:fail('تاريخ النهاية قبل البداية')
 if (b-a).days>365:fail('الفترة أكبر من سنة')
 valid={int(k) for k in (e['workweek'] or '').split(',') if k.strip().isdigit()}
 total=0
 for offset in range((b-a).days+1):
  day=a+datetime.timedelta(days=offset);historical=workforce.terms_at(c,e,day.isoformat()) if c is not None else e
  week={int(k) for k in historical['workweek'].split(',')}
  if day.weekday() in week and (c is None or not hr.holiday(c,historical,day.isoformat())):total+=1
 return total

def leave(c,e):
 return hr.leave(c,e,sys.modules[__name__])

def visible_employees(c,u,include_archived=False):
 q="SELECT e.*,(SELECT username FROM users WHERE employee_id=e.id AND role IN ('employee','manager') LIMIT 1) login_username,c.name company_name,(SELECT name FROM employees m WHERE m.id=e.manager_id) manager_name,(e.basic+e.housing+e.transport+e.other) gross_salary FROM employees e JOIN companies c ON c.id=e.company_id WHERE c.archived=0"
 p=[]
 if u['role']=='admin' and include_archived:q=q.replace('WHERE c.archived=0','WHERE 1=1')
 if not include_archived:q+=' AND e.archived=0'
 if u['role']=='employee':q+=' AND e.id=?';p=[u['employee_id']]
 elif u['role']=='manager':q+=' AND e.company_id=? AND (e.manager_id=? OR e.id=? OR e.manager_id IN (SELECT source_manager_id FROM approval_delegations WHERE target_manager_id=? AND company_id=? AND revoked=0 AND starts_on<=? AND ends_on>=?))';p=[u['company_id'],u['employee_id'],u['employee_id'],u['employee_id'],u['company_id'],TODAY().isoformat(),TODAY().isoformat()]
 scope=permissions.scope_company(c,u)
 if scope is not None:q+=' AND e.company_id=?';p.append(scope)
 q+=' ORDER BY e.name'
 rows=[dict(x) for x in c.execute(q,p)]
 if u['role']=='employee':
  for x in rows:
   for sensitive in ('basic','housing','transport','other','gross_salary'):pass # their own salary permitted
 profile=permissions.profile(c,u)
 if profile=='hr':
  for e in rows:
   for key in ('basic','housing','transport','other','gross_salary','iban','bank_name'):e.pop(key,None)
 elif profile in ('payroll','auditor'):
  for e in rows:
   for key in ('passport','birth_date','photo','qualification','specialization','identity_no'):e.pop(key,None)
 return [workforce.redact(e,u) for e in rows]

def visible_requests(c,u,include_details=True,request_id=None,aggregate=False):
 q='''SELECT r.*,e.name employee_name,e.employee_no,c.name company_name,c.approval_mode FROM requests r JOIN employees e ON e.id=r.employee_id JOIN companies c ON c.id=r.company_id WHERE 1=1''';p=[]
 if u['role']=='employee':q+=' AND r.employee_id=?';p=[u['employee_id']]
 elif u['role']=='manager':q+=' AND r.company_id=? AND (e.manager_id=? OR r.employee_id=? OR e.manager_id IN (SELECT source_manager_id FROM approval_delegations WHERE target_manager_id=? AND company_id=? AND revoked=0 AND starts_on<=? AND ends_on>=?))';p=[u['company_id'],u['employee_id'],u['employee_id'],u['employee_id'],u['company_id'],TODAY().isoformat(),TODAY().isoformat()]
 if u['role']!='admin':q+=" AND (r.type!='hr_contact' OR r.employee_id=?)";p.append(u['employee_id'])
 if u['role']=='manager':q+=" AND (r.type NOT IN ('advance','sick_leave','termination','investigation') OR r.employee_id=?)";p.append(u['employee_id'])
 scope=permissions.scope_company(c,u)
 if scope is not None:q+=' AND r.company_id=?';p.append(scope)
 profile=permissions.profile(c,u)
 if profile=='payroll':q+=" AND r.type='advance'"
 elif profile=='hr':q+=" AND r.type!='advance'"
 elif profile=='auditor':q+=" AND r.type NOT IN ('hr_contact','sick_leave','investigation')"
 if request_id is not None:q+=' AND r.id=?';p.append(request_id)
 if aggregate:
  q=q.replace('SELECT r.*,e.name employee_name,e.employee_no,c.name company_name,c.approval_mode','SELECT r.type,r.status,COUNT(*) count',1)
  return [dict(x) for x in c.execute(q+' GROUP BY r.type,r.status ORDER BY r.type,r.status',p)]
 q+=' ORDER BY r.id DESC'
 rows=[dict(x) for x in c.execute(q,p)]
 for row in rows:
  row['details']=json.loads(row['details'])
  if not include_details:continue
  creator=c.execute('SELECT username,display_name FROM users WHERE id=?',(row['created_by'],)).fetchone()
  row['creator_name']=creator['display_name'] or creator['username'] if creator else 'حساب سابق'
  row['comments']=[dict(x) for x in c.execute('SELECT username,message,created_at FROM request_comments WHERE request_id=? ORDER BY id',(row['id'],))]
  row['attachments']=[dict(x) for x in c.execute('SELECT id,name,mime,length(content) size FROM request_attachments WHERE request_id=?',(row['id'],))]
  row['decisions']=[dict(x) for x in c.execute('SELECT user_id,username,status,note,decided_at FROM request_decisions WHERE request_id=? ORDER BY id',(row['id'],))]
 return rows

class Handler(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def setup(self):
  super().setup();self.connection.settimeout(20)
 def write(self,data,status=200,ctype='application/json',headers=None):
  if isinstance(data,(dict,list)):data=json.dumps(data,ensure_ascii=False).encode()
  elif isinstance(data,str):data=data.encode()
  self.send_response(status);self.send_header('Content-Type',ctype+('; charset=utf-8' if ctype.startswith('text/') or ctype=='application/json' else ''));self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store')
  for k,v in {'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'same-origin','Permissions-Policy':'geolocation=(self), camera=(), microphone=()'}.items():self.send_header(k,v)
  for k,v in (headers or {}).items():self.send_header(k,v)
  self.end_headers();self.wfile.write(data)
 def input(self):
  if hasattr(self,'_parsed_body'):return self._parsed_body
  n=int(self.headers.get('Content-Length','0'))
  if n<0 or n>9*1024*1024:fail('الطلب كبير جدًا',413)
  try:
   data=json.loads(self.rfile.read(n) or b'{}')
   if not isinstance(data,dict):fail('بيانات JSON يجب أن تكون كائنًا')
   self._parsed_body=data
   return data
  except Exception:fail('بيانات JSON غير صالحة')
 def actor(self,c):
  cookie=SimpleCookie();cookie.load(self.headers.get('Cookie',''))
  token=cookie.get('ehkam_session');value=token.value if token else ''
  uid=security.actor(c,value)
  if not uid:fail('يرجى تسجيل الدخول',401)
  u=c.execute('SELECT id,username,role,employee_id,company_id,must_change_password,disabled FROM users WHERE id=?',(uid,)).fetchone()
  if not u:fail('جلسة غير صالحة',401)
  if not self.account_active(c,u):fail('الحساب غير نشط؛ راجع الإدارة',403)
  return u
 def account_active(self,c,u):
  if c.execute('SELECT disabled FROM users WHERE id=?',(u['id'],)).fetchone()[0]:return False
  if u['role']=='admin':return True
  return bool(c.execute('SELECT 1 FROM employees e JOIN companies co ON co.id=e.company_id WHERE e.id=? AND e.company_id=? AND e.archived=0 AND co.archived=0',(u['employee_id'],u['company_id'])).fetchone())
 def account_routes(self,c,u,path,method):
  if path=='/api/security/mfa' and method=='POST':
   import base64
   d=self.input();full=c.execute('SELECT password_hash FROM users WHERE id=?',(u['id'],)).fetchone()
   security.attempt(c,u['username'],security.peer(self),sys.modules[__name__])
   if not verifypw(str(d.get('password','')),full[0]):fail('كلمة المرور الحالية غير صحيحة',403)
   action=d.get('action');row=c.execute('SELECT * FROM user_mfa WHERE user_id=?',(u['id'],)).fetchone()
   if action=='enroll':
    if row and row['enabled']:fail('المصادقة مفعلة بالفعل؛ عطّلها أولًا')
    secret=base64.b32encode(secrets.token_bytes(20)).decode()
    c.execute('INSERT INTO user_mfa(user_id,secret) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET secret=excluded.secret',(u['id'],secret));c.commit();self.write({'secret':secret,'issuer':'EHKAM','account':u['username']});return True
   if action=='confirm':
    if not row or row['enabled']:fail('ابدأ إعداد المصادقة أولًا')
    step=int(time.time())//30
    if not any(hmac.compare_digest(security.totp(row['secret'],n),str(d.get('otp',''))) for n in (step-1,step,step+1)):fail('رمز المصادقة غير صالح')
    c.execute('UPDATE user_mfa SET enabled=1,last_step=? WHERE user_id=?',(step,u['id']))
   elif action=='disable':
    security.verify_mfa(c,u['id'],d.get('otp',''),sys.modules[__name__]);c.execute('DELETE FROM user_mfa WHERE user_id=?',(u['id'],))
   else:fail('إجراء غير صالح')
   record(c,u,'mfa_'+action,'user',u['id']);c.commit();self.write({'ok':True});return True
  if re.fullmatch('/api/admin/accounts/[0-9]+',path) and method=='POST':
   if permissions.profile(c,u)!='owner':fail('إدارة الحسابات للإدارة العامة فقط',403)
   d=self.input();security.attempt(c,u['username'],security.peer(self),sys.modules[__name__]);full=c.execute('SELECT password_hash FROM users WHERE id=?',(u['id'],)).fetchone()
   if not verifypw(str(d.get('password','')),full[0]):fail('كلمة المرور الحالية غير صحيحة',403)
   target=int(path.rsplit('/',1)[1]);row=c.execute("SELECT * FROM users WHERE id=? AND role='admin'",(target,)).fetchone()
   if not row or target==u['id']:fail('لا يمكن تعديل صلاحيات حسابك من هذا الإجراء')
   chosen=d.get('profile');scope=int(d['company_scope']) if d.get('company_scope') else None;disabled=d.get('disabled',False)
   if chosen not in ('owner','hr','payroll','auditor') or not isinstance(disabled,bool):fail('دور أو حالة غير صالحة')
   if chosen=='owner' and scope:fail('الإدارة العامة ليست مقيدة بشركة؛ اختر دورًا آخر')
   if scope and not c.execute('SELECT 1 FROM companies WHERE id=? AND archived=0',(scope,)).fetchone():fail('شركة غير متاحة')
   c.execute('DELETE FROM admin_profiles WHERE user_id=?',(target,))
   if chosen!='owner':c.execute('INSERT INTO admin_profiles(user_id,profile,company_id) VALUES(?,?,?)',(target,chosen,scope))
   c.execute('UPDATE users SET disabled=? WHERE id=?',(int(disabled),target));c.execute('DELETE FROM auth_sessions WHERE user_id=?',(target,))
   record(c,u,'admin_access_update','user',target,{'profile':chosen,'company_scope':scope,'disabled':disabled});c.commit();self.write({'ok':True});return True
  if path=='/api/admin/accounts':
   if u['role']!='admin':fail('غير مصرح',403)
   if method=='GET':
    self.write([dict(r) for r in c.execute("SELECT u.id,u.username,u.display_name,u.role,u.disabled,COALESCE(p.profile,'owner') profile,p.company_id company_scope FROM users u LEFT JOIN admin_profiles p ON p.user_id=u.id WHERE u.role='admin'")]);return True
   d=self.input();security.attempt(c,u['username'],security.peer(self),sys.modules[__name__]);full=c.execute('SELECT password_hash FROM users WHERE id=?',(u['id'],)).fetchone()
   if not verifypw(str(d.get('password','')),full[0]):fail('كلمة المرور الحالية غير صحيحة',403)
   username=str(d.get('username','')).strip();password=str(d.get('new_password',''));name=str(d.get('display_name','')).strip()
   if not re.fullmatch(r'[A-Za-z0-9_.-]{3,64}',username) or not 16<=len(password)<=128 or not name:fail('اسم دخول 3–64 حرفًا وكلمة مرور 16–128 حرفًا واسم معروض مطلوبون')
   if c.execute('SELECT 1 FROM users WHERE username=?',(username,)).fetchone():fail('اسم الدخول مستخدم')
   chosen=d.get('profile','owner')
   if chosen not in ('owner','hr','payroll','auditor'):fail('دور إداري غير صالح')
   if chosen=='owner' and d.get('company_scope'):fail('الإدارة العامة ليست مقيدة بشركة')
   cur=c.execute("INSERT INTO users(username,password_hash,role,display_name) VALUES(?,?,'admin',?)",(username,hashpw(password),name[:150]))
   if chosen!='owner':
    scope=int(d['company_scope']) if d.get('company_scope') else None
    if scope and not c.execute('SELECT 1 FROM companies WHERE id=? AND archived=0',(scope,)).fetchone():fail('شركة غير متاحة')
    c.execute('INSERT INTO admin_profiles(user_id,profile,company_id) VALUES(?,?,?)',(cur.lastrowid,chosen,scope))
   record(c,u,'admin_account_create','user',cur.lastrowid);c.commit();self.write({'id':cur.lastrowid},201);return True
  if path=='/api/security/sessions' and method=='GET':
   self.write([dict(r) for r in c.execute('SELECT substr(token_hash,1,16) id,created_at,last_seen,expires_at,user_agent FROM auth_sessions WHERE user_id=? ORDER BY created_at DESC',(u['id'],))]);return True
  if path=='/api/security/revoke' and method=='POST':
   d=self.input();full=c.execute('SELECT password_hash FROM users WHERE id=?',(u['id'],)).fetchone()
   if not verifypw(str(d.get('password','')),full[0]):fail('كلمة المرور الحالية غير صحيحة',403)
   c.execute('DELETE FROM auth_sessions WHERE user_id=?',(u['id'],));record(c,u,'sessions_revoked','user',u['id']);c.commit();self.write({'ok':True});return True
  if path=='/api/security/employee-passwords':
   if permissions.profile(c,u) not in ('owner','hr','manager'):fail('تغيير كلمات الموظفين للإدارة والمشرف المختص فقط',403)
   if method=='GET':
    allowed={e['id'] for e in visible_employees(c,u) if e['id']!=u['employee_id']}
    return self.write([dict(r) for r in c.execute("SELECT a.id user_id,a.username,e.id employee_id,e.name employee_name,e.company_id FROM users a JOIN employees e ON e.id=a.employee_id WHERE a.role='employee' AND a.disabled=0 ORDER BY e.name") if r['employee_id'] in allowed]) or True
   if method!='POST':fail('طريقة غير مسموحة',405)
   d=self.input();security.attempt(c,u['username'],security.peer(self),sys.modules[__name__])
   full=c.execute('SELECT password_hash FROM users WHERE id=?',(u['id'],)).fetchone()
   if not verifypw(str(d.get('current_password','')),full[0]):fail('كلمة مرور المسؤول غير صحيحة',403)
   target=c.execute("SELECT * FROM users WHERE id=? AND role='employee'",(int(d.get('user_id',0)),)).fetchone()
   if not target:fail('حساب الموظف غير موجود',404)
   employee=user_allowed_emp(c,u,target['employee_id'])
   if employee['id']==u['employee_id'] or employee['archived'] or not self.account_active(c,target):fail('الحساب غير متاح لهذا الإجراء',403)
   password=str(d.get('new_password',''))
   if not 12<=len(password)<=128:fail('كلمة المرور الجديدة 12–128 حرفًا')
   c.execute('UPDATE users SET password_hash=?,must_change_password=0 WHERE id=?',(hashpw(password),target['id']))
   c.execute('DELETE FROM auth_sessions WHERE user_id=?',(target['id'],))
   c.execute('UPDATE password_resets SET used=1 WHERE user_id=?',(target['id'],))
   record(c,u,'employee_password_change','employee',employee['id'],{'user_id':target['id']})
   c.commit();self.write({'ok':True});return True
  if path=='/api/security/password' and method=='POST':
   if u['role']=='employee':fail('تغيير كلمة مرور الموظف بواسطة الإدارة فقط',403)
   d=self.input();full=c.execute('SELECT password_hash FROM users WHERE id=?',(u['id'],)).fetchone()
   security.attempt(c,u['username'],security.peer(self),sys.modules[__name__])
   password=str(d.get('new_password',''))
   if not verifypw(str(d.get('current_password','')),full[0]):fail('كلمة المرور الحالية غير صحيحة',403)
   if not 12<=len(password)<=128 or password==str(d.get('current_password','')):fail('كلمة المرور الجديدة 12–128 حرفًا ومختلفة عن الحالية')
   c.execute('UPDATE users SET password_hash=?,must_change_password=0 WHERE id=?',(hashpw(password),u['id']));c.execute('DELETE FROM auth_sessions WHERE user_id=?',(u['id'],));record(c,u,'password_change','user',u['id']);c.commit();self.write({'ok':True});return True
  return False
 def do_GET(self):self.process('GET')
 def do_POST(self):self.process('POST')
 def process(self,method):
  if hasattr(self,'_parsed_body'):del self._parsed_body
  path=urlparse(self.path).path
  if method=='GET' and path=='/api/health':
   try:
    health=sqlite3.connect(DB.resolve().as_uri()+'?mode=ro',uri=True)
    try:
     ready=health.execute("SELECT 1 FROM schema_migrations WHERE version='requested-branches-v1'").fetchone()
     health.execute('SELECT 1 FROM users LIMIT 1').fetchone()
     expected=os.environ.get('EHKAM_DB_ID','').strip()
     if expected:
      identity=health.execute('SELECT database_id FROM production_identity WHERE id=1').fetchone()
      if not identity or identity[0]!=expected:raise RuntimeError('database_identity_mismatch')
    finally:health.close()
    if not ready:raise RuntimeError('schema_not_ready')
    revision=os.environ.get('RAILWAY_GIT_COMMIT_SHA','')
    return self.write({'status':'ok','release':requested_batch.VERSION,'database':'ok','commit':revision if re.fullmatch(r'[0-9a-fA-F]{40}',revision) else None})
   except (sqlite3.Error,RuntimeError):return self.write({'status':'unavailable','release':requested_batch.VERSION},503)
  if method=='GET' and path.startswith('/apply/'):
   return self.write((BASE/'apply.html').read_bytes(),ctype='text/html')
  if method=='GET' and path in ('/leaflet.js','/leaflet.css'):
   return self.write((BASE/path.lstrip('/')).read_bytes(),ctype='text/css' if path.endswith('.css') else 'text/javascript')
  if method=='GET' and path=='/company-first.js':return self.write((BASE/'company-first.js').read_bytes(),ctype='text/javascript')
  if method=='GET' and path=='/requested-ui.js':return self.write((BASE/'requested-ui.js').read_bytes(),ctype='text/javascript')
  if method=='GET' and path in ('/','/index.html'):
   return self.write((BASE/'index.html').read_bytes(),ctype='text/html')
  if method=='GET' and path=='/style.css':return self.write((BASE/'style.css').read_bytes(),ctype='text/css')
  if method=='GET' and path=='/assistant-ui.js':return self.write((BASE/'assistant-ui.js').read_bytes(),ctype='text/javascript')
  if method=='GET' and path=='/i18n.js':return self.write((BASE/'i18n.js').read_bytes(),ctype='text/javascript')
  if method=='GET' and path=='/workflow-ui.js':return self.write((BASE/'workflow-ui.js').read_bytes(),ctype='text/javascript')
  if method=='GET' and path=='/professional-ui.js':return self.write((BASE/'professional-ui.js').read_bytes(),ctype='text/javascript')
  if method=='GET' and path=='/hr-ui.js':return self.write((BASE/'hr-ui.js').read_bytes(),ctype='text/javascript')
  if method=='GET' and path=='/ux-controls.js':return self.write((BASE/'ux-controls.js').read_bytes(),ctype='text/javascript')
  if method=='GET' and path=='/intro.js':return self.write((BASE/'intro.js').read_bytes(),ctype='text/javascript')
  if method=='GET' and path=='/app.js':return self.write((BASE/'app.js').read_bytes(),ctype='text/javascript')
  if method=='GET' and path in ('/favicon.ico','/favicon.png','/ehkam-icon.png','/ehkam-logo-full.png'):
   return self.write((BASE/path.lstrip('/')).read_bytes(),ctype='image/x-icon' if path.endswith('.ico') else 'image/png')
  c=conn()
  try:
   if method=='POST':security.same_origin(self,sys.modules[__name__])
   if requested_batch.public(self,c,path,method,sys.modules[__name__]):return
   if method=='POST' and path=='/api/login':
    d=self.input();username=str(d.get('username','')).strip()[:128];security.attempt(c,username,security.peer(self),sys.modules[__name__]);u=c.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
    if not u or not verifypw(str(d.get('password',''))[:128],u['password_hash']) or u['role']!=d.get('account_role'):
     c.execute('INSERT INTO audit(user_id,username,action,entity,info,timestamp) VALUES(NULL,?,"login_failed","user",?,?)',(username,json.dumps({'peer_hash':security.digest(security.peer(self))}),now()));c.commit();fail('بيانات الدخول أو نوع الحساب غير صحيحة',401)
    if not self.account_active(c,u):fail('الحساب غير نشط؛ راجع الإدارة',403)
    security.verify_mfa(c,u['id'],d.get('otp',''),sys.modules[__name__])
    token=security.create(c,u['id'],self.headers.get('User-Agent',''))
    record(c,u,'login','user',u['id']);c.commit()
    return self.write({'ok':True},headers={'Set-Cookie':'ehkam_session='+token+'; HttpOnly; SameSite=Strict; Path=/'+('; Secure' if persistent_database_required() else '')})
   if method=='POST' and path=='/api/password/request':
    d=self.input();username=str(d.get('username','')).strip();requested=str(d.get('channel') or 'email').strip().lower();ip=security.peer(self)
    if requested not in ('email','whatsapp','dual'):requested='email'
    if not username:fail('اسم المستخدم مطلوب')
    security.attempt(c,'reset:'+username,security.peer(self),sys.modules[__name__])
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
    valid_phone=country == '+966' and bool(re.fullmatch(r'5[0-9]{8}',local))
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
    d=self.input();username=str(d.get('username','')).strip();ip=security.peer(self);current=int(time.time())
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
    if len(password)<12 or len(password)>128:fail('كلمة المرور الجديدة 12 حرفًا على الأقل',400)
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
    c.execute('UPDATE users SET password_hash=?,must_change_password=0 WHERE id=?',(hashpw(password),u['id']))
    with LOCK:
     for token,uid in list(SESSIONS.items()):
      if uid==u['id']:SESSIONS.pop(token,None)
    c.execute('DELETE FROM auth_sessions WHERE user_id=?',(u['id'],))
    record(c,u,'password_reset','user',u['id']);c.commit();return self.write({'ok':True})
   u=self.actor(c)
   permissions.guard(self,c,u,path,method,sys.modules[__name__])
   if u['must_change_password'] and u['role']!='employee' and path not in ('/api/me','/api/security/password','/api/security/sessions','/api/logout'):fail('غيّر كلمة المرور المؤقتة من أمان الحساب أولًا',403)
   requested_batch.probation_sync(c,sys.modules[__name__]);c.commit()
   if requested_batch.handle(self,c,u,path,method,sys.modules[__name__]):return
   if ai_assistant.handle(self,c,u,path,method,sys.modules[__name__]):return
   if ui_workflows.handle(self,c,u,path,method,sys.modules[__name__]):return
   if directory_tools.handle(self,c,u,path,method,sys.modules[__name__]):return
   if talent.handle(self,c,u,path,method,sys.modules[__name__]):return
   if workforce.handle(self,c,u,path,method,sys.modules[__name__]):return
   if payroll.handle(self,c,u,path,method,sys.modules[__name__]):return
   if self.account_routes(c,u,path,method):return
   if hr.handle(self,c,u,path,method,sys.modules[__name__]):return
   if path=='/api/supervisors' and method=='GET':
    if u['role']!='admin':fail('غير مصرح',403)
    return self.write(permissions.filter_rows(c,u,c.execute("SELECT e.id,e.name,e.company_id,u.id user_id,u.username FROM employees e JOIN users u ON u.employee_id=e.id AND u.role='manager' AND u.company_id=e.company_id WHERE e.archived=0")))
   if path=='/api/me' and method=='GET':return self.write({**dict(u),'must_change_password':bool(u['must_change_password'] and u['role']!='employee'),'timezone':'Asia/Riyadh','currency':'SAR','profile':permissions.profile(c,u),'company_scope':permissions.scope_company(c,u)})
   if path=='/api/logout' and method=='POST':
    record(c,u,'logout','user',u['id']);c.commit()
    cookie=SimpleCookie();cookie.load(self.headers.get('Cookie',''));v=cookie.get('ehkam_session')
    if v:
     with LOCK:SESSIONS.pop(v.value,None)
     c.execute('DELETE FROM auth_sessions WHERE token_hash=?',(security.digest(v.value),));c.commit()
    return self.write({'ok':True},headers={'Set-Cookie':'ehkam_session=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/'})
   if path=='/api/dashboard' and method=='GET':
    es=visible_employees(c,u);rr=visible_requests(c,u,False)
    co=c.execute('SELECT COUNT(*) FROM companies WHERE archived=0 AND (? IS NULL OR id=?)',(permissions.scope_company(c,u),permissions.scope_company(c,u))).fetchone()[0] if u['role']=='admin' else 1
    present=sum(bool(c.execute('SELECT 1 FROM attendance WHERE employee_id=? AND day=? AND check_in IS NOT NULL',(e['id'],TODAY().isoformat())).fetchone()) for e in es)
    expiring=sum(bool(e['contract_end'] and TODAY().isoformat()<=e['contract_end']<=(TODAY()+datetime.timedelta(days=60)).isoformat()) for e in es)
    return self.write({'companies':co,'employees':len(es),'requests':len(rr),'pending':sum(r['status'] in ('new','review') for r in rr),'recent':rr[:5],'attendance_today':present,'contracts_expiring':expiring,'request_states':{status:sum(r['status']==status for r in rr) for status in ('new','review','approved','rejected')},'as_of':TODAY().isoformat()})
   if path=='/api/companies' and method=='GET':
    if u['role']!='admin':fail('غير مصرح',403)
    return self.write(permissions.filter_rows(c,u,c.execute('''SELECT c.*,(SELECT COUNT(*) FROM employees e WHERE e.company_id=c.id AND e.archived=0) staff_count,(SELECT COUNT(*) FROM requests r WHERE r.company_id=c.id AND r.status IN ('new','review')) pending_count FROM companies c ORDER BY c.archived,c.id DESC'''),key='id'))
   if (path=='/api/companies' or re.fullmatch(r'/api/companies/[0-9]+',path)) and method=='POST':
    if u['role']!='admin':fail('غير مصرح',403)
    d=self.input();name=str(d.get('name','')).strip()
    if not name or len(name)>150:fail('اسم الشركة مطلوب ولا يتجاوز 150 حرفًا')
    previous=c.execute('SELECT * FROM companies WHERE id=?',(int(path.rsplit('/',1)[1]),)).fetchone() if path!='/api/companies' else None
    gps=str(d.get('gps_url',previous['gps_url'] if previous else '') or '').strip()
    try:
     if gps:
      if previous and gps==previous['gps_url'] and previous['latitude'] is not None and previous['longitude'] is not None:
       d.update(latitude=previous['latitude'],longitude=previous['longitude'])
      else:
       latitude,longitude=gps_location.resolve(gps);d.update(latitude=latitude,longitude=longitude)
     elif previous:
      d.setdefault('latitude',previous['latitude']);d.setdefault('longitude',previous['longitude'])
    except (gps_location.LocationError,ValueError) as exc:fail(str(exc))
    try:
     lat=float(d.get('latitude')) if d.get('latitude') not in (None,'') else None
     lon=float(d.get('longitude')) if d.get('longitude') not in (None,'') else None
     radius=float(d.get('geofence_radius') or (previous['geofence_radius'] if previous else 100))
    except (TypeError,ValueError):fail('إحداثيات الموقع أو نطاق البصمة غير صحيحة')
    if (lat is None)!=(lon is None):fail('يجب تحديد إحداثيات الموقع معًا')
    if lat is not None and not (-90<=lat<=90 and -180<=lon<=180):fail('إحداثيات الموقع خارج النطاق الصحيح')
    if not (20<=radius<=1000):fail('نطاق البصمة يجب أن يكون بين 20 و1000 متر')
    if path!='/api/companies':
     cid=int(path.rsplit('/',1)[1]);changed=c.execute('UPDATE companies SET name=?,name_en=?,contract_end=?,city=?,address=?,latitude=?,longitude=?,geofence_radius=?,gps_url=? WHERE id=?',(name,d.get('name_en',''),normalize_date(d.get('contract_end')),d.get('city',''),d.get('address',''),lat,lon,radius,gps,cid))
     if not changed.rowcount:fail('الشركة غير موجودة',404)
     record(c,u,'update','company',cid,name);c.commit();return self.write({'ok':True})
    cursor=c.execute('INSERT INTO companies(name,name_en,contract_end,city,address,latitude,longitude,geofence_radius,gps_url) VALUES(?,?,?,?,?,?,?,?,?)',(name,d.get('name_en',''),normalize_date(d.get('contract_end')),d.get('city',''),d.get('address',''),lat,lon,radius,gps))
    if d.get('employee_prefix'):
     prefix=str(d['employee_prefix']).strip()
     if not re.fullmatch(r'[A-Za-zأ-ي]{1,12}[-_]?',prefix) or c.execute('SELECT 1 FROM companies WHERE employee_prefix=?',(prefix,)).fetchone():fail('حروف الشركة غير صالحة أو مستخدمة')
     c.execute('UPDATE companies SET employee_prefix=? WHERE id=?',(prefix,cursor.lastrowid))
    record(c,u,'create','company',cursor.lastrowid,name);c.commit();return self.write({'ok':True},201)
   if path=='/api/employees' and method=='GET':return self.write(visible_employees(c,u,True))
   if path=='/api/employees' and method=='POST':
    if u['role']!='admin':fail('غير مصرح',403)
    d=normalize_employee_fields(self.input());co=int(d.get('company_id',0));c.execute('BEGIN IMMEDIATE');exists=c.execute('SELECT id FROM companies WHERE id=? AND archived=0',(co,)).fetchone()
    if not exists:fail('الشركة غير موجودة')
    if not str(d.get('name','')).strip():fail('الاسم مطلوب')
    d['employee_no']=requested_batch.next_number(c,co)
    country,phone,email=clean_contacts(d)
    username=str(d.get('username') or d['name']).strip();newpw=str(d.get('login_password',''))
    if not username or len(username)>128 or any(ord(ch)<32 for ch in username) or len(newpw)<10 or len(newpw)>128:fail('اسم موظف صالح لا يتجاوز 128 حرفًا وكلمة مرور 10–128 حرفًا مطلوبان')
    if c.execute('SELECT 1 FROM users WHERE username=?',(username,)).fetchone():fail('اسم المستخدم مستخدم بالفعل')
    extra_employee=['passport','nationality','birth_date','qualification','specialization','branch','iban','bank_name']
    keys=extra_employee+['phone_country','phone_local','email','photo','company_id','name','name_en','employee_no','identity_no','job','department','manager_id','status','hire_date','contract_start','contract_end','basic','housing','transport','other','annual_entitlement','carryover','workweek','work_start','work_end','payment_method']
    values=[(country if k=='phone_country' else phone if k=='phone_local' else email if k=='email' else d.get(k)) for k in keys];defaults={'name_en':'','identity_no':'','job':'','department':'','manager_id':None,'status':'نشط','basic':0,'housing':0,'transport':0,'other':0,'annual_entitlement':21,'carryover':0,'workweek':'0,1,2,3,5,6','work_start':'09:00','work_end':'17:00','payment_method':'bank'}
    values=[defaults.get(k) if v is None else v for k,v in zip(keys,values)]
    if d.get('manager_id'):
     m=c.execute("SELECT e.company_id FROM employees e JOIN users u ON u.employee_id=e.id AND u.role='manager' AND u.company_id=e.company_id WHERE e.id=?",(d['manager_id'],)).fetchone()
     if not m or m['company_id']!=co:fail('المشرف يجب أن يتبع نفس الشركة')
    try:cur=c.execute('INSERT INTO employees('+','.join(keys)+') VALUES('+','.join('?' for _ in keys)+')',values)
    except sqlite3.IntegrityError:fail('رقم الموظف مستخدم داخل هذه الشركة')
    if d.get('login_role','employee') not in ('employee','manager'):fail('نوع الحساب غير صالح')
    c.execute('INSERT INTO users(username,password_hash,role,employee_id,company_id) VALUES(?,?,?,?,?)',(username,hashpw(newpw),d.get('login_role','employee'),cur.lastrowid,co))
    if d.get('asset_name') or d.get('asset_serial'):
     asset_name=str(d.get('asset_name','')).strip();asset_serial=str(d.get('asset_serial','')).strip()
     if not asset_name or not asset_serial or max(len(asset_name),len(asset_serial))>200:fail('اسم العهدة ورقمها مطلوبان معًا')
     if c.execute('SELECT 1 FROM employee_assets WHERE serial=? AND returned_on IS NULL',(asset_serial,)).fetchone():fail('العهدة مسلمة بالفعل')
     c.execute("INSERT INTO employee_assets(employee_id,name,serial,issued_on,note,created_by,receipt_status) VALUES(?,?,?,?,?,?,'pending')",(cur.lastrowid,asset_name,asset_serial,TODAY().isoformat(),'',u['id']))
    hr.ensure_contract(c,c.execute('SELECT * FROM employees WHERE id=?',(cur.lastrowid,)).fetchone())
    if d.get('candidate_id'):
     requested_batch.start_probation(c,u,c.execute('SELECT * FROM employees WHERE id=?',(cur.lastrowid,)).fetchone(),int(d['candidate_id']),normalize_date(d.get('probation_start')) or TODAY().isoformat(),sys.modules[__name__])
    c.execute('UPDATE users SET display_name=? WHERE employee_id=?',(d['name'],cur.lastrowid))
    record(c,u,'create','employee',cur.lastrowid,d['name']);c.commit();return self.write({'id':cur.lastrowid,'employee_no':d['employee_no']},201)
   if path.startswith('/api/employees/') and method=='POST':
    if u['role']!='admin':fail('غير مصرح',403)
    eid=int(path.rsplit('/',1)[1]);e=user_allowed_emp(c,u,eid);d=normalize_employee_fields(self.input())
    permitted={'passport','nationality','birth_date','qualification','specialization','branch','iban','bank_name','phone_country','phone_local','email','schedule_id','site_id','photo','name','name_en','identity_no','job','department','manager_id','status','hire_date','contract_start','contract_end','basic','housing','transport','other','annual_entitlement','carryover','workweek','work_start','work_end','payment_method'}
    if any(key in d for key in ('phone_country','phone_local','email')):
     country,phone,email=clean_contacts({**dict(e),**d,'_legacy_phone':e['phone_local']});d.update(phone_country=country,phone_local=phone,email=email)
    for field,table in [('schedule_id','schedules'),('site_id','work_sites')]:
     if field not in d:continue
     d[field]=int(d[field]) if d[field] else None
     if d[field]:
      item=c.execute('SELECT * FROM '+table+' WHERE id=? AND company_id=?',(d[field],e['company_id'])).fetchone()
      if not item:fail('الجدول أو الموقع لا يتبع شركة الموظف')
      if field=='schedule_id':d.update(workweek=item['workweek'],work_start=item['work_start'],work_end=item['work_end'])
    changes={k:v for k,v in d.items() if k in permitted}
    start=changes.get('contract_start',e['contract_start']);end=changes.get('contract_end',e['contract_end'])
    if start and end and end<start:fail('نهاية العقد قبل بدايته')
    if not changes:fail('لا توجد بيانات للتعديل')
    if 'photo' in changes:
     photo=changes['photo']
     if photo is not None and (not isinstance(photo,str) or len(photo)>300000 or not photo.startswith(('data:image/png;base64,','data:image/jpeg;base64,','data:image/webp;base64,'))):fail('صيغة الصورة غير مقبولة أو حجمها كبير')
    if 'manager_id' in changes and str(changes['manager_id'] or '')!=str(e['manager_id'] or '') and c.execute("SELECT 1 FROM supervisor_tasks WHERE employee_id=? AND status!='approved'",(eid,)).fetchone():fail('اعتمد مهام المشرف المعلقة قبل تغيير المشرف المباشر',409)
    if changes.get('manager_id'):
     m=c.execute("SELECT e.company_id FROM employees e JOIN users u ON u.employee_id=e.id AND u.role='manager' AND u.company_id=e.company_id WHERE e.id=?",(changes['manager_id'],)).fetchone()
     if int(changes['manager_id'])==eid or not m or m['company_id']!=e['company_id']:fail('المشرف يجب أن يتبع نفس الشركة')
    if any(key in changes for key in ('contract_start','contract_end')) and c.execute('SELECT 1 FROM contracts WHERE employee_id=? AND closed_at IS NOT NULL',(eid,)).fetchone():fail('استخدم تجديد العقد للحفاظ على الأرصدة السابقة')
    c.execute('BEGIN IMMEDIATE')
    payroll.assert_open(c,eid,TODAY().isoformat(),TODAY().isoformat(),sys.modules[__name__])
    if any(k in changes for k in ('contract_start','contract_end','carryover')) and c.execute('SELECT 1 FROM requests WHERE employee_id=? LIMIT 1',(eid,)).fetchone():
     if any(changes.get(k,e[k])!=e[k] for k in ('contract_start','contract_end','carryover')):fail('العقد والرصيد الافتتاحي لهما حركات؛ استخدم التجديد أو تسوية موثقة بدل تعديلهما')
    c.execute('UPDATE employees SET '+','.join(k+'=?' for k in changes)+' WHERE id=?',list(changes.values())+[eid])
    fresh=c.execute('SELECT * FROM employees WHERE id=?',(eid,)).fetchone()
    workforce.save_terms(c,e,fresh,normalize_date(d.get('effective_on')) or TODAY().isoformat(),u['id'],d.get('change_reason') or 'تعديل إداري موثق',sys.modules[__name__])
    c.execute('UPDATE contracts SET start_date=?,end_date=?,entitlement=?,opening=? WHERE employee_id=? AND closed_at IS NULL',(fresh['contract_start'],fresh['contract_end'],fresh['annual_entitlement'],fresh['carryover'],eid))
    c.execute('UPDATE users SET display_name=? WHERE employee_id=?',(fresh['name'],eid))
    record(c,u,'update','employee',eid,{k:{'before':('صورة سابقة' if e[k] else 'بدون صورة') if k=='photo' else e[k] if k in e.keys() else None,'after':'تم تحديث الصورة' if k=='photo' else v} for k,v in changes.items()});c.commit();return self.write({'ok':True})
   if re.fullmatch(r'/api/leave/[0-9]+',path) and method=='GET':
    eid=int(path.rsplit('/',1)[1]);e=user_allowed_emp(c,u,eid);return self.write(leave(c,e))
   if path=='/api/advance/preview' and method=='GET':
    qs=parse_qs(urlparse(self.path).query);eid=int(qs.get('employee_id',[u['employee_id'] or 0])[0]);e=user_allowed_emp(c,u,eid)
    if u['role']=='manager' and e['id']!=u['employee_id']:fail('البيانات المالية خاصة بالموظف والإدارة',403)
    end=hr.employment_end(c,e);months=remaining_months(end,TODAY());gross=salary(e);monthly_limit=round(gross*.35,2)
    committed,monthly_committed=payroll.commitments(c,e['id'])
    return self.write({'gross_salary':gross,'remaining_months':months,'max_monthly_installment':monthly_limit,'max_advance':max(0,round(monthly_limit*months-committed/100,2)),'contract_end':end,'committed_amount':committed/100,'monthly_committed':monthly_committed/100,'repayment_plan_required':True,'policy_note':'Existing repository policy; not a statutory entitlement determination'})
   if path.startswith('/api/attachments/') and method=='GET':
    aid=int(path.rsplit('/',1)[1])
    attachment=c.execute('SELECT * FROM request_attachments WHERE id=?',(aid,)).fetchone()
    if not attachment:fail('المرفق غير موجود',404)
    r=c.execute('SELECT * FROM requests WHERE id=?',(attachment['request_id'],)).fetchone()
    request_access(c,u,r)
    if not visible_requests(c,u,request_id=r['id'],aggregate=True):fail('غير مصرح بعرض المرفق',403)
    preview=parse_qs(urlparse(self.path).query).get('preview')==['1'] and attachment['mime'] in ('image/png','image/jpeg')
    record(c,u,'attachment_view' if preview else 'attachment_download','request',r['id'],{'attachment_id':aid});c.commit()
    filename=urllib.parse.quote(attachment['name'],safe='')
    return self.write(attachment['content'],ctype=attachment['mime'],headers={'Content-Disposition':("inline" if preview else "attachment")+"; filename*=UTF-8''"+filename,'X-Content-Type-Options':'nosniff'})
   if re.fullmatch(r'/api/requests/[0-9]+',path) and method=='GET':
    rid=int(path.rsplit('/',1)[1]);row=c.execute('SELECT * FROM requests WHERE id=?',(rid,)).fetchone()
    if not row:fail('الطلب غير موجود',404)
    request_access(c,u,row);record(c,u,'view','request',rid);c.commit()
    return self.write(visible_requests(c,u,request_id=rid)[0])
   if path=='/api/requests' and method=='GET':
    summary=parse_qs(urlparse(self.path).query).get('summary',['0'])[0]=='1'
    rows=visible_requests(c,u,not summary)
    if summary:
     for row in rows:
      for key in ('details','notes','decision_note'):row.pop(key,None)
    return self.write(rows)
   if path=='/api/requests' and method=='POST':
    d=self.input();c.execute('BEGIN IMMEDIATE');eid=int(d.get('employee_id',u['employee_id'] or 0));e=user_allowed_emp(c,u,eid)
    if u['role']=='manager' and d.get('type') not in ('delegation','hr_contact'):fail('المشرف ينشئ الانتداب أو مراسلته الشخصية للموارد البشرية فقط',403)
    if u['role']=='employee' and eid!=u['employee_id']:fail('غير مصرح',403)
    if u['role']!='manager' and d.get('type')=='delegation':fail('الانتداب يبدأ من المشرف فقط',403)
    if d.get('type')=='hr_contact' and u['role']!='admin' and eid!=u['employee_id']:fail('يمكنك إرسال مراسلتك الشخصية فقط',403)
    attachments=validate_attachments(d.get('attachments',[]))
    if len(str(d.get('notes','')))>4000:fail('الملاحظات لا تتجاوز 4000 حرف')
    typ=d.get('type');allowed={'annual_leave','sick_leave','unpaid_leave','advance','permission','punch_fix','hr_contact','investigation','termination','delegation','open'}
    if typ not in allowed:fail('نوع الطلب غير صالح')
    ended=hr.employment_end(c,e)
    if ended and ended<TODAY().isoformat() and typ not in ('punch_fix','hr_contact','open'):fail('انتهت خدمة الموظف؛ لا يمكن إنشاء طلب تشغيلي جديد')
    detail=hr.details(c,u,e,d,sys.modules[__name__])
    a=normalize_date(d.get('start_date'));b=normalize_date(d.get('end_date'));days=0;amount=0;inst=0
    if a and b and b<a:fail('تاريخ النهاية قبل البداية')
    if typ=='advance':
     try:amount=float(d.get('amount') or 0);inst=float(d.get('installment') or 0)
     except (ValueError,TypeError):fail('مبلغ السلفة والقسط غير صحيحين')
     if not math.isfinite(amount) or not math.isfinite(inst):fail('مبلغ السلفة والقسط غير صحيحين')
    if typ in ('annual_leave','sick_leave','unpaid_leave','permission','delegation'):
     if not a or not b:fail('تاريخ البداية والنهاية مطلوبان')
     days=business_days(e,a,b,c)
    if typ=='annual_leave':
     if detail['last_day_fraction']!=1:
      if business_days(e,b,b,c)!=1:fail('آخر يوم جزئي يجب أن يكون يوم عمل')
      days=days-1+detail['last_day_fraction']
     bal=leave(c,e)
     if days<=0 or days>bal['available']:fail('عدد أيام الإجازة يتجاوز الرصيد المتاح')
    if typ=='advance':
     limit=round(salary(e)*.35*remaining_months(hr.employment_end(c,e),TODAY()),2)
     if amount<=0 or amount>limit:fail('طلب السلفة يتجاوز الحد المستحق: '+str(limit)+' ريال')
     payroll.validate_advance(c,e,amount,inst,sys.modules[__name__])
     if inst<0 or inst>salary(e)*.35:fail('القسط الشهري يتجاوز 35% من الراتب')
     if inst and inst*remaining_months(hr.employment_end(c,e),TODAY())<amount:fail('القسط لا يسدد السلفة قبل انتهاء العقد')
     minimum=c.execute('SELECT advance_min_days FROM companies WHERE id=?',(e['company_id'],)).fetchone()[0]
     if not e['contract_end'] or (datetime.date.fromisoformat(e['contract_end'])-TODAY()).days<minimum:fail('مدة العقد المتبقية أقل من الحد المسموح في سياسة الشركة')
    if typ=='sick_leave' and not d.get('notes') and not attachments:fail('أضف ملاحظات أو مستندًا طبيًا')
    if typ=='punch_fix' and not a:fail('تاريخ التصحيح مطلوب')
    if typ=='delegation' and not workforce.can_manage(c,u,e,sys.modules[__name__]):fail('يمكن للمشرف إصدار انتداب لموظفيه فقط')
    ts=now();cur=c.execute('''INSERT INTO requests(company_id,employee_id,type,start_date,end_date,days,amount,installment,notes,created_by,approver_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',(e['company_id'],eid,typ,a,b,days,amount,inst,str(d.get('notes','')),u['id'],e['manager_id'],ts,ts))
    c.execute('UPDATE requests SET details=?,contract_id=? WHERE id=?',(json.dumps(detail,ensure_ascii=False),hr.ensure_contract(c,e),cur.lastrowid))
    hr.notify(c,c.execute('SELECT * FROM requests WHERE id=?',(cur.lastrowid,)).fetchone(),sys.modules[__name__],'تم تسجيل طلب جديد',u)
    for name,mime,content in attachments:c.execute('INSERT INTO request_attachments(request_id,name,mime,content) VALUES(?,?,?,?)',(cur.lastrowid,name,mime,content))
    record(c,u,'create','request',cur.lastrowid,typ);c.commit();return self.write({'id':cur.lastrowid,'number':f'EH-{cur.lastrowid:06d}'},201)
   if re.fullmatch(r'/api/requests/[0-9]+',path) and method=='POST':
    rid=int(path.rsplit('/',1)[1]);d=self.input();c.execute('BEGIN IMMEDIATE');r=c.execute('SELECT * FROM requests WHERE id=?',(rid,)).fetchone()
    if not r:fail('الطلب غير موجود',404)
    request_access(c,u,r);e=user_allowed_emp(c,u,r['employee_id']);action=d.get('status')
    note=str(d.get('note') or '').strip()
    decision_files=validate_attachments(d.get('attachments',[]))
    if r['type']=='investigation' and u['role']!='admin':fail('معالجة التحقيق من اختصاص الإدارة',403)
    if r['type']=='annual_leave' and r['contract_id']!=hr.ensure_contract(c,e):fail('لا يمكن تغيير طلب تابع لعقد سبق ترحيله')
    if action in ('rejected','declined') and not note:fail('سبب الرفض مطلوب')
    if action=='withdrawn':
     if r['created_by']!=u['id'] or r['status'] not in ('new','review'):fail('يمكنك سحب طلبك المعلق فقط',403)
     action='cancelled'
    elif action=='cancelled' and u['role']!='admin':fail('إلغاء الطلب المعتمد متاح للإدارة فقط',403)
    if action not in ('review','approved','rejected','cancelled','closed','accepted','declined'):fail('حالة غير صحيحة')
    if r['status'] not in ('new','review') and not (action=='cancelled' and r['status']=='approved'):fail('لا يمكن تغيير هذه الحالة')
    if action in ('review','approved','rejected','closed'):
     if u['role']=='employee' or u['employee_id']==e['id']:fail('لا يمكن اعتماد طلبك',403)
     if u['role']=='manager' and not workforce.can_manage(c,u,e,sys.modules[__name__]):fail('ليس من موظفيك',403)
     if r['type']=='delegation':fail('الانتداب ينتظر قبول أو رفض الموظف')
    if action in ('accepted','declined'):
     if r['type']!='delegation' or u['role']!='employee' or u['employee_id']!=r['employee_id']:fail('فقط الموظف المعني ينتدبه المدير',403)
     if action=='declined' and not str(d.get('note','')).strip():fail('اذكر سبب رفض الانتداب',400)
    if action=='cancelled' and r['status']=='approved':
     if r['type']=='advance' and c.execute('SELECT 1 FROM loans WHERE request_id=?',(rid,)).fetchone():fail('لا يمكن إلغاء سلفة مصروفة؛ سجل التسوية المالية',409)
     if r['type']=='punch_fix':fail('التصحيح المعتمد لا يلغى؛ يلزم تصحيح لاحق يحفظ السجل',409)
     if r['type']=='termination' and c.execute('SELECT 1 FROM settlements WHERE request_id=?',(rid,)).fetchone():fail('الإنهاء له تسوية مالية؛ يلزم تسوية عكسية منفصلة',409)
    if r['start_date'] and r['end_date']:payroll.assert_open(c,e['id'],r['start_date'],r['end_date'],sys.modules[__name__])
    if action=='approved' and r['type']=='advance':
     if r['installment']<=0:fail('خطة سداد السلفة غير محددة؛ لا يمكن الاعتماد أو الصرف قبل مراجعتها',409)
     payroll.validate_advance(c,e,r['amount'],r['installment'],sys.modules[__name__],rid)
    if r['type']=='termination':
     last=json.loads(r['details']).get('last_work_day')
     if last:payroll.assert_open(c,e['id'],last,e['contract_end'] or '9999-12-31',sys.modules[__name__])
    if (action=='approved' and r['type'] in ('annual_leave','sick_leave','unpaid_leave')) or (action=='accepted' and r['type']=='delegation'):workforce.overlap(c,e['id'],r['start_date'],r['end_date'],rid,sys.modules[__name__])
    if action=='approved' and r['type']=='annual_leave':
     if c.execute("SELECT 1 FROM requests WHERE employee_id=? AND id!=? AND status='approved' AND type IN ('annual_leave','sick_leave','unpaid_leave') AND start_date<=? AND end_date>=?",(e['id'],rid,r['end_date'],r['start_date'])).fetchone():fail('توجد إجازة معتمدة متداخلة')
     if r['days']>leave(c,e)['available']:fail('الرصيد غير كافٍ عند الاعتماد')
    decision_status=action
    approval=c.execute('SELECT approval_mode FROM companies WHERE id=?',(e['company_id'],)).fetchone()[0]
    if r['type'] not in ('delegation','hr_contact','investigation','sick_leave','advance','termination') and action in ('approved','rejected','review'):
     if approval=='admin_only' and u['role']!='admin':fail('سياسة الشركة تقصر الاعتماد على الإدارة',403)
     if approval=='supervisor_then_admin' and action=='approved' and e['manager_id']:
      if u['role']=='manager':
       if c.execute("SELECT 1 FROM request_decisions WHERE request_id=? AND status='manager_approved'",(rid,)).fetchone():fail('تمت موافقة المشرف بالفعل؛ الطلب ينتظر الإدارة')
       decision_status='manager_approved';action='review'
      elif not c.execute("SELECT 1 FROM request_decisions d JOIN users u ON u.id=d.user_id WHERE d.request_id=? AND d.status='manager_approved' AND COALESCE(d.effective_manager_id,u.employee_id)=?",(rid,e['manager_id'])).fetchone():fail('ينتظر الطلب موافقة المشرف أولًا')
    stamp=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
    c.execute('UPDATE requests SET status=?,decision_note=?,updated_at=? WHERE id=?',(action,note,stamp,rid))
    c.execute('INSERT INTO request_decisions(request_id,user_id,username,status,note,decided_at,effective_manager_id) VALUES(?,?,?,?,?,?,?)',(rid,u['id'],u['username'],decision_status,note,stamp,e['manager_id'] if u['role']=='manager' else None))
    decision_id=c.execute('SELECT last_insert_rowid()').fetchone()[0]
    if action=='approved' and r['type']=='unpaid_leave':
     detail=json.loads(r['details']);deductions=[]
     first=datetime.date.fromisoformat(r['start_date']);last=datetime.date.fromisoformat(r['end_date'])
     for offset in range((last-first).days+1):
      day=first+datetime.timedelta(days=offset)
      if business_days(e,day.isoformat(),day.isoformat(),c):
       rate,basis=hr.daily_rate(c,e,day,sys.modules[__name__]);deductions.append({'day':day.isoformat(),'amount':round(rate,4),'basis':basis})
     detail['unpaid_salary_deductions']=deductions;c.execute('UPDATE requests SET details=? WHERE id=?',(json.dumps(detail),rid))
    for name,mime,content in decision_files:c.execute('INSERT INTO request_attachments(request_id,name,mime,content,decision_id) VALUES(?,?,?,?,?)',(rid,name,mime,content,decision_id))
    if action=='cancelled' and r['type']=='termination':c.execute("UPDATE employees SET status='نشط' WHERE id=? AND status='فترة إشعار'",(e['id'],))
    if action=='approved' and r['type']=='termination':c.execute("UPDATE employees SET status='فترة إشعار' WHERE id=?",(e['id'],))
    if action=='approved' and r['type']=='punch_fix' and r['start_date']:
     correction=json.loads(r['details']).get('correction_type','both')
     check_in=e['work_start'] if correction in ('check_in','both') else None
     check_out=e['work_end'] if correction in ('check_out','both') else None
     if check_out:requested_batch.checkout_guard(c,e['id'],r['start_date'],sys.modules[__name__])
     c.execute("INSERT INTO attendance(employee_id,day,check_in,check_out,source) VALUES(?,?,?,?,?) ON CONFLICT(employee_id,day) DO UPDATE SET check_in=COALESCE(attendance.check_in,excluded.check_in),check_out=COALESCE(attendance.check_out,excluded.check_out),source=excluded.source",(e['id'],r['start_date'],check_in,check_out,'approved_punch_fix'))
     if check_in:
      c.execute("UPDATE absences SET status='reversed',note=COALESCE(note,'') || ' | approved punch correction' WHERE employee_id=? AND day=? AND status='approved'",(e['id'],r['start_date']))
      record(c,u,'absence_reversed_by_correction','employee',e['id'],r['start_date'])
    if r['type'] in ('annual_leave','sick_leave','unpaid_leave') and action in ('approved','cancelled'):
     hr.reconcile_absences(c,e['id'],r['start_date'],r['end_date'],sys.modules[__name__])
    hr.notify(c,r,sys.modules[__name__],'تم تحديث الطلب: '+decision_status,u)
    record(c,u,'status_'+decision_status,'request',rid,d.get('note',''));c.commit();return self.write({'ok':True})
   if path=='/api/attendance' and method=='GET':
    qs=parse_qs(urlparse(self.path).query);eid=int(qs.get('employee_id',[u['employee_id'] or 0])[0]);user_allowed_emp(c,u,eid)
    q='SELECT * FROM attendance WHERE employee_id=?';p=[eid]
    if qs.get('month'):
     month=qs['month'][0]
     if not re.fullmatch(r'\d{4}-\d{2}',month):fail('الشهر غير صالح')
     datetime.date.fromisoformat(month+'-01');q+=' AND substr(day,1,7)=?';p.append(month)
    if qs.get('day'):q+=' AND day=?';p.append(normalize_date(qs['day'][0]))
    if qs.get('from'):q+=' AND day>=?';p.append(normalize_date(qs['from'][0]))
    if qs.get('to'):q+=' AND day<=?';p.append(normalize_date(qs['to'][0]))
    return self.write([dict(x) for x in c.execute(q+' ORDER BY day DESC',p)])
   if path=='/api/attendance' and method=='POST':
    c.execute('BEGIN IMMEDIATE')
    if u['role']!='employee':fail('تسجيل البصمة من حساب الموظف فقط',403)
    if not u['employee_id']:fail('حساب الموظف غير مرتبط بملف وظيفي',403)
    d=self.input()
    try:
     lat=float(d.get('latitude'));lon=float(d.get('longitude'));accuracy=float(d.get('accuracy',9999))
    except (TypeError,ValueError):fail('تعذر قراءة الموقع الجغرافي من الجهاز')
    if not (-90<=lat<=90 and -180<=lon<=180):fail('إحداثيات الموقع غير صحيحة')
    if not math.isfinite(accuracy) or accuracy<0 or accuracy>100:fail('دقة الموقع ضعيفة. شغّل GPS وحاول مرة أخرى في مكان مفتوح')
    e=c.execute('SELECT e.*,co.latitude company_lat,co.longitude company_lon,co.geofence_radius,co.address company_address FROM employees e JOIN companies co ON co.id=e.company_id WHERE e.id=? AND e.archived=0 AND co.archived=0',(u['employee_id'],)).fetchone()
    if not e:fail('ملف الموظف أو الشركة غير متاح',403)
    ended=hr.employment_end(c,e)
    if ended and ended<TODAY().isoformat():fail('انتهت خدمة الموظف ولا يمكن تسجيل بصمة جديدة',403)
    if e['site_id']:
     site=c.execute('SELECT * FROM work_sites WHERE id=? AND company_id=?',(e['site_id'],e['company_id'])).fetchone()
     if not site:fail('موقع العمل غير صالح')
     e=dict(e);e.update(company_lat=site['latitude'],company_lon=site['longitude'],geofence_radius=site['radius'])
    if e['company_lat'] is None or e['company_lon'] is None:fail('لم يحدد المدير موقع الشركة بعد')
    r=6371000.0
    p1=math.radians(lat);p2=math.radians(float(e['company_lat']))
    dp=math.radians(float(e['company_lat'])-lat);dl=math.radians(float(e['company_lon'])-lon)
    a=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    distance=2*r*math.asin(min(1,math.sqrt(a)))
    radius=float(e['geofence_radius'] or 100)
    if distance>radius:fail('أنت خارج نطاق البصمة المسموح. المسافة التقريبية '+str(round(distance))+' متر؛ النطاق '+str(round(radius))+' متر.')
    local_now=datetime.datetime.now(workforce.zone(c,e['company_id']))
    day=local_now.date().isoformat();stamp=local_now.strftime('%H:%M:%S')
    explicit=d.get('action')
    if explicit not in (None,'check_in','check_out'):fail('إجراء بصمة غير صالح')
    yesterday=(local_now.date()-datetime.timedelta(days=1)).isoformat()
    prior=c.execute('SELECT * FROM attendance WHERE employee_id=? AND day=? AND check_out IS NULL',(e['id'],yesterday)).fetchone()
    if prior:
     old=json.loads(prior['schedule_snapshot']) if prior['schedule_snapshot'] else dict(e)
     if old['work_end']<=old['work_start'] and local_now.time()<datetime.time.fromisoformat(old['work_start']):day=yesterday
    payroll.assert_open(c,e['id'],day,day,sys.modules[__name__])
    row=c.execute('SELECT * FROM attendance WHERE employee_id=? AND day=?',(e['id'],day)).fetchone()
    if not row:
     if explicit=='check_out':fail('لا توجد بصمة حضور مفتوحة')
     if e['work_end']<=e['work_start'] and local_now.time()<datetime.time.fromisoformat(e['work_end']):fail('الوردية الليلية السابقة بلا حضور؛ اطلب تصحيح بصمة')
     c.execute('INSERT INTO attendance(employee_id,day,check_in,source,check_in_at,schedule_snapshot) VALUES(?,?,?,?,?,?)',(e['id'],day,stamp,'gps_geofence',now(),json.dumps(workforce.snapshot(e))))
     action='check_in';message='تم تسجيل الحضور بنجاح'
    elif not row['check_out']:
     if explicit=='check_in':fail('الحضور مسجل بالفعل')
     requested_batch.checkout_guard(c,e['id'],day,sys.modules[__name__])
     if row['check_in_at'] and (datetime.datetime.now(datetime.timezone.utc)-datetime.datetime.fromisoformat(row['check_in_at'])).total_seconds()<60:fail('راجع البصمة؛ لا يمكن تسجيل انصراف خلال دقيقة من الحضور')
     c.execute('UPDATE attendance SET check_out=?,source=?,check_out_at=? WHERE id=?',(stamp,'gps_geofence',now(),row['id']))
     action='check_out';message='تم تسجيل الانصراف بنجاح'
    else:fail('تم تسجيل الحضور والانصراف لهذا اليوم بالفعل')
    record(c,u,action,'attendance',e['id'],'distance_m='+str(round(distance,1))+'; accuracy_m='+str(round(accuracy,1)))
    c.commit();return self.write({'ok':True,'action':action,'message':message,'distance_m':round(distance,1),'radius_m':radius,'time':stamp})
   if path=='/api/archive/history' and method=='GET':
    if u['role']!='admin':fail('غير مصرح',403)
    scope=permissions.scope_company(c,u)
    rows=c.execute("SELECT a.username,COALESCE(NULLIF(u.display_name,''),a.username) display_name,a.action,a.entity,a.entity_id,a.timestamp FROM audit a LEFT JOIN users u ON u.id=a.user_id WHERE a.action IN ('archive','restore') AND (? IS NULL OR a.company_id=?) ORDER BY a.id DESC LIMIT 100",(scope,scope)).fetchall()
    return self.write([dict(row) for row in rows])
   if path=='/api/archive' and method=='POST':
    if u['role']!='admin':fail('غير مصرح',403)
    d=self.input();target=d.get('target');eid=int(d.get('id',0));value=int(bool(d.get('archived')))
    if d.get('confirm') is not True or not d.get('password'):fail('التأكيد وكلمة المرور مطلوبان')
    full=c.execute('SELECT password_hash FROM users WHERE id=?',(u['id'],)).fetchone()
    security.attempt(c,u['username'],security.peer(self),sys.modules[__name__])
    if not verifypw(d['password'],full['password_hash']):fail('كلمة المرور غير صحيحة',403)
    table={'company':'companies','employee':'employees'}.get(target)
    if not table:fail('النوع غير صحيح')
    if value:
     asset_sql='SELECT a.name FROM employee_assets a JOIN employees e ON e.id=a.employee_id WHERE a.returned_on IS NULL AND '+('e.id=?' if target=='employee' else 'e.company_id=?')
     pending_assets=c.execute(asset_sql,(eid,)).fetchall()
     if pending_assets:fail('لا يمكن الأرشفة قبل إرجاع العهد: '+'، '.join(a[0] for a in pending_assets),409)
    cur=c.execute('UPDATE '+table+' SET archived=? WHERE id=?',(value,eid))
    if not cur.rowcount:fail('العنصر غير موجود',404)
    record(c,u,'archive' if value else 'restore',target,eid,'confirmed');c.commit();return self.write({'ok':True,'message':'تمت الأرشفة بنجاح' if value else 'تم الاسترجاع بنجاح'})
   if path=='/api/audit' and method=='GET':
    if u['role']!='admin':fail('غير مصرح',403)
    filters=parse_qs(urlparse(self.path).query);where=[];params=[]
    for key,column in [('action','a.action'),('entity','a.entity')]:
     if filters.get(key):where.append(column+'=?');params.append(filters[key][0])
    if filters.get('from'):where.append('substr(a.timestamp,1,10)>=?');params.append(normalize_date(filters['from'][0]))
    if filters.get('to'):where.append('substr(a.timestamp,1,10)<=?');params.append(normalize_date(filters['to'][0]))
    scope=permissions.scope_company(c,u)
    if scope is not None:
     where.append('a.company_id=?');params.append(scope)
    limit=max(1,min(200,int(filters.get('limit',[200])[0])));offset=max(0,int(filters.get('offset',[0])[0]))
    query='SELECT a.*,u.display_name current_name,u.role current_role FROM audit a LEFT JOIN users u ON u.id=a.user_id'+(' WHERE '+' AND '.join(where) if where else '')+' ORDER BY a.id DESC LIMIT ? OFFSET ?'
    rows=[]
    for item in c.execute(query,params+[limit,offset]):
     row=dict(item)
     try:
      detail=json.loads(row['info'])
      if isinstance(detail,dict) and detail.get('audit_version')==1:
       row.update({key:detail.get(key) for key in ('actor_name','actor_role','subject_name','employee_no','request_type')})
     except (ValueError,TypeError):pass
     rows.append(row)
    return self.write(rows)
   if path=='/api/report.csv' and method=='GET':
    rows=visible_employees(c,u);output=io.StringIO();w=csv.writer(output);w.writerow(['Company ID','Employee ID','Name','Employee No','Department','Status','Gross Salary'])
    for e in rows:w.writerow([hr.safe_cell(v) for v in [e['company_id'],e['id'],e['name'],e['employee_no'],e['department'],e['status'],e.get('gross_salary','محجوب')]])
    record(c,u,'report_export','user',u['id'],{'format':'csv','type':'employees'});c.commit()
    data='\ufeff'+output.getvalue();return self.write(data,ctype='text/csv',headers={'Content-Disposition':'attachment; filename="employees.csv"'})
   fail('المسار غير موجود',404)
  except AppError as x:self.write({'error':x.msg},x.status)
  except (ValueError,TypeError):self.write({'error':'قيمة غير صالحة في الطلب'},400)
  except Exception as x:
   print('Server error:',repr(x));self.write({'error':'خطأ داخلي؛ راجع مخرجات الخادم'},500)
  finally:c.close()

class ProductionHTTPServer(ThreadingHTTPServer):
 daemon_threads=True
 request_queue_size=64
 slots=threading.BoundedSemaphore(32)
 def process_request(self,request,client_address):
  if not self.slots.acquire(blocking=False):request.close();return
  try:super().process_request(request,client_address)
  except Exception:self.slots.release();raise
 def process_request_thread(self,request,client_address):
  try:super().process_request_thread(request,client_address)
  finally:self.slots.release()

if __name__=='__main__':
 validate_runtime_database();init();port=int(os.environ.get('PORT','8765'))
 print(f'EHKAM working local server: http://127.0.0.1:{port}');# Never print credentials into public hosting logs
 ProductionHTTPServer(('0.0.0.0',port),Handler).serve_forever()

"""EHKAM HR Portal - local functional prototype. Run: python server.py"""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import sqlite3, json, os, hashlib, hmac, secrets, datetime, csv, io, threading
from http.cookies import SimpleCookie
from pathlib import Path

BASE=Path(__file__).resolve().parent
DB=BASE/'ehkam_demo.sqlite3'
SESSIONS={}
LOCK=threading.Lock()
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
 c.commit();c.close()

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
  c=conn()
  try:
   if method=='POST' and path=='/api/login':
    d=self.input();u=c.execute('SELECT * FROM users WHERE username=?',(d.get('username',''),)).fetchone()
    if not u or not verifypw(d.get('password',''),u['password_hash']):fail('بيانات الدخول غير صحيحة',401)
    token=secrets.token_urlsafe(32)
    with LOCK:SESSIONS[token]=u['id']
    record(c,u,'login','user',u['id']);c.commit()
    return self.write({'ok':True},headers={'Set-Cookie':'ehkam_session='+token+'; HttpOnly; SameSite=Strict; Path=/'})
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
    keys=['company_id','name','name_en','employee_no','identity_no','job','department','manager_id','status','hire_date','contract_start','contract_end','basic','housing','transport','other','annual_entitlement','carryover','workweek','work_start','work_end','payment_method']
    values=[d.get(k) for k in keys];defaults={'name_en':'','identity_no':'','job':'','department':'','manager_id':None,'status':'نشط','basic':0,'housing':0,'transport':0,'other':0,'annual_entitlement':21,'carryover':0,'workweek':'0,1,2,3,5,6','work_start':'09:00','work_end':'17:00','payment_method':'bank'}
    values=[defaults.get(k) if v is None else v for k,v in zip(keys,values)]
    if d.get('manager_id'):
     m=c.execute('SELECT company_id FROM employees WHERE id=?',(d['manager_id'],)).fetchone()
     if not m or m['company_id']!=co:fail('المشرف يجب أن يتبع نفس الشركة')
    try:cur=c.execute('INSERT INTO employees('+','.join(keys)+') VALUES('+','.join('?' for _ in keys)+')',values)
    except sqlite3.IntegrityError:fail('رقم الموظف مستخدم داخل هذه الشركة')
    record(c,u,'create','employee',cur.lastrowid,d['name']);c.commit();return self.write({'id':cur.lastrowid},201)
   if path.startswith('/api/employees/') and method=='POST':
    if u['role']!='admin':fail('غير مصرح',403)
    eid=int(path.rsplit('/',1)[1]);e=user_allowed_emp(c,u,eid);d=self.input()
    permitted={'name','name_en','identity_no','job','department','manager_id','status','hire_date','contract_start','contract_end','basic','housing','transport','other','annual_entitlement','carryover','workweek','work_start','work_end','payment_method'}
    changes={k:v for k,v in d.items() if k in permitted}
    if not changes:fail('لا توجد بيانات للتعديل')
    if changes.get('manager_id'):
     m=c.execute('SELECT company_id FROM employees WHERE id=?',(changes['manager_id'],)).fetchone()
     if not m or m['company_id']!=e['company_id']:fail('المشرف يجب أن يتبع نفس الشركة')
    c.execute('UPDATE employees SET '+','.join(k+'=?' for k in changes)+' WHERE id=?',list(changes.values())+[eid]);record(c,u,'update','employee',eid,','.join(changes));c.commit();return self.write({'ok':True})
   if path.startswith('/api/leave/') and method=='GET':
    eid=int(path.rsplit('/',1)[1]);e=user_allowed_emp(c,u,eid);return self.write(leave(c,e))
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
     if r['type']!='delegation' or u['employee_id']!=r['employee_id']:fail('فقط الموظف المعني ينتدبه المدير',403)
    if action=='cancelled' and u['role']!='admin':fail('إلغاء الطلب المعتمد متاح للإدارة فقط في النموذج الأولي',403)
    if action=='approved' and r['type']=='annual_leave':
     if r['days']>leave(c,e)['available']:fail('الرصيد غير كافٍ عند الاعتماد')
    c.execute('UPDATE requests SET status=?,decision_note=?,updated_at=? WHERE id=?',(action,d.get('note',''),now(),rid))
    if action=='approved' and r['type']=='punch_fix' and r['start_date']:
     c.execute('''INSERT INTO attendance(employee_id,day,check_in,check_out,source) VALUES(?,?,?,?,?) ON CONFLICT(employee_id,day) DO UPDATE SET check_in=excluded.check_in,check_out=excluded.check_out,source=excluded.source''',(e['id'],r['start_date'],e['work_start'],e['work_end'],'approved_punch_fix'))
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
 print(f'EHKAM local prototype: http://127.0.0.1:{port}');print('Demo users: admin / Admin@123; manager / Manager@123; employee / Employee@123')
 ThreadingHTTPServer(('127.0.0.1',port),Handler).serve_forever()

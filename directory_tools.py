"""Validated CSV preview/import and printable employment letter templates."""
import csv
import datetime as dt
import hashlib
import html
import io
import json
import secrets
import re
import time
import permissions


def migrate(c):
    existing={r[1] for r in c.execute('PRAGMA table_info(users)')}
    if 'must_change_password' not in existing:c.execute('ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0')
    if 'disabled' not in existing:c.execute('ALTER TABLE users ADD COLUMN disabled INTEGER NOT NULL DEFAULT 0')
    c.execute('CREATE TABLE IF NOT EXISTS import_previews(token_hash TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),company_id INTEGER NOT NULL REFERENCES companies(id),payload TEXT NOT NULL,expires_at INTEGER NOT NULL,used INTEGER NOT NULL DEFAULT 0)')


def validate(c,text,company,core):
    allowed={'name','username','name_en','identity_no','department','job','email','phone_local','basic','housing','transport','other','contract_start','contract_end','hire_date'}
    reader=csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
    if not reader.fieldnames or not {'name','username'}.issubset(reader.fieldnames) or len(set(reader.fieldnames))!=len(reader.fieldnames) or set(reader.fieldnames)-allowed:core.fail('أعمدة CSV غير صالحة؛ name وusername مطلوبان. استخدم القالب المحدد')
    rows=[];errors=[];seen=set()
    for n,row in enumerate(reader,2):
        if n>201:core.fail('الحد الأقصى 200 موظف في الدفعة')
        try:
            if None in row or any(v is None for v in row.values()):core.fail('عدد الحقول لا يطابق الأعمدة')
            d={k:str(v).strip() for k,v in row.items()};username=d['username'];name=d['name']
            if not name or len(name)>150 or not re.fullmatch(r'[A-Za-z0-9_.-]{3,64}',username):core.fail('راجع الاسم واسم الدخول المستقل')
            if username in seen or c.execute('SELECT 1 FROM users WHERE username=?',(username,)).fetchone():core.fail('اسم الدخول مكرر')
            seen.add(username)
            for k in ('basic','housing','transport','other'):
                if not d.get(k):d[k]=0
            core.normalize_employee_fields(d)
            d['phone_country']='+966';d['phone_country'],d['phone_local'],d['email']=core.clean_contacts(d)
            d['company_id']=company
            rows.append(d)
        except (core.AppError,ValueError,TypeError) as exc:errors.append({'row':n,'error':exc.msg if isinstance(exc,core.AppError) else 'قيمة غير صالحة'})
    if not rows and not errors:core.fail('الملف فارغ')
    return rows,errors


def handle(h,c,u,path,method,core):
    from urllib.parse import parse_qs,urlparse
    def reply(data,status=200):h.write(data,status);return True
    if path=='/api/import/preview' and method=='POST':
        if u['role']!='admin':core.fail('استيراد الموظفين للإدارة فقط',403)
        d=h.input();company=int(d['company_id']);text=str(d.get('csv',''))
        if not c.execute('SELECT 1 FROM companies WHERE id=? AND archived=0',(company,)).fetchone() or len(text.encode())>1024*1024:core.fail('شركة غير متاحة أو ملف أكبر من 1 ميجابايت')
        rows,errors=validate(c,text,company,core);token=None
        if not errors:
            token=secrets.token_urlsafe(32);c.execute('DELETE FROM import_previews WHERE expires_at<?',(int(time.time()),));c.execute('INSERT INTO import_previews VALUES(?,?,?,?,?,0)',(hashlib.sha256(token.encode()).hexdigest(),u['id'],company,text,int(time.time())+600));c.commit()
        return reply({'rows':rows,'errors':errors,'token':token,'count':len(rows)})
    if path=='/api/import/commit' and method=='POST':
        if u['role']!='admin':core.fail('غير مصرح',403)
        d=h.input();c.execute('BEGIN IMMEDIATE');token=hashlib.sha256(str(d.get('token','')).encode()).hexdigest();preview=c.execute('SELECT * FROM import_previews WHERE token_hash=? AND user_id=? AND used=0 AND expires_at>?',(token,u['id'],int(time.time()))).fetchone()
        if not preview or not c.execute('SELECT 1 FROM companies WHERE id=? AND archived=0',(preview['company_id'],)).fetchone():core.fail('المعاينة منتهية أو مستخدمة أو تغيرت الشركة',409)
        permissions.company(c,u,preview['company_id'],core)
        rows,errors=validate(c,preview['payload'],preview['company_id'],core)
        if errors:return reply({'errors':errors},409)
        accounts=[]
        for d in rows:
            d['employee_no']=core.requested_batch.next_number(c,preview['company_id'])
            keys=[k for k in d if k!='username'];cur=c.execute('INSERT INTO employees('+','.join(keys)+') VALUES('+','.join('?' for _ in keys)+')',[d[k] for k in keys]);eid=cur.lastrowid
            password=secrets.token_urlsafe(18)
            c.execute('INSERT INTO users(username,password_hash,role,employee_id,company_id,display_name,must_change_password) VALUES(?,?,"employee",?,?,?,0)',(d['username'],core.hashpw(password),eid,d['company_id'],d['name']))
            core.hr.ensure_contract(c,c.execute('SELECT * FROM employees WHERE id=?',(eid,)).fetchone());core.record(c,u,'employee_import','employee',eid)
            accounts.append({'username':d['username'],'temporary_password':password,'employee_no':d['employee_no']})
        c.execute('UPDATE import_previews SET used=1,payload="" WHERE token_hash=?',(token,));c.commit();return reply({'created':len(accounts),'accounts':accounts,'notice':'اعرض بيانات الدخول مرة واحدة وسلمها لكل موظف عبر قناة آمنة. تغيير كلمة مرور الموظف يتم بواسطة الإدارة أو المشرف المختص.'},201)
    if path=='/api/employment-letter' and method=='GET':
        q=parse_qs(urlparse(h.path).query);eid=int(q.get('employee_id',[u['employee_id'] or 0])[0])
        if u['role']!='admin' and eid!=u['employee_id']:core.fail('الخطاب للموظف المعني والإدارة فقط',403)
        e=core.user_allowed_emp(c,u,eid);company=c.execute('SELECT name FROM companies WHERE id=?',(e['company_id'],)).fetchone()[0]
        body='<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><title>مسودة تعريف بالعمل</title><style>body{font:18px sans-serif;max-width:800px;margin:60px auto;line-height:2}p{white-space:pre-wrap}</style><h1>مسودة خطاب تعريف بالعمل والراتب</h1><p>'+html.escape(f"تشهد {company} بأن الموظف {e['name']}، الرقم الوظيفي {e['employee_no']}، يعمل بوظيفة {e['job']} منذ {e['hire_date'] or 'غير محدد'}.\nإجمالي الراتب المسجل {core.salary(e):,.2f} ريال سعودي.\nتاريخ الاستخراج {core.TODAY().isoformat()}.")+'</p><p>مسودة للمراجعة؛ لا تعتبر خطابًا رسميًا معتمدًا أو مختومًا. اعتماد الموارد البشرية: __________</p></html>'
        core.record(c,u,'employment_letter','employee',eid);c.commit();h.write(body,ctype='text/html');return True
    return False

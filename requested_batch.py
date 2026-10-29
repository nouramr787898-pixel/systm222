"""Additive, scoped October requirements. No production resets or external AI."""
import base64
import calendar
import datetime as dt
import math
import re
import secrets
from urllib.parse import parse_qs, urlparse
import permissions

VERSION='requested-branches-v1'

def migrate(c):
    additions={'companies':{'employee_prefix':"TEXT NOT NULL DEFAULT ''",'employee_sequence':'INTEGER NOT NULL DEFAULT 0'},'employee_assets':{'receipt_status':"TEXT NOT NULL DEFAULT 'legacy'",'receipt_at':'TEXT'},'vacancies':{'public_token':'TEXT','job_name':"TEXT NOT NULL DEFAULT ''",'retention_until':'TEXT'},'candidates':{'priority':'INTEGER NOT NULL DEFAULT 0','cv_name':'TEXT','cv_content':'BLOB','probation_start':'TEXT','probation_employee_id':'INTEGER REFERENCES employees(id)','probation_confirmed_at':'TEXT'}}
    for table,fields in additions.items():
        existing={r[1] for r in c.execute('PRAGMA table_info('+table+')')}
        for name,decl in fields.items():
            if name not in existing:c.execute('ALTER TABLE '+table+' ADD COLUMN '+name+' '+decl)
    c.execute('CREATE TABLE IF NOT EXISTS leave_accrual_cutover(contract_id INTEGER PRIMARY KEY REFERENCES contracts(id),effective_on TEXT NOT NULL,accrued REAL NOT NULL,history TEXT NOT NULL)')
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS company_employee_prefix ON companies(employee_prefix) WHERE employee_prefix!=''")
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS vacancy_public_token ON vacancies(public_token)')
    c.execute('''CREATE TABLE IF NOT EXISTS supervisor_tasks(id INTEGER PRIMARY KEY,employee_id INTEGER NOT NULL REFERENCES employees(id),supervisor_id INTEGER NOT NULL REFERENCES users(id),title TEXT NOT NULL,work_day TEXT NOT NULL,due_at TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'assigned' CHECK(status IN ('assigned','submitted','approved')),employee_note TEXT NOT NULL DEFAULT '',review_note TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,submitted_at TEXT,approved_at TEXT)''')
    c.execute('CREATE INDEX IF NOT EXISTS supervisor_tasks_pending ON supervisor_tasks(employee_id,work_day,status)')
    c.execute('INSERT OR IGNORE INTO schema_migrations VALUES(?)',(VERSION,))

def next_number(c,company):
    row=c.execute('SELECT employee_prefix FROM companies WHERE id=?',(company,)).fetchone()
    if row and row[0]:
        while True:
            n=c.execute('UPDATE companies SET employee_sequence=employee_sequence+1 WHERE id=? RETURNING employee_sequence',(company,)).fetchone()[0]
            number=row[0]+f'{n:04d}'
            if not c.execute('SELECT 1 FROM employees WHERE employee_no=?',(number,)).fetchone():return number
    while True:
        n=c.execute('UPDATE employee_number_sequence SET value=value+1 WHERE id=1 RETURNING value').fetchone()[0]
        number=f'EH-{n:04d}'
        if not c.execute('SELECT 1 FROM employees WHERE employee_no=?',(number,)).fetchone():return number

def checkout_guard(c,eid,day,core):
    rows=c.execute("SELECT title FROM supervisor_tasks WHERE employee_id=? AND work_day=? AND status!='approved'",(eid,day)).fetchall()
    if rows:core.fail('بانتظار اعتماد المشرف لإنجاز المهمة: '+'، '.join(r[0] for r in rows),409)

def probation_sync(c,core):
    for r in c.execute('SELECT a.*,e.archived,e.status employee_status FROM candidates a JOIN employees e ON e.id=a.probation_employee_id WHERE a.probation_start IS NOT NULL AND a.probation_confirmed_at IS NULL').fetchall():
        e=c.execute('SELECT * FROM employees WHERE id=?',(r['probation_employee_id'],)).fetchone()
        ended=core.hr.employment_end(c,e)
        if r['archived'] or r['employee_status']!='فترة تجربة' or (ended and ended<core.TODAY().isoformat()):continue
        if core.TODAY()>=dt.date.fromisoformat(r['probation_start'])+dt.timedelta(days=90):
            changed=c.execute("UPDATE employees SET status='نشط' WHERE id=? AND status='فترة تجربة' AND archived=0",(r['probation_employee_id'],))
            if not changed.rowcount:continue
            c.execute('UPDATE candidates SET probation_confirmed_at=? WHERE id=?',(core.now(),r['id']))
            core.record(c,{'id':None,'username':'system','role':'system'},'probation_auto_confirm','employee',r['probation_employee_id'],{'candidate_id':r['id'],'days':90})

def start_probation(c,u,e,cid,start,core):
    row=c.execute('SELECT a.*,v.company_id FROM candidates a JOIN vacancies v ON v.id=a.vacancy_id WHERE a.id=?',(cid,)).fetchone()
    if not row or row['company_id']!=e['company_id'] or row['probation_start'] or e['archived']:core.fail('راجع المرشح والشركة وملف الموظف')
    permissions.company(c,u,row['company_id'],core)
    if not start or (e['hire_date'] and start<e['hire_date']) or (e['contract_start'] and start<e['contract_start']) or (e['contract_end'] and start>e['contract_end']):core.fail('بداية التجربة يجب أن تكون داخل فترة عمل الموظف')
    if c.execute('SELECT 1 FROM candidates WHERE probation_employee_id=?',(e['id'],)).fetchone():core.fail('الموظف مرتبط بفترة تجربة بالفعل')
    c.execute("UPDATE candidates SET status='hired',probation_start=?,probation_employee_id=? WHERE id=?",(start,e['id'],cid))
    c.execute("UPDATE employees SET status='فترة تجربة' WHERE id=?",(e['id'],))
    core.record(c,u,'probation_start','employee',e['id'],{'candidate_id':cid,'days':90,'start':start})

def public(h,c,path,method,core):
    match=re.fullmatch('/api/recruitment/([A-Za-z0-9_-]{32,64})',path)
    if not match:return False
    v=c.execute('SELECT v.*,co.name company_name FROM vacancies v JOIN companies co ON co.id=v.company_id WHERE public_token=? AND co.archived=0',(match[1],)).fetchone()
    if not v:core.fail('رابط التقديم غير موجود',404)
    if not v['retention_until']:core.fail('التقديم غير منشور حتى تحديد مدة حفظ البيانات',409)
    if v['status']!='open' or v['closes_on']<core.TODAY().isoformat():core.fail('انتهى التقديم على هذه الوظيفة',410)
    if method=='GET':h.write({k:v[k] for k in ('title','job_name','company_name','closes_on','department','retention_until')});return True
    if method!='POST':core.fail('طريقة غير مسموحة',405)
    core.ai_assistant.reserve('recruit:'+core.security.peer(h),core)
    d=h.input();name=str(d.get('name','')).strip();email=str(d.get('email','')).strip().lower();phone=str(d.get('phone','')).strip()
    if not name or len(name)>150 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email) or not re.fullmatch(r'05\d{8}',phone):core.fail('راجع الاسم والبريد والجوال المكون من 10 أرقام')
    if d.get('consent') is not True:core.fail('الموافقة على حفظ البيانات لغرض التوظيف مطلوبة')
    cv=d.get('cv') or {};filename=str(cv.get('name',''))
    try:content=base64.b64decode(cv.get('data',''),validate=True)
    except (ValueError,TypeError):core.fail('ملف السيرة الذاتية غير صالح')
    valid=(filename.lower().endswith('.pdf') and content.startswith(b'%PDF-')) or (filename.lower().endswith('.docx') and content.startswith(b'PK\x03\x04')) or (filename.lower().endswith('.doc') and content.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'))
    if not valid or not 10<=len(content)<=2*1024*1024:core.fail('السيرة الذاتية PDF أو Word إلزامية، حتى 2 ميجابايت')
    c.execute('BEGIN IMMEDIATE')
    if c.execute('SELECT 1 FROM candidates WHERE vacancy_id=? AND lower(email)=?',(v['id'],email)).fetchone():core.fail('سبق التقديم على هذه الوظيفة',409)
    # Keep existing NOT NULL creator FK: creator is the vacancy owner; audit identifies public submission.
    c.execute('''INSERT INTO candidates(vacancy_id,name,email,phone,status,note,consent_on,retention_until,created_by,created_at,cv_name,cv_content) VALUES(?,?,?,?,'applied',?,?,?,?,?,?,?)''',(v['id'],name,email,'+966'+phone[1:],str(d.get('note',''))[:2000],core.TODAY().isoformat(),v['retention_until'],v['created_by'],core.now(),filename[:200],content))
    core.record(c,{'id':None,'username':'public-applicant','role':'public'},'public_application','company',v['company_id'],{'vacancy_id':v['id']});c.commit();h.write({'ok':True},201);return True

def handle(h,c,u,path,method,core):
    def reply(data,status=200):h.write(data,status);return True
    def admin():
        if permissions.profile(c,u) not in ('owner','hr'):core.fail('للإدارة والموارد البشرية فقط',403)
    if path=='/api/company-numbering' and method=='POST':
        admin();d=h.input();cid=int(d['company_id']);permissions.company(c,u,cid,core);prefix=str(d.get('prefix','')).strip()
        if not re.fullmatch(r'[A-Za-zأ-ي]{1,12}[-_]?',prefix):core.fail('حدد حروف الشركة، بحد أقصى 12 حرفًا، مع فاصل اختياري')
        if c.execute('SELECT 1 FROM companies WHERE employee_prefix=? AND id!=?',(prefix,cid)).fetchone():core.fail('الحروف مستخدمة لشركة أخرى')
        if not c.execute('UPDATE companies SET employee_prefix=? WHERE id=?',(prefix,cid)).rowcount:core.fail('الشركة غير موجودة',404)
        core.record(c,u,'company_numbering','company',cid,{'prefix':prefix,'existing_numbers_preserved':True});c.commit();return reply({'ok':True})
    if path=='/api/branch-preview' and method=='POST':
        admin();d=h.input()
        try:lat,lon=core.gps_location.resolve(str(d.get('gps','')))
        except (ValueError,core.gps_location.LocationError) as exc:core.fail(str(exc))
        return reply({'latitude':lat,'longitude':lon})
    if path=='/api/branch-sites':
        admin();q=parse_qs(urlparse(h.path).query);cid=int(q.get('company_id',[0])[0]);permissions.company(c,u,cid,core)
        if not c.execute('SELECT 1 FROM companies WHERE id=? AND archived=0',(cid,)).fetchone():core.fail('الشركة غير موجودة',404)
        if method=='POST':
            d=h.input();name=str(d.get('name','')).strip()
            try:
                gps=str(d.get('gps','')).strip();coords=re.fullmatch(r'(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)',gps)
                lat,lon=(float(coords[1]),float(coords[2])) if coords else core.gps_location.resolve(gps);radius=float(d.get('radius',0))
                if not -90<=lat<=90 or not -180<=lon<=180:raise ValueError('إحداثيات غير صالحة')
            except (ValueError,TypeError,core.gps_location.LocationError) as exc:core.fail(str(exc))
            if not name or len(name)>150 or not math.isfinite(radius) or not 1<=radius<=100000:core.fail('اسم الفرع ونطاق صحيح بالمتر مطلوبان (1–100000)')
            if d.get('id'):
                if not c.execute('UPDATE work_sites SET name=?,latitude=?,longitude=?,radius=? WHERE id=? AND company_id=?',(name,lat,lon,radius,int(d['id']),cid)).rowcount:core.fail('الفرع غير موجود في الشركة',404)
            else:c.execute('INSERT INTO work_sites(company_id,name,latitude,longitude,radius) VALUES(?,?,?,?,?)',(cid,name,lat,lon,radius))
            core.record(c,u,'branch_create','company',cid,{'name':name,'radius':radius});c.commit()
        return reply([dict(r) for r in c.execute('SELECT * FROM work_sites WHERE company_id=?',(cid,))])
    if path=='/api/employee-login' and method=='POST':
        admin();d=h.input();e=core.user_allowed_emp(c,u,int(d['employee_id']));name=str(d.get('username','')).strip()
        if not name or len(name)>128 or any(ord(ch)<32 for ch in name):core.fail('اسم مستخدم صالح مطلوب')
        target=c.execute("SELECT * FROM users WHERE employee_id=? AND role IN ('employee','manager')",(e['id'],)).fetchone()
        if not target:core.fail('حساب الموظف غير موجود',404)
        if c.execute('SELECT 1 FROM users WHERE username=? AND id!=?',(name,target['id'])).fetchone():core.fail('اسم المستخدم مستخدم بالفعل',409)
        c.execute('UPDATE users SET username=? WHERE id=?',(name,target['id']));c.execute('DELETE FROM auth_sessions WHERE user_id=?',(target['id'],));core.record(c,u,'username_change','employee',e['id'],{'before':target['username'],'after':name});c.commit();return reply({'ok':True})
    if path=='/api/employee-advances' and method=='GET':
        q=parse_qs(urlparse(h.path).query);e=core.user_allowed_emp(c,u,int(q.get('employee_id',[u['employee_id'] or 0])[0]))
        if u['role']=='manager' and e['id']!=u['employee_id']:core.fail('السلف بيانات مالية خاصة',403)
        return reply([{'amount':r['principal_cents']/100,'installment':r['installment_cents']/100,'remaining':core.payroll.remaining(c,r)/100} for r in c.execute('SELECT * FROM loans WHERE employee_id=?',(e['id'],))])
    if path=='/api/company-people' and method=='GET':
        q=parse_qs(urlparse(h.path).query);e=core.user_allowed_emp(c,u,int(q.get('employee_id',[u['employee_id'] or 0])[0]))
        return reply([dict(r) for r in c.execute('SELECT id,name,employee_no FROM employees WHERE company_id=? AND archived=0 ORDER BY name',(e['company_id'],))])
    if path=='/api/asset-receipts' and method=='GET':
        visible={e['id'] for e in core.visible_employees(c,u)}
        return reply([dict(r) for r in c.execute('SELECT a.*,e.name employee_name FROM employee_assets a JOIN employees e ON e.id=a.employee_id ORDER BY a.id DESC') if r['employee_id'] in visible])
    if re.fullmatch('/api/asset-receipts/[0-9]+',path) and method=='POST':
        c.execute('BEGIN IMMEDIATE');d=h.input();a=c.execute('SELECT * FROM employee_assets WHERE id=?',(int(path.rsplit('/',1)[1]),)).fetchone()
        if not a or a['employee_id']!=u['employee_id'] or u['role']!='employee':core.fail('إقرار الاستلام للموظف المعني فقط',403)
        if a['returned_on'] or a['receipt_status']!='pending':core.fail('سبق تسجيل القرار أو إرجاع العهدة',409)
        if d.get('status') not in ('accepted','rejected'):core.fail('اختر تأكيد أو رفض')
        c.execute('UPDATE employee_assets SET receipt_status=?,receipt_at=? WHERE id=?',(d['status'],core.now(),a['id']));core.record(c,u,'asset_receipt_'+d['status'],'employee',a['employee_id'],{'asset_id':a['id']});c.commit();return reply({'ok':True})
    if path=='/api/supervisor-tasks':
        if method=='POST':
            if u['role']!='manager':core.fail('إنشاء المهمة للمشرف فقط',403)
            c.execute('BEGIN IMMEDIATE')
            d=h.input();e=core.user_allowed_emp(c,u,int(d['employee_id']));day=core.normalize_date(d.get('work_day'));title=str(d.get('title','')).strip()
            if e['manager_id']!=u['employee_id'] or e['id']==u['employee_id'] or e['archived']:core.fail('المهمة لموظف تابع للمشرف مباشرة',403)
            if not day or day<core.TODAY().isoformat() or not title or len(title)>2000:core.fail('حدد اليوم والعنوان، دون تاريخ سابق')
            if c.execute('SELECT 1 FROM attendance WHERE employee_id=? AND day=? AND check_out IS NOT NULL',(e['id'],day)).fetchone():core.fail('سجل الموظف انصرافه بالفعل',409)
            terms=core.workforce.terms_at(c,e,day);due=dt.datetime.combine(dt.date.fromisoformat(day),dt.time.fromisoformat(terms['work_end']))
            if terms['work_end']<=terms['work_start']:due+=dt.timedelta(days=1)
            due=due.replace(tzinfo=core.workforce.zone(c,e['company_id'])).isoformat()
            cur=c.execute('INSERT INTO supervisor_tasks(employee_id,supervisor_id,title,work_day,due_at,created_at) VALUES(?,?,?,?,?,?)',(e['id'],u['id'],title,day,due,core.now()));core.record(c,u,'supervisor_task_assign','employee',e['id'],{'task_id':cur.lastrowid});c.commit();return reply({'id':cur.lastrowid},201)
        visible={e['id'] for e in core.visible_employees(c,u)}
        return reply([dict(r) for r in c.execute('SELECT t.*,e.name employee_name FROM supervisor_tasks t JOIN employees e ON e.id=t.employee_id ORDER BY t.work_day DESC,t.id') if r['employee_id'] in visible and (u['role']!='manager' or r['supervisor_id']==u['id'])])
    if re.fullmatch('/api/supervisor-tasks/[0-9]+',path) and method=='POST':
        c.execute('BEGIN IMMEDIATE');d=h.input();r=c.execute('SELECT * FROM supervisor_tasks WHERE id=?',(int(path.rsplit('/',1)[1]),)).fetchone()
        if not r:core.fail('المهمة غير موجودة',404)
        core.user_allowed_emp(c,u,r['employee_id']);action=d.get('action');note=str(d.get('note',''))[:2000]
        if action=='submit' and u['role']=='employee' and r['employee_id']==u['employee_id'] and r['status']=='assigned':
            c.execute("UPDATE supervisor_tasks SET status='submitted',employee_note=?,submitted_at=? WHERE id=?",(note,core.now(),r['id']))
        elif action in ('approve','return') and u['role']=='manager' and r['supervisor_id']==u['id'] and r['status']=='submitted':
            c.execute('UPDATE supervisor_tasks SET status=?,review_note=?,approved_at=? WHERE id=?',('approved' if action=='approve' else 'assigned',note,core.now() if action=='approve' else None,r['id']))
        else:core.fail('الإجراء غير متاح لهذا الحساب أو الحالة',403)
        core.record(c,u,'supervisor_task_'+action,'employee',r['employee_id'],{'task_id':r['id'],'note':note});c.commit();return reply({'ok':True})
    if re.fullmatch('/api/candidates/[0-9]+/(link|rank|cv|probation)',path):
        admin()
        if (path.endswith('/cv') and method!='GET') or (not path.endswith('/cv') and method!='POST'):core.fail('طريقة غير مسموحة',405)
        cid=int(path.split('/')[3]);r=c.execute('SELECT a.*,v.company_id FROM candidates a JOIN vacancies v ON v.id=a.vacancy_id WHERE a.id=?',(cid,)).fetchone()
        if not r:core.fail('المرشح غير موجود',404)
        permissions.company(c,u,r['company_id'],core)
        if path.endswith('/cv') and method=='GET':
            if not r['cv_content']:core.fail('لا توجد سيرة ذاتية',404)
            ext=r['cv_name'].rsplit('.',1)[-1].lower();mime={'pdf':'application/pdf','doc':'application/msword','docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}.get(ext,'application/octet-stream')
            h.write(r['cv_content'],ctype=mime,headers={'Content-Disposition':'attachment; filename="cv.'+ext+'"','X-Content-Type-Options':'nosniff'});return True
        d=h.input()
        if path.endswith('/rank'):
            priority=int(d.get('priority',0))
            if not 0<=priority<=1000000:core.fail('ترتيب غير صالح')
            c.execute('UPDATE candidates SET priority=priority+1 WHERE vacancy_id=? AND id!=? AND priority>=?',(r['vacancy_id'],cid,priority))
            c.execute('UPDATE candidates SET priority=? WHERE id=?',(priority,cid))
        elif path.endswith('/probation'):
            e=core.user_allowed_emp(c,u,int(d['employee_id']));start=core.normalize_date(d.get('start_date'))
            start_probation(c,u,e,cid,start,core)
        else:core.fail('إجراء غير صالح')
        core.record(c,u,'candidate_'+path.rsplit('/',1)[1],'company',r['company_id'],{'candidate_id':cid});probation_sync(c,core);c.commit();return reply({'ok':True})
    if re.fullmatch('/api/vacancies/[0-9]+/publish',path) and method=='POST':
        admin();vid=int(path.split('/')[3]);v=c.execute('SELECT * FROM vacancies WHERE id=?',(vid,)).fetchone()
        if not v:core.fail('الوظيفة غير موجودة',404)
        permissions.company(c,u,v['company_id'],core)
        d=h.input();retention=core.normalize_date(d.get('retention_until')) or v['retention_until']
        if not retention or retention<v['closes_on']:core.fail('حدد نهاية حفظ بيانات المتقدمين، بعد إغلاق التقديم')
        c.execute('UPDATE vacancies SET retention_until=? WHERE id=?',(retention,vid))
        token=v['public_token'] or secrets.token_urlsafe(24)
        c.execute('UPDATE vacancies SET public_token=? WHERE id=?',(token,vid));core.record(c,u,'vacancy_publish','company',v['company_id'],{'vacancy_id':vid});c.commit();return reply({'path':'/apply/'+token})
    return False


def capture_leave_cutover(c,core):
    """Freeze preexisting earned amounts; only future complete calendar months change."""
    import json
    if c.execute("SELECT 1 FROM schema_migrations WHERE version='leave-calendar-cutover-v1'").fetchone():return
    today=core.TODAY();effective=today.isoformat()
    for e in c.execute('SELECT * FROM employees').fetchall():
        contract=c.execute('SELECT id FROM contracts WHERE employee_id=? AND closed_at IS NULL',(e['id'],)).fetchone()
        if not contract:continue
        old=core.hr.legacy_leave(c,e,core)
        c.execute('INSERT OR IGNORE INTO leave_accrual_cutover VALUES(?,?,?,?)',(contract[0],effective,old['accrued']+old['expired_contract_accrual'],json.dumps(old['monthly_history'])))
    c.execute("INSERT INTO schema_migrations VALUES('leave-calendar-cutover-v1')")

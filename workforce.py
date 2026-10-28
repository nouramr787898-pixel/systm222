"""Additive HR history, document classification and operational workflows."""
import calendar
import datetime as dt
import json
import re
import permissions
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

VERSION = 'professional-v1'
TERM_FIELDS = ('company_id','manager_id','department','job','branch','basic','housing','transport','other','annual_entitlement','carryover','contract_start','contract_end','workweek','work_start','work_end','schedule_id','site_id','hire_date','status','payment_method')
PRIVATE_FIELDS = ('identity_no','passport','birth_date','iban','bank_name','phone_country','phone_local','email','basic','housing','transport','other','gross_salary','photo','carryover')


def migrate(c):
    fields = {
        'audit': {'company_id':'INTEGER'},
        'request_decisions': {'effective_manager_id':'INTEGER'},
        'companies': {'timezone': "TEXT NOT NULL DEFAULT 'Asia/Riyadh'"},
        'employee_files': {'document_type':"TEXT NOT NULL DEFAULT 'personal'", 'expires_on':'TEXT', 'version': 'INTEGER NOT NULL DEFAULT 1', 'supersedes_id':'INTEGER REFERENCES employee_files(id)'},
        'attendance': {'check_in_at':'TEXT','check_out_at':'TEXT','schedule_snapshot':'TEXT'},
    }
    for table, additions in fields.items():
        existing = {r[1] for r in c.execute('PRAGMA table_info('+table+')')}
        for name, declaration in additions.items():
            if name not in existing:c.execute('ALTER TABLE '+table+' ADD COLUMN '+name+' '+declaration)
    for sql in [
        'CREATE TABLE IF NOT EXISTS employee_terms(id INTEGER PRIMARY KEY,employee_id INTEGER NOT NULL REFERENCES employees(id),effective_on TEXT NOT NULL,snapshot TEXT NOT NULL,reason TEXT NOT NULL,changed_by INTEGER REFERENCES users(id),created_at TEXT NOT NULL,UNIQUE(employee_id,effective_on))',
        'CREATE TABLE IF NOT EXISTS lifecycle_tasks(id INTEGER PRIMARY KEY,employee_id INTEGER NOT NULL REFERENCES employees(id),kind TEXT NOT NULL,title TEXT NOT NULL,due_on TEXT NOT NULL,status TEXT NOT NULL DEFAULT "open",note TEXT NOT NULL DEFAULT "",assigned_to INTEGER REFERENCES users(id),created_by INTEGER NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,completed_at TEXT)',
        'CREATE TABLE IF NOT EXISTS employee_assets(id INTEGER PRIMARY KEY,employee_id INTEGER NOT NULL REFERENCES employees(id),name TEXT NOT NULL,serial TEXT NOT NULL,issued_on TEXT NOT NULL,returned_on TEXT,note TEXT NOT NULL,created_by INTEGER NOT NULL REFERENCES users(id),UNIQUE(serial,issued_on))',
        'CREATE TABLE IF NOT EXISTS company_policy_terms(id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),effective_on TEXT NOT NULL,snapshot TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(company_id,effective_on))',
        'CREATE TABLE IF NOT EXISTS approval_delegations(id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),source_manager_id INTEGER NOT NULL REFERENCES employees(id),target_manager_id INTEGER NOT NULL REFERENCES employees(id),starts_on TEXT NOT NULL,ends_on TEXT NOT NULL,note TEXT NOT NULL,revoked INTEGER NOT NULL DEFAULT 0,created_by INTEGER NOT NULL REFERENCES users(id),created_at TEXT NOT NULL)',
        'CREATE INDEX IF NOT EXISTS requests_employee_state ON requests(employee_id,type,status)',
        'CREATE INDEX IF NOT EXISTS requests_company_state ON requests(company_id,status,id)',
        'CREATE INDEX IF NOT EXISTS audit_search ON audit(entity,entity_id,id)',
        'CREATE INDEX IF NOT EXISTS files_expiry ON employee_files(expires_on,employee_id)',
        'CREATE INDEX IF NOT EXISTS tasks_due ON lifecycle_tasks(status,due_on)',
    ]:c.execute(sql)
    c.execute('INSERT OR IGNORE INTO schema_migrations VALUES(?)',(VERSION,))


def snapshot(e):
    return {k:e[k] for k in TERM_FIELDS}


def save_terms(c, before, after, effective, actor, reason, core):
    if not any(before[k] != after[k] for k in TERM_FIELDS):return
    if not str(reason).strip():core.fail('سبب تغيير البيانات الوظيفية مطلوب')
    if effective != core.TODAY().isoformat():core.fail('التغييرات الوظيفية تسري من اليوم؛ التسويات التاريخية تحتاج إجراء مستقل')
    c.execute('INSERT OR IGNORE INTO employee_terms(employee_id,effective_on,snapshot,reason,changed_by,created_at) VALUES(?,?,?,?,?,?)', (before['id'],'0001-01-01',json.dumps(snapshot(before),ensure_ascii=False),'legacy baseline; earlier history not verified',actor,core.now()))
    c.execute('INSERT INTO employee_terms(employee_id,effective_on,snapshot,reason,changed_by,created_at) VALUES(?,?,?,?,?,?) ON CONFLICT(employee_id,effective_on) DO UPDATE SET snapshot=excluded.snapshot,reason=excluded.reason,changed_by=excluded.changed_by,created_at=excluded.created_at', (before['id'],effective,json.dumps(snapshot(after),ensure_ascii=False),reason,actor,core.now()))


def terms_at(c,e,day):
    row = c.execute('SELECT snapshot FROM employee_terms WHERE employee_id=? AND effective_on<=? ORDER BY effective_on DESC LIMIT 1', (e['id'],str(day))).fetchone()
    result = dict(e)
    if row:result.update(json.loads(row[0]))
    return result


def redact(e,u):
    result=dict(e)
    if u['role']=='manager' and u['employee_id']!=e['id']:
        for key in PRIVATE_FIELDS:result.pop(key,None)
    return result


def zone(c,company):
    row=c.execute('SELECT timezone FROM companies WHERE id=?',(company,)).fetchone()
    return ZoneInfo(row[0] if row else 'Asia/Riyadh')


def overlap(c,eid,start,end,rid,core):
    if c.execute("SELECT 1 FROM requests WHERE employee_id=? AND id!=? AND start_date<=? AND end_date>=? AND ((type IN ('annual_leave','sick_leave','unpaid_leave') AND status='approved') OR (type='delegation' AND status='accepted'))",(eid,rid,end,start)).fetchone():
        core.fail('توجد إجازة أو انتداب معتمد متداخل')


def salary_basis_at(c,company,day):
    row=c.execute('SELECT snapshot FROM company_policy_terms WHERE company_id=? AND effective_on<=? ORDER BY effective_on DESC LIMIT 1',(company,day)).fetchone()
    if row:return json.loads(row[0])['salary_basis']
    return c.execute('SELECT salary_basis FROM companies WHERE id=?',(company,)).fetchone()[0]


def save_policy(c,company,before,core):
    keys=('salary_basis','advance_min_days','notice_days','approval_mode')
    c.execute('INSERT OR IGNORE INTO company_policy_terms(company_id,effective_on,snapshot,created_at) VALUES(?,?,?,?)',(company,'0001-01-01',json.dumps({k:before[k] for k in keys}),core.now()))
    after=c.execute('SELECT * FROM companies WHERE id=?',(company,)).fetchone()
    c.execute('INSERT INTO company_policy_terms(company_id,effective_on,snapshot,created_at) VALUES(?,?,?,?) ON CONFLICT(company_id,effective_on) DO UPDATE SET snapshot=excluded.snapshot',(company,core.TODAY().isoformat(),json.dumps({k:after[k] for k in keys}),core.now()))


def can_manage(c,u,e,core):
    if e['manager_id']==u['employee_id']:return True
    return bool(c.execute('SELECT 1 FROM approval_delegations WHERE source_manager_id=? AND target_manager_id=? AND company_id=? AND revoked=0 AND starts_on<=? AND ends_on>=?',(e['manager_id'],u['employee_id'],e['company_id'],core.TODAY().isoformat(),core.TODAY().isoformat())).fetchone())


def handle(h,c,u,path,method,core):
    from urllib.parse import parse_qs,urlparse
    q=parse_qs(urlparse(h.path).query)
    def reply(data,status=200):h.write(data,status);return True
    def admin():
        if u['role']!='admin':core.fail('هذه العملية للإدارة فقط',403)
    if path=='/api/delegations':
        if u['role']=='employee':core.fail('غير مصرح',403)
        if method=='POST':
            admin();d=h.input();source=core.user_allowed_emp(c,u,int(d['source_manager_id']));target=core.user_allowed_emp(c,u,int(d['target_manager_id']));a=core.normalize_date(d.get('starts_on'));b=core.normalize_date(d.get('ends_on'));note=str(d.get('note','')).strip()
            if source['id']==target['id'] or source['company_id']!=target['company_id'] or not a or not b or b<a or (dt.date.fromisoformat(b)-dt.date.fromisoformat(a)).days>30 or not note:core.fail('تفويض مبرر لنفس الشركة ومدته لا تتجاوز 31 يومًا مطلوب')
            for person in (source,target):
                if person['archived'] or not c.execute("SELECT 1 FROM users WHERE employee_id=? AND role='manager' AND disabled=0",(person['id'],)).fetchone():core.fail('التفويض بين حسابات مشرفين نشطة فقط')
            if c.execute('SELECT 1 FROM approval_delegations WHERE source_manager_id=? AND revoked=0 AND starts_on<=? AND ends_on>=?',(source['id'],b,a)).fetchone():core.fail('يوجد تفويض متداخل للمشرف')
            cur=c.execute('INSERT INTO approval_delegations(company_id,source_manager_id,target_manager_id,starts_on,ends_on,note,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)',(source['company_id'],source['id'],target['id'],a,b,note,u['id'],core.now()));core.record(c,u,'approval_delegation','company',source['company_id'],cur.lastrowid);c.commit();return reply({'id':cur.lastrowid},201)
        rows=permissions.filter_rows(c,u,c.execute('SELECT * FROM approval_delegations ORDER BY id DESC'))
        if u['role']=='manager':rows=[r for r in rows if r['company_id']==u['company_id'] and u['employee_id'] in (r['source_manager_id'],r['target_manager_id'])]
        return reply(rows)
    if re.fullmatch('/api/delegations/[0-9]+/revoke',path) and method=='POST':
        admin();rid=int(path.split('/')[3]);row=c.execute('SELECT * FROM approval_delegations WHERE id=? AND revoked=0',(rid,)).fetchone()
        if not row:core.fail('تفويض غير متاح',404)
        permissions.company(c,u,row['company_id'],core);c.execute('UPDATE approval_delegations SET revoked=1 WHERE id=?',(rid,));core.record(c,u,'approval_delegation_revoked','company',row['company_id'],rid);c.commit();return reply({'ok':True})
    if path=='/api/notifications/unread' and method=='GET':
        return reply({'count':c.execute('SELECT COUNT(*) FROM notifications WHERE user_id=? AND read_at IS NULL',(u['id'],)).fetchone()[0]})
    if path=='/api/employee-transfer' and method=='POST':
        admin();d=h.input();c.execute('BEGIN IMMEDIATE');e=core.user_allowed_emp(c,u,int(d['employee_id']));target=int(d['company_id']);end=core.normalize_date(d.get('contract_end'));reason=str(d.get('reason','')).strip();today=core.TODAY().isoformat()
        if target==e['company_id'] or not c.execute('SELECT 1 FROM companies WHERE id=? AND archived=0',(target,)).fetchone():core.fail('اختر شركة نشطة مختلفة')
        if not reason or not end or end<=today:core.fail('مبرر النقل ونهاية العقد الجديد مطلوبان')
        if not e['contract_end'] or e['contract_end']>=today:core.fail('النقل بين جهات العمل يتم بعد إنهاء وتسوية العقد السابق')
        if c.execute("SELECT 1 FROM requests WHERE employee_id=? AND status IN ('new','review')",(e['id'],)).fetchone():core.fail('راجع الطلبات المعلقة قبل النقل')
        if c.execute('SELECT 1 FROM employees WHERE manager_id=? AND archived=0',(e['id'],)).fetchone():core.fail('أعد إسناد موظفي المشرف قبل نقله')
        if c.execute('SELECT 1 FROM employee_assets WHERE employee_id=? AND returned_on IS NULL',(e['id'],)).fetchone():core.fail('أغلق العهد القائمة قبل النقل')
        import payroll
        if payroll.commitments(c,e['id'])[0]>0:core.fail('سَوِّ السلف والالتزامات القائمة قبل النقل')
        balance=core.leave(c,e)['available']
        if abs(balance)>.0001:core.fail('سَوِّ رصيد العقد السابق قبل النقل؛ لا يتم إسقاطه تلقائيًا')
        payroll.assert_open(c,e['id'],today,today,core)
        if c.execute("SELECT 1 FROM payroll_runs WHERE company_id=? AND month=? AND status IN ('approved','paid')",(target,today[:7])).fetchone():core.fail('مسير الشركة الجديدة مغلق لهذه الفترة',409)
        entitlement=float(d.get('annual_entitlement',21))
        if not 0<entitlement<=366:core.fail('استحقاق سنوي غير صالح')
        cid=core.hr.ensure_contract(c,e);c.execute('UPDATE contracts SET closed_at=?,closed_balance=0 WHERE id=?',(core.now(),cid))
        c.execute("UPDATE employees SET company_id=?,manager_id=NULL,site_id=NULL,schedule_id=NULL,contract_start=?,contract_end=?,carryover=0,annual_entitlement=?,status='نشط' WHERE id=?",(target,today,end,entitlement,e['id']))
        c.execute('UPDATE users SET company_id=? WHERE employee_id=?',(target,e['id']))
        fresh=c.execute('SELECT * FROM employees WHERE id=?',(e['id'],)).fetchone();core.hr.ensure_contract(c,fresh);save_terms(c,e,fresh,today,u['id'],reason,core)
        c.execute('DELETE FROM auth_sessions WHERE user_id IN (SELECT id FROM users WHERE employee_id=?)',(e['id'],))
        core.record(c,u,'employee_transfer','employee',e['id'],{'from_company':e['company_id'],'to_company':target,'reason':reason});c.commit();return reply({'ok':True})
    if path=='/api/terms':
        eid=int(q.get('employee_id',[u['employee_id'] or 0])[0]);core.user_allowed_emp(c,u,eid)
        if u['role']=='manager' and eid!=u['employee_id']:core.fail('التاريخ المالي خاص بالموظف والإدارة',403)
        return reply([{**dict(r),'snapshot':json.loads(r['snapshot'])} for r in c.execute('SELECT * FROM employee_terms WHERE employee_id=? ORDER BY effective_on DESC',(eid,))])
    if path=='/api/company-timezone' and method=='POST':
        admin();d=h.input();name=str(d.get('timezone',''))
        if name!='Asia/Riyadh':core.fail('التشغيل مخصص للسعودية؛ التوقيت Asia/Riyadh')
        try:ZoneInfo(name)
        except (ZoneInfoNotFoundError,ValueError):core.fail('منطقة زمنية غير صالحة')
        cid=int(d['company_id'])
        if not c.execute('UPDATE companies SET timezone=? WHERE id=?',(name,cid)).rowcount:core.fail('الشركة غير موجودة',404)
        core.record(c,u,'timezone_update','company',cid,name);c.commit();return reply({'ok':True})
    if path=='/api/tasks':
        admin()
        if method=='POST':
            d=h.input();e=core.user_allowed_emp(c,u,int(d['employee_id']));kind=d.get('kind');title=str(d.get('title','')).strip();due=core.normalize_date(d.get('due_on'))
            if kind not in ('onboarding','offboarding','renewal','general') or not title or len(title)>200 or not due:core.fail('نوع المهمة والعنوان والموعد مطلوبون')
            assigned=int(d['assigned_to']) if d.get('assigned_to') else None
            if assigned and not c.execute("SELECT 1 FROM users WHERE id=? AND role='admin'",(assigned,)).fetchone():core.fail('المسؤول يجب أن يكون حساب إدارة')
            cur=c.execute('INSERT INTO lifecycle_tasks(employee_id,kind,title,due_on,assigned_to,created_by,created_at) VALUES(?,?,?,?,?,?,?)',(e['id'],kind,title,due,assigned,u['id'],core.now()));core.record(c,u,'task_create','employee',e['id'],title);c.commit()
            return reply({'id':cur.lastrowid},201)
        return reply(permissions.filter_rows(c,u,c.execute('SELECT t.*,e.name employee_name,e.company_id FROM lifecycle_tasks t JOIN employees e ON e.id=t.employee_id ORDER BY t.status,t.due_on')))
    if re.fullmatch('/api/tasks/[0-9]+',path) and method=='POST':
        admin();d=h.input();status=d.get('status');note=str(d.get('note','')).strip()
        if status not in ('open','done','cancelled') or not note:core.fail('حدد الحالة واكتب ملاحظات الإجراء')
        tid=int(path.rsplit('/',1)[1]);row=c.execute('SELECT * FROM lifecycle_tasks WHERE id=?',(tid,)).fetchone()
        if not row:core.fail('المهمة غير موجودة',404)
        core.user_allowed_emp(c,u,row['employee_id'])
        c.execute('UPDATE lifecycle_tasks SET status=?,note=?,completed_at=? WHERE id=?',(status,note,core.now() if status=='done' else None,tid));core.record(c,u,'task_'+status,'employee',row['employee_id'],{'task_id':tid,'note':note});c.commit();return reply({'ok':True})
    if path=='/api/assets':
        admin()
        if method=='POST':
            d=h.input();e=core.user_allowed_emp(c,u,int(d['employee_id']));name=str(d.get('name','')).strip();serial=str(d.get('serial','')).strip()
            if not name or not serial or max(len(name),len(serial))>200:core.fail('اسم العهدة ورقمها مطلوبان')
            if c.execute('SELECT 1 FROM employee_assets WHERE serial=? AND returned_on IS NULL',(serial,)).fetchone():core.fail('العهدة مسلمة بالفعل')
            cur=c.execute('INSERT INTO employee_assets(employee_id,name,serial,issued_on,note,created_by) VALUES(?,?,?,?,?,?)',(e['id'],name,serial,core.TODAY().isoformat(),str(d.get('note',''))[:1000],u['id']));core.record(c,u,'asset_issue','employee',e['id'],name);c.commit();return reply({'id':cur.lastrowid},201)
        return reply(permissions.filter_rows(c,u,c.execute('SELECT a.*,e.name employee_name,e.company_id FROM employee_assets a JOIN employees e ON e.id=a.employee_id ORDER BY a.id DESC')))
    if re.fullmatch('/api/assets/[0-9]+/return',path) and method=='POST':
        admin();d=h.input();aid=int(path.split('/')[3]);note=str(d.get('note','')).strip()
        if not note:core.fail('ملاحظات الاستلام مطلوبة')
        row=c.execute('SELECT * FROM employee_assets WHERE id=? AND returned_on IS NULL',(aid,)).fetchone()
        if not row:core.fail('العهدة غير موجودة أو مستلمة',409)
        core.user_allowed_emp(c,u,row['employee_id'])
        c.execute('UPDATE employee_assets SET returned_on=?,note=note||? WHERE id=?',(core.TODAY().isoformat(),' | '+note,aid));core.record(c,u,'asset_return','employee',row['employee_id'],{'asset_id':aid,'note':note});c.commit();return reply({'ok':True})
    if path=='/api/alerts' and method=='GET':
        admin();today=core.TODAY();until=(today+dt.timedelta(days=60)).isoformat();result=[]
        for e in core.visible_employees(c,u):
            if e['contract_end'] and e['contract_end']<=until:result.append({'kind':'contract','employee_id':e['id'],'name':e['name'],'due_on':e['contract_end']})
        for r in c.execute('SELECT f.id,f.employee_id,f.document_type,f.expires_on,e.name FROM employee_files f JOIN employees e ON e.id=f.employee_id WHERE e.archived=0 AND f.expires_on<=? AND NOT EXISTS(SELECT 1 FROM employee_files n WHERE n.supersedes_id=f.id)',(until,)):
            result.append({'kind':'document','employee_id':r['employee_id'],'name':r['name'],'due_on':r['expires_on'],'document_type':r['document_type']})
        for r in c.execute("SELECT r.id,r.employee_id,e.name,r.created_at FROM requests r JOIN employees e ON e.id=r.employee_id WHERE r.status IN ('new','review') AND substr(r.created_at,1,10)<?",((today-dt.timedelta(days=3)).isoformat(),)):
            result.append({'kind':'overdue_request','employee_id':r['employee_id'],'name':r['name'],'due_on':r['created_at'][:10],'request_id':r['id']})
        visible={e['id'] for e in core.visible_employees(c,u)}
        return reply(sorted([r for r in result if r['employee_id'] in visible],key=lambda r:r['due_on']))
    return False


def attendance_metrics(c,e,row):
    terms=json.loads(row['schedule_snapshot']) if row['schedule_snapshot'] else terms_at(c,e,row['day'])
    date=dt.date.fromisoformat(row['day']);start=dt.datetime.combine(date,dt.time.fromisoformat(terms['work_start']));end=dt.datetime.combine(date,dt.time.fromisoformat(terms['work_end']))
    overnight=end<=start
    if overnight:end+=dt.timedelta(days=1)
    arrival=departure=None
    if row['check_in_at']:arrival=dt.datetime.fromisoformat(row['check_in_at']).astimezone(zone(c,e['company_id'])).replace(tzinfo=None)
    elif row['check_in']:arrival=dt.datetime.combine(date,dt.time.fromisoformat(row['check_in']))
    if row['check_out_at']:departure=dt.datetime.fromisoformat(row['check_out_at']).astimezone(zone(c,e['company_id'])).replace(tzinfo=None)
    elif row['check_out']:
        departure=dt.datetime.combine(date,dt.time.fromisoformat(row['check_out']))
        if overnight and departure<start:departure+=dt.timedelta(days=1)
    hours=max(0,(departure-arrival).total_seconds()/3600) if arrival and departure else 0
    return {'hours':round(hours,2),'late':bool(arrival and arrival>start),'early_out':bool(departure and departure<end)}

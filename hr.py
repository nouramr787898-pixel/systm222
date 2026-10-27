"""HR policies and transactional workflows. No external messaging or payroll transfers."""
import calendar
import csv
import datetime as dt
import io
import json
import math
import re
import zipfile
from xml.sax.saxutils import escape


def migrate(c):
    if c.execute("SELECT 1 FROM schema_migrations WHERE version='hr-v2'").fetchone():
        return
    for table, fields in {
        'employees': {'passport':'TEXT','nationality':'TEXT','birth_date':'TEXT','qualification':'TEXT','specialization':'TEXT','branch':'TEXT','iban':'TEXT','bank_name':'TEXT','schedule_id':'INTEGER','site_id':'INTEGER'},
        'users': {'display_name':"TEXT DEFAULT ''"},
        'companies': {'salary_basis':"TEXT DEFAULT 'calendar'",'advance_min_days':'INTEGER NOT NULL DEFAULT 0','notice_days':'INTEGER NOT NULL DEFAULT 60','approval_mode':"TEXT NOT NULL DEFAULT 'supervisor_or_admin'"},
        'requests': {'details':"TEXT NOT NULL DEFAULT '{}'",'contract_id':'INTEGER'},
        'request_attachments': {'decision_id':'INTEGER'},
    }.items():
        existing={r[1] for r in c.execute('PRAGMA table_info('+table+')')}
        for name,kind in fields.items():
            if name not in existing:c.execute(f'ALTER TABLE {table} ADD COLUMN {name} {kind}')
    statements=[
        'CREATE TABLE employee_files(id INTEGER PRIMARY KEY,employee_id INTEGER NOT NULL REFERENCES employees(id),name TEXT NOT NULL,mime TEXT NOT NULL,content BLOB NOT NULL,uploaded_by INTEGER NOT NULL REFERENCES users(id),created_at TEXT NOT NULL)',
        'CREATE TABLE overtime_reviews(id INTEGER PRIMARY KEY,employee_id INTEGER NOT NULL REFERENCES employees(id),day TEXT NOT NULL,minutes INTEGER NOT NULL,status TEXT NOT NULL,note TEXT NOT NULL,user_id INTEGER NOT NULL REFERENCES users(id),decided_at TEXT NOT NULL,UNIQUE(employee_id,day))',
        'CREATE TABLE contracts(id INTEGER PRIMARY KEY,employee_id INTEGER NOT NULL REFERENCES employees(id),start_date TEXT,end_date TEXT,entitlement REAL NOT NULL,opening REAL NOT NULL,closed_balance REAL,closed_at TEXT)',
        'CREATE UNIQUE INDEX one_current_contract ON contracts(employee_id) WHERE closed_at IS NULL',
        'CREATE TABLE absences(id INTEGER PRIMARY KEY,employee_id INTEGER NOT NULL REFERENCES employees(id),contract_id INTEGER REFERENCES contracts(id),day TEXT NOT NULL,status TEXT NOT NULL,leave_days REAL NOT NULL DEFAULT 1,salary_deduction REAL NOT NULL DEFAULT 0,salary_basis TEXT,original_salary REAL NOT NULL DEFAULT 0,note TEXT,decided_by INTEGER REFERENCES users(id),decided_at TEXT,UNIQUE(employee_id,day))',
        'CREATE TABLE settlements(id INTEGER PRIMARY KEY,request_id INTEGER NOT NULL UNIQUE REFERENCES requests(id),employee_id INTEGER NOT NULL REFERENCES employees(id),contract_id INTEGER NOT NULL REFERENCES contracts(id),days REAL NOT NULL,amount REAL NOT NULL,basis TEXT NOT NULL,created_by INTEGER NOT NULL REFERENCES users(id),created_at TEXT NOT NULL)',
        'CREATE TABLE email_outbox(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),request_id INTEGER REFERENCES requests(id),destination TEXT NOT NULL,subject TEXT NOT NULL,status TEXT NOT NULL DEFAULT "pending_configuration",attempts INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,sent_at TEXT)',
        'CREATE TABLE notifications(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),request_id INTEGER REFERENCES requests(id),message TEXT NOT NULL,created_at TEXT NOT NULL,read_at TEXT)',
        'CREATE TABLE request_comments(id INTEGER PRIMARY KEY,request_id INTEGER NOT NULL REFERENCES requests(id),user_id INTEGER NOT NULL REFERENCES users(id),username TEXT NOT NULL,message TEXT NOT NULL,created_at TEXT NOT NULL)',
        'CREATE TABLE schedules(id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),name TEXT NOT NULL,workweek TEXT NOT NULL,work_start TEXT NOT NULL,work_end TEXT NOT NULL)',
        'CREATE TABLE work_sites(id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),name TEXT NOT NULL,latitude REAL NOT NULL,longitude REAL NOT NULL,radius REAL NOT NULL)',
        'CREATE TABLE holidays(id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),name TEXT NOT NULL,start_date TEXT NOT NULL,end_date TEXT NOT NULL)',
        'CREATE INDEX notifications_user ON notifications(user_id,id)',
        'CREATE INDEX absence_contract ON absences(contract_id,status)',
        'CREATE INDEX contracts_employee ON contracts(employee_id,id)',
    ]
    for sql in statements:c.execute(sql)
    c.execute("UPDATE users SET display_name=COALESCE((SELECT name FROM employees WHERE id=users.employee_id),username)")
    for e in c.execute('SELECT * FROM employees').fetchall():
        cid=ensure_contract(c,e)
        c.execute('UPDATE requests SET contract_id=? WHERE employee_id=?',(cid,e['id']))
    c.execute("INSERT INTO schema_migrations VALUES('hr-v2')")


def ensure_contract(c,e):
    row=c.execute('SELECT id FROM contracts WHERE employee_id=? AND closed_at IS NULL',(e['id'],)).fetchone()
    if row:return row[0]
    return c.execute('INSERT INTO contracts(employee_id,start_date,end_date,entitlement,opening) VALUES(?,?,?,?,?)',
                     (e['id'],e['contract_start'],e['contract_end'],e['annual_entitlement'],e['carryover'])).lastrowid


def employment_end(c,e):
    end=e['contract_end']
    current=c.execute('SELECT id FROM contracts WHERE employee_id=? AND closed_at IS NULL',(e['id'],)).fetchone()
    if current:
        for row in c.execute("SELECT details FROM requests WHERE employee_id=? AND contract_id=? AND type='termination' AND status='approved'",(e['id'],current[0])):
            last=json.loads(row['details']).get('last_work_day')
            if last:end=min(end,last) if end else last
    return end


def leave(c,e,core,asof=None):
    today=asof or core.TODAY()
    end=employment_end(c,e)
    until=min(today,dt.date.fromisoformat(end)) if end else today
    elapsed=core.months_elapsed(e['contract_start'],until)
    monthly=float(e['annual_entitlement'])/12
    accrued=round(monthly*elapsed,4)
    contract=c.execute('SELECT id FROM contracts WHERE employee_id=? AND closed_at IS NULL',(e['id'],)).fetchone()
    cid=contract[0] if contract else None
    used=c.execute("SELECT COALESCE(SUM(days),0) FROM requests WHERE employee_id=? AND contract_id IS ? AND type='annual_leave' AND status='approved'",(e['id'],cid)).fetchone()[0]
    absent=c.execute("SELECT COALESCE(SUM(leave_days),0) FROM absences WHERE contract_id IS ? AND employee_id=? AND status='approved'",(cid,e['id'])).fetchone()[0]
    settled=c.execute('SELECT COALESCE(SUM(days),0) FROM settlements WHERE contract_id IS ? AND employee_id=?',(cid,e['id'])).fetchone()[0]
    carried=float(e['carryover'])
    expired=bool(end and today.isoformat()>=end)
    expired_accrual=accrued if expired else 0
    if expired:carried+=accrued;accrued=0
    history=[]
    if e['contract_start']:
        start=dt.date.fromisoformat(e['contract_start'])
        for i in range(1,elapsed+1):
            month=start.month-1+i;year=start.year+month//12;month=month%12+1
            date=dt.date(year,month,min(start.day,calendar.monthrange(year,month)[1]))
            history.append({'date':date.isoformat(),'days':round(monthly,4)})
    return {'contract_id':cid,'carryover':carried,'annual_entitlement':e['annual_entitlement'],'monthly':round(monthly,4),'accrued':accrued,
            'approved_used':used,'absence_used':absent,'settled_days':settled,'available':round(carried+accrued-used-absent-settled,4),
            'elapsed_months':elapsed,'monthly_history':history,'expired_contract_accrual':expired_accrual}


def holiday(c,e,day):
    return bool(c.execute('SELECT 1 FROM holidays WHERE company_id=? AND start_date<=? AND end_date>=?',(e['company_id'],day,day)).fetchone())


def details(c,u,e,d,core):
    kind=d['type'];out={}
    for key in ('title','incident_description','parties','destination'):
        out[key]=str(d.get(key) or '').strip()[:2000]
    if kind in ('open','hr_contact','investigation') and not out['title']:
        # Older API clients may supply notes only. Title derives from the note, never fabricated facts.
        out['title']=str(d.get('notes') or '').strip()[:120]
        if not out['title']:core.fail('عنوان الطلب مطلوب')
    if kind=='annual_leave':
        fraction=float(d.get('last_day_fraction',1))
        if fraction not in (.25,.5,.75,1):core.fail('كسر يوم الإجازة غير صالح')
        out['last_day_fraction']=fraction
    if kind=='investigation':
        if not out['incident_description']:core.fail('وصف الواقعة مطلوب')
        out['incident_date']=core.normalize_date(d.get('incident_date'))
        if not out['incident_date']:core.fail('تاريخ الواقعة مطلوب')
    if kind=='sick_leave' and not d.get('attachments'):core.fail('المستند الطبي إلزامي للإجازة المرضية')
    if kind=='punch_fix':
        out['correction_type']=d.get('correction_type','both')
        if out['correction_type'] not in ('check_in','check_out','both'):core.fail('نوع تصحيح البصمة غير صالح')
        if d.get('start_date') and e['contract_start'] and core.normalize_date(d['start_date'])<e['contract_start']:core.fail('تاريخ البصمة يسبق العقد الحالي؛ راجع الإدارة للتسوية التاريخية')
        if d.get('start_date') and core.normalize_date(d['start_date'])>core.TODAY().isoformat():core.fail('لا يمكن تصحيح بصمة مستقبلية')
    if kind=='permission':
        a=str(d.get('time_from') or '');b=str(d.get('time_to') or '')
        if not re.fullmatch(r'\d{2}:\d{2}',a) or not re.fullmatch(r'\d{2}:\d{2}',b):core.fail('حدد بداية الاستئذان ونهايته')
        try:
            a_time=dt.time.fromisoformat(a);b_time=dt.time.fromisoformat(b)
        except ValueError:core.fail('وقت الاستئذان غير صالح')
        duration=(b_time.hour*60+b_time.minute)-(a_time.hour*60+a_time.minute)
        if duration<=0:core.fail('نهاية الاستئذان يجب أن تكون بعد البداية')
        out.update(time_from=a,time_to=b,minutes=duration)
    if kind=='termination':
        out['termination_type']=d.get('termination_type','resignation')
        if out['termination_type'] not in ('resignation','nonrenewal','termination'):core.fail('نوع إنهاء التعاقد غير صالح')
        proposed=core.normalize_date(d.get('last_work_day'))
        if not proposed:core.fail('آخر يوم عمل مقترح مطلوب')
        notice=c.execute('SELECT notice_days FROM companies WHERE id=?',(e['company_id'],)).fetchone()[0]
        earliest=core.TODAY()+dt.timedelta(days=notice)
        if proposed<earliest.isoformat():core.fail(f'فترة الإشعار {notice} يومًا؛ أقرب تاريخ {earliest.isoformat()}')
        out.update(last_work_day=proposed,notice_days=notice,notice_end=earliest.isoformat())
    return out


def notify(c,r,core,message):
    recipients={row[0] for row in c.execute("SELECT id FROM users WHERE role='admin' OR employee_id=?",(r['employee_id'],))}
    if r['type']!='hr_contact':
        e=c.execute('SELECT manager_id,company_id FROM employees WHERE id=?',(r['employee_id'],)).fetchone()
        recipients.update(row[0] for row in c.execute("SELECT id FROM users WHERE role='manager' AND employee_id=? AND company_id=?",(e['manager_id'],e['company_id'])))
    for uid in recipients:
        c.execute('INSERT INTO notifications(user_id,request_id,message,created_at) VALUES(?,?,?,?)',(uid,r['id'],message,core.now()))
        contact=c.execute('SELECT e.email FROM users u JOIN employees e ON e.id=u.employee_id WHERE u.id=?',(uid,)).fetchone()
        if contact and re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',contact[0] or ''):
            c.execute('INSERT INTO email_outbox(user_id,request_id,destination,subject,created_at) VALUES(?,?,?,?,?)',(uid,r['id'],contact[0],message+f" EH-{r['id']:06d}",core.now()))


def coverage(c,eid,day):
    total=0.0
    rows=c.execute("SELECT * FROM requests WHERE employee_id=? AND start_date<=? AND end_date>=? AND ((status='approved' AND type IN ('annual_leave','sick_leave','unpaid_leave')) OR (status='accepted' AND type='delegation'))",(eid,day,day))
    for row in rows:
        fraction=json.loads(row['details']).get('last_day_fraction',1) if row['type']=='annual_leave' and row['end_date']==day else 1
        total+=float(fraction)
    return min(1,total)


def reconcile_absences(c,eid,start,end,core):
    for row in c.execute("SELECT * FROM absences WHERE employee_id=? AND day>=? AND day<=? AND status IN ('approved','covered_leave')",(eid,start,end)).fetchall():
        remaining=1-coverage(c,eid,row['day'])
        c.execute('UPDATE absences SET status=?,leave_days=?,salary_deduction=? WHERE id=?',('approved' if remaining else 'covered_leave',remaining,round(row['original_salary']*remaining,2),row['id']))


def scan_absences(c,e,start,end,core):
    a=dt.date.fromisoformat(start);b=dt.date.fromisoformat(end)
    if b>=core.TODAY():b=core.TODAY()-dt.timedelta(days=1)
    if (b-a).days>366:core.fail('اختر فترة لا تزيد على سنة')
    result=[];week={int(x) for x in e['workweek'].split(',')}
    for i in range(max(0,(b-a).days+1)):
        day=a+dt.timedelta(days=i);iso=day.isoformat()
        if day.weekday() not in week or holiday(c,e,iso):continue
        if (e['hire_date'] and iso<e['hire_date']) or (e['contract_start'] and iso<e['contract_start']) or (employment_end(c,e) and iso>employment_end(c,e)):continue
        entry=c.execute('SELECT * FROM attendance WHERE employee_id=? AND day=?',(e['id'],iso)).fetchone()
        approved=coverage(c,e['id'],iso)
        if approved>=1:continue
        decision=c.execute('SELECT * FROM absences WHERE employee_id=? AND day=?',(e['id'],iso)).fetchone()
        if decision:result.append(dict(decision));continue
        if not entry or not entry['check_in']:
            result.append({'employee_id':e['id'],'day':iso,'status':'pending','leave_days':round(1-approved,4),'salary_deduction':None})
        elif not entry['check_out']:
            result.append({'employee_id':e['id'],'day':iso,'status':'incomplete','leave_days':0,'salary_deduction':None})
    return result


def daily_rate(c,e,day,core):
    basis=c.execute('SELECT salary_basis FROM companies WHERE id=?',(e['company_id'],)).fetchone()[0]
    if basis not in ('30','calendar'):core.fail('حدد واعتمد أساس أجر اليوم في سياسة الشركة قبل تسجيل الخصم أو التسوية')
    divisor=30 if basis=='30' else calendar.monthrange(day.year,day.month)[1]
    return core.salary(e)/divisor,basis


def handle(h,c,u,path,method,core):
    """True means a response was written. Called only after authentication."""
    from urllib.parse import urlparse,parse_qs
    qs=parse_qs(urlparse(h.path).query)
    def respond(data,status=200):h.write(data,status);return True
    def admin():
        if u['role']!='admin':core.fail('هذه العملية للإدارة فقط',403)
    def employee():
        eid=int(qs.get('employee_id',[u['employee_id'] or 0])[0]);return core.user_allowed_emp(c,u,eid)
    if path=='/api/employee-files':
        e=employee()
        if method=='POST':
            admin();files=core.validate_attachments(h.input().get('attachments',[]))
            if not files:core.fail('اختر مرفقًا')
            for name,mime,content in files:c.execute('INSERT INTO employee_files(employee_id,name,mime,content,uploaded_by,created_at) VALUES(?,?,?,?,?,?)',(e['id'],name,mime,content,u['id'],core.now()))
            core.record(c,u,'employee_file_upload','employee',e['id']);c.commit()
        return respond([dict(r) for r in c.execute('SELECT id,name,mime,length(content) size,created_at FROM employee_files WHERE employee_id=?',(e['id'],))])
    if re.fullmatch('/api/employee-files/[0-9]+',path) and method=='GET':
        row=c.execute('SELECT * FROM employee_files WHERE id=?',(int(path.rsplit('/',1)[1]),)).fetchone()
        if not row:core.fail('المرفق غير موجود',404)
        core.user_allowed_emp(c,u,row['employee_id'])
        from urllib.parse import quote
        h.write(row['content'],ctype=row['mime'],headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(row['name'],safe=''),'X-Content-Type-Options':'nosniff'})
        return True
    if path=='/api/overtime':
        e=employee()
        if method=='POST':
            admin();d=h.input();day=core.normalize_date(d.get('day'));status=d.get('status');note=str(d.get('note') or '').strip()
            row=c.execute('SELECT * FROM attendance WHERE employee_id=? AND day=?',(e['id'],day)).fetchone()
            if not row or not row['check_out'] or status not in ('approved','rejected') or not note:core.fail('يلزم سجل انصراف وقرار وملاحظات')
            def minute(value):
                t=dt.time.fromisoformat(value);return t.hour*60+t.minute
            maximum=max(0,minute(row['check_out'])-minute(e['work_end']))
            minutes=int(d.get('minutes',0))
            if not 0<minutes<=maximum:core.fail('الدقائق تتجاوز الوقت المسجل بعد نهاية الدوام')
            if c.execute('SELECT 1 FROM overtime_reviews WHERE employee_id=? AND day=?',(e['id'],day)).fetchone():core.fail('تمت مراجعة الوقت الإضافي لهذا اليوم بالفعل')
            c.execute('INSERT INTO overtime_reviews(employee_id,day,minutes,status,note,user_id,decided_at) VALUES(?,?,?,?,?,?,?)',(e['id'],day,minutes,status,note,u['id'],core.now()))
            core.record(c,u,'overtime_'+status,'employee',e['id'],day);c.commit()
        return respond([dict(r) for r in c.execute('SELECT * FROM overtime_reviews WHERE employee_id=? ORDER BY day DESC',(e['id'],))])
    if path=='/api/email-outbox' and method=='GET':
        admin();return respond([dict(r) for r in c.execute('SELECT id,user_id,request_id,subject,status,attempts,created_at,sent_at FROM email_outbox ORDER BY id DESC LIMIT 100')])
    if path=='/api/notifications' and method=='GET':
        return respond([dict(r) for r in c.execute('SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 100',(u['id'],))])
    if path=='/api/notifications/read' and method=='POST':
        c.execute('UPDATE notifications SET read_at=? WHERE user_id=?',(core.now(),u['id']));c.commit();return respond({'ok':True})
    if path=='/api/policy':
        admin();co=int(qs.get('company_id',[0])[0])
        if method=='POST':
            d=h.input();basis=d.get('salary_basis','calendar');approval=d.get('approval_mode','supervisor_or_admin')
            if approval not in ('supervisor_or_admin','admin_only','supervisor_then_admin'):core.fail('مسار اعتماد غير صالح')
            if basis not in ('','30','calendar'):core.fail('أساس أجر اليوم غير صالح')
            minimum=int(d.get('advance_min_days',0));notice=int(d.get('notice_days',60))
            if not 0<=minimum<=365 or not 0<=notice<=365:core.fail('مدة غير صالحة')
            changed=c.execute('UPDATE companies SET salary_basis=?,advance_min_days=?,notice_days=?,approval_mode=? WHERE id=?',(basis,minimum,notice,approval,co))
            if not changed.rowcount:core.fail('الشركة غير موجودة',404)
            core.record(c,u,'policy_update','company',co);c.commit()
        row=c.execute('SELECT id,name,salary_basis,advance_min_days,notice_days,approval_mode FROM companies WHERE id=?',(co,)).fetchone()
        if not row:core.fail('الشركة غير موجودة',404)
        return respond(dict(row))
    if path in ('/api/schedules','/api/sites','/api/holidays'):
        admin();co=int(qs.get('company_id',[0])[0]);table={'/api/schedules':'schedules','/api/sites':'work_sites','/api/holidays':'holidays'}[path]
        if not c.execute('SELECT id FROM companies WHERE id=?',(co,)).fetchone():core.fail('الشركة غير موجودة',404)
        if method=='POST':
            d=h.input();name=str(d.get('name') or '').strip()
            if not name:core.fail('الاسم مطلوب')
            if table=='schedules':
                if any(not d.get(k) for k in ('workweek','work_start','work_end')):core.fail('حدد أيام وأوقات الجدول')
                core.normalize_employee_fields(d)
                for key in ('work_start','work_end'):dt.time.fromisoformat(d[key])
                keys=['name','workweek','work_start','work_end']
                if not d.get('workweek') or d['work_end']<=d['work_start']:core.fail('أيام أو وقت الجدول غير صحيح')
            elif table=='work_sites':
                if any(d.get(k) is None for k in ('latitude','longitude','radius')):core.fail('بيانات الموقع مطلوبة')
                lat=float(d['latitude']);lon=float(d['longitude']);radius=float(d['radius'])
                if not -90<=lat<=90 or not -180<=lon<=180 or not 20<=radius<=1000:core.fail('موقع غير صالح')
                keys=['name','latitude','longitude','radius']
            else:
                d['start_date']=core.normalize_date(d.get('start_date'));d['end_date']=core.normalize_date(d.get('end_date'))
                if not d['start_date'] or not d['end_date'] or d['end_date']<d['start_date']:core.fail('فترة عطلة غير صالحة')
                keys=['name','start_date','end_date']
            cur=c.execute('INSERT INTO '+table+'(company_id,'+','.join(keys)+') VALUES('+','.join('?' for _ in range(len(keys)+1))+')',[co]+[d[k] for k in keys])
            core.record(c,u,'create',table,cur.lastrowid,name);c.commit()
        return respond([dict(r) for r in c.execute('SELECT * FROM '+table+' WHERE company_id=? ORDER BY id',(co,))])
    if path=='/api/leave/preview' and method=='POST':
        d=h.input();e=core.user_allowed_emp(c,u,int(d.get('employee_id') or u['employee_id'] or 0))
        a=core.normalize_date(d.get('start_date'));b=core.normalize_date(d.get('end_date'))
        days=core.business_days(e,a,b,c);balance=leave(c,e,core)
        return respond({'days':days,'available':balance['available'],'after_approval':round(balance['available']-days,4)})
    if path=='/api/contracts' and method=='GET':
        e=employee();return respond([dict(r) for r in c.execute('SELECT * FROM contracts WHERE employee_id=? ORDER BY id',(e['id'],))])
    if path=='/api/contracts/renew' and method=='POST':
        admin();d=h.input();c.execute('BEGIN IMMEDIATE');e=core.user_allowed_emp(c,u,int(d['employee_id']))
        start=core.normalize_date(d.get('start_date'));end=core.normalize_date(d.get('end_date'));entitlement=float(d.get('annual_entitlement',e['annual_entitlement']))
        if not start or not end or end<=start or not math.isfinite(entitlement) or entitlement<=0:core.fail('بيانات التجديد غير صالحة')
        if not e['contract_end'] or e['contract_end']>core.TODAY().isoformat() or start<e['contract_end'] or start>core.TODAY().isoformat():core.fail('يتم التجديد بعد انتهاء العقد، وبداية العقد الجديد لا تسبق نهاية القديم ولا تتجاوز اليوم')
        old=ensure_contract(c,e)
        pending=c.execute("SELECT 1 FROM requests WHERE contract_id=? AND type='annual_leave' AND status IN ('new','review')",(old,)).fetchone()
        if pending:core.fail('راجع طلبات الإجازة المعلقة قبل تجديد العقد')
        balance=leave(c,e,core)['available']
        c.execute('UPDATE contracts SET closed_balance=?,closed_at=? WHERE id=?',(balance,core.now(),old))
        c.execute("UPDATE employees SET contract_start=?,contract_end=?,annual_entitlement=?,carryover=?,status='نشط' WHERE id=?",(start,end,entitlement,balance,e['id']))
        e=c.execute('SELECT * FROM employees WHERE id=?',(e['id'],)).fetchone();new=ensure_contract(c,e)
        core.record(c,u,'contract_renewal','employee',e['id'],json.dumps({'old':old,'new':new,'carryover':balance}));c.commit()
        return respond({'ok':True,'carryover':balance,'contract_id':new})
    if path=='/api/absences' and method=='GET':
        e=employee();start=core.normalize_date(qs.get('from',[core.TODAY().replace(day=1).isoformat()])[0]);end=core.normalize_date(qs.get('to',[(core.TODAY()-dt.timedelta(days=1)).isoformat()])[0])
        if not start or not end:core.fail('الفترة مطلوبة')
        return respond(scan_absences(c,e,start,end,core))
    if path=='/api/absences/decision' and method=='POST':
        admin();d=h.input();c.execute('BEGIN IMMEDIATE');e=core.user_allowed_emp(c,u,int(d['employee_id']));day=core.normalize_date(d['day']);status=d.get('status');note=str(d.get('note') or '').strip()
        if status not in ('approved','excused') or not note:core.fail('اختر القرار واكتب ملاحظاته')
        items=scan_absences(c,e,day,day,core)
        if not items or items[0]['status']!='pending':core.fail('اليوم غير متاح لاعتماد غياب جديد')
        rate,basis=daily_rate(c,e,dt.date.fromisoformat(day),core) if status=='approved' else (0,'')
        cid=ensure_contract(c,e);fraction=items[0]['leave_days'] if status=='approved' else 0
        c.execute('INSERT INTO absences(employee_id,contract_id,day,status,leave_days,salary_deduction,salary_basis,original_salary,note,decided_by,decided_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(e['id'],cid,day,status,fraction,round(rate*fraction,2),basis,rate,note,u['id'],core.now()))
        core.record(c,u,'absence_'+status,'employee',e['id'],day+': '+note);c.commit();return respond({'ok':True,'leave_deduction':fraction,'salary_deduction':round(rate*fraction,2)})
    if path=='/api/settlement/preview' and method=='GET':
        e=employee();last=core.normalize_date(qs.get('last_day',[core.TODAY().isoformat()])[0]);rate,basis=daily_rate(c,e,dt.date.fromisoformat(last),core);balance=leave(c,e,core,dt.date.fromisoformat(last))
        return respond({'available_days':balance['available'],'daily_rate':round(rate,4),'leave_value':round(max(0,balance['available'])*rate,2),'salary_basis':basis,'as_of':last,'estimate_only':True})
    if path=='/api/settlement/record' and method=='POST':
        admin();d=h.input();c.execute('BEGIN IMMEDIATE');r=c.execute('SELECT * FROM requests WHERE id=?',(int(d['request_id']),)).fetchone()
        if not r or r['type']!='termination' or r['status']!='approved':core.fail('يلزم طلب إنهاء تعاقد معتمد')
        e=core.user_allowed_emp(c,u,r['employee_id']);last=json.loads(r['details']).get('last_work_day')
        if not last or last>core.TODAY().isoformat():core.fail('تسجل التسوية بعد آخر يوم عمل فقط')
        if c.execute('SELECT 1 FROM settlements WHERE request_id=? OR (employee_id=? AND contract_id=?)',(r['id'],e['id'],ensure_contract(c,e))).fetchone():core.fail('سُجلت تسوية الموظف بالفعل')
        balance=leave(c,e,core,dt.date.fromisoformat(last));rate,basis=daily_rate(c,e,dt.date.fromisoformat(last),core);days=max(0,balance['available']);amount=round(days*rate,2)
        c.execute('INSERT INTO settlements(request_id,employee_id,contract_id,days,amount,basis,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)',(r['id'],e['id'],balance['contract_id'],days,amount,basis,u['id'],core.now()))
        c.execute("UPDATE employees SET status='منتهي' WHERE id=?",(e['id'],))
        core.record(c,u,'leave_settlement','employee',e['id'],str(amount));c.commit();return respond({'ok':True,'days':days,'amount':amount,'money_transferred':False})
    if re.fullmatch('/api/requests/[0-9]+/comments',path):
        rid=int(path.split('/')[3]);r=c.execute('SELECT * FROM requests WHERE id=?',(rid,)).fetchone()
        if not r:core.fail('الطلب غير موجود',404)
        core.request_access(c,u,r)
        if method=='POST':
            d=h.input();message=str(d.get('message') or '').strip()
            if not message:core.fail('اكتب الملاحظة')
            attachments=core.validate_attachments(d.get('attachments',[]))
            c.execute('INSERT INTO request_comments(request_id,user_id,username,message,created_at) VALUES(?,?,?,?,?)',(rid,u['id'],u['username'],message,core.now()))
            for name,mime,content in attachments:c.execute('INSERT INTO request_attachments(request_id,name,mime,content) VALUES(?,?,?,?)',(rid,name,mime,content))
            core.record(c,u,'comment','request',rid,message);notify(c,r,core,'ملاحظة جديدة على الطلب');c.commit()
        return respond([dict(row) for row in c.execute('SELECT * FROM request_comments WHERE request_id=? ORDER BY id',(rid,))])
    if path=='/api/reports' and method=='GET':
        if u['role']=='employee':core.fail('التقارير للمسؤولين فقط',403)
        rows=report_rows(c,u,qs,core);fmt=qs.get('format',['json'])[0]
        if fmt=='json':return respond(rows)
        if fmt=='xlsx':h.write(xlsx(rows),ctype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="ehkam-report.xlsx"'});return True
        if fmt=='csv':
            out=io.StringIO();w=csv.writer(out);w.writerow(list(rows[0]) if rows else ['لا توجد بيانات'])
            for row in rows:w.writerow([safe_cell(v) for v in row.values()])
            h.write('\ufeff'+out.getvalue(),ctype='text/csv',headers={'Content-Disposition':'attachment; filename="ehkam-report.csv"'});return True
        core.fail('صيغة غير صالحة')
    return False


def report_rows(c,u,qs,core):
    typ=qs.get('type',['employees'])[0];co=qs.get('company_id',[''])[0];eid=qs.get('employee_id',[''])[0]
    people=core.visible_employees(c,u,True)
    people=[e for e in people if (not co or e['company_id']==int(co)) and (not eid or e['id']==int(eid))]
    start=core.normalize_date(qs.get('from',[''])[0]);end=core.normalize_date(qs.get('to',[''])[0]);result=[]
    def in_range(day):return (not start or day>=start) and (not end or day<=end)
    for e in people:
        base={'company_name':e['company_name'],'company_id':e['company_id'],'employee_id':e['id'],'employee_no':e['employee_no'],'name':e['name']}
        if typ=='employees':result.append({**base,**{k:e[k] for k in ['identity_no','department','job','status','phone_country','phone_local','email','nationality','branch']}})
        elif typ=='contracts' and (not e['contract_end'] or in_range(e['contract_end'])):result.append({**base,'start':e['contract_start'],'end':e['contract_end'],'remaining_days':(dt.date.fromisoformat(e['contract_end'])-core.TODAY()).days if e['contract_end'] else None})
        elif typ=='leave':result.append({**base,**{k:v for k,v in leave(c,e,core).items() if k!='monthly_history'}})
        elif typ=='salaries':
            period_start=start or core.TODAY().replace(day=1).isoformat()
            period_end=end or core.TODAY().replace(day=calendar.monthrange(core.TODAY().year,core.TODAY().month)[1]).isoformat()
            a=dt.date.fromisoformat(period_start);b=dt.date.fromisoformat(period_end)
            if b<a or (b-a).days>366:core.fail('فترة الراتب يجب ألا تتجاوز سنة')
            earned=0
            for offset in range((b-a).days+1):
                day=a+dt.timedelta(days=offset)
                if e['hire_date'] and day.isoformat()<e['hire_date']:continue
                if employment_end(c,e) and day.isoformat()>employment_end(c,e):continue
                earned+=core.salary(e)/calendar.monthrange(day.year,day.month)[1]
            deductions=c.execute("SELECT COALESCE(SUM(salary_deduction),0) FROM absences WHERE employee_id=? AND status='approved' AND day>=? AND day<=?",(e['id'],period_start,period_end)).fetchone()[0]
            unpaid=0
            for request in c.execute("SELECT details FROM requests WHERE employee_id=? AND type='unpaid_leave' AND status='approved'",(e['id'],)):
                unpaid+=sum(item['amount'] for item in json.loads(request['details']).get('unpaid_salary_deductions',[]) if period_start<=item['day']<=period_end)
            result.append({**base,**{k:e[k] for k in ['basic','housing','transport','other','gross_salary','payment_method']},'period_start':period_start,'period_end':period_end,'earned_for_period':round(earned,2),'approved_absence_deductions':round(deductions,2),'unpaid_leave_deductions':round(unpaid,2),'net_estimate':round(max(0,earned-deductions-unpaid),2)})
        elif typ=='requests':
            for r in core.visible_requests(c,u):
                if r['employee_id']==e['id'] and in_range(r['created_at'][:10]):result.append({**base,**{k:r[k] for k in ['id','type','status','created_at','updated_at','notes']}})
        elif typ=='attendance':
            for row in c.execute('SELECT * FROM attendance WHERE employee_id=? ORDER BY day',(e['id'],)):
                if not in_range(row['day']):continue
                worked=0
                if row['check_in'] and row['check_out']:
                    try:worked=(dt.datetime.fromisoformat(row['day']+'T'+row['check_out'])-dt.datetime.fromisoformat(row['day']+'T'+row['check_in'])).total_seconds()/3600
                    except ValueError:pass
                result.append({**base,'day':row['day'],'check_in':row['check_in'],'check_out':row['check_out'],'hours':round(max(0,worked),2),'late':bool(row['check_in'] and row['check_in']>e['work_start']),'early_out':bool(row['check_out'] and row['check_out']<e['work_end']),'source':row['source'],'day_status':'حضور مسجل','overtime_approved_minutes':c.execute("SELECT COALESCE(SUM(minutes),0) FROM overtime_reviews WHERE employee_id=? AND day=? AND status='approved'",(e['id'],row['day'])).fetchone()[0]})
            if start and end:
                for absence in scan_absences(c,e,start,end,core):
                    if absence['status']!='incomplete':result.append({**base,'day':absence['day'],'check_in':None,'check_out':None,'hours':0,'late':False,'early_out':False,'source':'absence_review','day_status':absence['status'],'overtime_approved_minutes':0})
        elif typ not in ('employees','contracts','leave','requests','salaries','attendance'):core.fail('نوع التقرير غير صالح')
    return result


def safe_cell(value):
    text=str(value if value is not None else '')
    return "'"+text if text.startswith(('=','+','-','@','\t','\r')) else text


def xlsx(rows):
    output=io.BytesIO();headers=list(rows[0]) if rows else ['لا توجد بيانات'];grid=[headers]+[[r[k] for k in headers] for r in rows]
    sheet=['<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0" rightToLeft="1"/></sheetViews><sheetData>']
    for i,row in enumerate(grid,1):
        sheet.append(f'<row r="{i}">')
        for value in row:sheet.append('<c t="inlineStr"><is><t xml:space="preserve">'+escape(str(value if value is not None else ''))+'</t></is></c>')
        sheet.append('</row>')
    sheet.append('</sheetData></worksheet>')
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        z.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="EHKAM" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr('xl/worksheets/sheet1.xml',''.join(sheet))
    return output.getvalue()

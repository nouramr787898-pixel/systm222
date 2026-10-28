"""Small internal recruitment, appraisal and training workflows; no external sync."""
import json
import re
import permissions


def migrate(c):
    for sql in [
        'CREATE TABLE IF NOT EXISTS vacancies(id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),title TEXT NOT NULL,department TEXT NOT NULL,closes_on TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ("open","closed")),created_by INTEGER NOT NULL REFERENCES users(id),created_at TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS candidates(id INTEGER PRIMARY KEY,vacancy_id INTEGER NOT NULL REFERENCES vacancies(id),name TEXT NOT NULL,email TEXT NOT NULL,phone TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ("applied","interview","offered","hired","rejected")),note TEXT NOT NULL,consent_on TEXT NOT NULL,retention_until TEXT NOT NULL,created_by INTEGER NOT NULL REFERENCES users(id),created_at TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS appraisals(id INTEGER PRIMARY KEY,employee_id INTEGER NOT NULL REFERENCES employees(id),reviewer_id INTEGER NOT NULL REFERENCES users(id),period TEXT NOT NULL,objectives TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ("draft","submitted","acknowledged")),score INTEGER CHECK(score BETWEEN 1 AND 5),review TEXT NOT NULL DEFAULT "",employee_comment TEXT NOT NULL DEFAULT "",created_by INTEGER NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,submitted_at TEXT,acknowledged_at TEXT,UNIQUE(employee_id,period))',
        'CREATE TABLE IF NOT EXISTS training_records(id INTEGER PRIMARY KEY,employee_id INTEGER NOT NULL REFERENCES employees(id),title TEXT NOT NULL,due_on TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ("assigned","completed")),completed_on TEXT,evidence TEXT NOT NULL DEFAULT "",created_by INTEGER NOT NULL REFERENCES users(id),created_at TEXT NOT NULL)',
        'CREATE INDEX IF NOT EXISTS appraisal_reviewer ON appraisals(reviewer_id,status)',
    ]:c.execute(sql)


def handle(h,c,u,path,method,core):
    def reply(data,status=200):h.write(data,status);return True
    def admin():
        if u['role']!='admin':core.fail('للإدارة فقط',403)
    if path=='/api/vacancies':
        admin()
        if method=='POST':
            d=h.input();company=int(d['company_id']);title=str(d.get('title','')).strip();closes=core.normalize_date(d.get('closes_on'))
            if not title or len(title)>150 or not closes or closes<core.TODAY().isoformat() or not c.execute('SELECT 1 FROM companies WHERE id=? AND archived=0',(company,)).fetchone():core.fail('راجع الوظيفة والشركة وموعد الإغلاق')
            cur=c.execute('INSERT INTO vacancies(company_id,title,department,closes_on,status,created_by,created_at) VALUES(?,?,?,?,"open",?,?)',(company,title,str(d.get('department',''))[:150],closes,u['id'],core.now()));core.record(c,u,'vacancy_create','company',company,cur.lastrowid);c.commit();return reply({'id':cur.lastrowid},201)
        return reply(permissions.filter_rows(c,u,c.execute('SELECT v.*,co.name company_name FROM vacancies v JOIN companies co ON co.id=v.company_id ORDER BY v.id DESC')))
    if path=='/api/candidates':
        admin()
        if method=='POST':
            d=h.input();vid=int(d['vacancy_id']);job=c.execute("SELECT * FROM vacancies WHERE id=? AND status='open'",(vid,)).fetchone();name=str(d.get('name','')).strip();email=str(d.get('email','')).strip();phone=str(d.get('phone','')).strip();consent=core.normalize_date(d.get('consent_on'));retention=core.normalize_date(d.get('retention_until'))
            if job:permissions.company(c,u,job['company_id'],core)
            if not job or job['closes_on']<core.TODAY().isoformat():core.fail('الوظيفة مغلقة')
            if not name or len(name)>150 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email) or not re.fullmatch(r'\+9665\d{8}',phone):core.fail('راجع الاسم والبريد ورقم الجوال السعودي')
            if not consent or consent>core.TODAY().isoformat() or not retention or retention<core.TODAY().isoformat():core.fail('تاريخ الموافقة ومدة الاحتفاظ مطلوبان')
            if c.execute('SELECT 1 FROM candidates WHERE vacancy_id=? AND lower(email)=lower(?)',(vid,email)).fetchone():core.fail('المرشح مسجل لهذه الوظيفة')
            cur=c.execute('INSERT INTO candidates(vacancy_id,name,email,phone,status,note,consent_on,retention_until,created_by,created_at) VALUES(?,?,?,?,"applied",?,?,?,?,?)',(vid,name,email,phone,str(d.get('note',''))[:2000],consent,retention,u['id'],core.now()));core.record(c,u,'candidate_create','company',job['company_id'],{'candidate_id':cur.lastrowid});c.commit();return reply({'id':cur.lastrowid},201)
        return reply(permissions.filter_rows(c,u,c.execute('SELECT a.*,v.title vacancy,v.company_id FROM candidates a JOIN vacancies v ON v.id=a.vacancy_id ORDER BY a.id DESC')))
    if re.fullmatch('/api/candidates/[0-9]+',path) and method=='POST':
        admin();d=h.input();cid=int(path.rsplit('/',1)[1]);row=c.execute('SELECT a.*,v.company_id FROM candidates a JOIN vacancies v ON v.id=a.vacancy_id WHERE a.id=?',(cid,)).fetchone();status=d.get('status');note=str(d.get('note','')).strip()
        if row:permissions.company(c,u,row['company_id'],core)
        allowed={'applied':('interview','rejected'),'interview':('offered','rejected'),'offered':('hired','rejected')}
        if not row or status not in allowed.get(row['status'],()) or not note:core.fail('راجع تسلسل حالة المرشح وملاحظات القرار')
        c.execute('UPDATE candidates SET status=?,note=note||? WHERE id=?',(status,' | '+core.now()+': '+note[:2000],cid));core.record(c,u,'candidate_'+status,'company',row['company_id'],{'candidate_id':cid});c.commit();return reply({'ok':True,'employee_created':False})
    if path=='/api/appraisals':
        if method=='POST':
            admin();d=h.input();e=core.user_allowed_emp(c,u,int(d['employee_id']));reviewer=int(d['reviewer_id']);r=c.execute("SELECT * FROM users WHERE id=? AND role IN ('admin','manager')",(reviewer,)).fetchone();period=str(d.get('period','')).strip();objectives=str(d.get('objectives','')).strip()
            if not r or r['disabled'] or r['employee_id']==e['id'] or (r['role']=='manager' and (r['company_id']!=e['company_id'] or r['employee_id']!=e['manager_id'])):core.fail('المراجع يجب أن يكون الإدارة أو المشرف المباشر')
            permissions.company(c,r,e['company_id'],core)
            if r['role']=='admin' and permissions.profile(c,r) not in ('owner','hr'):core.fail('المراجع يجب أن يكون إدارة عامة أو موارد بشرية')
            if not period or len(period)>50 or not objectives or len(objectives)>4000:core.fail('الفترة والأهداف مطلوبة')
            if c.execute('SELECT 1 FROM appraisals WHERE employee_id=? AND period=?',(e['id'],period)).fetchone():core.fail('التقييم موجود لنفس الفترة')
            cur=c.execute('INSERT INTO appraisals(employee_id,reviewer_id,period,objectives,status,created_by,created_at) VALUES(?,?,?,?,"draft",?,?)',(e['id'],reviewer,period,objectives,u['id'],core.now()));core.record(c,u,'appraisal_create','employee',e['id'],cur.lastrowid);c.commit();return reply({'id':cur.lastrowid},201)
        result=[];visible={e['id'] for e in core.visible_employees(c,u)}
        for r in c.execute('SELECT a.*,e.name employee_name FROM appraisals a JOIN employees e ON e.id=a.employee_id ORDER BY a.id DESC'):
            if r['employee_id'] not in visible:continue
            if u['role']=='admin' or r['reviewer_id']==u['id'] or (r['employee_id']==u['employee_id'] and r['status']!='draft'):result.append(dict(r))
        return reply(result)
    if re.fullmatch('/api/appraisals/[0-9]+',path) and method=='POST':
        d=h.input();aid=int(path.rsplit('/',1)[1]);row=c.execute('SELECT * FROM appraisals WHERE id=?',(aid,)).fetchone()
        if not row:core.fail('التقييم غير موجود',404)
        core.user_allowed_emp(c,u,row['employee_id'])
        if d.get('action')=='submit':
            if row['reviewer_id']!=u['id'] or row['status']!='draft':core.fail('التقييم للمراجع المعين قبل الإرسال فقط',403)
            score=int(d.get('score',0));review=str(d.get('review','')).strip()
            if not 1<=score<=5 or not review or len(review)>4000:core.fail('درجة 1–5 ومراجعة موثقة مطلوبتان')
            c.execute('UPDATE appraisals SET status="submitted",score=?,review=?,submitted_at=? WHERE id=?',(score,review,core.now(),aid));event='submitted'
        elif d.get('action')=='acknowledge':
            if row['employee_id']!=u['employee_id'] or row['status']!='submitted':core.fail('الإقرار للموظف المعني بعد إرسال التقييم',403)
            c.execute('UPDATE appraisals SET status="acknowledged",employee_comment=?,acknowledged_at=? WHERE id=?',(str(d.get('comment',''))[:4000],core.now(),aid));event='acknowledged'
        else:core.fail('إجراء غير صالح')
        core.record(c,u,'appraisal_'+event,'employee',row['employee_id'],aid);c.commit();return reply({'ok':True})
    if path=='/api/training':
        if method=='POST':
            admin();d=h.input();e=core.user_allowed_emp(c,u,int(d['employee_id']));title=str(d.get('title','')).strip();due=core.normalize_date(d.get('due_on'))
            if not title or len(title)>200 or not due:core.fail('اسم الدورة وموعدها مطلوبان')
            cur=c.execute('INSERT INTO training_records(employee_id,title,due_on,status,created_by,created_at) VALUES(?,?,?,"assigned",?,?)',(e['id'],title,due,u['id'],core.now()));core.record(c,u,'training_assign','employee',e['id'],cur.lastrowid);c.commit();return reply({'id':cur.lastrowid},201)
        visible={e['id'] for e in core.visible_employees(c,u)}
        return reply([dict(r) for r in c.execute('SELECT t.*,e.name employee_name FROM training_records t JOIN employees e ON e.id=t.employee_id ORDER BY t.id DESC') if r['employee_id'] in visible])
    if re.fullmatch('/api/training/[0-9]+',path) and method=='POST':
        admin();d=h.input();tid=int(path.rsplit('/',1)[1]);row=c.execute('SELECT * FROM training_records WHERE id=?',(tid,)).fetchone();evidence=str(d.get('evidence','')).strip();date=core.normalize_date(d.get('completed_on'))
        if not row or row['status']!='assigned' or not date or date>core.TODAY().isoformat() or not evidence:core.fail('الدورة المفتوحة وتاريخ الإكمال ومرجع الشهادة مطلوبون')
        core.user_allowed_emp(c,u,row['employee_id'])
        c.execute('UPDATE training_records SET status="completed",completed_on=?,evidence=? WHERE id=?',(date,evidence[:2000],tid));core.record(c,u,'training_complete','employee',row['employee_id'],tid);c.commit();return reply({'ok':True})
    return False

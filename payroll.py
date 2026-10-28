"""Reviewed monthly payroll snapshots, integer minor units and explicit loan ledger.

Records payment evidence only. No bank transfer or country-specific statutory calculation.
"""
import calendar
import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import re
import workforce
import permissions


def cents(value):
    try:number=Decimal(str(value))
    except (InvalidOperation,TypeError,ValueError) as exc:raise ValueError('Invalid money') from exc
    if not number.is_finite() or abs(number)>Decimal('1000000000'):raise ValueError('Invalid money')
    return int((number*100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))


def migrate(c):
    for sql in [
        'CREATE TABLE IF NOT EXISTS loans(id INTEGER PRIMARY KEY,request_id INTEGER NOT NULL UNIQUE REFERENCES requests(id),employee_id INTEGER NOT NULL REFERENCES employees(id),principal_cents INTEGER NOT NULL CHECK(principal_cents>0),installment_cents INTEGER NOT NULL CHECK(installment_cents>0),disbursed_on TEXT NOT NULL,reference TEXT NOT NULL,created_by INTEGER NOT NULL REFERENCES users(id))',
        'CREATE TABLE IF NOT EXISTS payroll_runs(id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),month TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ("draft","review","approved","paid","cancelled")),created_by INTEGER NOT NULL REFERENCES users(id),reviewed_by INTEGER REFERENCES users(id),approved_by INTEGER REFERENCES users(id),created_at TEXT NOT NULL,approved_at TEXT,paid_at TEXT,payment_reference TEXT,source_hash TEXT NOT NULL,UNIQUE(company_id,month))',
        'CREATE TABLE IF NOT EXISTS payroll_lines(id INTEGER PRIMARY KEY,run_id INTEGER NOT NULL REFERENCES payroll_runs(id),employee_id INTEGER NOT NULL REFERENCES employees(id),snapshot TEXT NOT NULL,net_cents INTEGER NOT NULL,UNIQUE(run_id,employee_id))',
        'CREATE TABLE IF NOT EXISTS loan_repayments(id INTEGER PRIMARY KEY,loan_id INTEGER NOT NULL REFERENCES loans(id),amount_cents INTEGER NOT NULL CHECK(amount_cents>0),paid_on TEXT NOT NULL,reference TEXT NOT NULL,run_id INTEGER REFERENCES payroll_runs(id),created_by INTEGER NOT NULL REFERENCES users(id),UNIQUE(loan_id,reference),UNIQUE(loan_id,run_id))',
        'CREATE TABLE IF NOT EXISTS pay_adjustments(id INTEGER PRIMARY KEY,employee_id INTEGER NOT NULL REFERENCES employees(id),month TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN ("bonus","deduction","overtime","commission","statutory")),amount_cents INTEGER NOT NULL CHECK(amount_cents>0),note TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ("pending","approved","rejected")),created_by INTEGER NOT NULL REFERENCES users(id),approved_by INTEGER REFERENCES users(id),created_at TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS payroll_events(id INTEGER PRIMARY KEY,run_id INTEGER NOT NULL REFERENCES payroll_runs(id),status TEXT NOT NULL,user_id INTEGER NOT NULL REFERENCES users(id),note TEXT NOT NULL,created_at TEXT NOT NULL)',
    ]:c.execute(sql)


def month_value(value):
    if not re.fullmatch(r'\d{4}-\d{2}',str(value)):raise ValueError('Invalid month')
    dt.date.fromisoformat(value+'-01')
    return value


def bounds(month):
    first=dt.date.fromisoformat(month_value(month)+'-01')
    return first,first.replace(day=calendar.monthrange(first.year,first.month)[1])


def remaining(c,loan):
    repaid=c.execute('SELECT COALESCE(SUM(amount_cents),0) FROM loan_repayments WHERE loan_id=?',(loan['id'],)).fetchone()[0]
    return loan['principal_cents']-repaid


def commitments(c,eid,exclude=0):
    amount=installment=0
    for r in c.execute("SELECT * FROM requests WHERE employee_id=? AND type='advance' AND status IN ('new','review','approved') AND id!=?",(eid,exclude)):
        loan=c.execute('SELECT * FROM loans WHERE request_id=?',(r['id'],)).fetchone()
        outstanding=remaining(c,loan) if loan else cents(r['amount'])
        if outstanding>0:
            amount+=outstanding;installment+=min(outstanding,loan['installment_cents'] if loan else cents(r['installment']))
    return amount,installment


def validate_advance(c,e,amount,installment,core,exclude=0):
    old,monthly=commitments(c,e['id'],exclude)
    months=core.remaining_months(core.hr.employment_end(c,e),core.TODAY())
    limit=cents(core.salary(e)*.35)
    if cents(amount)+old>limit*months or cents(installment)+monthly>limit:
        core.fail('إجمالي السلف والأقساط القائمة يتجاوز الحد المسموح بعد تجميع الالتزامات')


def assert_open(c,eid,start,end,core):
    if c.execute("SELECT 1 FROM payroll_lines l JOIN payroll_runs r ON r.id=l.run_id WHERE l.employee_id=? AND r.status IN ('approved','paid') AND r.month>=? AND r.month<=?",(eid,start[:7],end[:7])).fetchone():
        core.fail('الفترة لها مسير رواتب معتمد؛ استخدم تسوية في فترة مفتوحة',409)


def calculate(c,e,month,core):
    first,last=bounds(month);earned=Decimal('0');schedules=set()
    end=core.hr.employment_end(c,e)
    for n in range((last-first).days+1):
        day=first+dt.timedelta(days=n);terms=workforce.terms_at(c,e,day.isoformat())
        if terms['hire_date'] and day.isoformat()<terms['hire_date']:continue
        if end and day.isoformat()>end:continue
        gross=sum(Decimal(str(terms[k] or 0)) for k in ('basic','housing','transport','other'))
        earned+=gross/Decimal(calendar.monthrange(day.year,day.month)[1])
        schedules.add((terms['workweek'],terms['work_start'],terms['work_end']))
    earned_cents=cents(earned)
    absence=sum(cents(r[0]) for r in c.execute("SELECT salary_deduction FROM absences WHERE employee_id=? AND status='approved' AND day BETWEEN ? AND ?",(e['id'],str(first),str(last))))
    unpaid=0
    for r in c.execute("SELECT details FROM requests WHERE employee_id=? AND type='unpaid_leave' AND status='approved'",(e['id'],)):
        unpaid+=sum(cents(x['amount']) for x in json.loads(r[0]).get('unpaid_salary_deductions',[]) if str(first)<=x['day']<=str(last))
    additions=deductions=0;adjustments=[]
    for r in c.execute("SELECT * FROM pay_adjustments WHERE employee_id=? AND month=? AND status='approved' ORDER BY id",(e['id'],month)):
        adjustments.append({'id':r['id'],'kind':r['kind'],'amount_cents':r['amount_cents'],'note':r['note']})
        if r['kind'] in ('deduction','statutory'):deductions+=r['amount_cents']
        else:additions+=r['amount_cents']
    loans=[]
    for loan in c.execute('SELECT * FROM loans WHERE employee_id=? AND disbursed_on<=? ORDER BY id',(e['id'],str(last))):
        outstanding=remaining(c,loan)
        if outstanding>0:loans.append({'loan_id':loan['id'],'amount_cents':min(outstanding,loan['installment_cents'])})
    loan_total=sum(x['amount_cents'] for x in loans)
    net=earned_cents+additions-absence-unpaid-deductions-loan_total
    if net<0:core.fail('صافي الموظف '+e['name']+' سالب؛ راجع الاستقطاعات قبل إعداد المسير')
    return {'employee_id':e['id'],'employee_no':e['employee_no'],'name':e['name'],'company_id':e['company_id'],'month':month,'currency':'SAR','earned_cents':earned_cents,'absence_cents':absence,'unpaid_cents':unpaid,'additions_cents':additions,'deductions_cents':deductions,'loan_cents':loan_total,'net_cents':net,'loans':loans,'adjustments':adjustments,'payment_method':e['payment_method'],'history_note':'Current values before first recorded change are a legacy baseline, not verified historical evidence.','statutory_note':'Only explicitly reviewed adjustments are included; no automatic tax, insurance or end-of-service entitlement.'}


def source(c,company,month,core):
    people=c.execute('SELECT e.* FROM employees e WHERE e.company_id=? ORDER BY e.id',(company,)).fetchall()
    lines=[calculate(c,e,month,core) for e in people if (not e['hire_date'] or e['hire_date']<=str(bounds(month)[1])) and (not core.hr.employment_end(c,e) or core.hr.employment_end(c,e)>=str(bounds(month)[0]))]
    if not lines:core.fail('لا يوجد موظفون مستحقون في الفترة')
    raw=json.dumps(lines,sort_keys=True,ensure_ascii=False)
    return lines,hashlib.sha256(raw.encode()).hexdigest()


def handle(h,c,u,path,method,core):
    from urllib.parse import parse_qs,urlparse
    q=parse_qs(urlparse(h.path).query)
    def reply(data,status=200):h.write(data,status);return True
    def admin():
        if u['role']!='admin':core.fail('العمليات المالية للإدارة فقط',403)
    if path=='/api/loans':
        if u['role']=='manager':core.fail('السلف للموظف المعني والإدارة فقط',403)
        rows=[]
        for loan in c.execute('SELECT l.*,e.name employee_name,e.company_id FROM loans l JOIN employees e ON e.id=l.employee_id ORDER BY l.id DESC'):
            if permissions.scope_company(c,u) is not None and loan['company_id']!=permissions.scope_company(c,u):continue
            if u['role']=='employee' and loan['employee_id']!=u['employee_id']:continue
            rows.append({**dict(loan),'remaining_cents':remaining(c,loan)})
        return reply(rows)
    if path=='/api/loans/disburse' and method=='POST':
        admin();d=h.input();c.execute('BEGIN IMMEDIATE');r=c.execute("SELECT * FROM requests WHERE id=? AND type='advance' AND status='approved'",(int(d['request_id']),)).fetchone()
        if not r:core.fail('يلزم طلب سلفة معتمد')
        reference=str(d.get('reference','')).strip();date=core.normalize_date(d.get('paid_on'))
        if not reference or len(reference)>150 or not date or date>core.TODAY().isoformat():core.fail('مرجع الصرف وتاريخه الصحيح مطلوبان')
        if c.execute('SELECT 1 FROM loans WHERE request_id=?',(r['id'],)).fetchone():core.fail('السلفة مصروفة بالفعل',409)
        e=core.user_allowed_emp(c,u,r['employee_id']);validate_advance(c,e,r['amount'],r['installment'],core,r['id'])
        cur=c.execute('INSERT INTO loans(request_id,employee_id,principal_cents,installment_cents,disbursed_on,reference,created_by) VALUES(?,?,?,?,?,?,?)',(r['id'],r['employee_id'],cents(r['amount']),cents(r['installment']),date,reference,u['id']));core.record(c,u,'loan_disbursement','employee',r['employee_id'],{'loan_id':cur.lastrowid,'reference':reference});c.commit();return reply({'id':cur.lastrowid,'money_transferred':False},201)
    if re.fullmatch('/api/loans/[0-9]+/repay',path) and method=='POST':
        admin();d=h.input();c.execute('BEGIN IMMEDIATE');loan=c.execute('SELECT * FROM loans WHERE id=?',(int(path.split('/')[3]),)).fetchone()
        if not loan:core.fail('السلفة غير موجودة',404)
        core.user_allowed_emp(c,u,loan['employee_id'])
        amount=cents(d['amount']);reference=str(d.get('reference','')).strip();date=core.normalize_date(d.get('paid_on'))
        if not reference or len(reference)>150 or amount<=0 or amount>remaining(c,loan) or not date or date<loan['disbursed_on'] or date>core.TODAY().isoformat():core.fail('راجع مبلغ السداد وتاريخه ومرجعه')
        if c.execute('SELECT 1 FROM loan_repayments WHERE loan_id=? AND reference=?',(loan['id'],reference)).fetchone():core.fail('مرجع السداد مسجل بالفعل',409)
        c.execute('INSERT INTO loan_repayments(loan_id,amount_cents,paid_on,reference,created_by) VALUES(?,?,?,?,?)',(loan['id'],amount,date,reference,u['id']));core.record(c,u,'loan_repayment','employee',loan['employee_id'],{'amount_cents':amount,'reference':reference});c.commit();return reply({'ok':True})
    if path=='/api/pay-adjustments':
        admin()
        if method=='POST':
            d=h.input();e=core.user_allowed_emp(c,u,int(d['employee_id']));month=month_value(d['month']);first,last=bounds(month);assert_open(c,e['id'],str(first),str(last),core)
            kind=d.get('kind');note=str(d.get('note','')).strip();amount=cents(d['amount'])
            if kind not in ('bonus','deduction','overtime','commission','statutory') or not note or len(note)>2000 or amount<=0:core.fail('راجع نوع البند وقيمته ومبرره')
            cur=c.execute('INSERT INTO pay_adjustments(employee_id,month,kind,amount_cents,note,status,created_by,created_at) VALUES(?,?,?,?,?,"pending",?,?)',(e['id'],month,kind,amount,note,u['id'],core.now()));core.record(c,u,'pay_adjustment_create','employee',e['id'],cur.lastrowid);c.commit();return reply({'id':cur.lastrowid},201)
        return reply(permissions.filter_rows(c,u,c.execute('SELECT a.*,e.name employee_name,e.company_id FROM pay_adjustments a JOIN employees e ON e.id=a.employee_id ORDER BY a.id DESC')))
    if re.fullmatch('/api/pay-adjustments/[0-9]+',path) and method=='POST':
        admin();d=h.input();c.execute('BEGIN IMMEDIATE');r=c.execute('SELECT * FROM pay_adjustments WHERE id=?',(int(path.rsplit('/',1)[1]),)).fetchone()
        if not r or r['status']!='pending':core.fail('البند غير موجود أو تمت مراجعته',409)
        core.user_allowed_emp(c,u,r['employee_id'])
        if u['id']==r['created_by']:core.fail('يلزم مراجع مالي مختلف عن منشئ البند',403)
        status=d.get('status')
        if status not in ('approved','rejected'):core.fail('قرار غير صالح')
        first,last=bounds(r['month']);assert_open(c,r['employee_id'],str(first),str(last),core)
        c.execute('UPDATE pay_adjustments SET status=?,approved_by=? WHERE id=?',(status,u['id'],r['id']));core.record(c,u,'pay_adjustment_'+status,'employee',r['employee_id'],r['id']);c.commit();return reply({'ok':True})
    if path=='/api/payroll':
        admin()
        if method=='POST':
            d=h.input();c.execute('BEGIN IMMEDIATE');company=int(d['company_id']);month=month_value(d['month'])
            if not c.execute('SELECT 1 FROM companies WHERE id=? AND archived=0',(company,)).fetchone():core.fail('شركة غير متاحة')
            if c.execute('SELECT 1 FROM payroll_runs WHERE company_id=? AND month=?',(company,month)).fetchone():core.fail('المسير موجود؛ أعد بناء المسودة بدل التكرار',409)
            lines,fingerprint=source(c,company,month,core)
            cur=c.execute('INSERT INTO payroll_runs(company_id,month,status,created_by,created_at,source_hash) VALUES(?,?,"draft",?,?,?)',(company,month,u['id'],core.now(),fingerprint));rid=cur.lastrowid
            for line in lines:c.execute('INSERT INTO payroll_lines(run_id,employee_id,snapshot,net_cents) VALUES(?,?,?,?)',(rid,line['employee_id'],json.dumps(line,ensure_ascii=False),line['net_cents']))
            c.execute('INSERT INTO payroll_events(run_id,status,user_id,note,created_at) VALUES(?,"draft",?,?,?)',(rid,u['id'],'created',core.now()));core.record(c,u,'payroll_create','company',company,{'run_id':rid,'month':month});c.commit();return reply({'id':rid},201)
        return reply(permissions.filter_rows(c,u,c.execute('SELECT r.*,co.name company_name,(SELECT SUM(net_cents) FROM payroll_lines WHERE run_id=r.id) total_cents FROM payroll_runs r JOIN companies co ON co.id=r.company_id ORDER BY r.month DESC,r.id DESC')))
    if re.fullmatch('/api/payroll/[0-9]+',path):
        admin();rid=int(path.rsplit('/',1)[1]);run=c.execute('SELECT * FROM payroll_runs WHERE id=?',(rid,)).fetchone()
        if not run:core.fail('المسير غير موجود',404)
        permissions.company(c,u,run['company_id'],core)
        if method=='POST':
            d=h.input();c.execute('BEGIN IMMEDIATE');run=c.execute('SELECT * FROM payroll_runs WHERE id=?',(rid,)).fetchone();action=d.get('action');note=str(d.get('note','')).strip()
            if not note:core.fail('ملاحظات الإجراء مطلوبة')
            if action=='rebuild' and run['status'] in ('draft','review'):
                lines,fingerprint=source(c,run['company_id'],run['month'],core);c.execute('DELETE FROM payroll_lines WHERE run_id=?',(rid,))
                for line in lines:c.execute('INSERT INTO payroll_lines(run_id,employee_id,snapshot,net_cents) VALUES(?,?,?,?)',(rid,line['employee_id'],json.dumps(line,ensure_ascii=False),line['net_cents']))
                c.execute('UPDATE payroll_runs SET source_hash=?,status="draft",created_by=?,reviewed_by=NULL WHERE id=?',(fingerprint,u['id'],rid));target='draft'
            else:
                transitions={'review':('draft','review'),'approve':('review','approved'),'pay':('approved','paid'),'unapprove':('approved','review'),'cancel':('draft','cancelled'),'reopen':('cancelled','draft')}
                if action not in transitions or run['status']!=transitions[action][0]:core.fail('انتقال حالة غير مسموح',409)
                target=transitions[action][1]
                if action in ('approve','pay','review'):
                    _,fingerprint=source(c,run['company_id'],run['month'],core)
                    if fingerprint!=run['source_hash']:core.fail('تغيرت بيانات المصدر؛ أعد بناء المسودة ومراجعتها',409)
                if action=='unapprove':
                    if u['id']==run['created_by']:core.fail('سحب الاعتماد للمراجع المستقل فقط',403)
                    c.execute('UPDATE payroll_runs SET approved_by=NULL,approved_at=NULL WHERE id=?',(rid,))
                if action=='approve':
                    if u['id']==run['created_by']:core.fail('اعتماد المسير يحتاج حساب إدارة مختلف عن منشئه',403)
                    c.execute('UPDATE payroll_runs SET approved_by=?,approved_at=? WHERE id=?',(u['id'],core.now(),rid))
                if action=='review':c.execute('UPDATE payroll_runs SET reviewed_by=? WHERE id=?',(u['id'],rid))
                if action=='pay':
                    if run['month']>core.TODAY().strftime('%Y-%m'):core.fail('لا يمكن تسجيل صرف فترة مستقبلية')
                    reference=str(d.get('reference','')).strip()
                    if not reference or len(reference)>150:core.fail('مرجع الصرف الفعلي مطلوب')
                    for row in c.execute('SELECT snapshot FROM payroll_lines WHERE run_id=?',(rid,)).fetchall():
                        for loan in json.loads(row[0])['loans']:
                            c.execute('INSERT INTO loan_repayments(loan_id,amount_cents,paid_on,reference,run_id,created_by) VALUES(?,?,?,?,?,?)',(loan['loan_id'],loan['amount_cents'],core.TODAY().isoformat(),'payroll:'+str(rid),rid,u['id']))
                    c.execute('UPDATE payroll_runs SET paid_at=?,payment_reference=? WHERE id=?',(core.now(),reference,rid))
                c.execute('UPDATE payroll_runs SET status=? WHERE id=?',(target,rid))
            c.execute('INSERT INTO payroll_events(run_id,status,user_id,note,created_at) VALUES(?,?,?,?,?)',(rid,target,u['id'],note,core.now()));core.record(c,u,'payroll_'+target,'company',run['company_id'],{'run_id':rid,'note':note});c.commit()
        run=dict(c.execute('SELECT * FROM payroll_runs WHERE id=?',(rid,)).fetchone());run['lines']=[json.loads(r[0]) for r in c.execute('SELECT snapshot FROM payroll_lines WHERE run_id=? ORDER BY employee_id',(rid,))];run['events']=[dict(r) for r in c.execute('SELECT * FROM payroll_events WHERE run_id=? ORDER BY id',(rid,))];return reply(run)
    if path=='/api/payslips' and method=='GET':
        if u['role']=='manager' and not u['employee_id']:core.fail('الحساب غير مرتبط',403)
        eid=int(q.get('employee_id',[u['employee_id'] or 0])[0])
        if u['role']!='admin' and eid!=u['employee_id']:core.fail('كشف الراتب خاص بالموظف والإدارة',403)
        core.user_allowed_emp(c,u,eid);result=[]
        for row in c.execute("SELECT l.snapshot,r.id run_id,r.paid_at,r.payment_reference FROM payroll_lines l JOIN payroll_runs r ON r.id=l.run_id WHERE l.employee_id=? AND r.status='paid' ORDER BY r.month DESC",(eid,)):
            result.append({**json.loads(row['snapshot']),'run_id':row['run_id'],'paid_at':row['paid_at'],'payment_reference':row['payment_reference']})
        core.record(c,u,'payslip_view','employee',eid);c.commit();return reply(result)
    return False

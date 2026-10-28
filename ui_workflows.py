"""Focused HR screens: pending actions, daily attendance and supervisor accounts.

These routes do not initialize databases, send messages, or change payroll policy.
"""
import datetime as dt
import re
import permissions
import security
import workforce

TYPE_NAMES = {'annual_leave':'إجازة سنوية','sick_leave':'إجازة مرضية','unpaid_leave':'إجازة غير مدفوعة','advance':'سلفة','permission':'استئذان','punch_fix':'تصحيح حضور وانصراف','termination':'إنهاء تعاقد','delegation':'انتداب','hr_contact':'تواصل مع الموارد البشرية','investigation':'تحقيق','open':'طلب'}


def action_items(c,u,core):
    if permissions.profile(c,u)=='auditor':return []
    rows=core.visible_requests(c,u,False)
    result=[]
    for row in rows:
        if row['status'] not in ('new','review'):continue
        if u['role']!='employee' and (row['employee_id']==u['employee_id'] or row['type']=='delegation'):continue
        if u['role']=='manager' and row['approval_mode']=='supervisor_then_admin' and c.execute("SELECT 1 FROM request_decisions d JOIN employees e ON e.id=? JOIN users actor ON actor.id=d.user_id WHERE d.request_id=? AND d.status='manager_approved' AND COALESCE(d.effective_manager_id,actor.employee_id)=e.manager_id",(row['employee_id'],row['id'])).fetchone():continue
        result.append({'id':row['id'],'employee_id':row['employee_id'],'employee_name':row['employee_name'],'employee_no':row['employee_no'],'type':row['type'],'status':row['status'],'created_at':row['created_at'],'message':row['employee_name']+' طلب '+TYPE_NAMES.get(row['type'],'طلب')})
    return result


def attendance_calendar(c,e,start,end,core,clock=None):
    a=dt.date.fromisoformat(start);b=dt.date.fromisoformat(end)
    if b<a or (b-a).days>366:core.fail('اختر فترة صحيحة لا تتجاوز سنة')
    clock=clock or dt.datetime.now(workforce.zone(c,e['company_id']))
    today=clock.date();b=min(b,today)
    raw={r['day']:dict(r) for r in c.execute('SELECT * FROM attendance WHERE employee_id=? AND day BETWEEN ? AND ?',(e['id'],str(a),str(b)))}
    decisions={r['day']:dict(r) for r in c.execute('SELECT * FROM absences WHERE employee_id=? AND day BETWEEN ? AND ?',(e['id'],str(a),str(b)))}
    reviewable={r['day'] for r in core.hr.scan_absences(c,e,str(a),str(b),core) if r['status']=='pending'} if b>=a else set()
    names=('الاثنين','الثلاثاء','الأربعاء','الخميس','الجمعة','السبت','الأحد');result=[]
    service_end=core.hr.employment_end(c,e)
    for offset in range(max(0,(b-a).days+1)):
        day=a+dt.timedelta(days=offset);iso=day.isoformat();terms=workforce.terms_at(c,e,iso);entry=raw.get(iso);decision=decisions.get(iso)
        item={'employee_id':e['id'],'employee_name':e['name'],'day':iso,'weekday':names[day.weekday()],'check_in':entry['check_in'] if entry else None,'check_out':entry['check_out'] if entry else None,'can_review':False}
        if entry and entry['check_in']:status='present'
        elif (terms['hire_date'] and iso<terms['hire_date']) or (service_end and iso>service_end):status='outside_service'
        elif terms['contract_start'] and iso<terms['contract_start'] and not c.execute('SELECT 1 FROM contracts WHERE employee_id=? AND start_date<=? AND end_date>=?',(e['id'],iso,iso)).fetchone():status='outside_service'
        elif day.weekday() not in {int(x) for x in terms['workweek'].split(',')}:status='rest'
        elif core.hr.holiday(c,terms,iso):status='holiday'
        elif core.hr.coverage(c,e['id'],iso)>=1:status='leave'
        elif decision and decision['status']=='excused':status='excused'
        elif entry and entry['check_out']:status='incomplete'
        else:
            finish=dt.datetime.combine(day,dt.time.fromisoformat(terms['work_end']),tzinfo=clock.tzinfo)
            if terms['work_end']<=terms['work_start']:finish+=dt.timedelta(days=1)
            status='awaiting' if clock<finish else 'absent'
            item['can_review']=status=='absent' and iso in reviewable
            if decision and decision['status']=='approved':status='approved_absence';item['can_review']=False
        item['status']=status;result.append(item)
    return result


def handle(h,c,u,path,method,core):
    from urllib.parse import parse_qs,urlparse
    q=parse_qs(urlparse(h.path).query)
    def reply(data,status=200):h.write(data,status);return True
    if path=='/api/action-items' and method=='GET':return reply(action_items(c,u,core))
    if path=='/api/notifications/unread' and method=='GET':
        return reply({'count':len(action_items(c,u,core))})
    if path=='/api/attendance-calendar' and method=='GET':
        e=core.user_allowed_emp(c,u,int(q.get('employee_id',[u['employee_id'] or 0])[0]))
        start=core.normalize_date(q.get('from',[core.TODAY().replace(day=1).isoformat()])[0]);end=core.normalize_date(q.get('to',[core.TODAY().isoformat()])[0])
        if not start or not end:core.fail('حدد بداية الفترة ونهايتها')
        return reply(attendance_calendar(c,e,start,end,core))
    if path=='/api/supervisor-candidates' and method=='GET':
        if u['role']!='admin':core.fail('إدارة حسابات المشرفين للإدارة فقط',403)
        rows=c.execute("SELECT e.id,e.name,e.name_en,e.employee_no,e.identity_no,e.company_id,co.name company_name,EXISTS(SELECT 1 FROM users a WHERE a.employee_id=e.id AND a.role='manager') has_supervisor FROM employees e JOIN companies co ON co.id=e.company_id WHERE e.archived=0 AND co.archived=0 ORDER BY e.name")
        return reply(permissions.filter_rows(c,u,rows))
    if path=='/api/supervisor-accounts' and method=='POST':
        if u['role']!='admin':core.fail('إدارة حسابات المشرفين للإدارة فقط',403)
        d=h.input();security.attempt(c,u['username'],security.peer(h),core)
        full=c.execute('SELECT password_hash FROM users WHERE id=?',(u['id'],)).fetchone()
        if not core.verifypw(str(d.get('password','')),full[0]):core.fail('كلمة مرور المسؤول الحالية غير صحيحة',403)
        c.execute('BEGIN IMMEDIATE');e=core.user_allowed_emp(c,u,int(d['employee_id']))
        if e['archived'] or not c.execute('SELECT 1 FROM companies WHERE id=? AND archived=0',(e['company_id'],)).fetchone():core.fail('الموظف أو الشركة غير نشط')
        if int(d.get('company_id',e['company_id']))!=e['company_id']:core.fail('حساب المشرف يجب أن يتبع شركة الموظف الحالية')
        username=str(d.get('username','')).strip();password=str(d.get('new_password',''))
        if not re.fullmatch(r'[A-Za-z0-9_.-]{3,64}',username) or not 12<=len(password)<=128:core.fail('اسم دخول مستقل 3–64 حرفًا وكلمة مرور 12–128 حرفًا مطلوبان')
        if c.execute("SELECT 1 FROM users WHERE employee_id=? AND role='manager'",(e['id'],)).fetchone():core.fail('الموظف له حساب مشرف بالفعل',409)
        if c.execute('SELECT 1 FROM users WHERE username=?',(username,)).fetchone():core.fail('اسم الدخول مستخدم بالفعل',409)
        cur=c.execute("INSERT INTO users(username,password_hash,role,employee_id,company_id,display_name,must_change_password) VALUES(?,?,'manager',?,?,?,1)",(username,core.hashpw(password),e['id'],e['company_id'],e['name']))
        core.record(c,u,'supervisor_account_create','employee',e['id'],{'user_id':cur.lastrowid,'username':username});c.commit()
        return reply({'id':cur.lastrowid,'employee_id':e['id'],'must_change_password':True},201)
    if path=='/api/company-employee-move' and method=='POST':
        if permissions.profile(c,u)!='owner':core.fail('النقل بين الشركات للإدارة العامة فقط',403)
        d=h.input();c.execute('BEGIN IMMEDIATE');e=core.user_allowed_emp(c,u,int(d['employee_id']));target=int(d['company_id']);reason=str(d.get('reason','')).strip();today=core.TODAY().isoformat()
        if e['archived'] or target==e['company_id']:core.fail('اختر موظفًا نشطًا من شركة أخرى')
        if not c.execute('SELECT 1 FROM companies WHERE id=? AND archived=0',(target,)).fetchone():core.fail('الشركة الجديدة غير متاحة')
        if not reason or len(reason)>2000:core.fail('مبرر النقل مطلوب')
        if c.execute("SELECT 1 FROM requests WHERE employee_id=? AND status IN ('new','review')",(e['id'],)).fetchone():core.fail('راجع الطلبات المعلقة قبل نقل الموظف')
        if c.execute('SELECT 1 FROM employees WHERE manager_id=? AND archived=0',(e['id'],)).fetchone():core.fail('أعد إسناد موظفي المشرف قبل نقله')
        if c.execute('SELECT 1 FROM employee_assets WHERE employee_id=? AND returned_on IS NULL',(e['id'],)).fetchone():core.fail('راجع العهد القائمة قبل النقل')
        if core.payroll.commitments(c,e['id'])[0]>0:core.fail('راجع السلف والالتزامات القائمة قبل النقل')
        core.payroll.assert_open(c,e['id'],today,today,core)
        if c.execute("SELECT 1 FROM payroll_runs WHERE company_id=? AND month=? AND status IN ('approved','paid')",(target,today[:7])).fetchone():core.fail('مسير الشركة الجديدة مغلق لهذه الفترة',409)
        c.execute('UPDATE employees SET company_id=?,manager_id=NULL,site_id=NULL,schedule_id=NULL WHERE id=?',(target,e['id']))
        c.execute('UPDATE users SET company_id=? WHERE employee_id=?',(target,e['id']))
        fresh=c.execute('SELECT * FROM employees WHERE id=?',(e['id'],)).fetchone();workforce.save_terms(c,e,fresh,today,u['id'],reason,core)
        c.execute('DELETE FROM auth_sessions WHERE user_id IN (SELECT id FROM users WHERE employee_id=?)',(e['id'],))
        core.record(c,u,'employee_company_move','employee',e['id'],{'from_company':e['company_id'],'to_company':target,'effective_on':today,'reason':reason});c.commit()
        return reply({'ok':True,'employee_id':e['id'],'effective_on':today})
    return False

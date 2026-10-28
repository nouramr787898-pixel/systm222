"""Global administrative duty separation. Company-level tenancy is unchanged.

Only the owner may grant roles. New routes are denied to restricted administrators
until explicitly classified; UI visibility is never used as authorization.
"""


def migrate(c):
    c.execute('CREATE TABLE IF NOT EXISTS admin_profiles(user_id INTEGER PRIMARY KEY REFERENCES users(id),profile TEXT NOT NULL CHECK(profile IN ("hr","payroll","auditor")))')


    if 'company_id' not in {r[1] for r in c.execute('PRAGMA table_info(admin_profiles)')}:c.execute('ALTER TABLE admin_profiles ADD COLUMN company_id INTEGER REFERENCES companies(id)')


def profile(c,u):
    if u['role']!='admin':return u['role']
    row=c.execute('SELECT profile FROM admin_profiles WHERE user_id=?',(u['id'],)).fetchone()
    return row[0] if row else 'owner'


def guard(h,c,u,path,method,core):
    scope_request(h,c,u,path,method,core)
    role=profile(c,u)
    if path=='/api/assistant' and method in ('GET','POST'):return
    if role in ('owner','manager','employee'):return
    if role=='hr' and path in ('/api/supervisor-candidates','/api/supervisor-accounts','/api/action-items','/api/attendance-calendar'):return
    if path=='/api/action-items' and method=='GET':return
    if path in ('/api/me','/api/logout') or path.startswith('/api/security/'):return
    if role=='auditor':
        if method!='GET' or not (path in ('/api/email-outbox','/api/delegations','/api/attachments','/api/employees','/api/companies','/api/requests','/api/dashboard','/api/audit','/api/payroll','/api/loans','/api/pay-adjustments','/api/reports','/api/notifications','/api/notifications/unread') or path.startswith('/api/payroll/')):core.fail('حساب المراجع للقراءة فقط',403)
        return
    financial=path.startswith(('/api/payroll','/api/loans','/api/pay-adjustments','/api/payslips'))
    if role=='hr':
        if path=='/api/admin/accounts' and method=='GET':return
        if financial or path=='/api/employment-letter' or path=='/api/terms' or path=='/api/audit' or path.startswith('/api/admin/') or path.startswith('/api/import/') or path=='/api/report.csv':core.fail('العملية تتطلب صلاحية مالية أو إدارة عامة',403)
        if path=='/api/policy' and method=='POST' and any(k in h.input() for k in ('salary_basis','advance_min_days')):core.fail('السياسة المالية للإدارة العامة',403)
        if path=='/api/reports' and 'type=salaries' in h.path:core.fail('التقرير المالي لصلاحية الرواتب',403)
        if path.startswith('/api/employees') and method=='POST':
            data=h.input()
            if any(k in data for k in ('basic','housing','transport','other','iban','bank_name')):core.fail('تعديل الراتب والبنك لصلاحية الرواتب',403)
        allowed=('/api/employees','/api/companies','/api/requests','/api/dashboard','/api/supervisors','/api/employee-files','/api/overtime','/api/notifications','/api/policy','/api/schedules','/api/sites','/api/holidays','/api/leave','/api/contracts','/api/absences','/api/tasks','/api/assets','/api/alerts','/api/reports','/api/archive','/api/vacancies','/api/candidates','/api/appraisals','/api/training','/api/company-timezone','/api/employee-transfer')
        if not any(path==p or path.startswith(p+'/') for p in allowed):core.fail('العملية غير مسموحة لدور الموارد البشرية',403)
    elif role=='payroll':
        if not financial and path not in ('/api/employees','/api/companies','/api/dashboard','/api/requests','/api/notifications','/api/notifications/unread','/api/notifications/read','/api/reports') and not path.startswith('/api/attachments/') and not path.startswith('/api/requests/') and not path.startswith('/api/employees/'):core.fail('العملية غير مسموحة لدور الرواتب',403)
        if method=='POST' and path=='/api/employees':core.fail('إنشاء الموظفين للموارد البشرية',403)
        if method=='POST' and path.startswith('/api/employees/'):
            if not set(h.input()).issubset({'basic','housing','transport','other','iban','bank_name','payment_method','change_reason','effective_on'}):core.fail('دور الرواتب يعدل الحقول المالية فقط',403)
        if method=='POST' and path=='/api/companies':core.fail('إنشاء الشركات للإدارة',403)
        if method=='POST' and path=='/api/requests':core.fail('تقديم الطلب من حساب الموظف',403)
        if path=='/api/reports':
            from urllib.parse import parse_qs,urlparse
            if parse_qs(urlparse(h.path).query).get('type',['employees'])[0] not in ('employees','salaries','contracts'):core.fail('التقرير غير مسموح لدور الرواتب',403)


def request_guard(c,u,r,core):
    role=profile(c,u)
    if role=='payroll' and r['type']!='advance':core.fail('دور الرواتب يراجع السلف فقط',403)
    if role=='hr' and r['type']=='advance':core.fail('اعتماد السلف لدور الرواتب',403)


def scope_company(c,u):
    if u['role']!='admin':return None
    row=c.execute('SELECT company_id FROM admin_profiles WHERE user_id=?',(u['id'],)).fetchone()
    return row[0] if row else None


def company(c,u,company_id,core):
    scope=scope_company(c,u)
    if scope is not None and int(company_id)!=scope:core.fail('الشركة خارج نطاق صلاحيات الحساب',403)


def person(c,u,eid,core):
    row=c.execute('SELECT company_id FROM employees WHERE id=?',(eid,)).fetchone()
    if not row:core.fail('الموظف غير موجود',404)
    company(c,u,row[0],core)


def filter_rows(c,u,rows,key='company_id'):
    scope=scope_company(c,u)
    return [dict(r) for r in rows if scope is None or r[key]==scope]


def scope_request(h,c,u,path,method,core):
    from urllib.parse import parse_qs,urlparse
    import re
    scope=scope_company(c,u)
    if scope is None:return
    query=parse_qs(urlparse(h.path).query)
    if query.get('company_id'):company(c,u,query['company_id'][0],core)
    if query.get('employee_id'):person(c,u,int(query['employee_id'][0]),core)
    if method=='POST':
        body=h.input()
        if body.get('company_id'):company(c,u,body['company_id'],core)
        if body.get('employee_id'):person(c,u,int(body['employee_id']),core)
    if re.fullmatch('/api/companies/[0-9]+',path):company(c,u,path.rsplit('/',1)[1],core)
    if path=='/api/archive' and method=='POST':
        body=h.input()
        if body.get('target')=='company':company(c,u,body.get('id',0),core)
        elif body.get('target')=='employee':person(c,u,int(body.get('id',0)),core)
    if path=='/api/employee-transfer':core.fail('النقل بين الشركات للإدارة العامة',403)

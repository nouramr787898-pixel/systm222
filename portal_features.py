"""Scoped employee answers, attendance sites and recruitment workflows."""
import base64
import calendar
import datetime as dt
import json
import re
import secrets
import unicodedata

CONSENT='أوافق على حفظ بياناتي ومراجعتها لغرض التقديم لهذه الوظيفة، وأُقرّ بصحة البيانات المقدَّمة لشركة إحكام المسار.'


def migrate(c):
    for table, columns in {
        'employees': {'allowed_site_ids': "TEXT NOT NULL DEFAULT '[]'"},
        'attendance': {'check_in_site': 'TEXT', 'check_out_site': 'TEXT'},
        'email_outbox': {'body': "TEXT NOT NULL DEFAULT ''"},
    }.items():
        existing={r[1] for r in c.execute('PRAGMA table_info('+table+')')}
        for key,kind in columns.items():
            if key not in existing:c.execute(f'ALTER TABLE {table} ADD COLUMN {key} {kind}')
    c.execute('CREATE TABLE IF NOT EXISTS whatsapp_outbox(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),request_id INTEGER NOT NULL REFERENCES requests(id),destination TEXT NOT NULL,parameters TEXT NOT NULL,status TEXT NOT NULL DEFAULT "pending_configuration",attempts INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,sent_at TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS vacancies(id INTEGER PRIMARY KEY,title TEXT NOT NULL,description TEXT NOT NULL,closes_on TEXT NOT NULL,retain_until TEXT NOT NULL,token TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS applications(id INTEGER PRIMARY KEY,vacancy_id INTEGER NOT NULL REFERENCES vacancies(id),name TEXT NOT NULL,email TEXT NOT NULL,phone TEXT NOT NULL,notes TEXT NOT NULL,cv_name TEXT NOT NULL,cv_mime TEXT NOT NULL,cv BLOB NOT NULL,consent_text TEXT NOT NULL,created_at TEXT NOT NULL)')


def purge_expired(c,today):
    c.execute('DELETE FROM applications WHERE vacancy_id IN (SELECT id FROM vacancies WHERE retain_until<?)',(today,))


def sites(c,e):
    ids=json.loads(e['allowed_site_ids'] or '[]')
    if not ids and e['site_id']:ids=[e['site_id']]
    if ids:
        return [dict(r) for r in c.execute('SELECT id,name,latitude,longitude,radius FROM work_sites WHERE company_id=? AND id IN ('+','.join('?' for _ in ids)+') ORDER BY id',[e['company_id']]+ids)]
    co=c.execute('SELECT * FROM companies WHERE id=?',(e['company_id'],)).fetchone()
    return [{'id':0,'name':co['name'],'latitude':co['latitude'],'longitude':co['longitude'],'radius':co['geofence_radius']}]


def validate_sites(c,e,value,core):
    if not isinstance(value,list) or len(value)>100 or any(type(x)!=int or x<=0 for x in value):core.fail('اختر مواقع العمل من القائمة')
    ids=sorted(set(value))
    if ids and c.execute('SELECT count(*) FROM work_sites WHERE company_id=? AND id IN ('+','.join('?' for _ in ids)+')',[e['company_id']]+ids).fetchone()[0]!=len(ids):core.fail('الموقع لا يتبع شركة الموظف')
    return json.dumps(ids)


def normalize(value):
    value=unicodedata.normalize('NFKC',str(value)).lower()
    value=re.sub('[\u064b-\u065f\u0670ـ]','',value)
    return value.translate(str.maketrans('أإآىة٠١٢٣٤٥٦٧٨٩','ااايه0123456789'))


def period(question,core):
    today=core.TODAY();q=normalize(question)
    dates=re.findall(r'\d{4}-\d{2}-\d{2}|\d{1,2}[/\-]\d{1,2}[/\-]\d{4}',q)
    if dates:
        start=core.normalize_date(dates[0]);end=core.normalize_date(dates[-1])
    elif 'امس' in q or 'مبارح' in q:start=end=(today-dt.timedelta(days=1)).isoformat()
    elif 'شهر' in q:
        day=today.replace(day=1)
        if 'الماضي' in q or 'اللي فات' in q:day=(day-dt.timedelta(days=1)).replace(day=1)
        start=day.isoformat();end=min(today,day.replace(day=calendar.monthrange(day.year,day.month)[1])).isoformat()
    elif 'اسبوع' in q:start=(today-dt.timedelta(days=6)).isoformat();end=today.isoformat()
    elif 'سنه' in q or 'العام' in q:start=today.replace(month=1,day=1).isoformat();end=today.isoformat()
    else:start=end=today.isoformat()
    if end<start or (dt.date.fromisoformat(end)-dt.date.fromisoformat(start)).days>366:core.fail('اختر فترة صحيحة لا تزيد على سنة')
    return start,end


def answer(c,u,d,core):
    question=str(d.get('question','')).strip()
    if not question or len(question)>2000:core.fail('اكتب سؤالًا لا يتجاوز 2000 حرف')
    q=normalize(question);people=core.visible_employees(c,u)
    matches=[e for e in people if (e['employee_no'] and re.search(r'(?<![\w-])'+re.escape(normalize(e['employee_no']))+r'(?![\w-])',q)) or normalize(e['name']) in q or (e['name_en'] and normalize(e['name_en']) in q)]
    if not matches:
        ignored={'الدكتور','دكتور','الموظف','موظف','الموظفه','موظفه','السيد','السيده','د','د.'}
        words=set(re.findall(r'[\w]+',q))-ignored
        for e in people:
            parts=set(normalize(e['name']).split())-ignored
            if parts and parts.intersection(words):matches.append(e)
    if d.get('employee_id') is not None:
        eid=int(d['employee_id']);matches=[e for e in people if e['id']==eid]
    if not matches and d.get('employee_id') is None and u['role']=='employee' and any(x in q for x in ('حضوري','غيابي','راتبي','اجازاتي','طلباتي','رصيدي','بياناتي')):matches=people
    if len(matches)>1:return {'answer':'يوجد أكثر من موظف مطابق؛ اختر الموظف المقصود.','choices':[{'id':e['id'],'name':e['name'],'employee_no':e['employee_no'],'company_name':e['company_name']} for e in matches]}
    if not matches:
        if any(x in q for x in ('عدد الموظفين','كم موظف','عدد الشركات')):return {'answer':f"الموظفون المتاحون لحسابك: {len(people)}، الشركات: {len({e['company_id'] for e in people})}."}
        return {'answer':'لم أجد موظفًا مطابقًا ضمن البيانات المتاحة لحسابك. اذكر الاسم أو الرقم الوظيفي المسجل.'}
    e=matches[0];core.user_allowed_emp(c,u,e['id']);start,end=period(question,core)
    rows=[dict(r) for r in c.execute('SELECT * FROM attendance WHERE employee_id=? AND day BETWEEN ? AND ? ORDER BY day',(e['id'],start,end))]
    out=[f"{e['name']} ({e['employee_no']}) — شركة {e['company_name']}."]
    if start==end:
        row=rows[0] if rows else None
        if row and row['check_in']:
            out.append(f"نعم، مسجل له حضور بتاريخ {start} الساعة {row['check_in']}، في موقع {row['check_in_site'] or e['company_name']}.")
            out.append('الانصراف: '+(row['check_out'] or 'لم يسجل بعد')+'.')
        else:out.append(f'لا توجد بصمة حضور مسجلة بتاريخ {start}؛ هذا لا يثبت غيابًا معتمدًا.')
    else:
        absent=core.hr.scan_absences(c,e,start,end,core)
        approved=sum(x['status']=='approved' for x in absent)
        pending=sum(x['status']=='pending' for x in absent)
        out.append(f"من {start} إلى {end}: أيام الحضور المسجلة {sum(bool(r['check_in']) for r in rows)}، الغياب المعتمد {approved}، وأيام بلا بصمة وتنتظر المراجعة {pending}.")
    seconds=0
    for row in rows:
        if row['check_in'] and row['check_out']:
            a=dt.datetime.fromisoformat(row['day']+'T'+row['check_in']);b=dt.datetime.fromisoformat(row['day']+'T'+row['check_out'])
            if b>=a:seconds+=int((b-a).total_seconds())
    out.append(f'إجمالي مدة الحضور المكتملة: {seconds//3600:02d}:{seconds//60%60:02d}:{seconds%60:02d}.')
    if any(x in q for x in ('اجاز','رصيد')):
        l=core.leave(c,e);out.append(f"رصيد الإجازات المتاح: {l['available']} يوم، المستخدم: {l['approved_used']} يوم.")
    if any(x in q for x in ('راتب','مرتب','بدل','رواتب')):out.append(f"إجمالي الراتب: {core.salary(e)} ريال؛ الأساسي: {e['basic']}، السكن: {e['housing']}، النقل: {e['transport']}، بدلات أخرى: {e['other']}.")
    if any(x in q for x in ('عقد','وظيف','قسم','بيانات','تليفون','هاتف','بريد')):out.append(f"الوظيفة: {e['job']}، القسم: {e['department']}، العقد من {e['contract_start']} إلى {e['contract_end']}، البريد: {e['email'] or 'غير مسجل'}، الهاتف: {e['phone_country'] or ''}{e['phone_local'] or ''}.")
    if any(x in q for x in ('مشرف','مدير')):out.append('المشرف المباشر: '+(e['manager_name'] or 'غير محدد')+'.')
    if any(x in q for x in ('موقع','فين','مكان','شركه','دوام','جدول')):
        allowed=sites(c,e);out.append('مواقع البصمة المسموحة: '+('، '.join(s['name'] for s in allowed) or 'غير محددة')+'.')
        out.append(f"الدوام من {e['work_start']} إلى {e['work_end']}، الحالة الوظيفية: {e['status']}.")
    if any(x in q for x in ('مستند','ملف','وثيق')):
        files=[r['name'] for r in c.execute('SELECT name FROM employee_files WHERE employee_id=? ORDER BY id',(e['id'],))]
        out.append('المستندات المسجلة: '+('، '.join(files) or 'لا توجد مستندات')+'.')
    if 'طلب' in q:

        requests=[r for r in core.visible_requests(c,u) if r['employee_id']==e['id'] and start<=r['created_at'][:10]<=end]
        labels={'new':'قيد المراجعة','pending':'قيد المراجعة','approved':'مقبول','rejected':'مرفوض','review':'قيد المراجعة','manager_approved':'بانتظار الإدارة','cancelled':'ملغي','closed':'مغلق','accepted':'مقبول','declined':'مرفوض'}
        out.append('الطلبات خلال الفترة: '+('؛ '.join(f"رقم {r['id']} ({r['type']}): {labels.get(r['status'],r['status'])}" for r in requests) or 'لا توجد طلبات مسجلة')+'.')
    out.append('المصدر: السجلات المتاحة لحسابك. موقع البصمة ليس تتبعًا للموقع الحالي.')
    return {'answer':'\n'.join(out),'employee_id':e['id'],'from':start,'to':end}


def handle(h,c,u,path,method,core):
    if path=='/api/whatsapp-outbox' and method=='GET':
        if u['role']!='admin':core.fail('هذه العملية للإدارة فقط',403)
        h.write([dict(r) for r in c.execute('SELECT id,request_id,status,attempts,created_at,sent_at FROM whatsapp_outbox ORDER BY id DESC LIMIT 100')]);return True
    if path=='/api/assistant' and method=='POST':h.write(answer(c,u,h.input(),core));return True
    if path=='/api/attendance-sites' and method=='GET':
        e=core.user_allowed_emp(c,u,u['employee_id'] or 0);h.write(sites(c,e));return True
    if path.startswith(('/api/vacancies','/api/applications')):
        purge_expired(c,core.TODAY().isoformat());c.commit()
    if path=='/api/vacancies':
        if u['role']!='admin':core.fail('هذه العملية للإدارة فقط',403)
        if method=='POST':
            d=h.input();title=str(d.get('title','')).strip();description=str(d.get('description','')).strip()
            close=core.normalize_date(d.get('closes_on'));retain=core.normalize_date(d.get('retain_until'))
            if not title or len(title)>200 or len(description)>10000 or not close or not retain or close<core.TODAY().isoformat() or retain<close:core.fail('أدخل اسم الوظيفة وتواريخ صحيحة؛ الحفظ حتى تاريخ بعد إغلاق التقديم')
            c.execute('INSERT INTO vacancies(title,description,closes_on,retain_until,token,created_at) VALUES(?,?,?,?,?,?)',(title,description,close,retain,secrets.token_urlsafe(24),core.now()));c.commit()
        h.write([dict(r) for r in c.execute('SELECT * FROM vacancies ORDER BY id DESC')]);return True
    if re.fullmatch(r'/api/vacancies/\d+/applications',path) and method=='GET':
        if u['role']!='admin':core.fail('هذه العملية للإدارة فقط',403)
        h.write([dict(r) for r in c.execute('SELECT id,name,email,phone,notes,cv_name,created_at,consent_text FROM applications WHERE vacancy_id=? ORDER BY id DESC',(int(path.split('/')[3]),))]);return True
    if re.fullmatch(r'/api/applications/\d+/cv',path) and method=='GET':
        if u['role']!='admin':core.fail('هذه العملية للإدارة فقط',403)
        row=c.execute('SELECT cv,cv_name FROM applications WHERE id=?',(int(path.split('/')[3]),)).fetchone()
        if not row:core.fail('الملف غير موجود',404)
        from urllib.parse import quote
        h.write(row['cv'],ctype='application/octet-stream',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(row['cv_name'],safe=''),'X-Content-Type-Options':'nosniff'});return True
    return False


def public(h,c,path,method,core):
    match=re.fullmatch('/(?:api/)?apply/([A-Za-z0-9_-]{20,80})',path)
    if not match:return False
    job=c.execute('SELECT * FROM vacancies WHERE token=?',(match[1],)).fetchone()
    if not job:core.fail('الوظيفة غير موجودة',404)
    if job['closes_on']<core.TODAY().isoformat():core.fail('انتهت فترة التقديم لهذه الوظيفة',410)
    if method=='GET':
        if path.startswith('/api/'):
            h.write({k:job[k] for k in ('title','description','closes_on','retain_until')})
        else:h.write((core.BASE/'apply.html').read_bytes(),ctype='text/html')
        return True
    if method!='POST' or not path.startswith('/api/'):core.fail('المسار غير موجود',404)
    key=('job-application',h.client_address[0]);now=int(core.time.time())
    with core.LOCK:
        times=[t for t in core.RESET_ATTEMPTS.get(key,[]) if now-t<3600]
        if len(times)>=10:core.fail('محاولات كثيرة؛ حاول لاحقًا',429)
        times.append(now);core.RESET_ATTEMPTS[key]=times
    d=h.input()
    if d.get('consent') is not True:core.fail('الموافقة والإقرار بصحة البيانات مطلوبان')
    name=str(d.get('name','')).strip();email=str(d.get('email','')).strip();phone=str(d.get('phone','')).strip();notes=str(d.get('notes','')).strip()
    if not name or len(name)>200 or len(email)>254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email) or not re.fullmatch(r'\d{10}',phone) or len(notes)>10000:core.fail('راجع الاسم والبريد ورقم الجوال (10 أرقام)')
    cv=d.get('cv')
    if not isinstance(cv,dict):core.fail('السيرة الذاتية مطلوبة')
    filename=str(cv.get('name',''))
    encoded=cv.get('data','')
    if not isinstance(encoded,str) or len(encoded)>2800000:core.fail('السيرة الذاتية أكبر من 2 ميجابايت')
    try:content=base64.b64decode(encoded,validate=True)
    except ValueError:core.fail('ملف غير صالح')
    ext=filename.lower().rsplit('.',1)[-1]
    valid=(ext=='pdf' and content.startswith(b'%PDF-')) or (ext=='docx' and content.startswith(b'PK\x03\x04')) or (ext=='doc' and content.startswith(bytes.fromhex('D0CF11E0A1B11AE1')))
    if not valid or not 0<len(content)<=2*1024*1024 or len(filename)>200:core.fail('أرفق PDF أو Word صحيحًا حتى 2 ميجابايت')
    c.execute('INSERT INTO applications(vacancy_id,name,email,phone,notes,cv_name,cv_mime,cv,consent_text,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(job['id'],name,email,phone,notes,filename,'application/octet-stream',content,CONSENT,core.now()));c.commit();h.write({'message':'تم إرسال التقديم بنجاح'},201);return True

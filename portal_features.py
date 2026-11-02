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


REQUEST_KINDS={'annual_leave':'إجازة سنوية','sick_leave':'إجازة مرضية','unpaid_leave':'إجازة غير مدفوعة','advance':'سلفة','permission':'استئذان','punch_fix':'تصحيح بصمة','termination':'إنهاء تعاقد','delegation':'انتداب','hr_contact':'تواصل مع الموارد البشرية','investigation':'تحقيق','open':'طلب مفتوح'}
REQUEST_STATES={'new':'قيد المراجعة','pending':'قيد المراجعة','review':'قيد المراجعة','approved':'مقبول','rejected':'مرفوض','manager_approved':'بانتظار الإدارة','cancelled':'ملغي','closed':'مغلق','accepted':'مقبول','declined':'مرفوض'}


def request_summary(c,u,question,core,employee_id=None,company_id=None):
    start,end=period(question,core);q=normalize(question)
    rows=[r for r in core.visible_requests(c,u) if start<=r['created_at'][:10]<=end and (employee_id is None or r['employee_id']==employee_id) and (company_id is None or r['company_id']==company_id)]
    filters=[(x,states) for x,states in [('مرفوض',{'rejected','declined'}),('مقبول',{'approved','accepted'}),('معتمد',{'approved','accepted'}),('قيد المراجعه',{'new','pending','review','manager_approved'}),('معلق',{'new','pending','review','manager_approved'})] if x in q]
    if filters:rows=[r for r in rows if r['status'] in set.union(*(states for _,states in filters))]
    out=[f'ملخص الطلبات الواردة من {start} إلى {end} ضمن صلاحيات حسابك: {len(rows)} طلب.']
    if not rows:return {'answer':'\n'.join(out+['لا توجد طلبات مطابقة في هذه الفترة.']),'from':start,'to':end}
    from collections import Counter
    states=Counter(REQUEST_STATES.get(r['status'],r['status']) for r in rows)
    kinds=Counter(REQUEST_KINDS.get(r['type'],r['type']) for r in rows)
    out+=['الحالات: '+ '، '.join(f'{k}: {v}' for k,v in states.items()),'الأنواع: '+ '، '.join(f'{k}: {v}' for k,v in kinds.items())]
    for r in rows[:30]:out.append(f"• طلب #{r['id']} — {r['employee_name']} ({r['employee_no']}) — {r['company_name']} — {REQUEST_KINDS.get(r['type'],r['type'])} — {REQUEST_STATES.get(r['status'],r['status'])}.")
    if len(rows)>30:out.append(f'عُرض أحدث 30 طلبًا من {len(rows)}؛ حدّد موظفًا أو فترة أقصر للتفاصيل.')
    return {'answer':'\n'.join(out),'from':start,'to':end}


def system_summary(c,u,question,core,people,company_id=None):
    q=normalize(question);start,end=period(question,core)
    if 'طلب' in q:return request_summary(c,u,question,core,company_id=company_id)
    if any(word in q for word in ('السيستم','النظام','ملخص عام')):
        result=request_summary(c,u,question,core,company_id=company_id);result['answer']=f"الموظفون المتاحون {len(people)}، الشركات {len({e['company_id'] for e in people})}.\n"+result['answer'];return result
    if any(word in q for word in ('حضر','حضور','غاب','غياب','حاضر','غائب')):
        out=[f'ملخص الحضور من {start} إلى {end} ضمن صلاحيات حسابك:'];count=0
        for e in people:
            rows=c.execute('SELECT day,check_in,check_out FROM attendance WHERE employee_id=? AND day BETWEEN ? AND ? ORDER BY day',(e['id'],start,end)).fetchall()
            present=sum(bool(r['check_in']) for r in rows);count+=present
            if start==end:out.append(f"• {e['name']} ({e['employee_no']}) — {e['company_name']}: "+('حاضر' if present else 'لم يسجل حضورًا')+'.')
            else:
                missing=core.hr.scan_absences(c,e,start,end,core)
                out.append(f"• {e['name']} ({e['employee_no']}) — {e['company_name']}: حضور {present} يوم، غياب معتمد {sum(r['status']=='approved' for r in missing)}، أيام بانتظار المراجعة {sum(r['status']=='pending' for r in missing)}.")
        out.insert(1,f'إجمالي أيام الحضور المسجلة: {count}. عدم وجود بصمة لا يثبت غيابًا معتمدًا.')
        return {'answer':'\n'.join(out),'from':start,'to':end}
    if any(word in q for word in ('موظف','موظفين','شرك','شركات')):
        return {'answer':f"الموظفون المتاحون لحسابك: {len(people)}، الشركات: {len({e['company_id'] for e in people})}.\n"+'\n'.join(f"• {e['name']} ({e['employee_no']}) — {e['company_name']} — {e['job']}" for e in people)}
    if any(word in q for word in ('اجاز','ارصده','رواتب','راتب','عقد')):
        lines=[]
        for e in people:
            if 'اجاز' in q or 'ارصده' in q:detail=f"رصيد الإجازات {core.leave(c,e)['available']} يوم"
            elif 'عقد' in q:detail=f"العقد من {e['contract_start']} إلى {e['contract_end']}"
            else:detail=f"إجمالي الراتب {core.salary(e)} ريال"
            lines.append(f"• {e['name']} ({e['employee_no']}) — {e['company_name']}: {detail}.")
        return {'answer':'\n'.join(lines) or 'لا توجد بيانات متاحة لحسابك.'}
    return None


def answer(c,u,d,core):
    question=str(d.get('question','')).strip()
    if not question or len(question)>2000:core.fail('اكتب سؤالًا لا يتجاوز 2000 حرف')
    original_question=question
    q=normalize(question);people=core.visible_employees(c,u)
    company_id=int(d['company_id']) if d.get('company_id') not in (None,'') else None
    if company_id is not None:
        allowed={r['id'] for r in c.execute('SELECT id FROM companies WHERE archived=0')} if u['role']=='admin' else {u['company_id']}
        if company_id not in allowed:core.fail('الشركة غير متاحة لحسابك',403)
        people=[e for e in people if e['company_id']==company_id]

    # A typed employee number after disambiguation continues the previous question.
    request_number=re.search(r'(?:الطلب|طلب)\s*(?:رقم|#)?\s*(\d+)',q)
    if request_number:
        target=next((r for r in core.visible_requests(c,u) if r['id']==int(request_number[1]) and (company_id is None or r['company_id']==company_id)),None)
        if not target:return {'answer':'الطلب غير موجود ضمن السجلات المتاحة لحسابك.'}
        return {'answer':f"طلب #{target['id']} — {target['employee_name']} ({target['employee_no']}) — {target['company_name']}.\nالنوع: {REQUEST_KINDS.get(target['type'],target['type'])}، الحالة: {REQUEST_STATES.get(target['status'],target['status'])}.\nتاريخ الإنشاء: {target['created_at'][:10]}.\nالملاحظات: {target['notes'] or 'لا توجد'}."}
    previous=str(d.get('previous_question') or '')[:2000]
    code_only=re.fullmatch(r'[\w-]+(?:\s*\([\w-]+\))?',question) or any(q in (normalize(e['name']),normalize(e['employee_no']),normalize(e['name'])+' ('+normalize(e['employee_no'])+')') for e in people)
    if previous and code_only:question=previous+' '+original_question;q=normalize(question)

    matches=[e for e in people if (e['employee_no'] and re.search(r'(?<![\w-])'+re.escape(normalize(e['employee_no']))+r'(?![\w-])',q)) or normalize(e['name']) in q or (e['name_en'] and normalize(e['name_en']) in q)]
    exact_codes=[e for e in people if e['employee_no'] and re.search(r'(?<![\w-])'+re.escape(normalize(e['employee_no']))+r'(?![\w-])',normalize(original_question))]
    if exact_codes:matches=exact_codes
    if not matches:
        ignored={'الدكتور','دكتور','الموظف','موظف','الموظفه','موظفه','السيد','السيده','د','د.'}
        words=set(re.findall(r'[\w]+',q))-ignored
        hint=re.search(r'(?:^|\s)(?:هل|الموظف|الموظفه|الدكتور|دكتور)\s+(.+?)(?=\s+(?:حضر|حضرت|حاضر|حاضره|غاب|غابت|اليوم|النهارده)|[؟?]|$)',q)
        name_words=set(re.findall(r'[\w]+',hint[1]))-ignored if hint else set()
        for e in people:
            parts=set(normalize(e['name']).split())-ignored
            if parts and (name_words.issubset(parts) if name_words else parts.intersection(words)):matches.append(e)
    if not matches and previous and d.get('context_employee_id') is not None and not any(x in q for x in ('الموظفين','لخص','ملخص','جميع','كل الموظف')):
        matches=[e for e in people if e['id']==int(d['context_employee_id'])]
    if d.get('employee_id') is not None:
        eid=int(d['employee_id']);matches=[e for e in people if e['id']==eid]
    if not matches and d.get('employee_id') is None and u['role']=='employee' and any(x in q for x in ('حضوري','غيابي','راتبي','اجازاتي','طلباتي','رصيدي','بياناتي')):matches=people
    if len(matches)>1:return {'answer':'يوجد أكثر من موظف مطابق؛ اختر الموظف المقصود.','context_question':question,'choices':[{'id':e['id'],'name':e['name'],'employee_no':e['employee_no'],'company_name':e['company_name']} for e in matches]}
    if not matches:
        # A summary is a system query, not an employee lookup.
        general=system_summary(c,u,question,core,people,company_id=company_id)
        if d.get('employee_id') is None and general and any(x in q for x in ('لخص','ملخص','الموظفين','الوارد','عدد الشركات','كم موظف','عدد الموظفين','الرواتب','الحضور اليوم','الحاضرين','الغائبين','مين حضر','من حضر','مين غاب','من غاب','مين حاضر','من حاضر','مين غائب','من غائب','السيستم','النظام','ملخص عام','طلبات اليوم','الطلبات اليوم')):return general
        return {'answer':'لم أجد الموظف المطلوب ضمن البيانات المتاحة لحسابك. اذكر اسمه المسجل أو رقمه الوظيفي.'}
    e=matches[0];core.user_allowed_emp(c,u,e['id']);start,end=period(question,core)
    if 'طلب' in q:
        result=request_summary(c,u,question,core,e['id'],company_id=company_id);result['employee_id']=e['id'];result['context_question']=question;return result
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
    out.append('المصدر: السجلات المتاحة لحسابك. موقع البصمة ليس تتبعًا للموقع الحالي.')
    return {'answer':'\n'.join(out),'employee_id':e['id'],'from':start,'to':end,'context_question':question}


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

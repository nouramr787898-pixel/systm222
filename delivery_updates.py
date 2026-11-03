"""Additive updates for the deployed branch; preserve existing permission checks."""
import datetime as dt
import json
import re

CONSENT='أوافق على حفظ بياناتي ومراجعتها لغرض التقديم لهذه الوظيفة، وأُقرّ بصحة البيانات المقدَّمة لشركة إحكام المسار.'


def migrate(c):
    fields={'employees':{'allowed_site_ids':"TEXT NOT NULL DEFAULT '[]'"},'attendance':{'check_in_site':'TEXT','check_out_site':'TEXT'},'email_outbox':{'body':"TEXT NOT NULL DEFAULT ''"},'candidates':{'consent_text':'TEXT'}}
    for table,columns in fields.items():
        existing={r[1] for r in c.execute('PRAGMA table_info('+table+')')}
        for name,kind in columns.items():
            if name not in existing:c.execute(f'ALTER TABLE {table} ADD COLUMN {name} {kind}')
    c.execute('CREATE TABLE IF NOT EXISTS whatsapp_outbox(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),request_id INTEGER NOT NULL REFERENCES requests(id),destination TEXT NOT NULL,parameters TEXT NOT NULL,status TEXT NOT NULL DEFAULT "pending_configuration",attempts INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,sent_at TEXT)')


def sites(c,e):
    ids=json.loads(e['allowed_site_ids'] or '[]')
    if not ids and e['site_id']:ids=[e['site_id']]
    if ids:return [dict(r) for r in c.execute('SELECT * FROM work_sites WHERE company_id=? AND id IN ('+','.join('?' for _ in ids)+') ORDER BY id',[e['company_id']]+ids)]
    co=c.execute('SELECT * FROM companies WHERE id=?',(e['company_id'],)).fetchone()
    return [{'id':0,'name':co['name'],'latitude':co['latitude'],'longitude':co['longitude'],'radius':co['geofence_radius']}]


def validate_sites(c,e,value,core):
    if not isinstance(value,list) or len(value)>100 or any(type(x)!=int or x<=0 for x in value):core.fail('اختر مواقع البصمة من القائمة')
    ids=sorted(set(value))
    if ids and c.execute('SELECT count(*) FROM work_sites WHERE company_id=? AND id IN ('+','.join('?' for _ in ids)+')',[e['company_id']]+ids).fetchone()[0]!=len(ids):core.fail('الموقع لا يتبع شركة الموظف')
    return json.dumps(ids)


def selected_company(c,u,value,core):
    if value in (None,''):return None
    co=int(value);core.permissions.company(c,u,co,core)
    if u['role']!='admin' and co!=u['company_id']:core.fail('الشركة خارج صلاحيات الحساب',403)
    if not c.execute('SELECT 1 FROM companies WHERE id=? AND archived=0',(co,)).fetchone():core.fail('الشركة غير متاحة',403)
    return co


def effective_question(question,history,people,normal):
    def identity(text):
        text=normal(text)
        return [e for e in people if text in (normal(e['name']),normal(e['employee_no']),normal(e['name']+' '+e['employee_no']))]
    if history and identity(question):
        for message in reversed(history):
            if message['role']=='user' and not identity(message['content']):return message['content']+' '+question
    general=bool(re.search(r'(?<!\w)(?:مين|من|اعرض|وريني|لخص|ملخص)(?!\w)',normal(question))) or any(x in normal(question) for x in ('الموظفين','الموظفون','طلبات الموظفين','المهام','متعملش'))
    if history and not general and any(x in normal(question) for x in ('رصيده','طلباته','عقده','انصرف','غيابه')):
        for message in reversed(history):
            if message['role']!='user':continue
            matches=[e for e in people if normal(e['employee_no']) in normal(message['content']) or normal(e['name']) in normal(message['content'])]
            if len(matches)==1:return question+' '+matches[0]['employee_no']
    return question


def date_range(question,core,normal):
    q=normal(question);today=core.TODAY();a=b=today.isoformat()
    raw=question.translate(str.maketrans('٠١٢٣٤٥٦٧٨٩','0123456789'))
    dates=re.findall(r'\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}',raw)
    if dates:a=core.normalize_date(dates[0]);b=core.normalize_date(dates[-1])
    elif 'امس' in q or 'مبارح' in q or 'امبارح' in q:a=b=(today-dt.timedelta(days=1)).isoformat()
    elif 'شهر' in q:
        start=today.replace(day=1)
        if 'الماضي' in q or 'اللي فات' in q:start=(start-dt.timedelta(days=1)).replace(day=1);b=(today.replace(day=1)-dt.timedelta(days=1)).isoformat()
        a=start.isoformat()
    elif 'اسبوع' in q:a=(today-dt.timedelta(days=6)).isoformat()
    if a>b or (dt.date.fromisoformat(b)-dt.date.fromisoformat(a)).days>366:core.fail('اختر فترة صحيحة لا تزيد على سنة')
    return a,b


def request_summary(c,u,question,co,core,normal,kinds,states):
    q=normal(question)
    if 'طلب' not in q or any(w in q for w in ('كيف','ازاي','خطوات','طريقه','اشرح')):return None
    a,b=date_range(question,core,normal)
    rows=core.visible_requests(c,u,include_details=False)
    rows=[r for r in rows if co is None or r['company_id']==co]
    def day(r):
        stamp=dt.datetime.fromisoformat(r['created_at'].replace('Z','+00:00'))
        return (stamp.astimezone(core.workforce.zone(c,r['company_id'])) if stamp.tzinfo else stamp).date().isoformat()
    rows=[r for r in rows if a<=day(r)<=b]
    pending={'new','review','manager_approved'}
    if 'مرفوض' in q:rows=[r for r in rows if r['status'] in ('rejected','declined')]
    elif 'معتمد' in q or 'مقبول' in q:rows=[r for r in rows if r['status'] in ('approved','accepted')]
    elif 'معلق' in q or 'قيد المراجعه' in q:rows=[r for r in rows if r['status'] in pending]
    from collections import Counter
    counts=Counter(states.get(r['status'],r['status']) for r in rows)
    text=f'ملخص الطلبات الواردة من {a} إلى {b}: {len(rows)} طلب ضمن صلاحيات حسابك.\n'
    text+='، '.join(f'{state}: {count}' for state,count in counts.items())+'\n'
    for r in rows[:20]:text+=f"• EH-{r['id']:06d} — {r['employee_name']} ({r['employee_no']}) — {r['company_name']} — {kinds.get(r['type'],r['type'])} — {states.get(r['status'],r['status'])}.\n"
    if not rows:text+='لا توجد طلبات مطابقة في الفترة المحددة.'
    if len(rows)>20:text+='عُرض أحدث 20 طلبًا؛ حدّد فترة أقصر لمزيد من التفاصيل.'
    return {'answer':text[:3900],'sources':['سجل الطلبات الفعلي ضمن صلاحيات الحساب والشركة المختارة']}


def notification_body(c,core,r,uid,kinds):
    import os
    from urllib.parse import urlparse
    employee=c.execute('SELECT e.* FROM users u JOIN employees e ON e.id=u.employee_id WHERE u.id=? AND e.id=?',(uid,r['employee_id'])).fetchone()
    if not employee:return None
    state=c.execute('SELECT status FROM requests WHERE id=?',(r['id'],)).fetchone()[0]
    state={'new':'قيد المراجعة','review':'قيد المراجعة','approved':'تم القبول','rejected':'مرفوض','accepted':'تم القبول','declined':'مرفوض','cancelled':'ملغي','closed':'مغلق','withdrawn':'مسحوب','manager_approved':'قيد المراجعة لدى الإدارة'}.get(state,state)
    origin=os.environ.get('EHKAM_PUBLIC_URL','').rstrip('/');parsed=urlparse(origin)
    link=origin+f"/#request-{r['id']}" if parsed.scheme=='https' and parsed.netloc and not parsed.username and not parsed.password else 'يرجى الدخول إلى موقع إحكام المسار لعرض تفاصيل الطلب.'
    parameters=[str(r['id']),kinds.get(r['type'],r['type']),employee['employee_no'],state]
    phone=str(employee['phone_country'] or '').lstrip('+')+re.sub(r'\D','',str(employee['phone_local'] or '')).lstrip('0')
    if re.fullmatch(r'[1-9]\d{7,14}',phone):c.execute('INSERT INTO whatsapp_outbox(user_id,request_id,destination,parameters,created_at) VALUES(?,?,?,?,?)',(uid,r['id'],phone,json.dumps(parameters,ensure_ascii=False),core.now()))
    return f"موظفنا العزيز،\nنفيدكم بأن طلبكم رقم ({r['id']}) الخاص بطلب ({parameters[1]}) بالرقم الوظيفي ({employee['employee_no']}) حالته: {state}.\nلمزيد من التفاصيل يرجى زيارة موقع إحكام المسار عبر الرابط\n{link}\nإحكام المسار، نسعد بخدمتكم"

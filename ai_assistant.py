"""Local rule-based guidance and scoped counts. No model, key or external calls."""
import re
import threading
import time
import unicodedata

GUIDE = [
    {'title':'الطلبات','view':'requests','text':'تقديم الطلب من حساب الموظف. الأنواع تشمل الإجازات والسلف والاستئذان وتصحيح البصمة. الموافقة حسب صلاحيات ومسار الشركة. الرفض يحتاج سببًا. لا يجوز اعتماد طلبك بنفسك.'},
    {'title':'أرصدة الإجازات','view':'leave','text':'الرصيد يحسبه النظام من الاستحقاق والمكتسب والمتراكم والاستخدام والتسويات. الشهري جزء من الاستحقاق السنوي وليس إضافة مستقلة. لا تخترع قيمة رصيد غير مرفقة بالسياق.'},
    {'title':'الحضور والانصراف','view':'attendance','text':'تسجيل البصمة من حساب الموظف مع السماح بالموقع والتحقق من نطاق الموقع ودقة القياس. حساب الإدارة يستعرض السجلات. عدم البصمة لا يثبت وحده غيابًا معتمدًا أو خصمًا.'},
    {'title':'الشركات والموظفون','view':'employees','text':'إضافة موظف داخل الشركة تتيح اختيار موظف مسجل ونقله مع حفظ تاريخه، وفق قيود الالتزامات. إنشاء موظف جديد من الموظفين. لا تغيّر شركة أو بيانات موظف عبر المحادثة.'},
    {'title':'الرواتب والسلف','view':'payroll','text':'إعداد مسير ثم مراجعة واعتماد مسؤول مستقل ثم تسجيل الصرف بمرجع. تسجيل الصرف لا ينفذ تحويلًا بنكيًا. الموافقة على السلفة تختلف عن صرفها. لا تحسب ضرائب أو تأمينات من عندك.'},
    {'title':'الإشعارات','view':'notifications','text':'تعرض الطلبات التي تنتظر إجراء. الطلب المنتهي يختفي من قائمة الإجراءات ويظل محفوظًا. تفاصيل البريد والتتبع في سجل العمليات.'},
    {'title':'مراجعة الغياب','view':'absence','text':'اختيار الموظف والفترة يعرض حالة كل يوم. أيام الراحة والعطلات والإجازات المعتمدة مستثناة. اليوم الجاري ينتظر نهاية الوردية. لا خصم قبل المراجعة والاعتماد.'},
    {'title':'أمان الحساب','view':'security','text':'تغيير كلمة المرور وإدارة الجلسات والمصادقة الثنائية. تنتهي الجلسة بعد 30 دقيقة خمول أو 12 ساعة. صلاحيات النظام ليست صلاحيات GitHub أو Railway.'},
    {'title':'التوظيف والتقييم والتدريب','view':'talent','text':'إدارة الوظائف والمرشحين وتقييم الأهداف وإسناد التدريب. تعيين مرشح لا ينشئ ملف موظف تلقائيًا. التقييم والتدريب لا يغيران الراتب تلقائيًا.'},
]
_LOCK=threading.Lock()
_CALLS={}
_ACTIVE=set()
RATE_LIMIT=30


def reserve(uid,core):
    with _LOCK:
        stamp=time.monotonic()
        for key in list(_CALLS):
            if not _CALLS[key] or stamp-_CALLS[key][-1]>=60:del _CALLS[key]
        calls=_CALLS.setdefault(uid,[])
        calls[:]=[t for t in calls if stamp-t<60]
        if len(calls)>=RATE_LIMIT:core.fail('وصلت لحد ٣٠ سؤالًا في الدقيقة؛ حاول بعد قليل',429)
        calls.append(stamp)


def validate_history(value,core):
    if not isinstance(value,list) or len(value)>6 or len(value)%2:
        core.fail('سياق المحادثة غير صالح؛ ابدأ محادثة جديدة',400)
    total=0
    clean=[]
    for index,item in enumerate(value):
        if not isinstance(item,dict) or item.get('role')!=('user' if index%2==0 else 'assistant'):
            core.fail('سياق المحادثة غير صالح؛ ابدأ محادثة جديدة',400)
        content=item.get('content')
        limit=1200 if index%2==0 else 4000
        if not isinstance(content,str) or not 1<=len(content)<=limit:
            core.fail('سياق المحادثة غير صالح؛ ابدأ محادثة جديدة',400)
        total+=len(content)
        clean.append({'role':item['role'],'content':content})
    if total>12000:core.fail('سياق المحادثة أطول من المسموح؛ ابدأ محادثة جديدة',400)
    return clean


def acquire(uid,core):
    with _LOCK:
        if uid in _ACTIVE:core.fail('فيه سؤال لحسابك قيد المعالجة؛ انتظر الرد الأول',429)
        if len(_ACTIVE)>=4:core.fail('المساعد مشغول حاليًا؛ حاول بعد قليل',429)
        _ACTIVE.add(uid)


def context(c,u,core):
    return {'date':core.TODAY().isoformat(),'role':core.permissions.profile(c,u),
            'request_counts':core.visible_requests(c,u,include_details=False,aggregate=True)}


def normalize(text):
    text=unicodedata.normalize('NFKC',text).lower().translate(str.maketrans('أإآىة','ااايه'))
    return re.sub(r'[^\w\s]',' ',''.join(ch for ch in text if not unicodedata.combining(ch) and ch!='ـ')).strip()


def contains(text,words):
    return any(normalize(word) in text for word in words)


# Ordered topics disambiguate leave balance, missing punches and generic requests.
TOPIC_TERMS=[
 (1,('رصيد','ارصده','استحقاق','متراكم','leave balance')),
 (6,('مراجعه الغياب','غايب','غائب','غياب','absence')),
 (2,('حضور','انصراف','بصمه','attendance','check in','check out')),
 (4,('راتب','رواتب','مسير','سلفه','سلف','مكافاه','خصم الراتب','payroll','loan')),
 (5,('اشعار','اشعارات','تنبيه','notifications')),
 (7,('امان','كلمه المرور','كلمه السر','مصادقه','صلاحيات','جلسات','password','security','github','railway')),
 (8,('توظيف','تدريب','تقييم','مرشح','وظيفه شاغره','training','appraisal')),
 (3,('نقل موظف','انقل موظف','انقل الموظف','نقل الموظف','اضيف موظف','اضافه موظف','بيانات الموظف','الشركات','الموظفين','employee transfer')),
 (0,('طلب','طلبات','اجازه','اذن','استئذان','موافقه','موافقات','request')),
]
STEPS={
 0:['من حساب الموظف، افتح «طلباتي» واضغط «طلب جديد».','اختار نوع الطلب، واملأ الحقول المطلوبة وأرفق المستند عند الحاجة.','راجع البيانات واضغط «إرسال الطلب»، وتابع الحالة من الطلب نفسه.','المسؤول المخوّل يفتح الطلب ويراجع البيانات، ثم يختار القرار مع سبب عند الرفض. مسار الاعتماد حسب إعداد الشركة.'],
 1:['افتح «أرصدة الإجازات» واختار الموظف المتاح لحسابك.','راجع المتراكم والمكتسب والمستخدم والتسويات والإجمالي المتاح.','الاستحقاق الشهري جزء من السنوي؛ ما تجمعهمش مرتين. القيمة الفعلية تؤخذ من شاشة الرصيد.'],
 2:['سجّل الدخول بحساب الموظف المرتبط بملفه.','افتح «الحضور والانصراف»، واسمح للمتصفح بالموقع، وتأكد إنك داخل نطاق العمل المسموح.','اختار تسجيل الحضور عند البداية وتسجيل الانصراف عند نهاية الدوام. حساب الإدارة مخصص للمراجعة.','لو البصمة ناقصة، قدّم طلب تصحيح بصمة للمراجعة؛ المساعد لا يسجل بصمة نيابة عنك.'],
 3:['للإدارة المخوّلة: افتح «الشركات المتعاقدة»، ثم موظفي الشركة المطلوبة.','اضغط «إضافة موظف»، وابحث عن الموظف المسجل واختاره.','راجع سبب النقل والتاريخ ثم أكّد من شاشة النظام. الموظف ينتقل للشركة الجديدة مع الاحتفاظ بتاريخه، وفق قيود الالتزامات.','لإنشاء موظف جديد، استخدم «الموظفون» ثم «إضافة موظف».'],
 4:['إعداد المسير من «الرواتب والسلف»، ثم مراجعته واعتماده بواسطة مسؤول مستقل مخوّل.','بعد الصرف الفعلي خارج النظام، يُسجّل الصرف بمرجع من المسؤول المختص؛ النظام لا يحوّل الأموال للبنك.','السلفة تبدأ بطلب وموافقة، ثم تسجيل صرفها كإجراء منفصل. الاعتماد وحده لا يعني تسليم المبلغ.'],
 5:['افتح «الإشعارات» لمتابعة الطلبات التي تنتظر إجراءً من حسابك.','افتح الطلب للمراجعة واتخذ القرار لو عندك الصلاحية.','بعد إنهاء الإجراء يختفي من قائمة المهام المعلّقة ويظل الطلب وسجله محفوظين.'],
 6:['المسؤول المخوّل يفتح «مراجعة الغياب» ويختار الموظف والفترة.','راجع حالة كل يوم والبصمات؛ أيام الراحة والعطلات والإجازات المعتمدة مستثناة.','الوردية غير المنتهية لا تُعامل كغياب نهائي. نقص الانصراف لا يثبت غياب يوم كامل.','اعتمد عذرًا أو قرار الغياب من الشاشة بعد المراجعة. عرض البيانات وحده لا يخصم رصيدًا أو راتبًا.'],
 7:['افتح «أمان الحساب» لتغيير كلمة المرور ومراجعة الجلسات.','فعّل المصادقة الثنائية واتبع خطوات تطبيق المصادقة، واحتفظ بوسيلة الاسترداد في مكان آمن.','صلاحيات المالك والموارد البشرية والرواتب والمراجع تحدد المهام ونطاق الشركات. المشرف يراجع الموظفين المتاحين له فقط.','صلاحيات النظام لا تمنح أي صلاحية على GitHub أو Railway.'],
 8:['من «التوظيف والتقييم والتدريب» أضف وظيفة شاغرة ثم المرشحين ومراحلهم.','أنشئ تقييمًا بأهداف ومراجع ودرجات وأسباب، وأرسله لإقرار الموظف.','كلّف الموظف بدورة وسجّل الإكمال ومرجع الإثبات عند إتمامها.','تعيين مرشح لا ينشئ ملف موظف تلقائيًا، والتقييم أو التدريب لا يغيّر الراتب تلقائيًا.'],
}
EXAMPLES={1:'مثال توضيحي فقط: متراكم ٢٥ + مكتسب ١٥٫٧٥ − مستخدم ٢ = متاح ٣٨٫٧٥ يوم. ده مثال مش رصيد حسابك.',4:'مثال: موافقة الإدارة على سلفة لا تعني صرفها؛ تسجيل الصرف يتم بعد تسليم المبلغ فعليًا.',6:'مثال: يوم راحة من غير بصمة مش يوم غياب معتمد. يوم عمل انتهت ورديته من غير بصمة يحتاج مراجعة.'}
TYPE_WORDS={
 'annual_leave':('سنويه','سنوي'), 'sick_leave':('مرضيه','مرضي'), 'unpaid_leave':('بدون راتب','غير مدفوعه'),
 'advance':('سلفه','سلف'), 'permission':('اذن','استئذان'), 'punch_fix':('تصحيح البصمه','تصحيح بصمه'),
 'hr_contact':('تواصل الموارد البشريه','مراسلات الموارد البشريه'), 'termination':('انهاء','استقاله'),
 'delegation':('انتداب',),'investigation':('تحقيق',),'open':('طلب مفتوح','طلبات مفتوحه'),
}
TYPE_NAMES={'annual_leave':'إجازة سنوية','sick_leave':'إجازة مرضية','unpaid_leave':'إجازة بدون راتب','advance':'سلفة','permission':'استئذان','punch_fix':'تصحيح بصمة','hr_contact':'تواصل الموارد البشرية','termination':'إنهاء تعاقد','delegation':'انتداب','investigation':'تحقيق','open':'طلب مفتوح'}
STATUS_WORDS={'new':('جديد','جديده'),'review':('تحت المراجعه',),'approved':('معتمد','معتمده','موافق عليه','تمت الموافقه','تم اعتماده'),'rejected':('مرفوض','مرفوضه'),'cancelled':('ملغي','ملغيه'),'closed':('مغلق','مغلقه'),'accepted':('مقبول','مقبوله'),'declined':('رفض الانتداب',),'withdrawn':('مسحوب','مسحوبه')}
STATUS_NAMES={'new':'جديد','review':'تحت المراجعة','approved':'معتمد','rejected':'مرفوض','cancelled':'ملغي','closed':'مغلق','accepted':'مقبول','declined':'رفض الانتداب','withdrawn':'مسحوب'}


def is_statistics(q):
    metric=bool(re.search(r'(?:^|\s)(?:احصاء\w*|الاحصاء\w*|احصائ\w*|الاحصائ\w*|لخص|ملخص|عدد|العدد|كام|كم|summary)(?:\s|$)',q))
    subject=contains(q,('طلب','اجازه','اجازات','requests')) or any(contains(q,words) for words in TYPE_WORDS.values())
    return metric and (subject or q in ('احصاءات','الاحصاءات','احصائيات','الاحصائيات'))


def topic(q):
    for index,words in TOPIC_TERMS:
        if contains(q,words):return index
    return None


def generate(question,data,history=None):
    q=normalize(question)
    # Only prior user questions can select a guide topic; never trust an old answer.
    prior=[normalize(x['content']) for x in (history or []) if x['role']=='user']
    follow=contains(q,('وضح اكتر','اشرح اكتر','اشرح الخطوات','وضح الخطوات','مثال','النقطه الاولي','الخطوه الاولي'))
    selected=topic(q)
    effective=q
    if selected is None and follow and prior:
        effective=prior[-1]
        selected=topic(effective)
    if is_statistics(effective):
        if contains(effective,('اليوم','امبارح','امس','اسبوع','شهر','سنه','سنين','موظف','رقم الطلب')) or re.search(r'\d',effective):
            return {'answer':'المتاح هنا إجمالي الطلبات المحفوظة ضمن صلاحيات حسابك، مع تصفية النوع والحالة فقط. التصفية بتاريخ أو موظف محدد غير مدعومة في المحادثة؛ استخدم شاشة الطلبات أو التقارير. مثال مدعوم: «كام طلب معتمد عندي؟».','sources':['نطاق إحصاءات المساعد المحلي']}
        if contains(effective,('غير معتمد','غير مقبول','غير مرفوض','مش معتمد','ما عدا','باستثناء')):
            return {'answer':'حدد الحالة المقصودة بدقة: جديد، تحت المراجعة، معتمد، مرفوض، ملغي أو مسحوب. مثلًا: «كام طلب تحت المراجعة عندي؟». عبارة غير معتمد ممكن تشمل حالات مختلفة.','sources':['حالات الطلبات في النظام']}
        vocabulary=set(normalize(' '.join(word for words in (*TYPE_WORDS.values(),*STATUS_WORDS.values()) for word in words)).split())
        vocabulary.update(normalize('كام كم عدد طلب طلبات الطلبات طلباتي طلباتنا اجازه اجازات احصاء احصاءات الاحصاءات احصائيات الاحصائيات لخص ملخص اجمالي الاجمالي عندي عندنا لدي المتاحه لحسابي حسابي حسب النوع والحاله الحاله والأنواع النوع والحالات لي ليا لنا فريقي فريقنا كل جميع فقط بس عايز عاوز اعرف ممكن تقولي ايه هي التي اللي الي هل عند يوجد في من requests summary my all total count approved rejected').split())
        if any(word not in vocabulary and not (word.startswith('و') and word[1:] in vocabulary) and not (word.startswith('ال') and word[2:] in vocabulary) for word in effective.split()):
            return {'answer':'أقدر أحسب الطلبات حسب النوع والحالة ضمن صلاحيات حسابك فقط. الصيغة دي فيها تحديد إضافي مش مدعوم؛ جرّب «كام طلب معتمد عندي؟» أو «كم سلفة معتمدة عندي؟»، أو استخدم شاشة الطلبات للتصفية التفصيلية.','sources':['نطاق إحصاءات المساعد المحلي']}
        types={k for k,words in TYPE_WORDS.items() if contains(effective,words)}
        if not types and contains(effective,('اجازه','اجازات')):types={'annual_leave','sick_leave','unpaid_leave'}
        statuses={k for k,words in STATUS_WORDS.items() if contains(effective,words)}
        if contains(effective,('معلقه','منتظره','قيد الانتظار')):statuses.update(('new','review'))
        rows=[r for r in data['request_counts'] if (not types or r['type'] in types) and (not statuses or r['status'] in statuses)]
        total=sum(r['count'] for r in rows)
        lines=[f"إحصاءات الطلبات المتاحة لحسابك بتاريخ {data['date']}:",f'الإجمالي وفق السؤال: {total} طلب.']
        if not rows:lines.append('لا توجد طلبات مطابقة ضمن صلاحيات حسابك.')
        for row in rows:lines.append(f"• {TYPE_NAMES.get(row['type'],'نوع آخر')} — {STATUS_NAMES.get(row['status'],'حالة أخرى')}: {row['count']}")
        lines.append('الإحصاءات تشمل الطلبات المحفوظة المتاحة للحساب، وليست مهام الاعتماد المعلّقة فقط. لا تشمل حسابات أو شركات خارج صلاحياتك.')
        return {'answer':'\n'.join(lines),'sources':['إحصاءات الطلبات المتاحة لحسابك بتاريخ '+data['date']]}
    if selected is not None:
        guide=GUIDE[selected]
        if contains(q,('مثال',)) and selected in EXAMPLES:
            text=EXAMPLES[selected]
        else:
            text='استخدام قسم «'+guide['title']+'»:\n'+'\n'.join(f'{i+1}. {step}' for i,step in enumerate(STEPS[selected]))
        if data['role'] not in ('owner','hr','payroll') and selected in (3,4,6):
            text+='\nالخطوات الإدارية دي توضيحية؛ تنفيذها يحتاج الحساب المخوّل. تقدر تتابع بياناتك وطلباتك المسموحة من حسابك.'
        if selected==1:text+='\nلا أقرأ رصيدك الشخصي من المحادثة حاليًا؛ افتح شاشة الرصيد لمعرفة الرقم الفعلي.'
        return {'answer':text,'sources':['دليل استخدام النظام — '+guide['title']]}
    return {'answer':'أنا مساعد إحكام الإرشادي المحلي. أقدر أشرح الطلبات والإجازات والبصمة والنقل والرواتب والإشعارات وأمان الحساب والتوظيف، وأعرض إحصاءات الطلبات المتاحة لحسابك. اكتب سؤالًا محددًا، زي «إزاي أنقل موظف؟» أو «كام طلب معتمد عندي؟». الحوار الحر وقراءة المستندات والبيانات الشخصية غير مدعومين.','sources':['نطاق مساعد إحكام الإرشادي']}


def saudi(text):
    for old,new in [('إزاي','كيف'),('ازاي','كيف'),('كام','كم'),('دلوقتي','الحين'),('عايز','تبي'),('عاوز','تبي'),('ما تجمعهمش','لا تجمعهم'),('مش ','مو '),('ده مثال','هذا مثال'),('زي «','مثل «'),('اختار','اختر'),('ما تقدرش','ما تقدر'),(' دي ',' هذي '),('الصيغة دي','هالصيغة'),('بيتحسب','ينحسب'),('النهارده','اليوم'),('اكتر','أكثر'),('علشان','عشان'),('مفيش','ما فيه')]:text=re.sub(r'(?<!\w)'+re.escape(old.strip())+r'(?!\w)',lambda _:new.strip(),text)
    return text


def employee_facts_answer(c,u,question,core):
    """Read-only, deterministic employee facts. Every source keeps its role scope."""
    import datetime as dt
    q=normalize(question)
    attendance=contains(q,('حضر','حضرت','حاضر','حضور','غايب','غائب','بصمه','انصراف','فين','وين'))
    advance=contains(q,('سلفه','سلف','قرض'))
    requests=contains(q,('طلب','طلبات','قدمت','قدم'))
    balance=contains(q,('رصيد','متراكم','اجمالي الاجازات'))
    contract=contains(q,('عقد','مسمى','قسم','اداره','وظيفته','وظيفتها','شركته','شركتها'))
    if not any((attendance,advance,requests,balance,contract)) or contains(q,('كيف','ازاي','طريقه','خطوات')):return None
    visible=core.visible_employees(c,u)
    def phrase(value):return bool(re.search(r'(?<!\w)'+re.escape(normalize(value))+r'(?!\w)',q))
    exact=[e for e in visible if phrase(e['name']) or phrase(e['employee_no'])]
    matches=exact or [e for e in visible if any(len(word)>1 and phrase(word) for word in e['name'].split())]
    if not matches:
        if contains(q,('رصيدي','طلباتي','حضرت انا','انا حضرت','سلفتي','عقدي')):matches=[e for e in visible if e['id']==u['employee_id']]
        elif not contains(q,('موظف','موظفه','شوف','شف ','وين','فين','هل ')):return None
    if not matches:return {'answer':'ما لقيت موظفًا بهذا الاسم ضمن الموظفين المسموح لك بالاطلاع عليهم. اكتب الاسم الكامل أو الرقم الوظيفي.','sources':['نطاق الموظفين المسموح لحسابك']}
    if len(matches)>1:return {'answer':'أبشر، فيه أكثر من موظف بهذا الاسم: '+'، '.join(e['name']+' ('+e['employee_no']+')' for e in matches)+'. حدّد الاسم الكامل أو الرقم الوظيفي.','sources':['الموظفون ضمن صلاحيات الحساب']}
    e=matches[0];today=core.TODAY();start=end=today.isoformat();period='اليوم'
    raw=question.translate(str.maketrans('٠١٢٣٤٥٦٧٨٩','0123456789'))
    date=re.search(r'\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})\b',raw)
    if date:start=end=core.normalize_date(date[0]);period='بتاريخ '+start
    elif contains(q,('امس','امبارح')):start=end=(today-dt.timedelta(days=1)).isoformat();period='أمس'
    elif contains(q,('هذا الشهر','الشهر الحالي','الشهر ده')):start=today.replace(day=1).isoformat();period='هذا الشهر حتى اليوم'
    elif contains(q,('كل الطلبات','كل السلف','سجل الطلبات','سجل السلف')):start='0001-01-01';period='في السجل المتاح'
    profile=core.permissions.profile(c,u)
    def result(text,source):return {'answer':'أبشر، '+e['name']+' '+text,'sources':[source+' — '+period]}
    def date_in(value):
        if not value:return False
        stamp=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
        if stamp.tzinfo:stamp=stamp.astimezone(core.workforce.zone(c,e['company_id']))
        return start<=stamp.date().isoformat()<=end
    if advance:
        if (profile=='manager' and e['id']!=u['employee_id']) or profile=='hr':return result('بيانات السلف الخاصة به مو ضمن صلاحيات حسابك.','صلاحيات البيانات المالية')
        loans=[dict(row) for row in c.execute('SELECT * FROM loans WHERE employee_id=? ORDER BY id DESC',(e['id'],)) if start<=row['disbursed_on']<=end]
        allowed=[r for r in core.visible_requests(c,u,include_details=False) if r['employee_id']==e['id'] and r['type']=='advance' and date_in(r['created_at'])]
        labels={'new':'جديد','review':'تحت المراجعة','approved':'معتمد','rejected':'مرفوض','cancelled':'ملغي'}
        text=('فيه '+str(len(loans))+' سلفة مسجّل صرفها '+period+': '+ '؛ '.join(str(row['principal_cents']/100)+' ريال، صرف '+row['disbursed_on'] for row in loans[:10])+'.') if loans else 'ما فيه سلفة مسجّل صرفها '+period+'. هذا حسب إثبات الصرف في النظام.'
        text+=' طلبات السلفة المقدمة '+period+': '+( '؛ '.join('طلب EH-'+str(r['id']).zfill(6)+' — '+labels.get(r['status'],r['status']) for r in allowed[:10]) if allowed else 'ما فيه طلبات ظاهرة لحسابك')+'. الاعتماد وحده ما يعني صرف المبلغ.'
        return result(text,'طلبات السلف وسجل الصرف ضمن الصلاحيات')
    if requests:
        rows=[r for r in core.visible_requests(c,u,include_details=False) if r['employee_id']==e['id'] and date_in(r['created_at'])]
        labels={'new':'جديد','review':'تحت المراجعة','approved':'معتمد','rejected':'مرفوض','cancelled':'ملغي','accepted':'مقبول','declined':'مرفوض من الموظف','closed':'مغلق'}
        text=('عنده '+str(len(rows))+' طلب ظاهر ضمن صلاحياتك '+period+': '+ '؛ '.join('EH-'+str(r['id']).zfill(6)+' '+TYPE_NAMES.get(r['type'],r['type'])+' — '+labels.get(r['status'],r['status']) for r in rows[:10])+'.') if rows else 'ما فيه طلبات ظاهرة ضمن صلاحياتك '+period+'.'
        return result(text,'سجل الطلبات المتاح للحساب')
    if balance:
        if profile in ('payroll','auditor'):return result('رصيد الإجازات مو ضمن صلاحيات حسابك.','صلاحيات أرصدة الإجازات')
        info=core.leave(c,core.user_allowed_emp(c,u,e['id']))
        return result('رصيده المتاح '+str(info['available'])+' يوم، والسنوي '+str(info['annual_entitlement'])+' يوم. دفعة الشهر تنضاف في نهاية الشهر حسب السجل.','رصيد الإجازات الفعلي')
    if contract:
        return result('شركته '+e['company_name']+'، القسم '+(e['department'] or 'غير مسجل')+'، المسمى '+(e['job'] or 'غير مسجل')+'، والعقد من '+str(e['contract_start'] or 'غير مسجل')+' إلى '+str(e['contract_end'] or 'غير مسجل')+'.','الملف الوظيفي المتاح')
    if start!=end:return result('حدّد يومًا واحدًا لعرض بصمة الحضور؛ مثل اليوم أو أمس أو 02/11/2026.','سجل الحضور')
    record=c.execute('SELECT check_in,check_out FROM attendance WHERE employee_id=? AND day=?',(e['id'],start)).fetchone()
    if record and record['check_in']:state='حاضر '+period+'، سجّل الحضور الساعة '+record['check_in']+(' والانصراف الساعة '+record['check_out'] if record['check_out'] else '، وما سجّل انصراف إلى الآن')
    elif c.execute("SELECT 1 FROM absences WHERE employee_id=? AND day=? AND status='approved'",(e['id'],start)).fetchone():state='عليه غياب معتمد '+period
    elif c.execute("SELECT 1 FROM requests WHERE employee_id=? AND status='approved' AND type IN ('annual_leave','sick_leave','unpaid_leave') AND start_date<=? AND end_date>=?",(e['id'],start,start)).fetchone():state='عنده إجازة معتمدة '+period
    elif core.hr.holiday(c,e,start) or str(dt.date.fromisoformat(start).weekday()) not in e['workweek'].split(','):state=period+' راحة أو عطلة حسب جدوله'
    else:state='ما سجّل حضور '+period+'؛ هذا ما يثبت غيابًا معتمدًا، راجع الدوام والإجازات'
    return result(state+'.','سجل الحضور ضمن صلاحيات الحساب')


def handle(h,c,u,path,method,core):
    if path!='/api/assistant':return False
    if method=='GET':
        h.write({'enabled':True,'mode':'local_guidance','brand':'مساعد إحكام الإرشادي — System أحكام الأعمال','conversation_turns':3});return True
    if method!='POST':core.fail('طريقة غير مسموحة',405)
    d=h.input();question=d.get('question')
    if not isinstance(question,str) or not 2<=len(question.strip())<=1200:core.fail('اكتب سؤالًا من حرفين إلى 1200 حرف')
    history=validate_history(d.get('history',[]),core)
    acquire(u['id'],core)
    try:
        reserve(u['id'],core)
        facts=employee_facts_answer(c,u,question.strip(),core)
        result=facts or generate(question.strip(),context(c,u,core),history)
        if not facts:result['answer']=saudi(result['answer'])
        core.record(c,u,'assistant_answer','user',u['id'],{'mode':'local_guidance','question_length':len(question)})
        c.commit()
    finally:
        with _LOCK:_ACTIVE.discard(u['id'])
    h.write({**result,'mode':'local_guidance','notice':'إجابات من السجلات المتاحة لحسابك وإرشادات محلية؛ لا يتم تعديل البيانات من المحادثة.'});return True

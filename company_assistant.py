"""Read-only company questions using the same row and role scope as portal screens."""
import re
import datetime as dt
import delivery_updates


def _has(q,*words):
    return any(word in q for word in words)


def _response(lines,sources,intent):
    # Keep complete lines and an honest truncation notice within chat history limits.
    kept=[];size=0
    for line in lines:
        if size+len(line)>3750:
            kept.append('توجد تفاصيل إضافية؛ حدّد موظفًا أو فترة أقصر أو افتح الشاشة المختصة.')
            break
        kept.append(line);size+=len(line)+1
    return {'answer':'\n'.join(kept),'sources':sources,'intent':intent}


def _person(e):
    return f"{e['name']} ({e['employee_no']})"


def company_query(c,u,question,company,people,core,normal,kinds,states):
    q=normal(question)
    if _has(q,'كيف','ازاي','خطوات','طريقه','اشرح'):return None
    numbered=re.search(r'(?:الطلب|طلب)\s+(?:رقم\s+)?(?:eh\s+)?(\d+)',q.translate(str.maketrans('٠١٢٣٤٥٦٧٨٩','0123456789')))
    if numbered:
        rows=core.visible_requests(c,u,include_details=False,request_id=int(numbered[1]))
        row=next((r for r in rows if company is None or r['company_id']==company),None)
        if not row:return _response(['الطلب غير موجود ضمن الشركة المختارة وصلاحيات حسابك.'],['نطاق الطلبات'],'request_detail')
        progress=core.ui_workflows.request_progress(c,row,core)
        return _response([f"الطلب EH-{row['id']:06d} — {row['employee_name']} ({row['employee_no']}) — {row['company_name']}.",f"النوع: {kinds.get(row['type'],row['type'])}، الحالة: {states.get(row['status'],row['status'])}.",f"تاريخ الإنشاء: {row['created_at'][:10]}، الإجراء التالي عند: {progress['responsible']}.",f"الملاحظات: {row['notes'] or 'لا توجد'}."],['سجل الطلب الفعلي وحالة الإجراء ضمن الصلاحيات'],'request_detail')
    collective=_has(q,'الموظفين','الموظفون','موظفين','الموظفات','العاملين','فريق الشركه','الشركه','الشركة')
    listing=_has(q,'اعرض','عرض','وريني','هات','قائمه','اسماء','اسامي','مين','عدد','كام','كم','قولي','لخص','ملخص','كل الموظف','من هم')
    attendance=_has(q,'حضر','حاضر','حضور','غاب','غائب','غايب','غياب','انصراف','انصرف','بصم','الغايبين')
    pending=_has(q,'متعملش','ما اتعملش','ما تعملش','لم يعمل','لم يتم','متبقي','المتبقي','باقي','معلق','متاخر','ناقص','لم تنجز','غير المنجز','غير المكتمل','غير مكتمل','مخلصتش','ما خلص','لم تنفذ','لم تنته','غير منجز')
    explicit_name=any(normal(e['name']) in q or re.search(r'(?<!\w)'+re.escape(normal(e['employee_no']))+r'(?!\w)',q) for e in people)
    roster_reference=_has(q,'الموظفين','الموظفون','موظفين','موظفي','الموظفات','العاملين')
    name_ignored={'دكتور','الدكتور','الموظف','موظف','الموظفه','موظفه','السيد','السيده','بن','علي'}
    if not roster_reference:
        words=set(q.split())
        explicit_name=explicit_name or any((set(normal(e['name']).split())-name_ignored)&words for e in people)

    general=not explicit_name and (collective or listing or _has(q,'الطلبات','المهام','الحاضرين','الغائبين'))
    if pending and not explicit_name:
        return pending_work(c,u,question,company,people,core,normal,kinds,states)
    if attendance and general:
        return attendance_summary(c,u,question,company,people,core,normal)
    if collective and (listing or q in ('الموظفين','الموظفون','موظفين')) and not _has(q,'طلب','اجاز','رصيد','ارصده','عقد','عقود','تعاقد','راتب','رواتب','مرتب','مرتبات','اجور','سلف','عهد','مستند'):
        company_name=c.execute('SELECT name FROM companies WHERE id=?',(company,)).fetchone()[0] if company is not None else 'الشركات المتاحة لحسابك'
        filtered=people
        if _has(q,'المؤرشفين','الارشيف'):
            return _response(['قائمة الأرشيف لها شاشة مستقلة؛ القائمة الحالية تعرض الموظفين غير المؤرشفين المتاحين لحسابك.'],['نطاق قائمة الموظفين'],'employees')
        lines=[f'موظفو {company_name}: {len(filtered)} موظف متاح لحسابك.']
        for e in filtered:lines.append(f"• {_person(e)} — {e['job'] or 'المسمى غير مسجل'} — {e['department'] or 'القسم غير مسجل'} — {e['status'] or 'الحالة غير مسجلة'}.")
        if not filtered:lines.append('لا يوجد موظفون متاحون لحسابك في الشركة المختارة.')
        return _response(lines,['قائمة الموظفين الفعلية للشركة المختارة ضمن صلاحيات الحساب'],'employees')
    if general and 'طلب' in q:
        result=delivery_updates.request_summary(c,u,question,company,core,normal,kinds,states)
        if result:result['intent']='requests'
        return result
    if general and (collective or _has(q,'لخص','ملخص')) and _has(q,'عقد','عقود','تعاقد','اجاز','رصيد','ارصده','راتب','رواتب','مرتب','مرتبات','اجور'):
        return people_summary(c,u,question,people,core,normal)
    if general and _has(q,'عهد','مستند'):
        return evidence_summary(c,u,question,people,core,normal)
    if not explicit_name and _has(q,'ملخص الشركه','لخص الشركه','ملخص النظام','لخص النظام','ملخص السيستم','لخص السيستم'):
        return pending_work(c,u,question,company,people,core,normal,kinds,states)
    return None


def attendance_summary(c,u,question,company,people,core,normal):
    if core.permissions.profile(c,u) in ('payroll','auditor'):
        return _response(['تفاصيل الحضور ليست ضمن صلاحيات حسابك.'],['صلاحيات الحضور'],'attendance')
    a,b=delivery_updates.date_range(question,core,normal);q=normal(question)
    statuses={'present':'حاضر','absent':'لم يسجل حضورًا وبانتظار مراجعة الغياب','awaiting':'لم يسجل حضورًا حتى الآن؛ الوردية لم تنتهِ','approved_absence':'غياب معتمد','rest':'راحة أسبوعية','holiday':'عطلة رسمية','leave':'إجازة أو انتداب معتمد','excused':'غياب بعذر','incomplete':'سجل غير مكتمل','outside_service':'خارج فترة الخدمة'}
    totals={};details=[]
    only_present=_has(q,'مين حضر','من حضر','الحاضرين','اللي حضر','اللي حضرت')
    only_departed=_has(q,'مين انصرف','من انصرف','المنصرفين','اللي انصرف')
    open_punch=_has(q,'منصرفش','مانصرفش','لم ينصرف','ما انصرف','لم يسجل الانصراف')
    only_missing=_has(q,'محضرش','مين غاب','من غاب','الغائبين','الغايبين','اللي غاب','اللي محضرش','لم يحضر','ما حضر')
    for person in people:
        e=core.user_allowed_emp(c,u,person['id'])
        rows=core.ui_workflows.attendance_calendar(c,e,a,b,core)
        for r in rows:totals[r['status']]=totals.get(r['status'],0)+1
        if a==b:
            if not rows:continue
            r=rows[0]
            if only_present and r['status']!='present':continue
            if only_departed and not r['check_out']:continue
            if open_punch and not (r['check_in'] and not r['check_out']):continue
            if only_missing and r['status'] not in ('absent','awaiting','approved_absence','incomplete'):continue
            text=f"• {_person(person)}: {statuses.get(r['status'],r['status'])}"
            if r['check_in']:text+=' — الحضور الساعة '+r['check_in']
            if r['check_out']:text+=' — الانصراف الساعة '+r['check_out']
            details.append(text+'.')
        else:
            details.append(f"• {_person(person)}: حضور {sum(r['status']=='present' for r in rows)} يوم، غياب معتمد {sum(r['status']=='approved_absence' for r in rows)}، أيام بلا بصمة تنتظر المراجعة {sum(r['status']=='absent' for r in rows)}.")
    lines=[f'ملخص الحضور من {a} إلى {b} ضمن الشركة والصلاحيات المختارة:', '، '.join(f'{statuses.get(k,k)}: {v}' for k,v in totals.items()) or 'لا توجد سجلات للفترة.']
    lines+=details or ['لا توجد نتائج مطابقة لهذا السؤال.']
    lines.append('عدم وجود بصمة لا يعني غيابًا معتمدًا؛ الراحة والإجازة والوردية غير المنتهية موضحة حسب سجلات النظام.')
    return _response(lines,['تقويم الحضور والبصمات والإجازات ومراجعة الغياب'],'attendance')


def pending_work(c,u,question,company,people,core,normal,kinds,states):
    a,b=delivery_updates.date_range(question,core,normal);profile=core.permissions.profile(c,u)
    visible={e['id']:e for e in people}
    def request_day(r):
        stamp=dt.datetime.fromisoformat(r['created_at'].replace('Z','+00:00'))
        return (stamp.astimezone(core.workforce.zone(c,r['company_id'])) if stamp.tzinfo else stamp).date().isoformat()
    requests=[r for r in core.visible_requests(c,u,include_details=False) if (company is None or r['company_id']==company) and r['status'] in ('new','review','manager_approved') and request_day(r)<=b]
    lines=[f'الأعمال غير المكتملة حتى {b} في الشركة المختارة، حسب حالتها الحالية وصلاحياتك:',f'طلبات بانتظار إجراء: {len(requests)} (تشمل الطلبات السابقة التي ما زالت معلقة).']
    for r in requests[:8]:
        progress=core.ui_workflows.request_progress(c,r,core)
        lines.append(f"• EH-{r['id']:06d} — {r['employee_name']} ({r['employee_no']}) — {kinds.get(r['type'],r['type'])} — {states.get(r['status'],r['status'])} — الإجراء عند {progress['responsible']}.")
    if profile in ('owner','hr','manager','employee'):
        tasks=[dict(r) for r in c.execute("SELECT * FROM supervisor_tasks WHERE work_day<=? AND status!='approved' ORDER BY work_day,id",(b,)) if r['employee_id'] in visible and (u['role']!='manager' or r['supervisor_id']==u['id'])]
        lines.append(f'مهام المشرف غير المكتملة: {len(tasks)}.')
        for r in tasks[:8]:
            label='لم يرسل الموظف نتيجة التنفيذ بعد' if r['status']=='assigned' else 'أرسلها الموظف وبانتظار اعتماد المشرف'
            lines.append(f"• مهمة #{r['id']} — {_person(visible[r['employee_id']])} — {r['title'][:120]} — يوم {r['work_day']} — {label}.")
        if profile in ('owner','hr'):
            lifecycle=[dict(r) for r in c.execute("SELECT * FROM lifecycle_tasks WHERE due_on<=? AND status='open' ORDER BY due_on,id",(b,)) if r['employee_id'] in visible]
            lines.append(f'مهام إدارية مفتوحة ومستحقة: {len(lifecycle)}.')
            for r in lifecycle[:6]:lines.append(f"• مهمة إدارية #{r['id']} — {_person(visible[r['employee_id']])} — {r['title'][:120]} — مستحقة {r['due_on']}.")
        calendar=[]
        for person in people:
            e=core.user_allowed_emp(c,u,person['id'])
            for day in core.ui_workflows.attendance_calendar(c,e,a,b,core):
                if day['status'] in ('absent','awaiting','incomplete') or (day['check_in'] and not day['check_out']):calendar.append((person,day))
        lines.append(f'سجلات حضور تحتاج متابعة خلال الفترة: {len(calendar)}.')
        labels={'absent':'لم يسجل حضورًا؛ يحتاج مراجعة','awaiting':'لم يسجل حضورًا حتى الآن؛ الوردية لم تنتهِ','incomplete':'سجل بصمة غير مكتمل','present':'حاضر ولم يسجل الانصراف بعد؛ قد تكون الوردية مستمرة'}
        for person,r in calendar[:8]:lines.append(f"• {_person(person)} — {r['day']}: {labels[r['status']]}.")
    else:lines.append('المهام وتفاصيل الحضور ليست ضمن صلاحيات حسابك؛ عُرضت الطلبات المسموحة فقط.')
    lines.append('هذه متابعة للسجلات المسجلة فقط؛ لا يفترض النظام وجود أعمال غير مسجلة، ولا يعتبر انتظار نهاية الوردية تقصيرًا.')
    return _response(lines,['الطلبات وحالة مسؤول الإجراء','المهام وتقويم الحضور ضمن صلاحيات الحساب'],'pending_work')


def people_summary(c,u,question,people,core,normal):
    q=normal(question);profile=core.permissions.profile(c,u);lines=[]
    if _has(q,'راتب','رواتب','مرتب','مرتبات','اجور'):
        if profile=='hr':return _response(['الرواتب ليست ضمن صلاحيات حسابك.'],['صلاحيات البيانات المالية'],'payroll')
        allowed=[e for e in people if 'gross_salary' in e]
        lines=['الرواتب المسجلة المتاحة لحسابك، وليست إثبات صرف:',f"الإجمالي: {sum(e['gross_salary'] for e in allowed):.2f} ريال."]
        lines.extend(f"• {_person(e)}: {e['gross_salary']:.2f} ريال." for e in allowed)
        return _response(lines,['البيانات المالية المسموحة من ملفات الموظفين'],'payroll')
    if _has(q,'رصيد','ارصده','اجاز'):
        if profile in ('payroll','auditor'):return _response(['أرصدة الإجازات ليست ضمن صلاحيات حسابك.'],['صلاحيات الإجازات'],'leave')
        lines=['أرصدة الإجازات المتاحة حاليًا:']
        for person in people:
            if profile=='manager' and person['id']!=u['employee_id']:continue
            balance=core.leave(c,core.user_allowed_emp(c,u,person['id']))
            lines.append(f"• {_person(person)}: الرصيد المتاح {balance['available']} يوم.")
        return _response(lines,['حساب رصيد الإجازات الفعلي ضمن صلاحيات الحساب'],'leave')
    lines=['العقود المسجلة للموظفين المتاحين في الشركة:']
    lines.extend(f"• {_person(e)}: من {e['contract_start'] or 'غير مسجل'} إلى {e['contract_end'] or 'غير مسجل'}." for e in people)
    return _response(lines,['ملفات العقود ضمن صلاحيات الحساب'],'contracts')


def evidence_summary(c,u,question,people,core,normal):
    profile=core.permissions.profile(c,u)
    if profile not in ('owner','hr'):return _response(['عرض العهد والمستندات الجماعي يتطلب صلاحية الإدارة أو الموارد البشرية.'],['صلاحيات العهد والمستندات'],'evidence')
    ids={e['id']:e for e in people};q=normal(question)
    if 'عهد' in q:
        rows=[r for r in c.execute('SELECT * FROM employee_assets WHERE returned_on IS NULL ORDER BY id') if r['employee_id'] in ids]
        lines=[f'العهد غير المسترجعة: {len(rows)}.']
        lines.extend(f"• {_person(ids[r['employee_id']])}: {r['name']} — {r['receipt_status']}." for r in rows)
    else:
        rows=[r for r in c.execute('SELECT employee_id,name,document_type,expires_on FROM employee_files ORDER BY id') if r['employee_id'] in ids]
        lines=[f'المستندات المسجلة: {len(rows)} (أسماء وبيانات التسجيل فقط).']
        lines.extend(f"• {_person(ids[r['employee_id']])}: {r['name']} — انتهاء {r['expires_on'] or 'غير محدد'}." for r in rows)
    return _response(lines,['سجلات العهد والمستندات ضمن صلاحيات الحساب'],'evidence')

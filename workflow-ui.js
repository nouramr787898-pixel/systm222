/* Focused screens requested by the user. Stored policies and history are preserved. */
function employeeSearchText(value){return String(value||'').normalize('NFKC').toLowerCase().replace(/[\u064b-\u065f\u0670]/g,'').replace(/[أإآ]/g,'ا').trim()}
function employeeMatches(e,term){return [e.name,e.name_en,e.employee_no,e.identity_no].some(v=>employeeSearchText(v).includes(employeeSearchText(term)))}
function chooseCompanyEmployee(companyId){
 const company=companies.find(c=>c.id===companyId&&!c.archived);if(!company)return notify('الشركة غير متاحة',true);
 const candidates=employees.filter(e=>!e.archived);let selected=null;
 openModal('اختيار موظف مسجل',formwrap([dropdown('company_id','الشركة',[[company.id,company.name]],company.id),dropdown('source_company_id','شركة الموظف الحالية',[['','اختر الشركة أولًا'],...activeCompanyOptions().filter(([id])=>id!==companyId)]),field('existingEmployeeSearch','بحث بالاسم أو الهوية أو الرقم الوظيفي','search'),'<div class="employee-picker full" id="existingEmployeeChoices"></div>',field('reason','مبرر النقل','text','','required maxlength="2000"')],'نقل الموظف للشركة'),async v=>{
  if(!selected)throw Error('اختر موظفًا من القائمة');
  await api('/company-employee-move',{company_id:companyId,employee_id:selected,reason:v.reason});selectedEmployee=selected;notify('تم نقل الموظف مع حفظ تاريخه وأرصده');
 });
 $('[name=company_id]').disabled=true;const confirm=$('#modal [type=submit]');confirm.disabled=true;
 const draw=()=>{const rows=candidates.filter(e=>e.company_id===Number($('[name=source_company_id]').value)&&employeeMatches(e,$('[name=existingEmployeeSearch]').value));
  $('#existingEmployeeChoices').innerHTML=rows.map(e=>`<label class="employee-choice"><input type="radio" name="employee_id" value="${e.id}" ${e.company_id===companyId?'disabled':''} ${e.id===selected?'checked':''}><span><strong data-i18n-ignore>${esc(e.name)}</strong><small data-i18n-ignore>${esc(e.employee_no)} · ${esc(e.company_name)}</small>${e.company_id===companyId?'<small>موجود بالفعل في الشركة</small>':''}</span></label>`).join('')||'<p class="empty">لا يوجد موظف مطابق للبحث</p>';
  confirm.disabled=!rows.some(e=>e.id===selected&&e.company_id!==companyId);
  $('#existingEmployeeChoices').querySelectorAll('[name=employee_id]').forEach(r=>r.onchange=()=>{selected=Number(r.value);confirm.disabled=false});
 };
 $('[name=source_company_id]').required=true;$('[name=source_company_id]').onchange=()=>{selected=null;draw()};$('[name=existingEmployeeSearch]').oninput=draw;draw();
}
const employeeDetailBeforeGrid=window.employeeDetail;
window.employeeDetail=async function(id){
 await employeeDetailBeforeGrid(id);const body=$('#modalBody');if(!body)return;
 const buttons=[...body.querySelectorAll('button')].filter(b=>!['savePhoto','uploadEmployeeFiles'].includes(b.id));if(!buttons.length)return;
 const grid=document.createElement('div');grid.className='employee-actions-grid';grid.setAttribute('aria-label','إجراءات الموظف');buttons.forEach(b=>grid.append(b));
 body.querySelectorAll('.actions').forEach(group=>{if(!group.children.length)group.remove()});body.append(grid);
};
const auditBeforeMail=auditPage;
auditPage=async function(){
 await auditBeforeMail();if(!['owner','auditor'].includes(me.profile))return;
 const rows=await api('/email-outbox');if(view!=='audit')return;
 const section=document.createElement('section');section.className='panel';section.id='mailDeliveryAudit';section.innerHTML='<h3>حالة البريد الخارجي</h3>'+simpleTable(rows);$('#main').append(section);
};
async function newSupervisorAccount(){
 const rows=await api('/supervisor-candidates');let selected=null;
 openModal('إضافة حساب مشرف',formwrap([dropdown('company_id','الشركة',[['','اختر الشركة أولًا'],...activeCompanyOptions()]),field('supervisorSearch','بحث بالاسم أو الهوية أو الرقم الوظيفي','search'),'<div class="employee-picker full" id="supervisorChoices"></div>',field('username','اسم دخول المشرف','text','','required pattern="[A-Za-z0-9_.-]{3,64}" autocomplete="off"'),field('new_password','كلمة مرور المشرف','password','','required minlength="12" maxlength="128" autocomplete="new-password"'),field('password','كلمة مرور المسؤول الحالية','password','','required autocomplete="current-password"')]),async v=>{
  if(!selected)throw Error('اختر موظفًا من القائمة');await api('/supervisor-accounts',{...v,employee_id:selected});notify('تم إنشاء حساب المشرف؛ تغيير كلمة المرور مطلوب عند أول دخول');
 });
 const submit=$('#modal [type=submit]');submit.disabled=true;
 const draw=()=>{const matches=rows.filter(e=>e.company_id===Number($('[name=company_id]').value)&&employeeMatches(e,$('[name=supervisorSearch]').value));
  $('#supervisorChoices').innerHTML=matches.map(e=>`<label class="employee-choice"><input type="radio" name="employee_id" value="${e.id}" ${e.has_supervisor?'disabled':''} ${e.id===selected?'checked':''}><span><strong data-i18n-ignore>${esc(e.name)}</strong><small data-i18n-ignore>${esc(e.employee_no)}</small>${e.has_supervisor?'<small>له حساب مشرف بالفعل</small>':''}</span></label>`).join('')||'<p class="empty">لا يوجد موظف مطابق للبحث</p>';
  submit.disabled=!matches.some(e=>e.id===selected&&!e.has_supervisor);
  $('#supervisorChoices').querySelectorAll('[name=employee_id]').forEach(r=>r.onchange=()=>{selected=Number(r.value);submit.disabled=false});
 };
 $('[name=company_id]').onchange=()=>{selected=null;draw()};$('[name=supervisorSearch]').oninput=draw;draw();
}
const securityBeforeSupervisor=securityPage;
securityPage=async function(){await securityBeforeSupervisor();if(me.role==='admin'&&['owner','hr'].includes(me.profile)){$('#accountActions').append(actionButton('إضافة حساب مشرف',newSupervisorAccount))}};
Object.assign(uiEnglish,{'إنشاء حساب مشرف':'Create supervisor account','نقل موظف بين الشركات':'Move employee between companies',
 'طلبات الموظفين المعلّقة':'Pending employee requests','لا توجد طلبات معلّقة':'No pending requests','لا يُخصم شيء قبل الاعتماد':'Nothing is deducted before approval',
 'حاضر':'Present','غائب — لم يسجل حضور':'Absent — no check-in','غياب معتمد':'Approved absence','لم يسجل حضور حتى الآن':'No check-in yet','راحة أسبوعية':'Weekly rest day','عطلة رسمية':'Public holiday','إجازة أو انتداب معتمد':'Approved leave or assignment','غياب بعذر':'Excused absence','سجل حضور غير مكتمل':'Incomplete attendance record','خارج فترة الخدمة':'Outside employment period','لا توجد أيام في الفترة المحددة':'No days in the selected period','اعتماد الغياب':'Approve absence',
 'اختيار موظف مسجل':'Choose an existing employee','نقل الموظف للشركة':'Move employee to company','مبرر النقل':'Reason for move','موجود بالفعل في الشركة':'Already in this company','لا يوجد موظف مطابق للبحث':'No matching employee','إجراءات الموظف':'Employee actions',
 'إضافة حساب مشرف':'Add supervisor account','اسم دخول المشرف':'Supervisor login name','كلمة مرور المشرف':'Supervisor password','كلمة مرور المسؤول الحالية':'Current administrator password','له حساب مشرف بالفعل':'Already has a supervisor account','تم نقل الموظف مع حفظ تاريخه وأرصده':'Employee moved with history and balances preserved','تم إنشاء حساب المشرف؛ تغيير كلمة المرور مطلوب عند أول دخول':'Supervisor account created; password change required at first sign-in',
 ...Object.fromEntries(Object.values(types).map(ar=>['طلب '+ar,'Request: '+(uiEnglish[ar]||ar)]))
});

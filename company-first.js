/* Shared company-first employee selection. Never expands the server-visible roster. */
const companyPageChoices=new Map();
const companyOptionMeta=new WeakMap();
const employeeSelectorNames=new Set(['employee_id','manager_id','source_manager_id','target_manager_id','reviewer_id','user_id']);
function visibleCompanyOptions(includeArchived=false){
 const result=new Map();
 for(const c of companies)if(includeArchived||!c.archived)result.set(String(c.id),c.name);
 for(const e of employees)if((includeArchived||!e.archived)&&!(!includeArchived&&companies.find(c=>c.id===e.company_id)?.archived)&&!result.has(String(e.company_id)))result.set(String(e.company_id),e.company_name||'الشركة');
 return [...result.entries()];
}
const dropdownBeforeCompany=dropdown;
dropdown=function(key,title,items,value=''){
 const html=dropdownBeforeCompany(key,title,items,value);
 if(!items.some(item=>item.length>2))return html;
 const box=document.createElement('div');box.innerHTML=html;
 [...box.querySelectorAll('option')].forEach((option,i)=>{const meta=items[i][2];if(meta!==undefined)option.dataset.companyId=String(meta)});
 return box.innerHTML;
};
function companyControl(name='selection_company_id',value='',includeArchived=false){return dropdown(name,'الشركة',[['','اختر الشركة أولًا'],...visibleCompanyOptions(includeArchived)],value)}
function optionCompany(select,option){
 if(option.dataset.companyId!==undefined)return option.dataset.companyId;
 if(['user_id','reviewer_id','request_id','vacancy_id'].includes(select.name))return null;
 return String(employees.find(e=>String(e.id)===option.value)?.company_id||'');
}
function bindEmployeeCompany(form){
 const selectors=[...form.querySelectorAll('select')].filter(s=>employeeSelectorNames.has(s.name)||s.name==='request_id'||s.name==='vacancy_id');
 if(!selectors.length)return;
 const recordCompany=form.querySelector('[data-company-context]')?.dataset.companyContext;
 let company=form.querySelector('[name=company_id],[name=selection_company_id],[name=filter_company_id]');
 if(!company){const box=document.createElement('div');box.innerHTML=companyControl('selection_company_id',recordCompany||companyPageChoices.get(view)||'');company=box.querySelector('select');selectors[0].closest('label').before(company.closest('label'))}
 if(recordCompany){const markup=visibleCompanyOptions(true).filter(([id])=>id===recordCompany).map(([id,name])=>`<option value="${esc(id)}">${esc(name)}</option>`).join('');if(company.innerHTML!==markup)company.innerHTML=markup;company.value=recordCompany}
 company.required=true;
 // A real preselected context (company page or employee record) is retained.
 if(!company.dataset.companyReady){company.dataset.companyReady='1';if(!company.disabled&&!companyPageChoices.has(view)&&company.options.length>1&&!company.querySelector('option[value=""]')){company.insertAdjacentHTML('afterbegin','<option value="">اختر الشركة أولًا</option>');company.value=''}
  company.addEventListener('change',()=>{for(const select of form.querySelectorAll('select[data-company-employee]'))filterCompanyEmployee(select,company,true)},true);
 }
 for(const select of selectors){
  if(!companyOptionMeta.has(select)){
   companyOptionMeta.set(select,{options:[...select.options].map(o=>o.cloneNode(true)),markup:select.innerHTML});select.dataset.companyEmployee='1';
   if(['employee_id','user_id','request_id','vacancy_id','source_manager_id','target_manager_id','reviewer_id'].includes(select.name))select.required=true;
  }
  const model=companyOptionMeta.get(select);
  // Some existing screens rebuild their own authorized options after company changes.
  if(model.markup!==select.innerHTML){model.options=[...select.options].map(o=>o.cloneNode(true));model.markup=select.innerHTML}
  filterCompanyEmployee(select,company,false);
 }
 form.dataset.companyFirst='1';
}
function filterCompanyEmployee(select,company,reset){
 const model=companyOptionMeta.get(select);if(!model)return;
 const previous=select.value,chosen=company.value;
 const options=chosen?model.options.filter(option=>option.value&&[String(chosen),'*'].includes(optionCompany(select,option))):[];
 const empty=document.createElement('option');empty.value='';
 const prompt={vacancy_id:'اختر الوظيفة',request_id:'اختر طلب السلفة',reviewer_id:'اختر المراجع'}[select.name]||'اختر الموظف';
 empty.textContent=!chosen?'اختر الشركة أولًا':!select.required?'بدون':options.length?prompt:'لا توجد خيارات متاحة في هذه الشركة';
 const nodes=[empty,...options.map(option=>option.cloneNode(true))],markup=nodes.map(o=>o.outerHTML).join('');
 if(select.innerHTML!==markup){select.replaceChildren(...nodes);model.markup=select.innerHTML}
 select.disabled=!chosen;select.closest('label').hidden=!chosen;
 if(select.dataset.kinds&&formRequestKind(select)!=='investigation')select.closest('label').hidden=true;
 select.value=!reset&&[...select.options].some(o=>o.value===previous)?previous:'';
 if(reset&&previous)select.dispatchEvent(new Event('change',{bubbles:true}));
}

function formRequestKind(select){return select.closest('form')?.querySelector('[name=type]')?.value}
// Replace the two former one-off filters with the common implementation.
companyFirstEmployee=bindEmployeeCompany;
const modalBeforeCompany=openModal;
openModal=function(title,body,save){modalBeforeCompany(title,body,save);const form=$('#modal form');if(form)bindEmployeeCompany(form)};
let companyEnhanceQueued=false;
new MutationObserver(()=>{if(companyEnhanceQueued)return;companyEnhanceQueued=true;queueMicrotask(()=>{companyEnhanceQueued=false;const form=$('#modal form');if(form)bindEmployeeCompany(form)})}).observe($('#modalBody'),{childList:true,subtree:true});

function pageCompanyGate(key,draw,includeArchived=false){
 const chosen=companyPageChoices.get(key)||'';
 if(!visibleCompanyOptions(includeArchived).some(([id])=>id===String(chosen))){companyPageChoices.delete(key);$('#main').innerHTML=`<section class="panel company-first-gate"><p>اختر الشركة أولًا لعرض موظفيها وإجراءاتهم.</p>${companyControl('page_company_id','',includeArchived)}</section>`;$('#main [name=page_company_id]').onchange=e=>{if(!e.target.value)return;companyPageChoices.set(key,e.target.value);selectedEmployee=null;draw().catch(error=>notify(error.message,true))};return false}
 return true;
}
function pageCompanyBar(key,draw){
 if(view!==key)return;$('#main .company-first-filter')?.remove();
 const label=document.createElement('div');label.className='filterbar company-first-filter';label.innerHTML=companyControl('page_company_id',companyPageChoices.get(key)||'');$('#main').prepend(label);
 label.querySelector('select').onchange=e=>{companyPageChoices.set(key,e.target.value);selectedEmployee=null;draw().catch(error=>notify(error.message,true))};
}
function filterPageEmployee(name,key){
 const select=$(`[name=${name}]`);if(!select)return;const company=companyPageChoices.get(key);
 for(const option of [...select.options])if(option.value&&String(employees.find(e=>String(e.id)===option.value)?.company_id)!==String(company))option.remove();
 if(selectedEmployee)select.value=String(selectedEmployee);
}
function companyRoster(key){const co=companyPageChoices.get(key);return employees.filter(e=>!e.archived&&String(e.company_id)===String(co))}
function firstCompanyEmployee(key){const co=companyPageChoices.get(key);const rows=employees.filter(e=>!e.archived&&String(e.company_id)===co);if(!rows.some(e=>e.id===selectedEmployee))selectedEmployee=rows[0]?.id||null;return rows}
const leaveBeforeCompany=leavePage;
leavePage=async function(){if(!pageCompanyGate('leave',leavePage))return;const rows=firstCompanyEmployee('leave');if(rows.length){await leaveBeforeCompany();filterPageEmployee('leaveEmployee','leave')}else $('#main').innerHTML='<p class="empty">لا يوجد موظفون متاحون في هذه الشركة</p>';pageCompanyBar('leave',leavePage)};
const attendanceBeforeCompany=attendancePage;
attendancePage=async function(){if(me.role==='employee')return attendanceBeforeCompany();if(!pageCompanyGate('attendance',attendancePage))return;const rows=firstCompanyEmployee('attendance');if(rows.length){await attendanceBeforeCompany();filterPageEmployee('attEmployee','attendance')}else $('#main').innerHTML='<p class="empty">لا يوجد موظفون متاحون في هذه الشركة</p>';pageCompanyBar('attendance',attendancePage)};
const absenceBeforeCompany=absencePage;
absencePage=async function(){if(!pageCompanyGate('absence',absencePage))return;const rows=firstCompanyEmployee('absence');if(rows.length){await absenceBeforeCompany();filterPageEmployee('absenceEmployee','absence')}else $('#main').innerHTML='<p class="empty">لا يوجد موظفون متاحون في هذه الشركة</p>';pageCompanyBar('absence',absencePage)};

// Operational lists also respect the page's chosen company; source authorization remains on the server.
const apiBeforeCompany=api;
api=async function(path,body,method){const result=await apiBeforeCompany(path,body,method);const co=companyPageChoices.get(view);if(body||method&&method!=='GET'||!co)return result;
 const route=path.split('?')[0];if(route==='/data-quality'&&Array.isArray(result.issues))return {...result,issues:result.issues.filter(row=>String(row.company_id)===String(co))};if(!Array.isArray(result))return result;if(!['/action-items','/asset-receipts','/tasks','/assets','/alerts','/payroll','/loans','/pay-adjustments','/vacancies','/candidates','/appraisals','/training','/supervisor-tasks','/delegations'].includes(route))return result;
 return result.filter(row=>String(row.company_id||employees.find(e=>e.id===row.employee_id)?.company_id)===String(co));
};
const operationsBeforeCompany=operationsPage;operationsPage=async function(){if(!pageCompanyGate('operations',operationsPage))return;await operationsBeforeCompany();pageCompanyBar('operations',operationsPage)};
const payrollBeforeCompany=payrollPage;payrollPage=async function(){if(!pageCompanyGate('payroll',payrollPage))return;await payrollBeforeCompany();pageCompanyBar('payroll',payrollPage)};
const talentBeforeCompany=talentPage;talentPage=async function(){if(!pageCompanyGate('talent',talentPage))return;await talentBeforeCompany();pageCompanyBar('talent',talentPage)};
const tasksBeforeCompany=supervisorTasksPage;supervisorTasksPage=async function(){if(!pageCompanyGate('supervisor_tasks',supervisorTasksPage))return;await tasksBeforeCompany();pageCompanyBar('supervisor_tasks',supervisorTasksPage)};

Object.assign(uiEnglish,{'اختر الشركة أولًا':'Choose a company first','اختر الموظف':'Choose an employee','لا يوجد موظفون متاحون في هذه الشركة':'No available employees in this company','الرصيد الافتتاحي المسجل':'Recorded opening balance'});

const notificationsBeforeCompany=notificationsPage;notificationsPage=async function(){if(me.role==='employee')return notificationsBeforeCompany();if(!pageCompanyGate('notifications',notificationsPage))return;await notificationsBeforeCompany();pageCompanyBar('notifications',notificationsPage)};
const contactBeforeCompany=hrContactPage;hrContactPage=async function(){if(me.role!=='admin')return contactBeforeCompany();if(!pageCompanyGate('hr_contact',hrContactPage))return;await contactBeforeCompany();pageCompanyBar('hr_contact',hrContactPage)};

const renderBeforeCompanyQuality=render;render=async function(){if(view!=='data_quality')return renderBeforeCompanyQuality();menu();$('#pageTitle').textContent=names.data_quality[lang==='ar'?0:1];if(!pageCompanyGate('data_quality',render))return;await renderBeforeCompanyQuality();pageCompanyBar('data_quality',render)};

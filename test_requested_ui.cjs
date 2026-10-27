// Run against a fresh isolated demo.py server only.
const {chromium}=require('playwright'),assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({executablePath:'/usr/bin/chromium',headless:true,args:['--no-sandbox']});try{
 const origin=process.env.EHKAM_TEST_URL||'http://127.0.0.1:8880';const page=await browser.newPage(),errors=[];page.setDefaultTimeout(15000);page.on('pageerror',e=>errors.push(e.message));
 await page.goto(process.env.EHKAM_TEST_URL||'http://127.0.0.1:8880');
 const forgot=page.locator('#forgotPassword');assert.equal(await forgot.isVisible(),false);
 await page.locator('[name=account_role][value=manager]').check();assert.equal(await forgot.isVisible(),false);
 await page.locator('[name=account_role][value=employee]').check();assert.equal(await forgot.isVisible(),true);assert.equal(await forgot.innerText(),'هل نسيت كلمة المرور؟');
 async function login(role,user,pw){await page.locator(`[name=account_role][value=${role}]`).check();await page.locator('[name=username]').fill(user);await page.locator('[name=password]').fill(pw);await page.locator('#loginForm [type=submit]').click();await page.locator('#shell').waitFor({state:'visible'})}
 async function nav(key){console.log('screen:',key);await page.locator(`[data-nav=${key}]`).click()}
 async function submit(){await page.locator('#modal [type=submit]').click();await page.locator('#modal').waitFor({state:'hidden'})}
 await login('admin','admin_demo','DemoAdmin@2026');await nav('companies');await page.locator('#addCompany').click();assert.equal(await page.locator('#modal [name=latitude],#modal [name=longitude],#modal [name=geofence_radius]').count(),0);
 await page.locator('#modal [name=name]').fill('شركة اختبار تصميم');await page.locator('[name=address]').fill('عنوان تجريبي');await submit();
 await nav('employees');await page.locator('#addEmployee').click();assert.equal(await page.locator('[name=login_username]').count(),0);
 await page.locator('[name=name]').fill('موظف اختبار التصميم');await page.locator('[name=login_password]').fill('TestOnly@2026abcdef');
 const birth=page.locator('[name=birth_date]');assert.match(await birth.inputValue(),/^\d{2}\/\d{2}\/\d{4}$/);
 for(const value of ['1/2/1995','٠١/٠٢/١٩٩٥','01-02-1995']){await birth.fill(value);assert(await birth.evaluate(e=>e.checkValidity()),value)}
 await birth.fill('1/2/1995');await submit();
 const employees=await (await page.request.get(origin+'/api/employees')).json(),emp=employees.find(e=>e.name==='موظف اختبار التصميم');assert(emp);assert.equal(emp.birth_date,'1995-02-01');
 await nav('reports');await page.locator('[name=reportFrom]').fill('');await page.locator('[name=reportTo]').fill('');
 await page.locator('[name=reportType]').selectOption('contracts');await page.waitForFunction(()=>document.querySelector('#reportOutput').textContent.includes('بداية العقد'));
 assert(!(await page.locator('#reportOutput').innerText()).includes('الجوال'));
 await page.locator('[name=reportType]').selectOption('leave');await page.waitForFunction(()=>document.querySelector('#reportOutput').textContent.includes('الإجمالي المتاح'));
 await page.locator('#logout').click();await login('employee','employee_demo','DemoEmp@2026');await nav('my_requests');await page.locator('#personalRequest').click();assert.equal(await page.locator('[name=last_day_fraction]').count(),0);
 await page.waitForFunction(()=>document.querySelector('.advance-preview').textContent.includes('الرصيد الشهري الحالي'));assert((await page.locator('.advance-preview').innerText()).includes('المتوقع بعد الاعتماد إجماليًا'));
 await page.locator('[name=start_date]').fill('02/11/2026');await page.locator('[name=end_date]').fill('03/11/2026');await page.locator('[name=end_date]').dispatchEvent('change');await submit();
 await nav('notifications');await page.waitForFunction(()=>document.querySelector('#main').textContent.includes('سارة'));let content=await page.locator('#main').innerText();assert(content.includes('إجازة سنوية'));assert(content.includes('موظف'));
 await page.locator('#logout').click();await login('admin','admin_demo','DemoAdmin@2026');
 const requests=await (await page.request.get(origin+'/api/requests')).json();const req=requests.sort((a,b)=>b.id-a.id).find(r=>r.employee_id===2&&r.type==='annual_leave'&&r.status==='new');assert(req);
 const decision=await page.request.post(origin+'/api/requests/'+req.id,{data:{status:'rejected',note:'سبب رفض تجريبي'}});assert.equal(decision.status(),200,await decision.text());
 await nav('audit');await page.waitForFunction(()=>document.querySelector('#main').textContent.includes('تغيير حالة الطلب: مرفوض'));content=await page.locator('#main').innerText();assert(content.includes('سارة'));assert(content.includes('أدمن'));assert(content.includes('سبب رفض تجريبي'));
 await page.locator('#logout').click();await login('employee','موظف اختبار التصميم','TestOnly@2026abcdef');await page.locator('.emp-profile').waitFor();
 assert.deepEqual(errors,[]);console.log('PASS: requested design, dates, employee-name login, reports auto-refresh, leave preview, notifications and Arabic audit');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});

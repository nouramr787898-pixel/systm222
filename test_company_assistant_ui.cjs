// Only run against an isolated disposable demo; these checks create synthetic records.
const {chromium}=require('playwright'),assert=require('node:assert/strict');
const base=process.env.EHKAM_TEST_URL||'http://127.0.0.1:8932';
(async()=>{const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
try{
 await page.goto(base);await page.keyboard.press('Escape');await page.locator('[name=account_role][value=admin]').check();await page.locator('#loginForm [name=username]').fill('admin_demo');await page.locator('#loginForm [name=password]').fill('DemoAdmin@2026');await page.locator('#loginForm [type=submit]').click();await page.locator('#shell').waitFor({state:'visible'});
 const fixture=await page.evaluate(async()=>{const made=await api('/companies',{name:'شركة مستقلة للمساعد',address:'عنوان اختبار'});const list=await api('/companies');const co=list.find(c=>c.id===made.id)||list.find(c=>c.name==='شركة مستقلة للمساعد');await api('/employees',{company_id:co.id,name:'موظف خارج الشركة الأولى',username:'outside_roster_'+Date.now(),login_password:'CompanyRoster@2026'});await api('/tasks',{employee_id:2,kind:'general',title:'مهمة إدارية لم تنجز اليوم',due_on:currentDate()});return {company:co.id,today:currentDate()}});
 const employee=await browser.newContext(),manager=await browser.newContext();
 assert.equal((await employee.request.post(base+'/api/login',{data:{username:'employee_demo',password:'DemoEmp@2026',account_role:'employee'}})).status(),200);
 const permitted=await (await employee.request.get(base+'/api/attendance-sites')).json(),site=permitted[0];const punches=await (await employee.request.get(base+'/api/attendance')).json();if(!punches.some(r=>r.day===fixture.today&&r.check_in)){const punch=await employee.request.post(base+'/api/attendance',{data:{action:'check_in',site_id:site.id,latitude:site.latitude,longitude:site.longitude,accuracy:10}});assert.equal(punch.status(),200,await punch.text());}
 assert.equal((await manager.request.post(base+'/api/login',{data:{username:'supervisor_demo',password:'DemoSuper@2026',account_role:'manager'}})).status(),200);
 assert.equal((await manager.request.post(base+'/api/supervisor-tasks',{data:{employee_id:2,title:'مهمة الموظف لم تنجز اليوم',work_day:fixture.today}})).status(),201);
 await employee.close();await manager.close();await page.reload();await page.keyboard.press('Escape');await page.locator('[data-nav=assistant]').click();await page.locator('[name=assistantCompany]').selectOption('1');
 async function ask(q){const count=await page.locator('.chat-message.assistant').count();await page.locator('#assistantQuestion').fill(q);await page.locator('#assistantSend').click();await page.waitForFunction(n=>document.querySelectorAll('.chat-message.assistant').length>n&&!document.querySelector('.is-thinking'),count);return page.locator('.chat-message.assistant').last().innerText()}
 const roster=await ask('اعرض ليا الموظفين');assert(roster.includes('سارة')&&roster.includes('أحمد')&&roster.includes('خالد'));assert(!roster.includes('خارج الشركة الأولى'));assert(!roster.includes('ما لقيت موظف'));
 const present=await ask('هل سارة حضرت اليوم؟');assert(present.includes('حاضر')&&present.includes('سارة'));
 const summary=await ask('لخص الطلبات');assert(summary.includes('ملخص الطلبات الواردة'));
 const pending=await ask('قولي اي الي متعملش اليوم');assert(pending.includes('مهمة الموظف لم تنجز اليوم'));assert(pending.includes('مهمة إدارية لم تنجز اليوم'));assert(pending.includes('طلبات بانتظار إجراء'));
 await page.screenshot({path:'/tmp/company-assistant-general.png'});
 await page.locator('[name=assistantCompany]').selectOption(String(fixture.company));assert.equal(await page.locator('.chat-message').count(),0);
 const other=await ask('اعرض ليا الموظفين');assert(other.includes('موظف خارج الشركة الأولى'));assert(!other.includes('سارة'));
 assert((await ask('لخص الطلبات')).includes('0 طلب'));
 const otherPending=await ask('قولي اي الي متعملش اليوم');assert(!otherPending.includes('مهمة الموظف لم تنجز اليوم'));assert(!otherPending.includes('مهمة إدارية لم تنجز اليوم'));
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:'/tmp/company-assistant-scoped-mobile.png'});assert.deepEqual(errors,[]);
 console.log('PASS: actual user examples in chat, live employee presence, current request summary, unfinished supervisor/admin tasks, company switch clears history, no cross-company names or tasks, desktop/mobile');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});

// Run only against a disposable demo; never against production.
const {chromium}=require('playwright'),assert=require('node:assert/strict');
const base=process.env.EHKAM_TEST_URL||'http://127.0.0.1:8930';
(async()=>{const b=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});const context=await b.newContext({viewport:{width:1440,height:1000}}),p=await context.newPage(),errors=[];p.on('pageerror',e=>errors.push(e.message));
try{
 await p.goto(base);await p.keyboard.press('Escape');await p.locator('[name=account_role][value=admin]').check();await p.locator('#loginForm [name=username]').fill('admin_demo');await p.locator('#loginForm [name=password]').fill('DemoAdmin@2026');await p.locator('#loginForm button[type=submit]').click();await p.locator('#shell').waitFor({state:'visible'});
 await p.locator('[data-nav=assistant]').click();await p.locator('[name=assistantCompany]').selectOption('1');
 async function ask(q){const n=await p.locator('.chat-message.assistant').count();await p.locator('#assistantQuestion').fill(q);await p.locator('#assistantSend').click();await p.waitForFunction(n=>document.querySelectorAll('.chat-message.assistant').length>n&&!document.querySelector('.is-thinking'),n);return p.locator('.chat-message.assistant').last().innerText()}
 assert((await ask('لخص الطلبات الواردة من الموظفين اليوم')).includes('ملخص الطلبات الواردة'));
 const e=await p.evaluate(async()=>{const es=await api('/employees');const e=es.find(e=>e.id===2);await api('/employees/3',{name:e.name});return e});
 assert((await ask('هل سارة حضرت اليوم؟')).includes('أكثر من موظف'));assert((await ask(e.employee_no)).includes(e.employee_no));assert(!(await p.locator('.chat-message.assistant').last().innerText()).includes('الإرشادي المحلي'));
 await p.screenshot({path:'/tmp/deployed-assistant-company.png'});
 await p.locator('[data-nav=absence]').click();await p.locator('[name=page_company_id]').selectOption('1');await p.locator('#absenceAttendanceTotal').waitFor();await p.locator('[name=absenceEmployee]').selectOption('2');await p.waitForFunction(()=>document.querySelector('#absenceRows tbody')?.textContent.includes('سارة'));assert((await p.locator('#absenceAttendanceTotal').innerText()).includes(':'));
 assert.equal(await p.evaluate(()=>timeDisplay('20:19:01')),'08:19:01 م');assert.equal(await p.evaluate(()=>attendanceDurationText([{duration_seconds:1}])),'حضر ثانية واحدة');assert.equal(await p.evaluate(()=>attendanceDuration([{duration_seconds:28801}])),'08:00:01');assert.equal(await p.locator('#absenceRows .attendance-summary section').count(),3);
 const summary=await p.evaluate(()=>{const wrap=document.createElement('div');wrap.innerHTML=attendanceTable([{day:'2026-11-01',check_in:'20:58:18',check_out:'21:01:56',duration_seconds:218,source:'gps_geofence'}]);return [...wrap.querySelectorAll('.attendance-summary section')].map(e=>e.textContent)});
 assert(summary[0].includes('الحضور')&&summary[0].includes('08:58:18 م'));assert(summary[1].includes('الانصراف')&&summary[1].includes('09:01:56 م'));assert(summary[2].includes('إجمالي مدة الحضور')&&summary[2].includes('00:03:38'));
 const period=await p.evaluate(()=>{const el=document.createElement('div');el.innerHTML=attendanceSummary([{day:'2026-11-02',check_in:'09:00:00',check_out:null,duration_seconds:0},{day:'2026-11-01',check_in:'08:00:00',check_out:'10:00:00',duration_seconds:7200}]);return el.textContent});assert(period.includes('أول حضور مسجل · وردية 01/11/2026'));assert(period.includes('آخر انصراف مسجل · وردية 01/11/2026'));assert(period.includes('02:00:00'));
 await p.screenshot({path:'/tmp/deployed-attendance-duration.png'});
 const job=await p.evaluate(async()=>{await api('/vacancies',{company_id:1,title:'وظيفة اختبار الشعار',closes_on:'2027-12-31',retention_until:'2028-01-31'});return (await api('/vacancies'))[0]});assert(job.public_token);
 const applicant=await context.newPage();applicant.on('pageerror',e=>errors.push(e.message));await applicant.goto(base+'/apply/'+job.public_token);await applicant.locator('#application').waitFor({state:'visible'});assert((await applicant.locator('form').innerText()).includes('وأُقرّ بصحة البيانات'));
 const logo=applicant.locator('main>img');assert((await logo.getAttribute('src')).includes('transparent'));
 for(const width of [1440,390]){await applicant.setViewportSize({width,height:900});const image=await logo.boundingBox(),panel=await applicant.locator('main').boundingBox();assert(Math.abs((image.x+image.width/2)-(panel.x+panel.width/2))<2)}
 await applicant.screenshot({path:'/tmp/deployed-transparent-logo-mobile.png'});assert.deepEqual(errors,[]);
 console.log('PASS: deployed chat layout, company scope, live request summary, typed employee follow-up, attendance totals and one second, 12-hour clocks, centered transparent logo and consent on desktop/mobile');
}finally{await b.close()}})().catch(e=>{console.error(e);process.exitCode=1});

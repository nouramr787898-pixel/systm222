// Run against an isolated demo: EHKAM_TEST_URL=http://127.0.0.1:8891 node test_portal_ui.cjs
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',args:['--no-sandbox']});
 const context=await browser.newContext({viewport:{width:1280,height:900},geolocation:{latitude:30.1390381,longitude:31.2558794,accuracy:10},permissions:['geolocation']});
 const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 const url=process.env.EHKAM_TEST_URL||'http://127.0.0.1:8891';
 async function login(user,password,role){await page.goto(url);await page.locator(`[name=account_role][value=${role}]`).check();await page.locator('[name=username]').fill(user);await page.locator('[name=password]').fill(password);await page.locator('#loginForm button[type=submit]').click();await page.locator('#shell').waitFor({state:'visible'});}
 try{
 await login('admin_demo','DemoAdmin@2026','admin');await page.evaluate(()=>api('/employees/3',{name:'خالد علي'}));
 await page.locator('[data-nav=assistant]').click();await page.locator('[name=assistantCompany]').waitFor();assert.equal(await page.locator('[name=assistantCompany]').inputValue(),'1');await page.locator('#assistantQuestion').fill('سارة حضرت النهارده؟');await page.locator('#assistantForm button').click();await page.waitForFunction(()=>document.querySelector('#assistantAnswer').textContent.includes('سارة'));
 await page.locator('#assistantQuestion').fill('لخص ليا الطلبات الواردة من الموظفين اليوم');await page.locator('#assistantForm button').click();await page.waitForFunction(()=>document.querySelector('#assistantAnswer').textContent.includes('ملخص الطلبات الواردة'));
 await page.locator('[name=assistantCompany]').dispatchEvent('change');assert.equal(await page.locator('#assistantAnswer').innerText(),'');
 const employee=await page.evaluate(async()=>{const rows=await api('/employees');const person=rows.find(e=>e.id===2);await api('/employees/3',{name:person.name});return person});
 await page.locator('#assistantQuestion').fill('هل سارة حضرت النهارده؟');await page.locator('#assistantForm button').click();await page.locator('[data-person]').first().waitFor();assert.equal(await page.locator('[data-person]').count(),2);
 await page.locator('#assistantQuestion').fill(employee.employee_no);await page.locator('#assistantForm button').click();await page.waitForFunction(code=>document.querySelector('#assistantAnswer').textContent.includes(code),employee.employee_no);assert(!(await page.locator('#assistantAnswer').innerText()).includes('اختر الموظف'));assert((await page.locator('#assistantAnswer').innerText()).includes('حضور'));
 const duration=await page.evaluate(()=>attendanceDuration([{check_in:'20:58:18',check_out:'21:01:56'},{check_in:'09:00',check_out:'10:00'},{check_in:'09:00',check_out:null}]));assert.equal(duration,'01:03:38');
 assert.equal(await page.evaluate(()=>attendanceDuration(Array(4).fill({check_in:'00:00:00',check_out:'10:00:00'}))),'40:00:00');
 assert.equal(await page.evaluate(()=>timeDisplay('20:19')),'08:19 م');
 assert.equal(await page.evaluate(()=>timeDisplay('00:00:01')),'12:00:01 ص');
 assert.equal(await page.evaluate(()=>dateDisplay('2026-11-03T21:01:56')),'03/11/2026 09:01:56 م');
 const second=await page.evaluate(()=>absenceAttendanceTable([{day:'2026-11-01',check_in:'20:19:00',check_out:'20:19:01'}],[],'2026-11-01','2026-11-01',{name:'اختبار'}));
 assert(second.includes('حضر ثانية واحدة'));assert(second.includes('00:00:01'));assert(second.includes('08:19:00 م'));assert(second.includes('حاضر'));
 await page.locator('[data-nav=absence]').click();await page.locator('#absenceAttendanceTotal').waitFor();assert((await page.locator('#absenceRows').innerText()).includes('إجمالي عدد ساعات الحضور'));
 await page.locator('[data-nav=vacancies]').click();await page.locator('#addVacancy').waitFor();assert((await page.locator('#main').innerText()).includes('وظائف شاغرة ورابط التقديم'));
 await page.locator('#addVacancy').click();await page.locator('#modal [name=title]').fill('محلل بيانات تجريبي');await page.locator('#modal [name=closes_on]').fill('01/01/2027');await page.locator('#modal [name=retain_until]').fill('01/02/2027');await page.locator('#modal [type=submit]').click();await page.locator('#modal').waitFor({state:'hidden'});await page.locator('#main a').first().waitFor();const link=await page.locator('#main a').first().getAttribute('href');
 const publicPage=await context.newPage();publicPage.on('pageerror',e=>errors.push(e.message));await publicPage.goto(url+link);await publicPage.locator('#application').waitFor({state:'visible'});assert((await publicPage.locator('.consent').innerText()).includes('وأُقرّ بصحة البيانات'));
 for(const width of [1280,390]){await publicPage.setViewportSize({width,height:900});const box=await publicPage.locator('.brand').boundingBox();assert(Math.abs(box.x+box.width/2-width/2)<2);assert(await publicPage.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));}
 await publicPage.locator('[name=name]').fill('متقدم اختبار');await publicPage.locator('[name=email]').fill('test@example.test');await publicPage.locator('[name=phone]').fill('0500000000');await publicPage.locator('[name=cv]').setInputFiles({name:'cv.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-1.4\n%%EOF')});await publicPage.locator('[name=consent]').check();await publicPage.locator('button[type=submit]').click();await publicPage.waitForFunction(()=>document.querySelector('#message').textContent.includes('بنجاح'));
 await page.locator('#logout').click();await page.waitForLoadState('load');await page.locator('#login').waitFor({state:'visible'});await login('employee_demo','DemoEmp@2026','employee');await page.locator('[data-nav=attendance]').click();await page.locator('[name=punchSite]').waitFor();assert.equal(await page.locator('[name=punchSite] option').count(),1);await page.locator('#attendanceTotal').waitFor();await page.locator('#geoPunch').click();await page.waitForFunction(()=>document.querySelector('#attendanceRows').textContent.includes('gps_geofence'));assert.deepEqual(errors,[]);
 console.log('PASS: assistant answer, exact seconds and totals over 24h, vacancy creation, consent, public application submission, centered logo on desktop/mobile, site picker and GPS attendance');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});

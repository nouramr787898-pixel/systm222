// Requires Playwright and Chromium. Run only against a disposable demo database:
// EHKAM_DB_PATH=/tmp/ehkam-ui.sqlite3 PORT=8876 python3 server.py
// EHKAM_TEST_URL=http://127.0.0.1:8876 node test_ui.cjs
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox']});
 const context=await browser.newContext({geolocation:{latitude:24.7136,longitude:46.6753,accuracy:10},permissions:['geolocation']});
 const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 const url=process.env.EHKAM_TEST_URL||'http://127.0.0.1:8876';
 async function login(role,password){await page.goto(url);await page.locator(`[name=account_role][value=${role}]`).check();await page.locator('[name=username]').fill(role);await page.locator('[name=password]').fill(password);await page.locator('#loginForm button[type=submit]').click();await page.locator('#shell').waitFor({state:'visible'});await page.locator('#nav button').first().waitFor();}
 async function nav(key){await page.locator(`[data-nav=${key}]`).click();}
 async function submit(){await page.locator('#modal form [type=submit]').click();await page.locator('#modal').waitFor({state:'hidden'});}
 try{
 await login('employee','Employee@123');
 await page.locator('.emp-profile').waitFor();
 assert.equal(await page.locator('.emp-profile input,.emp-profile textarea').count(),0);
 assert(!(await page.locator('.emp-profile').innerText()).includes('للعرض فقط'));
 assert((await page.locator('.emp-profile').innerText()).includes('الاثنين'));
 assert((await page.locator('.emp-profile').innerText()).includes('01/01/2026'));
 await nav('my_requests');await page.locator('#personalRequest').click();
 assert.equal(await page.locator('[name=reason]').count(),0);
 assert.equal(await page.locator('[name=type] option[value=hr_contact]').count(),0);
 assert.equal(await page.locator('[name=amount]').isVisible(),false);
 await page.locator('[name=type]').selectOption('advance');assert(await page.locator('[name=amount]').isVisible());assert(await page.locator('[name=installment]').isVisible());
 await page.locator('[name=amount]').fill('100');await page.locator('[name=type]').selectOption('open');assert.equal(await page.locator('[name=installment]').isVisible(),false);
 await page.locator('[name=notes]').fill('ملاحظة اختبار الواجهة');
 await page.locator('[name=attachments]').setInputFiles({name:'ui-test.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-1.4\n%%EOF')});
 await submit();
 await page.locator('#main button', {hasText:'التفاصيل'}).first().click();
 assert(await page.locator('#modal a[download]').isVisible());assert(!(await page.locator('#modal').innerText()).includes('مبلغ السلفة:'));
 await page.locator('#closeModal').click();
 await page.locator('#personalRequest').click();await page.locator('[name=start_date]').fill('31/02/2026');await page.locator('[name=type]').selectOption('open');await page.locator('#modal [type=submit]').click();await page.waitForFunction(()=>document.querySelector('#toast').textContent.includes('التاريخ غير صالح'));assert(await page.locator('#modal').isVisible());await page.locator('#closeModal').click();
 await nav('hr_contact');await page.locator('#contactHR').click();assert.equal(await page.locator('[name=type]').inputValue(),'hr_contact');await page.locator('[name=notes]').fill('مراسلة موارد بشرية');await submit();
 await nav('my_requests');assert(!(await page.locator('#main table').innerText()).includes('تواصل مع الموارد البشرية'));
 await nav('attendance');await page.locator('#geoPunch').click();await page.waitForFunction(()=>document.querySelector('#attendanceRows').textContent.includes('gps_geofence'));
 // Checkout must fail when the new location is outside the geofence.
 await context.setGeolocation({latitude:0,longitude:0,accuracy:10});await page.locator('#geoPunch').click();await page.waitForFunction(()=>document.querySelector('#toast').textContent.includes('خارج نطاق'));assert(await page.locator('#geoPunch').isEnabled());
 await page.locator('#logout').click();await login('manager','Manager@123');
 assert(!(await page.locator('#main table').innerText()).includes('تواصل مع الموارد البشرية'));
 await page.locator('#main button',{hasText:'التفاصيل'}).first().click();
 page.once('dialog',d=>d.accept('مستند غير مكتمل'));await page.locator('[data-status=rejected]').click();await page.locator('#modal').waitFor({state:'hidden'});
 await page.locator('#main button',{hasText:'التفاصيل'}).first().click();assert((await page.locator('#modal').innerText()).includes('manager'));assert((await page.locator('#modal').innerText()).includes('مستند غير مكتمل'));await page.locator('#closeModal').click();
 await page.locator('#logout').click();await login('admin','Admin@123');await nav('employees');await page.locator('#addEmployee').click();assert.equal(await page.locator('[name=employee_no]').count(),0);assert.equal(await page.locator('[name=contract_start]').getAttribute('type'),'text');await page.locator('#closeModal').click();
 await page.locator('#main button',{hasText:'عرض الملف'}).first().click();await page.locator('#editEmployee').click();await page.locator('.workweek').waitFor();assert((await page.locator('.workweek').innerText()).includes('الأحد'));await page.locator('#closeModal').click();
 assert.deepEqual(errors,[]);
 console.log('PASS: browser profile, Arabic weekdays, date entry, conditional advance fields, attachment upload/download link, HR section/privacy, geofence, decisions and employee form');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});

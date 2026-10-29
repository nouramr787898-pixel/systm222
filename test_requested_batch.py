"""Request-specific integration checks against disposable fixtures, never production."""
import base64
import datetime
import json
import unittest
import server
import test_workflow as fixture

class RequestedBatchTests(unittest.TestCase):
    setUp=fixture.WorkflowTests.setUp
    tearDown=fixture.WorkflowTests.tearDown
    client=fixture.WorkflowTests.client

    def test_scoped_numbering_phone_and_username(self):
        self.assertEqual(self.admin('company-numbering',{'company_id':1,'prefix':'MT'})[0],200)
        body={'company_id':1,'name':'جديد','username':'new_person','login_password':'Testing@12345','phone_local':'0501234567'}
        for phone in ('501234567','05012345678'):
            self.assertEqual(self.admin('employees',{**body,'phone_local':phone})[0],400)
        code,out=self.admin('employees',body);self.assertEqual(code,201,out);self.assertEqual(out['employee_no'],'MT0001')
        self.assertEqual(self.employee('employee-login',{'employee_id':2,'username':'x'})[0],403)
        self.assertEqual(self.admin('employee-login',{'employee_id':2,'username':'admin'})[0],409)
        self.assertEqual(self.admin('employee-login',{'employee_id':2,'username':'employee_new'})[0],200)
        self.assertEqual(self.employee('me')[0],401)
        self.client('employee_new','Employee@123','employee')

    def test_asset_acceptance_and_archive_guard(self):
        code,out=self.admin('assets',{'employee_id':2,'name':'حاسب','serial':'TEST-asset'});self.assertEqual(code,201,out)
        aid=out['id'];self.assertEqual(self.employee('asset-receipts')[1][0]['receipt_status'],'pending')
        self.assertEqual(self.manager('asset-receipts/'+str(aid),{'status':'accepted'})[0],403)
        self.assertEqual(self.employee('asset-receipts/'+str(aid),{'status':'accepted'})[0],200)
        archive={'target':'employee','id':2,'archived':True,'confirm':True,'password':'Admin@123'}
        self.assertEqual(self.admin('archive',archive)[0],409)
        self.assertEqual(self.admin('assets/'+str(aid)+'/return',{'note':'استلام فعلي'})[0],200)
        self.assertEqual(self.admin('archive',archive)[0],200)

    def test_supervisor_task_review_cannot_be_bypassed(self):
        code,out=self.manager('supervisor-tasks',{'employee_id':2,'title':'تسليم التقرير','work_day':'2026-10-27'});self.assertEqual(code,201,out)
        tid=out['id'];self.assertEqual(self.admin('supervisor-tasks/'+str(tid),{'action':'approve'})[0],403)
        with server.conn() as c:
            with self.assertRaises(server.AppError):server.requested_batch.checkout_guard(c,2,'2026-10-27',server)
            server.requested_batch.checkout_guard(c,2,'2026-10-28',server)
        self.assertEqual(self.employee('supervisor-tasks/'+str(tid),{'action':'submit','note':'تم'})[0],200)
        with server.conn() as c:
            with self.assertRaises(server.AppError):server.requested_batch.checkout_guard(c,2,'2026-10-27',server)
        self.assertEqual(self.manager('supervisor-tasks/'+str(tid),{'action':'approve'})[0],200)
        with server.conn() as c:server.requested_batch.checkout_guard(c,2,'2026-10-27',server)

    def test_real_checkout_endpoint_waits_for_review(self):
        with server.conn() as c:day=server.datetime.datetime.now(server.workforce.zone(c,1)).date().isoformat()
        code,out=self.manager('supervisor-tasks',{'employee_id':2,'title':'مهمة اليوم','work_day':day});self.assertEqual(code,201,out)
        tid=out['id']
        with server.conn() as c:
            e=c.execute('SELECT * FROM employees WHERE id=2').fetchone()
            c.execute('INSERT OR REPLACE INTO attendance(employee_id,day,check_in,check_in_at,schedule_snapshot) VALUES(?,?,?,?,?)',(2,day,'09:00:00',(server.datetime.datetime.now(server.datetime.timezone.utc)-server.datetime.timedelta(minutes=2)).isoformat(),json.dumps(server.workforce.snapshot(e))))
            co=c.execute('SELECT * FROM companies WHERE id=1').fetchone();gps={'latitude':co['latitude'],'longitude':co['longitude'],'accuracy':5,'action':'check_out'}
        self.assertEqual(self.employee('attendance',gps)[0],409)
        self.assertEqual(self.employee('supervisor-tasks/'+str(tid),{'action':'submit'})[0],200)
        self.assertEqual(self.employee('attendance',gps)[0],409)
        self.assertEqual(self.manager('supervisor-tasks/'+str(tid),{'action':'approve'})[0],200)
        code,out=self.employee('attendance',gps);self.assertEqual(code,200,out)

    def test_public_recruitment_cv_scope_ranking_and_probation(self):
        code,out=self.admin('vacancies',{'company_id':1,'job_name':'محاسبة','title':'محاسب','closes_on':'2026-12-31'});self.assertEqual(code,201,out)
        vid=out['id'];code,out=self.admin('vacancies/'+str(vid)+'/publish',{'retention_until':'2027-03-01'});self.assertEqual(code,200,out)
        token=out['path'].split('/')[-1];guest=self.client();body={'name':'مرشح','email':'candidate@example.com','phone':'0501234567','consent':True}
        self.assertEqual(guest('recruitment/'+token,body)[0],400)
        body['cv']={'name':'cv.pdf','data':base64.b64encode(b'%PDF-1.4\nCV test').decode()}
        code,out=guest('recruitment/'+token,body);self.assertEqual(code,201,out)
        self.assertEqual(guest('recruitment/'+token,body)[0],409)
        candidate=self.admin('candidates')[1][0];cid=candidate['id'];self.assertNotIn('cv_content',candidate)
        self.assertEqual(self.employee('candidates/'+str(cid)+'/cv',binary=True)[0],403)
        self.assertEqual(self.admin('candidates/'+str(cid)+'/cv',binary=True)[0],200)
        self.assertEqual(self.admin('candidates/'+str(cid)+'/rank',{'priority':3})[0],200)
        self.assertEqual(self.admin('candidates/'+str(cid)+'/probation',{'employee_id':2,'start_date':'2026-10-27'})[0],200)
        with server.conn() as c:c.execute("UPDATE employees SET contract_end='2027-12-31' WHERE id=2");c.execute("UPDATE contracts SET end_date='2027-12-31' WHERE employee_id=2")
        server.TODAY=lambda:datetime.date(2027,1,25)
        result=self.admin('candidates')[1][0];self.assertTrue(result['probation_confirmed_at'])
        self.assertEqual(next(e for e in self.admin('employees')[1] if e['id']==2)['status'],'نشط')

    def test_candidate_employee_creation_and_asset_are_atomic(self):
        vid=self.admin('vacancies',{'company_id':1,'title':'محاسب','closes_on':'2026-12-31'})[1]['id']
        code,out=self.admin('candidates',{'vacancy_id':vid,'name':'مرشح كامل','email':'new@example.test','phone':'+966501234567','consent_on':'2026-10-27','retention_until':'2027-05-01'});self.assertEqual(code,201,out);cid=out['id']
        body={'company_id':1,'name':'مرشح كامل','username':'candidate_account','login_password':'Testing@12345','contract_start':'2026-10-27','contract_end':'2027-10-26','candidate_id':cid,'probation_start':'2026-10-27','asset_name':'حاسب جديد','asset_serial':'CANDIDATE-PC'}
        code,out=self.admin('employees',body);self.assertEqual(code,201,out);eid=out['id']
        person=next(e for e in self.admin('employees')[1] if e['id']==eid);self.assertEqual(person['status'],'فترة تجربة')
        row=next(c for c in self.admin('candidates')[1] if c['id']==cid);self.assertEqual(row['probation_employee_id'],eid)
        asset=next(a for a in self.admin('asset-receipts')[1] if a['employee_id']==eid);self.assertEqual(asset['receipt_status'],'pending')
        before=len(self.admin('employees')[1]);code,_=self.admin('employees',{**body,'username':'duplicate_candidate','asset_serial':'SECOND-PC'})
        self.assertEqual(code,400);self.assertEqual(len(self.admin('employees')[1]),before)

    def test_first_partial_month_gets_full_grant_only_at_month_end(self):
        code,out=self.admin('employees',{'company_id':1,'name':'موظف منتصف الشهر','username':'partial_hire','login_password':'Testing@12345','contract_start':'2026-11-15','contract_end':'2027-11-14','annual_entitlement':21})
        self.assertEqual(code,201,out);eid=out['id']
        server.TODAY=lambda:datetime.date(2026,11,29)
        self.assertEqual(self.admin('leave/'+str(eid))[1]['available'],0)
        server.TODAY=lambda:datetime.date(2026,11,30)
        self.assertEqual(self.admin('leave/'+str(eid))[1]['available'],1.75)
        code,out=self.admin('employees',{'company_id':1,'name':'موظف انتهى منتصف الشهر','username':'partial_end','login_password':'Testing@12345','contract_start':'2026-11-01','contract_end':'2026-11-15','annual_entitlement':21})
        self.assertEqual(code,201,out);self.assertEqual(self.admin('leave/'+str(out['id']))[1]['available'],0)

    def test_assistant_attendance_is_local_scoped_and_saudi(self):
        with server.conn() as c:
            name=c.execute('SELECT name FROM employees WHERE id=2').fetchone()[0]
            c.execute('INSERT OR REPLACE INTO attendance(employee_id,day,check_in) VALUES(?,?,?)',(2,'2026-10-27','09:05:00'))
        code,out=self.manager('assistant',{'question':'شف لي '+name+' حاضر اليوم؟'})
        self.assertEqual(code,200,out);self.assertIn('أبشر',out['answer']);self.assertIn('حاضر اليوم',out['answer']);self.assertEqual(out['mode'],'local_guidance')
        code,out=self.manager('assistant',{'question':'شف لي موظف غير معروف حاضر اليوم؟'})
        self.assertEqual(code,200,out);self.assertIn('المسموح',out['answer'])

    def test_branch_scope_and_calendar_month_end(self):
        code,out=self.admin('branch-sites?company_id=1',{'name':'جدة','gps':'21.5433,39.1728','radius':250});self.assertEqual(code,200,out);self.assertEqual(out[-1]['radius'],250)
        self.assertEqual(self.employee('branch-sites?company_id=1')[0],403)
        with server.conn() as c:
            e=c.execute('SELECT * FROM employees WHERE id=2').fetchone();before=server.leave(c,e)['available']
        server.TODAY=lambda:datetime.date(2026,11,15)
        with server.conn() as c:
            e=c.execute('SELECT * FROM employees WHERE id=2').fetchone();mid=server.leave(c,e);self.assertEqual(mid['available'],before);self.assertEqual(mid['monthly_allowance'],0)
        server.TODAY=lambda:datetime.date(2026,11,30)
        with server.conn() as c:
            e=c.execute('SELECT * FROM employees WHERE id=2').fetchone();last=server.leave(c,e);self.assertAlmostEqual(last['available']-before,e['annual_entitlement']/12,places=4)

if __name__=='__main__':unittest.main()

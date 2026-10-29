"""Regression tests on temporary fixtures only; never use a production database."""
import datetime as dt
import json
import sqlite3
import time
import unittest
from unittest.mock import patch
import payroll
import security
import server
import test_workflow as fixture


class ProfessionalTests(unittest.TestCase):
    setUp=fixture.WorkflowTests.setUp
    tearDown=fixture.WorkflowTests.tearDown
    client=fixture.WorkflowTests.client
    create=fixture.WorkflowTests.create
    request=fixture.WorkflowTests.request
    file=fixture.WorkflowTests.file
    new_employee=fixture.WorkflowTests.new_employee

    def reviewer(self):
        with server.conn() as c:
            c.execute("INSERT INTO users(username,password_hash,role,display_name) VALUES(?,?,'admin','Reviewer')",('reviewer',server.hashpw('Reviewer@123456789')))
        return self.client('reviewer','Reviewer@123456789','admin')

    def test_privacy_enforced_on_server(self):
        person=next(e for e in self.manager('employees')[1] if e['id']==2)
        for key in ('basic','gross_salary','iban','identity_no','email'):self.assertNotIn(key,person)
        self.assertIn('basic',self.employee('employees')[1][0])
        self.assertEqual(self.manager('reports?type=salaries')[0],403)
        self.assertEqual(self.manager('employee-files?employee_id=2')[0],403)
        self.assertEqual(self.manager('loans')[0],403)
        self.assertEqual(self.manager('payslips?employee_id=2')[0],403)

    def test_session_expiry_revocation_and_restart_survival(self):
        with server.conn() as c:
            token=security.create(c,1,'test-agent');c.commit()
            self.assertEqual(security.actor(c,token),1)
            server.SESSIONS.clear()
            self.assertEqual(security.actor(c,token),1)
            c.execute('UPDATE auth_sessions SET last_seen=0 WHERE token_hash=?',(security.digest(token),));c.commit()
            self.assertIsNone(security.actor(c,token))
        self.assertEqual(self.employee('security/revoke',{'password':'Employee@123'})[0],200)
        self.assertEqual(self.employee('me')[0],401)

    def test_login_throttle_and_failure_audit(self):
        client=self.client()
        for _ in range(10):self.assertEqual(client('login',{'username':'unknown','password':'bad','account_role':'employee'})[0],401)
        self.assertEqual(client('login',{'username':'unknown','password':'bad','account_role':'employee'})[0],429)
        self.assertTrue(any(r['action']=='login_failed' for r in self.admin('audit')[1]))

    def test_password_change_revokes_all_sessions(self):
        other=self.client('admin','Admin@123')
        self.assertEqual(self.admin('security/password',{'current_password':'bad','new_password':'LongNewPassword!123'})[0],403)
        self.assertEqual(self.admin('security/password',{'current_password':'Admin@123','new_password':'LongNewPassword!123'})[0],200)
        self.assertEqual(other('me')[0],401)
        self.client('admin','LongNewPassword!123')

    def test_totp_enrollment_and_replay_prevention(self):
        stamp=int(time.time())//30*30
        with patch.object(security.time,'time',return_value=stamp):
            code,result=self.employee('security/mfa',{'action':'enroll','password':'Employee@123'});self.assertEqual(code,200)
            secret=result['secret'];otp=security.totp(secret,stamp//30)
            self.assertEqual(self.employee('security/mfa',{'action':'confirm','password':'Employee@123','otp':otp})[0],200)
        with patch.object(security.time,'time',return_value=stamp+30):
            client=self.client();data={'username':'employee','password':'Employee@123','account_role':'employee','otp':security.totp(secret,stamp//30+1)}
            self.assertEqual(client('login',data)[0],200)
            self.assertEqual(client('login',data)[0],401)

    def test_saudi_only_contacts_and_timezone(self):
        self.assertEqual(self.admin('employees/2',{'phone_country':'+20','phone_local':'1012345678'})[0],400)
        self.assertEqual(self.admin('employees/2',{'phone_country':'+966','phone_local':'0501234567'})[0],200)
        with server.conn() as c:self.assertEqual(c.execute('SELECT timezone FROM companies WHERE id=1').fetchone()[0],'Asia/Riyadh')

    def test_aggregate_advance_limits_and_pending_reservations(self):
        self.create('advance',amount=2000,installment=1000)
        self.assertEqual(self.employee('requests',{'type':'advance','amount':1000,'installment':500})[0],400)
        self.assertEqual(len([r for r in self.employee('requests')[1] if r['type']=='advance']),1)

    def test_history_preserves_old_salary_and_leave_accrual(self):
        before=self.employee('leave/2')[1]['accrued']
        self.assertEqual(self.admin('employees/2',{'basic':6000,'annual_entitlement':30,'change_reason':'Salary revision'})[0],200)
        report=self.admin('reports?type=salaries&employee_id=2&from=2026-09-01&to=2026-09-30')[1][0]
        self.assertEqual(report['earned_for_period'],4000)
        self.assertEqual(self.employee('leave/2')[1]['accrued'],before)
        terms=self.employee('terms')[1];self.assertEqual(len(terms),2)
        self.assertEqual(self.admin('employees/2',{'basic':7000,'effective_on':'2025-01-01'})[0],400)
        self.assertEqual(self.employee('employees')[1][0]['basic'],6000)

    def test_fraction_preview_and_cross_type_overlap(self):
        body={'employee_id':2,'start_date':'2026-11-02','end_date':'2026-11-02','last_day_fraction':.5}
        preview=self.employee('leave/preview',body)[1];self.assertEqual(preview['days'],.5);self.assertEqual(preview['monthly_available'],1.75)
        rid=self.create('annual_leave',**body);self.assertEqual(self.admin(f'requests/{rid}',{'status':'approved'})[0],200)
        other=self.create('sick_leave',start_date='2026-11-02',end_date='2026-11-02',attachments=[self.file()])
        self.assertEqual(self.admin(f'requests/{other}',{'status':'approved'})[0],400)

    def test_withdrawal_and_termination_cancel(self):
        rid=self.create();self.assertEqual(self.employee(f'requests/{rid}',{'status':'withdrawn'})[0],200)
        rid=self.create('termination',last_work_day='2026-12-31')
        self.assertEqual(self.admin(f'requests/{rid}',{'status':'approved'})[0],200)
        self.assertEqual(self.admin(f'requests/{rid}',{'status':'cancelled'})[0],200)
        self.assertEqual(self.employee('employees')[1][0]['status'],'نشط')

    def test_document_versions_expiry_and_download_audit(self):
        body={'attachments':[self.file()],'document_type':'identity','expires_on':'2026-11-01'}
        status,rows=self.admin('employee-files?employee_id=2',body);self.assertEqual(status,200)
        aid=rows[0]['id'];self.assertEqual(self.employee('employee-files/'+str(aid),binary=True)[0],200)
        body['supersedes_id']=aid
        self.assertEqual(self.admin('employee-files?employee_id=2',body)[1][-1]['version'],2)
        self.assertEqual(self.admin('employee-files?employee_id=2',body)[0],400)
        self.assertTrue(any(r['kind']=='document' for r in self.admin('alerts')[1]))
        self.assertTrue(any(r['action']=='employee_file_download' for r in self.admin('audit')[1]))

    def test_tasks_assets_and_authorization(self):
        self.assertEqual(self.employee('tasks')[0],403)
        tid=self.admin('tasks',{'employee_id':2,'kind':'onboarding','title':'Collect documents','due_on':'2026-11-01'})[1]['id']
        self.assertEqual(self.admin('tasks/'+str(tid),{'status':'done','note':'Checked'})[0],200)
        asset={'employee_id':2,'name':'Laptop','serial':'LAP-1'}
        aid=self.admin('assets',asset)[1]['id'];self.assertEqual(self.admin('assets',asset)[0],400)
        self.assertEqual(self.admin(f'assets/{aid}/return',{'note':'Good condition'})[0],200)
        self.assertEqual(self.admin(f'assets/{aid}/return',{'note':'Again'})[0],409)

    def test_payroll_loan_ledger_maker_checker_and_immutable_payslip(self):
        reviewer=self.reviewer()
        rid=self.create('advance',amount=100,installment=50)
        self.assertEqual(self.admin(f'requests/{rid}',{'status':'approved'})[0],200)
        body={'request_id':rid,'paid_on':'2026-10-27','reference':'BANK-1'}
        loan=self.admin('loans/disburse',body)[1]['id'];self.assertEqual(self.admin('loans/disburse',body)[0],409)
        run=self.admin('payroll',{'company_id':1,'month':'2026-10'})[1]['id']
        self.assertEqual(self.employee('payroll')[0],403)
        self.assertEqual(self.admin(f'payroll/{run}',{'action':'review','note':'checked'})[0],200)
        self.assertEqual(self.admin(f'payroll/{run}',{'action':'approve','note':'self'})[0],403)
        self.assertEqual(reviewer(f'payroll/{run}',{'action':'approve','note':'reviewed'})[0],200)
        self.assertEqual(self.admin('employees/2',{'basic':5000})[0],409)
        self.assertEqual(self.admin(f'payroll/{run}',{'action':'pay','note':'paid','reference':'PAY-1'})[0],200)
        self.assertEqual(self.admin(f'payroll/{run}',{'action':'pay','note':'again','reference':'PAY-1'})[0],409)
        self.assertEqual(self.employee('loans')[1][0]['remaining_cents'],5000)
        slip=self.employee('payslips')[1][0];self.assertEqual(slip['loan_cents'],5000);self.assertEqual(slip['net_cents'],395000)
        with server.conn() as c:c.execute('UPDATE employees SET basic=99999 WHERE id=2')
        self.assertEqual(self.employee('payslips')[1][0]['net_cents'],395000)
        self.assertEqual(self.admin(f'requests/{rid}',{'status':'cancelled'})[0],409)

    def test_stale_payroll_is_rejected_and_rebuilt(self):
        run=self.admin('payroll',{'company_id':1,'month':'2026-10'})[1]['id']
        self.assertEqual(self.admin('employees/2',{'basic':5000})[0],200)
        self.assertEqual(self.admin(f'payroll/{run}',{'action':'review','note':'stale'})[0],409)
        self.assertEqual(self.admin(f'payroll/{run}',{'action':'rebuild','note':'refresh'})[0],200)
        self.assertEqual(self.admin(f'payroll/{run}',{'action':'review','note':'fresh'})[0],200)

    def test_adjustments_need_independent_review_and_money_precision(self):
        reviewer=self.reviewer();body={'employee_id':2,'month':'2026-10','kind':'bonus','amount':'10.05','note':'Approved incentive'}
        aid=self.admin('pay-adjustments',body)[1]['id']
        self.assertEqual(self.admin(f'pay-adjustments/{aid}',{'status':'approved'})[0],403)
        self.assertEqual(reviewer(f'pay-adjustments/{aid}',{'status':'approved'})[0],200)
        rid=self.admin('payroll',{'company_id':1,'month':'2026-10'})[1]['id']
        line=next(l for l in self.admin(f'payroll/{rid}')[1]['lines'] if l['employee_id']==2)
        self.assertEqual(line['additions_cents'],1005);self.assertEqual(payroll.cents('1.005'),101)

    def test_invalid_photo_and_migration_repeatability(self):
        self.assertEqual(self.new_employee(photo='x" onerror="alert(1)')[0],400)
        server.init()
        with server.conn() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM users').fetchone()[0],3)
            self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_import_preview_atomic_commit_and_admin_managed_password(self):
        body={'company_id':1,'csv':'name,username,basic,phone_local\nImport Test,import_user,4500,0501234567\n'}
        status,preview=self.admin('import/preview',body);self.assertEqual(status,200);self.assertEqual(preview['errors'],[])
        status,result=self.admin('import/commit',{'token':preview['token']});self.assertEqual(status,201,result)
        account=result['accounts'][0];person=self.client(account['username'],account['temporary_password'],'employee')
        self.assertEqual(person('employees')[0],200)
        self.assertEqual(person('security/password',{'current_password':account['temporary_password'],'new_password':'UniquePassword!2026'})[0],403)
        self.assertEqual(self.client('import_user',account['temporary_password'],'employee')('employees')[0],200)
        self.assertEqual(self.admin('import/commit',{'token':preview['token']})[0],409)
        bad=self.admin('import/preview',{'company_id':1,'csv':'name,username,basic\nBad,bad_import,-1\n'})[1]
        self.assertIsNone(bad['token']);self.assertEqual(len(bad['errors']),1)

    def test_appraisal_reviewer_employee_acknowledgment_and_training(self):
        body={'employee_id':2,'reviewer_id':2,'period':'2026-Q4','objectives':'Quality and attendance'}
        # Seed user 2 is the direct supervisor.
        with server.conn() as c:body['reviewer_id']=c.execute("SELECT id FROM users WHERE username='manager'").fetchone()[0]
        status,result=self.admin('appraisals',body);self.assertEqual(status,201,result);aid=result['id']
        self.assertEqual(self.employee('appraisals')[1],[])
        self.assertEqual(self.employee(f'appraisals/{aid}',{'action':'submit','score':5,'review':'Self review'})[0],403)
        self.assertEqual(self.manager(f'appraisals/{aid}',{'action':'submit','score':4,'review':'Evidence based'})[0],200)
        self.assertEqual(self.employee('appraisals')[1][0]['score'],4)
        self.assertEqual(self.employee(f'appraisals/{aid}',{'action':'acknowledge','comment':'Read'})[0],200)
        self.assertEqual(self.manager(f'appraisals/{aid}',{'action':'submit','score':5,'review':'Rewrite'})[0],403)
        tid=self.admin('training',{'employee_id':2,'title':'Safety','due_on':'2026-11-01'})[1]['id']
        self.assertEqual(self.employee('training')[1][0]['title'],'Safety')
        self.assertEqual(self.employee(f'training/{tid}',{'completed_on':'2026-10-27','evidence':'Self'})[0],403)
        self.assertEqual(self.admin(f'training/{tid}',{'completed_on':'2026-10-27','evidence':'CERT-1'})[0],200)

    def test_recruitment_consent_and_status_sequence(self):
        jid=self.admin('vacancies',{'company_id':1,'title':'Accountant','closes_on':'2026-12-31'})[1]['id']
        candidate={'vacancy_id':jid,'name':'Synthetic Candidate','email':'candidate@example.test','phone':'+966501234567','consent_on':'2026-10-27','retention_until':'2027-01-01'}
        status,row=self.admin('candidates',candidate);self.assertEqual(status,201,row);cid=row['id']
        self.assertEqual(self.manager('candidates')[0],403)
        self.assertEqual(self.admin(f'candidates/{cid}',{'status':'hired','note':'Skip interview'})[0],400)
        for state in ('interview','offered','hired'):self.assertEqual(self.admin(f'candidates/{cid}',{'status':state,'note':'Reviewed'})[0],200)
        self.assertEqual(len(self.admin('employees')[1]),3)

    def test_overnight_attendance_and_exact_start_not_late(self):
        import workforce
        with server.conn() as c:
            c.execute("UPDATE employees SET work_start='22:00',work_end='06:00' WHERE id=2")
            c.execute("INSERT INTO attendance(employee_id,day,check_in,check_out,source) VALUES(2,'2026-10-26','22:00:00','06:00:00','test')")
        row=self.admin('reports?type=attendance&employee_id=2&from=2026-10-26&to=2026-10-26')[1][0]
        self.assertEqual(row['hours'],8);self.assertFalse(row['late']);self.assertFalse(row['early_out'])
        self.assertEqual(self.admin('schedules?company_id=1',{'name':'Night','workweek':'0,1,2,3,6','work_start':'22:00','work_end':'06:00'})[0],200)

    def test_letter_privacy_and_notification_count(self):
        self.assertEqual(self.manager('employment-letter?employee_id=2',binary=True)[0],403)
        status,data=self.employee('employment-letter',binary=True);self.assertEqual(status,200);self.assertIn('مسودة'.encode(),data)
        self.create();self.assertGreater(self.employee('notifications/unread')[1]['count'],0)
        self.employee('notifications/read',{})
        self.assertEqual(self.employee('notifications/unread')[1]['count'],len(self.employee('action-items')[1]))

    def test_policy_history_preserves_old_daily_wage_basis(self):
        self.admin('policy?company_id=1',{'salary_basis':'30','approval_mode':'admin_only'})
        import hr
        with server.conn() as c:
            e=c.execute('SELECT * FROM employees WHERE id=2').fetchone()
            amount,basis=hr.daily_rate(c,e,dt.date(2026,10,1),server)
            self.assertEqual(basis,'calendar');self.assertAlmostEqual(amount,4000/31)

    def test_cross_origin_mutation_is_rejected(self):
        import urllib.request,urllib.error
        request=urllib.request.Request(self.base+'login',data=b'{}',headers={'Content-Type':'application/json','Origin':'https://evil.example'})
        with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(request)
        self.assertEqual(caught.exception.code,403)

    def test_administrative_duty_separation_is_server_enforced(self):
        clients={}
        with server.conn() as c:
            for profile in ('hr','payroll','auditor'):
                cur=c.execute("INSERT INTO users(username,password_hash,role,display_name) VALUES(?,?,'admin',?)",(profile,server.hashpw('RolePassword!2026'),profile))
                c.execute('INSERT INTO admin_profiles(user_id,profile) VALUES(?,?)',(cur.lastrowid,profile))
        for profile in ('hr','payroll','auditor'):clients[profile]=self.client(profile,'RolePassword!2026','admin')
        self.assertEqual(clients['hr']('payroll')[0],403)
        self.assertEqual(clients['hr']('reports?type=%73alaries')[0],403)
        self.assertNotIn('gross_salary',clients['hr']('employees')[1][0])
        self.assertEqual(clients['hr']('employees/2',{'basic':5000})[0],403)
        self.assertEqual(clients['hr']('employees/2',{'job':'HR-managed job'})[0],200)
        self.assertEqual(clients['payroll']('employees/2',{'job':'Not finance'})[0],403)
        self.assertEqual(clients['payroll']('employees/2',{'basic':5000,'change_reason':'Finance update'})[0],200)
        self.assertEqual(clients['payroll']('candidates')[0],403)
        self.assertEqual(clients['auditor']('employees/2',{'basic':0})[0],403)
        self.assertEqual(clients['auditor']('payroll')[0],200)
        self.assertEqual(clients['auditor']('security/sessions')[0],200)
        self.assertEqual(clients['payroll']('admin/accounts',{'password':'RolePassword!2026','username':'escalate','new_password':'RolePassword!2026','display_name':'Bad'})[0],403)

    def test_transfer_requires_clearance_and_preserves_contract_history(self):
        company=self.admin('companies',{'name':'New employer'})[1]
        with server.conn() as c:target=c.execute("SELECT id FROM companies WHERE name='New employer'").fetchone()[0]
        body={'employee_id':2,'company_id':target,'contract_end':'2027-10-27','reason':'Approved new employment'}
        self.assertEqual(self.admin('employee-transfer',body)[0],400)
        with server.conn() as c:
            c.execute("UPDATE employees SET contract_start='2026-10-01',contract_end='2026-10-26',carryover=0 WHERE id=2")
            c.execute("UPDATE contracts SET start_date='2026-10-01',end_date='2026-10-26',opening=0 WHERE employee_id=2")
        self.assertEqual(self.admin('employee-transfer',body)[0],200)
        self.assertEqual(self.employee('me')[0],401)
        person=self.client('employee','Employee@123')
        self.assertEqual(person('me')[1]['company_id'],target)
        self.assertEqual(len(person('contracts')[1]),2)
        self.assertEqual(self.manager('leave/2')[0],403)

    def test_approved_run_can_be_recalled_only_by_independent_reviewer(self):
        reviewer=self.reviewer();rid=self.admin('payroll',{'company_id':1,'month':'2026-10'})[1]['id']
        self.admin(f'payroll/{rid}',{'action':'review','note':'review'})
        reviewer(f'payroll/{rid}',{'action':'approve','note':'approve'})
        self.assertEqual(self.admin(f'payroll/{rid}',{'action':'unapprove','note':'self recall'})[0],403)
        self.assertEqual(reviewer(f'payroll/{rid}',{'action':'unapprove','note':'Correction needed'})[0],200)
        self.assertEqual(self.admin(f'payroll/{rid}',{'action':'rebuild','note':'Recalculate'})[0],200)

    def test_company_scope_blocks_lists_direct_ids_and_financial_mutations(self):
        with server.conn() as c:
            company=c.execute("INSERT INTO companies(name) VALUES('Other company')").lastrowid
            ids={}
            for profile in ('hr','payroll','auditor'):
                uid=c.execute("INSERT INTO users(username,password_hash,role,display_name) VALUES(?,?,'admin',?)",('scoped_'+profile,server.hashpw('ScopePassword!2026'),profile)).lastrowid
                c.execute('INSERT INTO admin_profiles(user_id,profile,company_id) VALUES(?,?,1)',(uid,profile));ids[profile]=uid
        hr=self.client('scoped_hr','ScopePassword!2026','admin');finance=self.client('scoped_payroll','ScopePassword!2026','admin');auditor=self.client('scoped_auditor','ScopePassword!2026','admin')
        other=self.admin('employees',{'company_id':company,'name':'Other employee','login_password':'OtherPassword!2026','basic':4000,'contract_start':'2026-01-01','contract_end':'2027-01-01'})[1]['id']
        self.assertEqual([r['id'] for r in hr('companies')[1]],[1])
        self.assertTrue(all(e['company_id']==1 for e in hr('employees')[1]))
        self.assertEqual(hr(f'policy?company_id={company}')[0],403)
        self.assertEqual(hr(f'employees/{other}',{'job':'Forbidden'})[0],403)
        task=self.admin('tasks',{'employee_id':other,'kind':'general','title':'Other private task','due_on':'2026-11-01'})[1]['id']
        self.assertEqual(hr('tasks')[1],[])
        self.assertEqual(hr(f'tasks/{task}',{'status':'done','note':'Forbidden'})[0],403)
        run=self.admin('payroll',{'company_id':company,'month':'2026-10'})[1]['id']
        self.assertEqual(finance('payroll')[1],[]);self.assertEqual(finance(f'payroll/{run}')[0],403)
        self.assertEqual(finance(f'payroll/{run}',{'action':'review','note':'Forbidden'})[0],403)
        rid=self.admin('requests',{'employee_id':other,'type':'advance','amount':100,'installment':50})[1]['id']
        self.admin(f'requests/{rid}',{'status':'approved'})
        loan=self.admin('loans/disburse',{'request_id':rid,'reference':'OTHER','paid_on':'2026-10-27'})[1]['id']
        self.assertEqual(finance('loans')[1],[])
        self.assertEqual(finance(f'loans/{loan}/repay',{'amount':10,'reference':'FORBIDDEN','paid_on':'2026-10-27'})[0],403)
        self.assertEqual(finance(f'requests/{rid}')[0],403)
        self.assertTrue(all(r['company_id']==1 for r in auditor('audit')[1]))
        self.assertFalse(any('Other employee' in r.get('info','') for r in auditor('audit')[1]))

    def test_temporary_delegation_approval_chain_and_revocation(self):
        result=self.new_employee(42,login_role='manager');self.assertEqual(result[0],201);eid=result[1]['id']
        acting=self.client('موظف تجريبي 42','Testing@12345','manager')
        self.assertEqual(acting('leave/2')[0],403)
        did=self.admin('delegations',{'source_manager_id':1,'target_manager_id':eid,'starts_on':'2026-10-27','ends_on':'2026-10-30','note':'Approved absence cover'})[1]['id']
        self.assertEqual(acting('leave/2')[0],200)
        self.admin('policy?company_id=1',{'approval_mode':'supervisor_then_admin'})
        rid=self.create('annual_leave',start_date='2026-11-02',end_date='2026-11-02')
        self.assertEqual(acting(f'requests/{rid}',{'status':'approved'})[0],200)
        self.assertEqual(self.admin(f'requests/{rid}',{'status':'approved'})[0],200)
        own=self.admin('requests',{'employee_id':eid,'type':'open','title':'Own request'})[1]['id']
        self.assertEqual(acting(f'requests/{own}',{'status':'approved'})[0],403)
        self.assertEqual(self.admin(f'delegations/{did}/revoke',{})[0],200)
        self.assertEqual(acting('leave/2')[0],403)

    def test_admin_account_scope_change_and_disable_revoke_sessions(self):
        body={'password':'Admin@123','username':'limited_auditor','display_name':'Auditor','new_password':'NewAccountPassword!2026','profile':'auditor','company_scope':1}
        status,row=self.admin('admin/accounts',body);self.assertEqual(status,201,row);uid=row['id']
        other=self.client('limited_auditor','NewAccountPassword!2026','admin')
        self.assertEqual(other('me')[1]['company_scope'],1)
        self.assertEqual(self.admin(f'admin/accounts/{uid}',{'password':'Admin@123','profile':'payroll','company_scope':1,'disabled':True})[0],200)
        self.assertEqual(other('me')[0],401)
        self.assertEqual(self.client()('login',{'username':'limited_auditor','password':'NewAccountPassword!2026','account_role':'admin'})[0],403)
        self.assertEqual(self.admin('admin/accounts/1',{'password':'Admin@123','profile':'auditor','disabled':True})[0],400)


class BackupTests(unittest.TestCase):
    def test_independent_backup_guard_and_verified_snapshot(self):
        import tempfile
        from pathlib import Path
        import backup_job,production_db,hashlib
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);db=root/'source.sqlite3';dest=root/'backup';dest.mkdir()
            result=production_db.initialize(db,'owner','UniquePassword!2026','Owner')
            with self.assertRaises(ValueError):backup_job.run(db,dest,result['database_id'])
            manifest=backup_job.run(db,dest,result['database_id'],allow_same_device=True)
            self.assertFalse(manifest['independent_device']);self.assertEqual(manifest['integrity'],'ok')
            self.assertEqual(hashlib.sha256((dest/manifest['file']).read_bytes()).hexdigest(),manifest['sha256'])
            production_db.copy_database(dest/manifest['file'],root/'restore-copy.sqlite3',result['database_id'])


class ProxyTests(unittest.TestCase):
    def test_forwarded_address_requires_a_trusted_proxy(self):
        from types import SimpleNamespace
        import os
        request=SimpleNamespace(client_address=('127.0.0.1',1000),headers={'X-Forwarded-For':'198.51.100.10'})
        with patch.dict(os.environ,{'EHKAM_TRUSTED_PROXY_NETS':''}):self.assertEqual(security.peer(request),'127.0.0.1')
        request.headers['X-Forwarded-For']='198.51.100.99, 203.0.113.10, 10.0.0.2'
        with patch.dict(os.environ,{'EHKAM_TRUSTED_PROXY_NETS':'127.0.0.0/8,10.0.0.0/8'}):self.assertEqual(security.peer(request),'203.0.113.10')


if __name__=='__main__':unittest.main()

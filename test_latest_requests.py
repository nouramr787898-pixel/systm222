"""Five new approved requirements, tested exclusively with disposable data."""
import datetime as dt
import json
import sqlite3
import unittest
from unittest.mock import patch
import server
import demo
import test_workflow as fixture

class LatestRequestsTests(unittest.TestCase):
    setUp=fixture.WorkflowTests.setUp
    tearDown=fixture.WorkflowTests.tearDown
    client=fixture.WorkflowTests.client
    create=fixture.WorkflowTests.create

    def job(self):
        code,out=self.admin('vacancies',{'company_id':1,'title':'وظيفة للحذف','closes_on':'2026-12-31','retention_until':'2027-01-31'})
        self.assertEqual(code,201,out)
        return next(r for r in self.admin('vacancies')[1] if r['id']==out['id'])

    def candidate(self,job):
        code,out=self.admin('candidates',{'vacancy_id':job['id'],'name':'مرشح تجريبي','email':'candidate@example.test','phone':'+966501234567','consent_on':'2026-10-27','retention_until':'2027-01-31'})
        self.assertEqual(code,201,out)
        with server.conn() as c:c.execute('UPDATE candidates SET cv_name=?,cv_content=? WHERE id=?',('cv.pdf',b'%PDF-1.4 Synthetic',out['id']))
        return out['id']

    def test_delete_password_confirmation_scope_and_public_link(self):
        job=self.job();cid=self.candidate(job)
        body={'confirm':True,'password':'Admin@123','job_token':job['public_token']};route='vacancies/'+str(job['id'])+'/delete'
        self.assertEqual(self.employee(route,body)[0],403)
        self.assertEqual(self.manager(route,body)[0],403)
        self.assertEqual(self.admin(route,{**body,'password':'wrong'})[0],403)
        self.assertEqual(self.admin(route,{**body,'confirm':False})[0],400)
        self.assertEqual(self.admin(route,{**body,'job_token':'stale-token'})[0],409)
        with server.conn() as c:
            before=[tuple(row) for row in c.execute('SELECT * FROM employees')]
            self.assertIsNotNone(c.execute('SELECT cv_content FROM candidates WHERE id=?',(cid,)).fetchone())
        code,out=self.admin(route,body);self.assertEqual(code,200,out);self.assertEqual(out['deleted_candidates'],1)
        with server.conn() as c:
            self.assertEqual(before,[tuple(row) for row in c.execute('SELECT * FROM employees')])
            self.assertIsNone(c.execute('SELECT 1 FROM candidates WHERE id=?',(cid,)).fetchone())
            self.assertIsNone(c.execute('SELECT 1 FROM vacancies WHERE id=?',(job['id'],)).fetchone())
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
        self.assertEqual(self.client()('recruitment/'+job['public_token'])[0],404)
        self.assertTrue(any(row['action']=='vacancy_delete' for row in self.admin('audit')[1]))

    def test_deleted_application_preserves_employee_probation(self):
        job=self.job();cid=self.candidate(job)
        with server.conn() as c:
            c.execute("UPDATE employees SET contract_end='2027-12-31' WHERE id=2")
            c.execute("UPDATE contracts SET end_date='2027-12-31' WHERE employee_id=2")
        self.assertEqual(self.admin('candidates/'+str(cid)+'/probation',{'employee_id':2,'start_date':'2026-10-27'})[0],200)
        self.assertEqual(self.admin('vacancies/'+str(job['id'])+'/delete',{'confirm':True,'password':'Admin@123','job_token':job['public_token']})[0],200)
        server.TODAY=lambda:dt.date(2027,1,25)
        self.admin('me')
        with server.conn() as c:
            self.assertEqual(c.execute('SELECT status FROM employees WHERE id=2').fetchone()[0],'نشط')
            self.assertTrue(c.execute('SELECT confirmed_at FROM employee_probations WHERE employee_id=2').fetchone()[0])

    def test_allowance_details_match_total_and_simulation_is_readonly(self):
        with server.conn() as c:
            c.execute('UPDATE employees SET housing=300,transport=150,other=50 WHERE id=2')
            before={t:[tuple(r) for r in c.execute('SELECT * FROM '+t)] for t in ('employees','payroll_runs','payroll_lines','loans','loan_repayments')}
        code,out=self.admin('payroll/simulate?company_id=1&month=2026-10');self.assertEqual(code,200,out)
        line=next(r for r in out['lines'] if r['employee_id']==2)
        self.assertEqual(line['allowance_breakdown_cents'],{'housing':30000,'transport':15000,'other':5000})
        self.assertEqual(sum(line['allowance_breakdown_cents'].values()),line['allowances_cents'])
        with server.conn() as c:
            for table,rows in before.items():self.assertEqual(rows,[tuple(r) for r in c.execute('SELECT * FROM '+table)])

    def test_assistant_reads_requests_and_disbursement_not_just_approval(self):
        with server.conn() as c:
            name=c.execute('SELECT name FROM employees WHERE id=2').fetchone()[0]
        rid=self.create('advance',amount=100,installment=100)
        with server.conn() as c:c.execute("UPDATE requests SET created_at='2026-10-27T08:00:00+00:00' WHERE id=?",(rid,))
        code,out=self.admin('assistant',{'question':'هل '+name+' أخذ سلفة اليوم؟'});self.assertEqual(code,200,out);self.assertIn('ما فيه سلفة مسجّل صرفها',out['answer']);self.assertIn('جديد',out['answer'])
        self.assertEqual(self.admin('requests/'+str(rid),{'status':'approved'})[0],200)
        self.assertEqual(self.admin('loans/disburse',{'request_id':rid,'paid_on':'2026-10-27','reference':'Synthetic transfer'})[0],201)
        out=self.admin('assistant',{'question':'هل '+name+' أخذ سلفة اليوم؟'})[1];self.assertIn('1 سلفة مسجّل صرفها',out['answer'])
        self.assertIn('مو ضمن صلاحيات',self.manager('assistant',{'question':'هل '+name+' أخذ سلفة اليوم؟'})[1]['answer'])
        out=self.employee('assistant',{'question':'هل '+name+' عمل طلب اليوم؟'})[1];self.assertIn('EH-'+str(rid).zfill(6),out['answer'])
        with patch('urllib.request.urlopen',side_effect=AssertionError('external AI forbidden')):
            out=self.employee('assistant',{'question':'كم رصيد '+name+'؟'})[1];self.assertIn('رصيده المتاح',out['answer'])

    def test_display_details_do_not_change_existing_payroll_source_hash(self):
        import hashlib
        with server.conn() as c:
            lines,digest=server.payroll.source(c,1,'2026-10',server)
            old_lines=[{k:v for k,v in row.items() if k!='allowance_breakdown_cents'} for row in lines]
            legacy_hash=hashlib.sha256(json.dumps(old_lines,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            self.assertEqual(digest,legacy_hash)
            c.execute("UPDATE employees SET hire_date='2026-11-15',housing=.01,transport=.01,other=.01 WHERE id=2")
            e=c.execute('SELECT * FROM employees WHERE id=2').fetchone()
            partial=server.payroll.calculate(c,e,'2026-11',server)
            self.assertEqual(sum(partial['allowance_breakdown_cents'].values()),partial['allowances_cents'])
            self.assertTrue(all(v>=0 for v in partial['allowance_breakdown_cents'].values()))

    def test_assistant_handles_riyadh_day_boundary_and_duplicate_names(self):
        with server.conn() as c:
            c.execute("UPDATE employees SET name='هبة الغامدي' WHERE id=2")
            c.execute("INSERT INTO employees(id,company_id,name,employee_no) VALUES(99,1,'هبة أحمد','TEST-99')")
        out=self.admin('assistant',{'question':'هل هبة عملت طلب اليوم؟'})[1];self.assertIn('أكثر من موظف',out['answer'])
        rid=self.create('open',notes='Synthetic request')
        with server.conn() as c:c.execute("UPDATE requests SET created_at='2026-10-26T22:15:00+00:00' WHERE id=?",(rid,))
        out=self.admin('assistant',{'question':'هل هبة الغامدي عملت طلب اليوم؟'})[1];self.assertIn('EH-'+str(rid).zfill(6),out['answer'])

    def test_assistant_does_not_expand_payroll_role_into_leave_balances(self):
        with server.conn() as c:
            uid=c.execute("INSERT INTO users(username,password_hash,role) VALUES('finance_facts',?,'admin')",(server.hashpw('FinancePassword!2026'),)).lastrowid
            c.execute("INSERT INTO admin_profiles(user_id,profile) VALUES(?,'payroll')",(uid,))
            name=c.execute('SELECT name FROM employees WHERE id=2').fetchone()[0]
        finance=self.client('finance_facts','FinancePassword!2026','admin')
        code,out=finance('assistant',{'question':'كم رصيد '+name+'؟'})
        self.assertEqual(code,200,out);self.assertIn('مو ضمن صلاحيات',out['answer'])

    def test_migration_backup_repeatability(self):
        with server.conn() as c:
            c.execute('DROP TABLE employee_probations')
            c.execute('DELETE FROM schema_migrations WHERE version=?',(server.requested_batch.DELETE_VERSION,))
        server.init();backup=server.DB.with_name(server.DB.name+'.before-vacancy-delete-v1.bak');self.assertTrue(backup.exists())
        data=backup.read_bytes();server.init();self.assertEqual(data,backup.read_bytes())
        with sqlite3.connect(backup) as c:self.assertFalse(c.execute("SELECT 1 FROM sqlite_master WHERE name='employee_probations'").fetchone())

if __name__=='__main__':unittest.main()

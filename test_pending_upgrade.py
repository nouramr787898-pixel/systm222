"""Explicit pending requirements; disposable SQLite fixtures only."""
import unittest
import server
import payroll
import test_workflow as fixture

class PendingUpgradeTests(unittest.TestCase):
    setUp=fixture.WorkflowTests.setUp
    tearDown=fixture.WorkflowTests.tearDown
    client=fixture.WorkflowTests.client
    create=fixture.WorkflowTests.create

    def test_employee_self_change_stays_blocked_and_recovery_requires_verified_token(self):
        self.assertEqual(self.employee('security/password',{'current_password':'Employee@123','new_password':'ChangedPassword!2026'})[0],403)
        self.assertEqual(self.client()('password/confirm',{'username':'employee','new_password':'ChangedPassword!2026','reset_token':'unverified-token'})[0],403)
        self.client('employee','Employee@123')

    def test_admin_changes_employee_password_and_revokes_sessions(self):
        target=self.admin('security/employee-passwords')[1]
        uid=next(x['user_id'] for x in target if x['employee_id']==2)
        body={'user_id':uid,'current_password':'Admin@123','new_password':'ChangedPassword!2026'}
        self.assertEqual(self.employee('security/employee-passwords',body)[0],403)
        self.assertEqual(self.admin('security/employee-passwords',{**body,'current_password':'wrong'})[0],403)
        self.assertEqual(self.admin('security/employee-passwords',body)[0],200)
        self.assertEqual(self.employee('me')[0],401)
        self.client('employee','ChangedPassword!2026')
        self.assertTrue(any(x['action']=='employee_password_change' for x in self.admin('audit')[1]))
        with server.conn() as c:
            admin=c.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
        self.assertEqual(self.admin('security/employee-passwords',{**body,'user_id':admin})[0],404)

    def test_supervisor_cannot_reset_another_company_employee(self):
        with server.conn() as c:
            c.execute("INSERT INTO companies(id,name) VALUES(2,'Other')")
            c.execute("INSERT INTO employees(id,company_id,name,employee_no) VALUES(99,2,'Other employee','EH-0099')")
            uid=c.execute("INSERT INTO users(username,password_hash,role,employee_id,company_id) VALUES('other',?,'employee',99,2)",(server.hashpw('OtherPassword!123'),)).lastrowid
        self.assertEqual(self.manager('security/employee-passwords',{'user_id':uid,'current_password':'Manager@123','new_password':'ChangedPassword!2026'})[0],403)

    def test_unplanned_advance_has_no_invented_installment_and_cannot_be_approved(self):
        before=self.employee('leave/2')[1]
        code,out=self.employee('requests',{'employee_id':2,'type':'advance','amount':100,'notes':'No inferred installment'})
        self.assertEqual(code,201,out);rid=out['id']
        self.assertEqual(self.admin('requests/'+str(rid),{'status':'approved'})[0],409)
        with server.conn() as c:
            self.assertEqual(c.execute('SELECT installment,status FROM requests WHERE id=?',(rid,)).fetchone()[:],(0,'new'))
            self.assertEqual(c.execute('SELECT COUNT(*) FROM loans').fetchone()[0],0)
        self.assertEqual(self.employee('leave/2')[1],before)

    def test_advance_preview_uses_remaining_time_and_existing_commitments(self):
        info=self.employee('advance/preview')[1]
        self.assertEqual(info['remaining_months'],server.remaining_months(info['contract_end'],server.TODAY()))
        self.assertEqual(self.manager('advance/preview?employee_id=2')[0],403)
        self.create('advance',amount=100,installment=100)
        after=self.employee('advance/preview')[1]
        self.assertAlmostEqual(info['max_advance']-after['max_advance'],100)

    def test_payroll_simulation_does_not_write_financial_state(self):
        with server.conn() as c:
            tables=('payroll_runs','payroll_lines','payroll_events','loans','loan_repayments','pay_adjustments','requests','absences','settlements')
            before={t:[tuple(x) for x in c.execute('SELECT * FROM '+t)] for t in tables}
        code,out=self.admin('payroll/simulate?company_id=1&month=2026-10')
        self.assertEqual(code,200,out);self.assertTrue(out['simulation']);self.assertFalse(out['money_transferred']);self.assertGreater(len(out['lines']),0)
        self.assertEqual(out['net_cents'],sum(x['net_cents'] for x in out['lines']))
        self.assertEqual(self.employee('payroll/simulate?company_id=1&month=2026-10')[0],403)
        with server.conn() as c:
            self.assertEqual(before,{t:[tuple(x) for x in c.execute('SELECT * FROM '+t)] for t in tables})

    def test_leave_evidence_preserves_partial_historical_requests(self):
        rid=self.create('annual_leave',start_date='2026-11-02',end_date='2026-11-02',last_day_fraction=.5)
        self.assertEqual(self.admin('requests/'+str(rid),{'status':'approved'})[0],200)
        code,out=self.employee('leave/evidence');self.assertEqual(code,200,out)
        self.assertEqual(out['approved_leave'][0]['days'],.5)
        self.assertEqual(sum(r['days'] for r in out['approved_leave']),out['balance']['approved_used'])
        self.assertEqual(self.employee('leave/evidence?employee_id=1')[0],403)
        self.assertFalse(out['automatic_changes'])

    def test_data_quality_is_read_only_and_restricted(self):
        with server.conn() as c:before=[tuple(r) for r in c.execute('SELECT * FROM employees')]
        self.assertEqual(self.employee('data-quality')[0],403)
        self.assertEqual(self.manager('data-quality')[0],403)
        code,out=self.admin('data-quality');self.assertEqual(code,200,out);self.assertFalse(out['automatic_changes'])
        with server.conn() as c:self.assertEqual(before,[tuple(r) for r in c.execute('SELECT * FROM employees')])

    def test_next_responsible_and_stage_times_follow_sequential_approval(self):
        self.assertEqual(self.admin('policy?company_id=1',{'approval_mode':'supervisor_then_admin'})[0],200)
        rid=self.create('annual_leave',start_date='2026-11-02',end_date='2026-11-02')
        first=self.employee('request-progress?request_id='+str(rid))[1]
        self.assertEqual(first['next_role'],'manager');self.assertIsNone(first['delay_threshold_seconds'])
        self.assertEqual(self.admin('action-items')[1],[])
        self.assertEqual(self.manager('requests/'+str(rid),{'status':'approved'})[0],200)
        after=self.employee('request-progress?request_id='+str(rid))[1]
        self.assertEqual(after['next_role'],'admin');self.assertEqual(after['stages'][0]['status'],'manager_approved')
        self.assertGreaterEqual(after['stages'][0]['elapsed_seconds'],0)
        self.assertEqual(self.manager('action-items')[1],[])
        self.assertEqual(self.admin('action-items')[1][0]['id'],rid)
        self.assertEqual(self.admin('requests/'+str(rid),{'status':'approved'})[0],200)
        self.assertEqual(self.admin('action-items')[1],[])
        self.assertEqual(self.employee('request-progress?request_id='+str(rid))[1]['next_role'],'none')
        with server.conn() as c:self.assertEqual(c.execute('SELECT status FROM requests WHERE id=?',(rid,)).fetchone()[0],'approved')

    def test_company_scope_and_duties_apply_to_new_read_only_routes(self):
        with server.conn() as c:
            c.execute("INSERT INTO companies(id,name) VALUES(2,'Other')")
            for profile in ('hr','payroll','auditor'):
                uid=c.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,'admin')",(profile,server.hashpw('ScopePassword!2026'))).lastrowid
                c.execute('INSERT INTO admin_profiles(user_id,profile,company_id) VALUES(?,?,1)',(uid,profile))
        hr=self.client('hr','ScopePassword!2026','admin');finance=self.client('payroll','ScopePassword!2026','admin');audit=self.client('auditor','ScopePassword!2026','admin')
        self.assertEqual(hr('payroll/simulate?company_id=1&month=2026-10')[0],403)
        self.assertEqual(finance('data-quality')[0],403)
        self.assertEqual(finance('payroll/simulate?company_id=2&month=2026-10')[0],403)
        self.assertEqual(audit('data-quality')[0],200)
        self.assertEqual(audit('security/employee-passwords')[0],403)
        self.assertEqual(finance('security/employee-passwords')[0],403)

if __name__=='__main__':unittest.main()

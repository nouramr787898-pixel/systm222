"""Regression coverage for the explicitly requested simplified workflows."""
import datetime as dt
import unittest
import server
import ui_workflows
import payroll
import test_workflow as fixture


class RequestedWorkflowsTests(unittest.TestCase):
    setUp=fixture.WorkflowTests.setUp
    tearDown=fixture.WorkflowTests.tearDown
    client=fixture.WorkflowTests.client
    create=fixture.WorkflowTests.create
    request=fixture.WorkflowTests.request
    file=fixture.WorkflowTests.file

    def test_pending_feed_removes_resolved_requests_and_deduplicates_comments(self):
        rid=self.create('annual_leave',start_date='2026-11-02',end_date='2026-11-02')
        self.assertEqual([r['id'] for r in self.admin('action-items')[1]],[rid])
        self.employee(f'requests/{rid}/comments',{'message':'Follow-up'})
        self.employee(f'requests/{rid}/comments',{'message':'Another follow-up'})
        self.assertEqual([r['id'] for r in self.admin('action-items')[1]],[rid])
        self.assertEqual(self.admin('notifications/unread')[1]['count'],1)
        self.assertEqual(self.manager(f'requests/{rid}',{'status':'approved'})[0],200)
        self.assertEqual(self.admin('action-items')[1],[])
        self.assertEqual(self.admin('notifications/unread')[1]['count'],0)
        self.employee(f'requests/{rid}/comments',{'message':'After approval'})
        self.assertEqual(self.admin('action-items')[1],[])
        self.assertEqual(self.request(rid)['status'],'approved')
        self.assertTrue(any(r['action']=='status_approved' for r in self.admin('audit')[1]))
        self.assertGreater(len(self.employee('notifications')[1]),0)  # History is retained.

    def test_calendar_shows_presence_absence_rest_holiday_and_leave(self):
        with server.conn() as c:
            c.execute("INSERT INTO attendance(employee_id,day,check_in,check_out,source) VALUES(2,'2026-10-20','09:00','17:00','test')")
            c.execute("INSERT INTO holidays(company_id,name,start_date,end_date) VALUES(1,'Holiday','2026-10-21','2026-10-21')")
        rid=self.create('annual_leave',start_date='2026-10-22',end_date='2026-10-22')
        self.admin(f'requests/{rid}',{'status':'approved'})
        code,rows=self.admin('attendance-calendar?employee_id=2&from=2026-10-19&to=2026-10-24');self.assertEqual(code,200)
        days={r['day']:r for r in rows}
        self.assertEqual(days['2026-10-19']['status'],'absent');self.assertTrue(days['2026-10-19']['can_review'])
        self.assertEqual(days['2026-10-20']['status'],'present')
        self.assertEqual(days['2026-10-21']['status'],'holiday')
        self.assertEqual(days['2026-10-22']['status'],'leave')
        self.assertEqual(days['2026-10-23']['status'],'rest')
        self.assertEqual(days['2026-10-24']['weekday'],'السبت')
        with server.conn() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM absences').fetchone()[0],0)
        self.assertEqual(self.employee('attendance-calendar?employee_id=1')[0],403)

    def test_calendar_does_not_mark_an_unfinished_day_or_night_shift_absent(self):
        import workforce
        with server.conn() as c:
            e=c.execute('SELECT * FROM employees WHERE id=2').fetchone();zone=workforce.zone(c,1)
            early=ui_workflows.attendance_calendar(c,e,'2026-10-26','2026-10-26',server,dt.datetime(2026,10,26,10,tzinfo=zone))
            late=ui_workflows.attendance_calendar(c,e,'2026-10-26','2026-10-26',server,dt.datetime(2026,10,26,18,tzinfo=zone))
            self.assertEqual(early[0]['status'],'awaiting');self.assertEqual(late[0]['status'],'absent')
            c.execute("UPDATE employees SET work_start='22:00',work_end='06:00' WHERE id=2")
            e=c.execute('SELECT * FROM employees WHERE id=2').fetchone()
            rows=ui_workflows.attendance_calendar(c,e,'2026-10-26','2026-10-26',server,dt.datetime(2026,10,27,2,tzinfo=zone))
            self.assertEqual(rows[0]['status'],'awaiting')

    def test_supervisor_account_preserves_existing_employee_account(self):
        body={'employee_id':2,'company_id':1,'username':'new_supervisor','new_password':'InitialSupervisor!2026','password':'Admin@123'}
        self.assertEqual(self.employee('supervisor-candidates')[0],403)
        self.assertEqual(self.manager('supervisor-accounts',body)[0],403)
        status,result=self.admin('supervisor-accounts',body);self.assertEqual(status,201,result)
        supervisor=self.client('new_supervisor','InitialSupervisor!2026','manager')
        self.assertTrue(supervisor('me')[1]['must_change_password'])
        self.assertEqual(supervisor('employees')[0],403)
        self.assertEqual(supervisor('security/password',{'current_password':'InitialSupervisor!2026','new_password':'ChangedSupervisor!2026'})[0],200)
        self.client('new_supervisor','ChangedSupervisor!2026','manager')
        self.assertEqual(self.employee('me')[0],200)
        self.assertEqual(self.admin('supervisor-accounts',{**body,'username':'another_supervisor'})[0],409)
        with server.conn() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM users WHERE employee_id=2 AND role='employee'").fetchone()[0],1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM users WHERE employee_id=2 AND role='manager'").fetchone()[0],1)

    def test_supervisor_requires_password_matching_company_and_unique_username(self):
        body={'employee_id':2,'company_id':1,'username':'manager','new_password':'InitialSupervisor!2026','password':'bad'}
        self.assertEqual(self.admin('supervisor-accounts',body)[0],403)
        self.assertEqual(self.admin('supervisor-accounts',{**body,'password':'Admin@123'})[0],409)
        self.assertEqual(self.admin('supervisor-accounts',{**body,'password':'Admin@123','username':'valid_manager','company_id':999})[0],400)

    def test_move_preserves_identity_contract_balances_requests_and_salary_history(self):
        self.admin('companies',{'name':'Destination'})
        with server.conn() as c:
            target=c.execute("SELECT id FROM companies WHERE name='Destination'").fetchone()[0]
            before=dict(c.execute('SELECT * FROM employees WHERE id=2').fetchone())
            contract=c.execute('SELECT id FROM contracts WHERE employee_id=2').fetchone()[0]
        rid=self.create('annual_leave',start_date='2026-11-02',end_date='2026-11-02');self.admin(f'requests/{rid}',{'status':'approved'})
        balance=self.employee('leave/2')[1]['available']
        body={'employee_id':2,'company_id':target,'reason':'Client assignment changed'}
        status,result=self.admin('company-employee-move',body);self.assertEqual(status,200,result)
        self.assertEqual(self.employee('me')[0],401)
        person=self.client('employee','Employee@123');self.assertEqual(person('me')[1]['company_id'],target)
        self.assertEqual(person('leave/2')[1]['available'],balance)
        self.assertEqual(person(f'requests/{rid}')[1]['company_id'],1)
        with server.conn() as c:
            after=dict(c.execute('SELECT * FROM employees WHERE id=2').fetchone())
            for field in ('id','employee_no','contract_start','contract_end','annual_entitlement','carryover','basic','housing','transport','other'):self.assertEqual(after[field],before[field],field)
            self.assertEqual(c.execute('SELECT id FROM contracts WHERE employee_id=2').fetchone()[0],contract)
            old=next(r for r in payroll.source(c,1,'2026-10',server)[0] if r['employee_id']==2)
            new=next(r for r in payroll.source(c,target,'2026-10',server)[0] if r['employee_id']==2)
            self.assertEqual(old['earned_cents']+new['earned_cents'],400000)
            self.assertEqual(old['company_id'],1);self.assertEqual(new['company_id'],target)
            c.execute("UPDATE companies SET salary_basis='30' WHERE id=?",(target,))
            self.assertAlmostEqual(server.hr.daily_rate(c,after,dt.date(2026,10,1),server)[0],4000/31)
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_move_blocks_pending_work_and_company_scoped_administrator(self):
        with server.conn() as c:
            target=c.execute("INSERT INTO companies(name) VALUES('Destination')").lastrowid
            uid=c.execute("INSERT INTO users(username,password_hash,role,display_name) VALUES(?,?,'admin','HR')",('hr_scope',server.hashpw('ScopedPassword!2026'))).lastrowid
            c.execute("INSERT INTO admin_profiles(user_id,profile,company_id) VALUES(?,'hr',1)",(uid,))
        limited=self.client('hr_scope','ScopedPassword!2026','admin')
        self.assertTrue(all(r['company_id']==1 for r in limited('supervisor-candidates')[1]))
        body={'employee_id':2,'company_id':target,'reason':'Move'}
        self.assertEqual(limited('company-employee-move',body)[0],403)
        self.create()
        self.assertEqual(self.admin('company-employee-move',body)[0],400)
        with server.conn() as c:self.assertEqual(c.execute('SELECT company_id FROM employees WHERE id=2').fetchone()[0],1)

    def test_manager_action_disappears_when_the_next_step_is_administration(self):
        self.admin('policy?company_id=1',{'approval_mode':'supervisor_then_admin'})
        rid=self.create('annual_leave',start_date='2026-11-02',end_date='2026-11-02')
        self.assertIn(rid,[r['id'] for r in self.manager('action-items')[1]])
        self.assertEqual(self.manager(f'requests/{rid}',{'status':'approved'})[0],200)
        self.assertNotIn(rid,[r['id'] for r in self.manager('action-items')[1]])
        self.assertIn(rid,[r['id'] for r in self.admin('action-items')[1]])
        self.assertEqual(self.admin(f'requests/{rid}',{'status':'approved'})[0],200)
        self.assertNotIn(rid,[r['id'] for r in self.admin('action-items')[1]])


if __name__=='__main__':unittest.main()

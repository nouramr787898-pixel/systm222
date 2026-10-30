"""Monthly/accumulated presentation over real balances in temporary SQLite only."""
import datetime as dt
import unittest
import server
import test_workflow as fixture

class LeaveDisplayTests(unittest.TestCase):
    setUp=fixture.WorkflowTests.setUp
    tearDown=fixture.WorkflowTests.tearDown
    client=fixture.WorkflowTests.client

    def employee_with_opening(self):
        code,out=self.admin('employees',{'company_id':1,'name':'اختبار تقسيم الرصيد','username':'leave_split','login_password':'Testing@12345','contract_start':'2026-11-01','contract_end':'2027-10-31','annual_entitlement':21,'carryover':25})
        self.assertEqual(code,201,out)
        return out['id']

    def test_user_example_and_next_month_no_duplicate_grant(self):
        eid=self.employee_with_opening()
        server.TODAY=lambda:dt.date(2026,11,30)
        b=self.admin('leave/'+str(eid))[1]
        self.assertEqual((b['monthly_available'],b['accumulated_available'],b['available']),(1.75,25,26.75))
        server.TODAY=lambda:dt.date(2026,12,1)
        b=self.admin('leave/'+str(eid))[1]
        self.assertEqual((b['monthly_available'],b['accumulated_available'],b['available']),(0,26.75,26.75))
        server.TODAY=lambda:dt.date(2026,12,31)
        b=self.admin('leave/'+str(eid))[1]
        self.assertEqual((b['monthly_available'],b['accumulated_available'],b['available']),(1.75,26.75,28.5))

    def test_before_month_end_and_no_financial_writes(self):
        eid=self.employee_with_opening();server.TODAY=lambda:dt.date(2026,11,29)
        with server.conn() as c:
            before={t:[tuple(r) for r in c.execute('SELECT * FROM '+t)] for t in ('employees','contracts','requests','settlements','absences','leave_accrual_cutover')}
            e=c.execute('SELECT * FROM employees WHERE id=?',(eid,)).fetchone()
            b=server.leave(c,e)
            self.assertEqual((b['monthly_available'],b['accumulated_available'],b['available']),(0,25,25))
            for t,rows in before.items():self.assertEqual(rows,[tuple(r) for r in c.execute('SELECT * FROM '+t)])

    def test_prior_nine_grants_are_not_presented_as_one_month(self):
        b=self.employee('leave/2')[1]
        current=sum(r['days'] for r in b['monthly_history'] if r['date'][:7]=='2026-10')
        self.assertEqual(b['monthly_available'],min(current,max(0,b['available'])))
        self.assertLessEqual(b['monthly_available'],1.75)
        self.assertEqual(b['monthly_available']+b['accumulated_available'],b['available'])
        self.assertEqual(self.manager('leave/2')[1],b)
        self.assertEqual(self.admin('leave/2')[1],b)

    def test_deductions_do_not_reappear_or_get_subtracted_twice(self):
        eid=self.employee_with_opening();server.TODAY=lambda:dt.date(2026,11,30)
        code,out=self.admin('requests',{'employee_id':eid,'type':'annual_leave','start_date':'2026-11-01','end_date':'2026-11-30','notes':'استخدام 26 يوم عمل'})
        self.assertEqual(code,201,out)
        code,out=self.admin('requests/'+str(out['id']),{'status':'approved'})
        self.assertEqual(code,200,out)
        b=self.admin('leave/'+str(eid))[1]
        self.assertEqual(b['approved_used'],26)
        self.assertEqual((b['monthly_available'],b['accumulated_available'],b['available']),(.75,0,.75))

if __name__=='__main__':unittest.main()

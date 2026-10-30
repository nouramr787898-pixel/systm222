"""Company/employee selection consistency on disposable databases."""
import unittest
import server
import test_workflow as fixture

class CompanyFirstTests(unittest.TestCase):
    setUp=fixture.WorkflowTests.setUp
    tearDown=fixture.WorkflowTests.tearDown
    client=fixture.WorkflowTests.client

    def test_mismatched_company_employee_is_rejected_from_server(self):
        with server.conn() as c:c.execute("INSERT INTO companies(id,name) VALUES(2,'شركة اختبار ثانية')")
        data={'employee_id':2,'name':'حاسب','serial':'COMPANY-FIRST','selection_company_id':2}
        self.assertEqual(self.admin('assets',data)[0],403)
        with server.conn() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM employee_assets').fetchone()[0],0)
        self.assertEqual(self.admin('assets',{**data,'selection_company_id':1})[0],201)

    def test_employee_and_supervisor_cannot_expand_scope_via_company_picker(self):
        with server.conn() as c:
            c.execute("INSERT INTO companies(id,name) VALUES(2,'شركة اختبار ثانية')")
            c.execute("INSERT INTO employees(id,company_id,name,employee_no) VALUES(90,2,'موظف خارج النطاق','TEST-90')")
        self.assertEqual(self.manager('supervisor-tasks',{'employee_id':90,'selection_company_id':2,'title':'غير مسموح','work_day':'2026-10-27'})[0],403)
        self.assertEqual(self.employee('requests',{'employee_id':90,'selection_company_id':2,'type':'open','notes':'غير مسموح'})[0],403)

if __name__=='__main__':unittest.main()

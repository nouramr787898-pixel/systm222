"""Company chat examples, grounded answers, company isolation and role restrictions."""
import datetime as dt
import json
import unittest
import server
import test_workflow as fixture


class CompanyAssistantTests(unittest.TestCase):
    setUp=fixture.WorkflowTests.setUp
    tearDown=fixture.WorkflowTests.tearDown
    client=fixture.WorkflowTests.client
    create=fixture.WorkflowTests.create

    def ask(self,question,client=None,company=1,history=None):
        code,out=(client or self.admin)('assistant',{'question':question,'company_id':company,'history':history or []})
        self.assertEqual(code,200,out)
        return out

    def test_employee_list_is_not_a_location_or_name_query(self):
        with server.conn() as c:
            co=c.execute("INSERT INTO companies(name) VALUES('شركة أخرى')").lastrowid
            c.execute("INSERT INTO employees(company_id,name,employee_no) VALUES(?,'اسم خارج الشركة','OUT-1')",(co,))
            c.execute("UPDATE employees SET archived=1 WHERE id=3")
        for q in ['اعرض ليا الموظفين','اعرض لي الموظفين','وريني موظفين الشركة','اسماء الموظفين','عدد الموظفين','قائمة الموظفين']:
            with self.subTest(q=q):
                out=self.ask(q);self.assertEqual(out['intent'],'employees')
                self.assertIn('أحمد محمد',out['answer']);self.assertIn('سارة خالد',out['answer'])
                self.assertIn('2 موظف',out['answer']);self.assertNotIn('خالد علي',out['answer']);self.assertNotIn('خارج الشركة',out['answer'])
                self.assertNotIn('ما لقيت موظف',out['answer'])
        own=self.ask('اعرض ليا الموظفين',self.employee)
        self.assertIn('سارة خالد',own['answer']);self.assertNotIn('أحمد محمد',own['answer'])
        self.assertEqual(self.manager('assistant',{'question':'اعرض الموظفين','company_id':co})[0],403)
        empty=self.ask('اعرض ليا الموظفين',company=co)
        self.assertIn('اسم خارج الشركة',empty['answer']);self.assertNotIn('سارة خالد',empty['answer'])

    def test_request_summary_and_named_employee_are_distinct(self):
        rid=self.create()
        with server.conn() as c:
            c.execute('UPDATE requests SET created_at=? WHERE id=?',(server.TODAY().isoformat()+'T09:00:00+03:00',rid))
            other=c.execute("INSERT INTO requests(company_id,employee_id,type,created_by,created_at,updated_at) VALUES(1,3,'open',1,?,?)",(server.TODAY().isoformat()+'T09:00:00+03:00',server.now())).lastrowid
        out=self.ask('لخص الطلبات');self.assertEqual(out['intent'],'requests');self.assertIn('2 طلب',out['answer'])
        personal=self.ask('لخص طلبات سارة اليوم')
        self.assertIn(f'EH-{rid:06d}',personal['answer']);self.assertNotIn(f'EH-{other:06d}',personal['answer'])
        self.assertNotIn('ما لقيت',personal['answer'])
        detail=self.ask(f'حالة الطلب رقم {rid}')
        self.assertEqual(detail['intent'],'request_detail');self.assertIn(f'EH-{rid:06d}',detail['answer']);self.assertNotIn(f'EH-{other:06d}',detail['answer'])


    def test_presence_and_deictic_selection(self):
        with server.conn() as c:c.execute("INSERT INTO attendance(employee_id,day,check_in,check_out,source) VALUES(2,?,'09:00:00','09:00:01','gps_geofence')",(server.TODAY().isoformat(),))
        out=self.ask('هل سارة حضرت اليوم؟');self.assertIn('حاضر',out['answer']);self.assertIn('شركة الأفق',out['answer'])
        everyone=self.ask('مين حضر اليوم');self.assertEqual(everyone['intent'],'attendance');self.assertIn('سارة خالد',everyone['answer'])
        self.assertNotIn('أحمد محمد',everyone['answer'])
        choices=self.ask('هل الموظف ده حضر ام لا');self.assertTrue(choices['choices'])
        history=[{'role':'user','content':'هل الموظف ده حضر ام لا'},{'role':'assistant','content':choices['answer']}]
        out=self.ask('EH-0002',history=history);self.assertIn('حاضر',out['answer'])
        followup=[{'role':'user','content':'هل سارة حضرت اليوم؟'},{'role':'assistant','content':out['answer'][:4000]}]
        departed=self.ask('مين انصرف اليوم',history=followup)
        self.assertEqual(departed['intent'],'attendance');self.assertIn('سارة خالد',departed['answer']);self.assertNotIn('أحمد محمد',departed['answer'])
        roster=self.ask('اعرض ليا الموظفين',history=followup)
        self.assertEqual(roster['intent'],'employees');self.assertIn('أحمد محمد',roster['answer'])


    def test_pending_work_uses_actual_requests_and_tasks_without_writes(self):
        rid=self.create();private=self.employee('requests',{'type':'hr_contact','employee_id':2,'notes':'PRIVATE_HR_ONLY'})[1]['id']
        task=self.manager('supervisor-tasks',{'employee_id':2,'title':'مهمة يومية غير منفذة','work_day':server.TODAY().isoformat()})
        self.assertEqual(task[0],201,task)
        administrative=self.admin('tasks',{'employee_id':2,'kind':'general','title':'مراجعة ملف إداري','due_on':server.TODAY().isoformat()})
        self.assertEqual(administrative[0],201,administrative)
        with server.conn() as c:
            c.execute('UPDATE requests SET created_at=?',(server.TODAY().isoformat()+'T09:00:00+03:00',))
            before={t:[tuple(r) for r in c.execute('SELECT * FROM '+t)] for t in ['requests','supervisor_tasks','lifecycle_tasks','attendance','payroll_runs','loans']}
        for q in ['قولي اي الي متعملش اليوم','ايه اللي متعملش النهارده','ما المهام غير المكتملة اليوم؟']:
            with self.subTest(q=q):
                out=self.ask(q);self.assertEqual(out['intent'],'pending_work')
                self.assertIn('مهمة يومية غير منفذة',out['answer']);self.assertIn('مراجعة ملف إداري',out['answer']);self.assertIn(f'EH-{rid:06d}',out['answer'])
                self.assertIn('طلبات بانتظار إجراء: 2',out['answer'])
        manager=self.ask('قولي اي الي متعملش اليوم',self.manager)['answer']
        self.assertIn('مهمة يومية غير منفذة',manager);self.assertNotIn(f'EH-{private:06d}',manager);self.assertNotIn('مراجعة ملف إداري',manager)
        own=self.ask('المهام غير المكتملة اليوم',self.employee)['answer'];self.assertIn('مهمة يومية غير منفذة',own);self.assertNotIn('مراجعة ملف إداري',own)
        with server.conn() as c:
            for t,rows in before.items():self.assertEqual(rows,[tuple(r) for r in c.execute('SELECT * FROM '+t)],t)

    def test_scoped_hr_finance_and_auditor_cannot_expand_data(self):
        with server.conn() as c:
            for role in ['hr','payroll','auditor']:
                uid=c.execute('INSERT INTO users(username,password_hash,role) VALUES(?,?,"admin")',('company_'+role,server.hashpw('ScopedPass@2026'))).lastrowid
                c.execute('INSERT INTO admin_profiles(user_id,profile,company_id) VALUES(?,?,1)',(uid,role))
        hr=self.client('company_hr','ScopedPass@2026','admin');finance=self.client('company_payroll','ScopedPass@2026','admin');auditor=self.client('company_auditor','ScopedPass@2026','admin')
        self.assertIn('ليست ضمن صلاحيات',self.ask('لخص رواتب الموظفين',hr)['answer'])
        for client in (finance,auditor):
            self.assertIn('ليست ضمن صلاحيات',self.ask('لخص حضور الموظفين اليوم',client)['answer'])
            self.assertIn('ليست ضمن صلاحيات',self.ask('لخص أرصدة الموظفين',client)['answer'])
            self.assertIn('الطلبات المسموحة فقط',self.ask('قولي اي الي متعملش اليوم',client)['answer'])


if __name__=='__main__':unittest.main()

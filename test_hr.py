"""Requirements from both PDFs and written calculations; isolated HTTP/SQLite tests."""
import datetime as dt
import io
import json
import sqlite3
import unittest
from unittest.mock import patch
import zipfile
import xml.etree.ElementTree as ET

import server
import hr
import test_workflow as fixture


class HRTests(unittest.TestCase):
    setUp=fixture.WorkflowTests.setUp
    tearDown=fixture.WorkflowTests.tearDown
    client=fixture.WorkflowTests.client
    create=fixture.WorkflowTests.create
    request=fixture.WorkflowTests.request
    file=fixture.WorkflowTests.file
    new_employee=fixture.WorkflowTests.new_employee

    def test_exact_advance_example_and_installment_repayment(self):
        server.TODAY=lambda:dt.date(2026,8,1)
        info=self.employee('advance/preview')[1]
        self.assertEqual((info['gross_salary'],info['remaining_months'],info['max_advance']),(4000,5,7000))
        self.assertEqual(self.employee('requests',{'type':'advance','amount':9000,'installment':1400})[0],400)
        self.assertEqual(self.employee('requests',{'type':'advance','amount':7000,'installment':1400})[0],201)
        self.assertEqual(self.employee('requests',{'type':'advance','amount':7000,'installment':100})[0],400)
        self.assertEqual(self.admin('policy?company_id=1',{'salary_basis':'calendar','advance_min_days':200,'notice_days':60})[0],200)
        self.assertEqual(self.employee('requests',{'type':'advance','amount':100,'installment':100})[0],400)

    def test_exact_leave_example_fractional_request_and_no_early_deduction(self):
        server.TODAY=lambda:dt.date(2026,4,1)
        balance=self.employee('leave/2')[1]
        self.assertEqual((balance['monthly'],balance['accrued'],balance['carryover'],balance['available']),(1.75,5.25,25,30.25))
        self.assertEqual(len(balance['monthly_history']),3)
        rid=self.create('annual_leave',start_date='01/04/2026',end_date='01/04/2026',last_day_fraction=.25)
        self.assertEqual(self.employee('leave/2')[1]['available'],30.25)
        self.assertEqual(self.manager(f'requests/{rid}',{'status':'approved'})[0],200)
        self.assertEqual(self.employee('leave/2')[1]['available'],30)
        self.assertEqual(self.admin(f'requests/{rid}',{'status':'cancelled'})[0],200)
        self.assertEqual(self.employee('leave/2')[1]['available'],30.25)

    def test_renewal_rolls_balance_once_and_stops_old_accrual(self):
        server.TODAY=lambda:dt.date(2026,4,1)
        self.assertEqual(self.admin('employees/2',{'contract_end':'01/04/2026'})[0],200)
        old=self.employee('leave/2')[1]
        code,data=self.admin('contracts/renew',{'employee_id':2,'start_date':'01/04/2026','end_date':'01/04/2027','annual_entitlement':21})
        self.assertEqual(code,200,data);self.assertEqual(data['carryover'],30.25)
        self.assertEqual(self.employee('leave/2')[1]['accrued'],0)
        self.assertEqual(self.employee('leave/2')[1]['available'],30.25)
        self.assertEqual(self.admin('contracts/renew',{'employee_id':2,'start_date':'01/04/2026','end_date':'01/04/2027'})[0],400)
        history=self.employee('contracts?employee_id=2')[1]
        self.assertEqual(len(history),2);self.assertEqual(history[0]['closed_balance'],30.25)
        server.TODAY=lambda:dt.date(2026,5,1)
        self.assertEqual(self.employee('leave/2')[1]['available'],32)
        self.assertNotEqual(old['contract_id'],self.employee('leave/2')[1]['contract_id'])

    def test_contract_end_cap_and_month_end_anniversary(self):
        server.TODAY=lambda:dt.date(2028,10,27)
        self.assertEqual(self.employee('leave/2')[1]['accrued'],0)
        self.assertEqual(self.employee('leave/2')[1]['carryover'],46)
        self.assertEqual(server.months_elapsed('2026-01-31',dt.date(2026,2,28)),1)

    def test_absence_approval_actual_month_salary_and_correction_reversal(self):
        before=self.employee('leave/2')[1]['available']
        rows=self.admin('absences?employee_id=2&from=26/10/2026&to=26/10/2026')[1]
        self.assertEqual(rows[0]['status'],'pending');self.assertEqual(self.employee('leave/2')[1]['available'],before)
        data={'employee_id':2,'day':'26/10/2026','status':'approved','note':'غياب بعد المراجعة'}
        self.assertEqual(self.manager('absences/decision',data)[0],403)
        code,result=self.admin('absences/decision',data)
        self.assertEqual(code,200,result);self.assertEqual(result['salary_deduction'],129.03)
        self.assertEqual(self.employee('leave/2')[1]['available'],before-1)
        self.assertEqual(self.admin('absences/decision',data)[0],400)
        rid=self.create('punch_fix',start_date='26/10/2026',correction_type='check_in')
        self.assertEqual(self.manager(f'requests/{rid}',{'status':'approved'})[0],200)
        row=self.employee('attendance?day=26/10/2026')[1][0]
        self.assertEqual(row['check_in'],'09:00');self.assertIsNone(row['check_out'])
        self.assertEqual(self.employee('leave/2')[1]['available'],before)
        salaries=self.admin('reports?type=salaries&employee_id=2')[1]
        self.assertEqual(salaries[0]['approved_absence_deductions'],0)

    def test_partial_leave_absence_and_later_cancellation_reconcile(self):
        rid=self.create('annual_leave',start_date='26/10/2026',end_date='26/10/2026',last_day_fraction=.25)
        self.assertEqual(self.manager(f'requests/{rid}',{'status':'approved'})[0],200)
        data={'employee_id':2,'day':'26/10/2026','status':'approved','note':'غياب بقية اليوم'}
        code,result=self.admin('absences/decision',data)
        self.assertEqual(code,200,result);self.assertEqual(result['leave_deduction'],.75)
        self.assertEqual(result['salary_deduction'],96.77)
        self.assertEqual(self.admin(f'requests/{rid}',{'status':'cancelled'})[0],200)
        rows=self.admin('absences?employee_id=2&from=26/10/2026&to=26/10/2026')[1]
        self.assertEqual((rows[0]['leave_days'],rows[0]['salary_deduction']),(1,129.03))

    def test_friday_and_holidays_and_schedules(self):
        self.assertEqual(self.admin('holidays?company_id=1',{'name':'عطلة تجريبية','start_date':'26/10/2026','end_date':'26/10/2026'})[0],200)
        self.assertEqual(self.employee('absences?from=23/10/2026&to=26/10/2026')[1][0]['day'],'2026-10-24')
        out=self.employee('leave/preview',{'employee_id':2,'start_date':'23/10/2026','end_date':'26/10/2026'})[1]
        self.assertEqual(out['days'],2)
        code,schedules=self.admin('schedules?company_id=1',{'name':'كول سنتر الجمعة','workweek':'4','work_start':'10:00','work_end':'18:00'})
        self.assertEqual(code,200,schedules)
        self.assertEqual(self.admin('employees/2',{'schedule_id':schedules[0]['id']})[0],200)
        e=self.employee('employees')[1][0];self.assertEqual((e['workweek'],e['work_start']),('4','10:00'))
        self.assertEqual(self.employee('leave/preview',{'employee_id':2,'start_date':'23/10/2026','end_date':'23/10/2026'})[1]['days'],1)

    def test_site_assignment_and_cross_company_denial(self):
        code,sites=self.admin('sites?company_id=1',{'name':'موقع آخر','latitude':20,'longitude':20,'radius':100})
        self.assertEqual(code,200,sites)
        self.assertEqual(self.admin('employees/2',{'site_id':sites[0]['id']})[0],200)
        self.assertEqual(self.employee('attendance',{'latitude':24.7136,'longitude':46.6753,'accuracy':10})[0],400)
        self.assertEqual(self.employee('attendance',{'latitude':20,'longitude':20,'accuracy':10})[0],200)
        with server.conn() as c:c.execute("INSERT INTO companies(id,name) VALUES(2,'أخرى')")
        site=self.admin('sites?company_id=2',{'name':'أخرى','latitude':30,'longitude':30,'radius':100})[1][0]
        self.assertEqual(self.admin('employees/2',{'site_id':site['id']})[0],400)
        self.assertEqual(self.manager('sites?company_id=1')[0],403)

    def test_permission_sick_and_investigation_requirements(self):
        self.assertEqual(self.employee('requests',{'type':'permission','start_date':'01/11/2026','end_date':'01/11/2026'})[0],400)
        rid=self.create('permission',start_date='01/11/2026',end_date='01/11/2026',time_from='10:00',time_to='11:30')
        self.assertEqual(self.request(rid)['details']['minutes'],90)
        self.assertEqual(self.employee('requests',{'type':'sick_leave','notes':'نص فقط','start_date':'01/11/2026','end_date':'01/11/2026'})[0],400)
        rid=self.create('investigation',title='تحقيق',incident_description='وصف',incident_date='25/10/2026',parties='أطراف')
        self.assertEqual(self.manager(f'requests/{rid}',{'status':'approved'})[0],403)
        self.assertEqual(self.admin(f'requests/{rid}',{'status':'approved'})[0],200)

    def test_notice_and_leave_settlement(self):
        self.assertEqual(self.employee('requests',{'type':'termination','last_work_day':'01/11/2026'})[0],400)
        rid=self.create('termination',last_work_day='31/12/2026',termination_type='resignation')
        self.assertEqual(self.request(rid)['details']['notice_end'],'2026-12-26')
        self.assertEqual(self.admin(f'requests/{rid}',{'status':'approved'})[0],200)
        self.assertEqual(self.employee('employees')[1][0]['status'],'فترة إشعار')
        self.assertEqual(self.admin('settlement/record',{'request_id':rid})[0],400)
        preview=self.employee('settlement/preview?last_day=31/12/2026')[1]
        self.assertEqual(preview['daily_rate'],round(4000/31,4));self.assertTrue(preview['estimate_only'])
        server.TODAY=lambda:dt.date(2026,12,31)
        code,result=self.admin('settlement/record',{'request_id':rid})
        self.assertEqual(code,200,result);self.assertGreater(result['amount'],0);self.assertFalse(result['money_transferred'])
        self.assertEqual(self.employee('leave/2')[1]['available'],0)
        self.assertEqual(self.admin('settlement/record',{'request_id':rid})[0],400)
        server.TODAY=lambda:dt.date(2027,1,31)
        self.assertEqual(self.employee('leave/2')[1]['available'],0)
        self.assertEqual(self.employee('requests',{'type':'advance','amount':100,'installment':50})[0],400)

    def test_comments_decision_attachments_and_notifications(self):
        rid=self.create('hr_contact')
        self.assertEqual(self.manager('notifications')[1],[])
        self.assertTrue(self.admin('notifications')[1]);self.assertTrue(self.employee('notifications')[1])
        self.assertEqual(self.admin(f'requests/{rid}/comments',{'message':'رد الموارد البشرية','attachments':[self.file()]})[0],200)
        self.assertEqual(self.request(rid)['comments'][0]['message'],'رد الموارد البشرية')
        self.assertEqual(self.admin(f'requests/{rid}',{'status':'approved','attachments':[self.file()]})[0],200)
        self.assertEqual(len(self.request(rid)['attachments']),2)
        self.assertEqual(self.manager(f'requests/{rid}/comments')[0],403)
        self.assertEqual(self.employee('notifications/read',{})[0],200)
        self.assertTrue(all(n['read_at'] for n in self.employee('notifications')[1]))

    def test_exports_filters_and_readable_xlsx(self):
        self.create()
        for typ in ['employees','contracts','leave','requests','salaries','attendance']:
            code,data=self.admin(f'reports?type={typ}&company_id=1&employee_id=2')
            self.assertEqual(code,200,(typ,data))
            self.assertTrue(all(r['employee_id']==2 for r in data))
        code,data=self.admin('reports?type=employees&format=xlsx',binary=True)
        self.assertEqual(code,200)
        with zipfile.ZipFile(io.BytesIO(data)) as z:ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
        import openpyxl
        book=openpyxl.load_workbook(io.BytesIO(data));self.assertEqual(book.active.max_row,4)
        self.assertEqual(self.employee('reports?type=employees')[0],403)
        self.assertEqual(self.manager('reports?company_id=999&type=employees')[1],[])

    def test_account_assignment_profile_and_archive_trace(self):
        self.assertEqual(self.new_employee(manager_id=3)[0],400)
        code,person=self.new_employee(login_role='manager',nationality='تجريبية',department='إدارة الاختبار')
        self.assertEqual(code,201,person)
        supervisors=self.admin('supervisors')[1];self.assertTrue(any(r['id']==person['id'] for r in supervisors))
        self.assertEqual(self.admin('employees/2',{'manager_id':person['id'],'passport':'TEST-001'})[0],200)
        self.assertEqual(self.employee('employees')[1][0]['passport'],'TEST-001')
        self.assertEqual(self.employee('employees/2',{'passport':'bad'})[0],403)
        self.assertEqual(self.admin('archive',{'target':'employee','id':3,'archived':True,'confirm':True,'password':'Admin@123'})[0],200)
        row=self.admin('archive/history')[1][0]
        self.assertEqual((row['username'],row['display_name']),('admin','admin'))
        self.assertEqual(self.admin('archive',{'target':'employee','id':3,'archived':False,'confirm':'yes','password':'Admin@123'})[0],400)

    def test_configurable_approval_sequence(self):
        self.assertEqual(self.admin('policy?company_id=1',{'salary_basis':'calendar','notice_days':60,'approval_mode':'supervisor_then_admin'})[0],200)
        rid=self.create('annual_leave',start_date='02/11/2026',end_date='02/11/2026')
        before=self.employee('leave/2')[1]['available']
        self.assertEqual(self.admin(f'requests/{rid}',{'status':'approved'})[0],400)
        self.assertEqual(self.manager(f'requests/{rid}',{'status':'approved'})[0],200)
        self.assertEqual(self.request(rid)['status'],'review')
        self.assertEqual(self.request(rid)['decisions'][0]['status'],'manager_approved')
        self.assertEqual(self.employee('leave/2')[1]['available'],before)
        self.assertEqual(self.admin(f'requests/{rid}',{'status':'approved'})[0],200)
        self.assertEqual(self.employee('leave/2')[1]['available'],before-1)
        self.assertEqual(self.admin('policy?company_id=1',{'salary_basis':'calendar','approval_mode':'admin_only'})[0],200)
        other=self.create()
        self.assertEqual(self.manager(f'requests/{other}',{'status':'approved'})[0],403)

    def test_employee_documents_and_overtime_review(self):
        code,files=self.admin('employee-files?employee_id=2',{'attachments':[self.file()]})
        self.assertEqual(code,200,files)
        self.assertEqual(self.employee('employee-files/'+str(files[0]['id']),binary=True)[0],200)
        self.assertEqual(self.employee('employee-files?employee_id=2',{'attachments':[self.file()]})[0],403)
        self.assertEqual(self.employee('employee-files?employee_id=3')[0],403)
        with server.conn() as c:c.execute("INSERT INTO attendance(employee_id,day,check_in,check_out,source) VALUES(2,'2026-10-26','09:00','18:00','demo')")
        self.assertEqual(self.admin('overtime?employee_id=2')[1],[])
        self.assertEqual(self.admin('overtime?employee_id=2',{'day':'26/10/2026','minutes':90,'status':'approved','note':'تجربة'})[0],400)
        self.assertEqual(self.admin('overtime?employee_id=2',{'day':'26/10/2026','minutes':60,'status':'approved','note':'اعتماد إضافي'})[0],200)
        report=self.admin('reports?type=attendance&employee_id=2&from=26/10/2026&to=26/10/2026')[1]
        self.assertEqual(report[0]['overtime_approved_minutes'],60)

    def test_hr_migration_backup_and_repeatability(self):
        rid=self.create()
        with server.conn() as c:
            for table in ['email_outbox','employee_files','overtime_reviews','notifications','request_comments','settlements','absences','contracts','schedules','work_sites','holidays']:c.execute('DROP TABLE '+table)
            c.execute("DELETE FROM schema_migrations WHERE version='hr-v2'")
        server.init()
        backup=server.DB.with_name(server.DB.name+'.before-hr-v2.bak')
        self.assertTrue(backup.exists())
        with sqlite3.connect(backup) as c:self.assertFalse(c.execute("SELECT 1 FROM sqlite_master WHERE name='contracts'").fetchone())
        self.assertEqual(self.request(rid)['notes'],'ملاحظة')
        self.assertIsNotNone(self.request(rid)['contract_id'])
        server.init()
        with server.conn() as c:self.assertEqual(c.execute('SELECT count(*) FROM contracts').fetchone()[0],3)

    def test_unpaid_leave_daily_salary_and_cancellation(self):
        rid=self.create('unpaid_leave',start_date='26/10/2026',end_date='26/10/2026')
        self.assertEqual(self.manager(f'requests/{rid}',{'status':'approved'})[0],200)
        row=self.admin('reports?type=salaries&employee_id=2&from=01/10/2026&to=31/10/2026')[1][0]
        self.assertEqual(row['earned_for_period'],4000)
        self.assertEqual(row['unpaid_leave_deductions'],129.03)
        self.assertEqual(row['net_estimate'],3870.97)
        self.assertEqual(self.admin(f'requests/{rid}',{'status':'cancelled'})[0],200)
        self.assertEqual(self.admin('reports?type=salaries&employee_id=2')[1][0]['unpaid_leave_deductions'],0)

    def test_email_queue_dry_run_and_mock_delivery(self):
        import notification_worker
        self.assertEqual(self.admin('employees/2',{'email':'demo@example.test'})[0],200)
        self.create()
        with server.conn() as c:
            with patch.dict(server.os.environ,{'RESEND_API_KEY':'','RESEND_FROM_EMAIL':''}):
                result=notification_worker.deliver(c,send=True)
                self.assertEqual(result['sent'],0);self.assertFalse(result['configured'])
            with patch.dict(server.os.environ,{'RESEND_API_KEY':'mock-test-only','RESEND_FROM_EMAIL':'demo@example.test'}),patch.object(notification_worker,'send_email') as send:
                result=notification_worker.deliver(c,send=True)
                self.assertEqual(result['sent'],1);send.assert_called_once()
                self.assertEqual(notification_worker.deliver(c,send=True)['sent'],0)

    def test_archiving_blocks_employee_login_and_existing_sessions(self):
        self.assertEqual(self.admin('archive',{'target':'employee','id':2,'archived':True,'confirm':True,'password':'Admin@123'})[0],200)
        self.assertEqual(self.employee('me')[0],403)
        self.assertEqual(self.client()('login',{'username':'employee','password':'Employee@123','account_role':'employee'})[0],403)
        self.assertEqual(self.admin('archive',{'target':'employee','id':2,'archived':False,'confirm':True,'password':'Admin@123'})[0],200)
        self.assertEqual(self.employee('me')[0],200)

    def test_public_health_identifies_release_without_private_data(self):
        import urllib.request
        with urllib.request.urlopen(self.base+'health') as response:result=json.load(response)
        self.assertEqual(result['status'],'ok')
        self.assertEqual(result['release'],'professional-v1')
        self.assertEqual(set(result),{'status','release','database','commit'})

    def test_otp_email_or_phone_with_mocked_providers(self):
        self.assertEqual(self.admin('employees/2',{'email':'demo@example.test','phone_country':'+966','phone_local':'501234567'})[0],200)
        captured=[]
        with patch.object(server,'resend_send_reset',side_effect=lambda dest,code:captured.append(code) or True):
            self.assertEqual(self.employee('password/request',{'username':'employee','channel':'email'})[0],200)
        result=self.employee('password/verify',{'username':'employee','channel':'email','code':captured[0]})
        self.assertEqual(result[0],200,result)
        self.assertEqual(self.employee('password/confirm',{'username':'employee','reset_token':result[1]['reset_token'],'new_password':'Changed@123456'})[0],200)
        other=self.client('employee','Changed@123456')
        with patch.object(server,'authevo_send',return_value=True),patch.object(server,'authevo_verify',return_value=True):
            self.assertEqual(other('password/request',{'username':'employee','channel':'whatsapp'})[0],200)
            self.assertEqual(other('password/verify',{'username':'employee','channel':'whatsapp','code':'123456'})[0],200)


if __name__=='__main__':unittest.main()

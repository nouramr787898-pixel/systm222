"""Deployed-branch regressions on disposable databases only."""
import base64
import datetime as dt
import json
import unittest
from unittest.mock import patch
import ai_assistant
import delivery_updates
import notification_worker
import server
import test_workflow as fixture


class DeliveryUpdatesTests(unittest.TestCase):
    setUp=fixture.WorkflowTests.setUp
    tearDown=fixture.WorkflowTests.tearDown
    client=fixture.WorkflowTests.client
    create=fixture.WorkflowTests.create

    def test_company_selection_requests_and_employee_followup(self):
        rid=self.create()
        with server.conn() as c:
            c.execute('UPDATE requests SET created_at=? WHERE id=?',(server.TODAY().isoformat()+'T10:00:00+03:00',rid))
            c.execute("UPDATE employees SET name='دكتور ياسر' WHERE id IN (2,3)")
            c.execute("INSERT INTO attendance(employee_id,day,check_in,check_out,source) VALUES(2,?,'20:19:00','20:19:01','gps_geofence')",(server.TODAY().isoformat(),))
        q='هل الموظف دكتور ياسر حضر اليوم؟'
        first=self.admin('assistant',{'question':q,'company_id':1})[1]
        self.assertEqual(len(first['choices']),2)
        history=[{'role':'user','content':q},{'role':'assistant','content':first['answer']}]
        out=self.admin('assistant',{'question':'EH-0002','company_id':1,'history':history})[1]
        self.assertIn('حاضر',out['answer']);self.assertIn('شركة الأفق',out['answer']);self.assertNotIn('الإرشادي المحلي',out['answer'])
        summary=self.admin('assistant',{'question':'لخص ليا الطلبات الواردة من الموظفين اليوم','company_id':1})[1]
        self.assertIn('1 طلب',summary['answer']);self.assertIn(f'EH-{rid:06d}',summary['answer'])
        with server.conn() as c:co=c.execute("INSERT INTO companies(name) VALUES('شركة ثانية')").lastrowid
        self.assertIn('0 طلب',self.admin('assistant',{'question':'لخص الطلبات الواردة اليوم','company_id':co})[1]['answer'])
        self.assertEqual(self.employee('assistant',{'question':'لخص الطلبات اليوم','company_id':co})[0],403)

    def test_second_and_overnight_duration(self):
        with server.conn() as c:
            e=c.execute('SELECT * FROM employees WHERE id=2').fetchone()
            row={'day':'2026-10-26','check_in':'20:19:00','check_out':'20:19:01','check_in_at':None,'check_out_at':None,'schedule_snapshot':None}
            self.assertEqual(server.workforce.attendance_metrics(c,e,row)['duration_seconds'],1)
            row.update(check_in_at='2026-10-26T20:00:00+03:00',check_out_at='2026-10-27T04:00:01+03:00')
            self.assertEqual(server.workforce.attendance_metrics(c,e,row)['duration_seconds'],28801)
            c.execute("INSERT INTO attendance(employee_id,day,check_in,check_out,source) VALUES(2,'2026-10-26','20:19:00','20:19:01','gps_geofence')")
        calendar=self.admin('attendance-calendar?employee_id=2&from=2026-10-26&to=2026-10-26')[1][0]
        self.assertEqual(calendar['status'],'present');self.assertEqual(calendar['duration_seconds'],1)

    def test_selected_sites_and_cross_company_rejection(self):
        a=self.admin('sites?company_id=1',{'name':'المكتب الأول','latitude':30.1390381,'longitude':31.2558794,'radius':100})[1][0]
        b=self.admin('sites?company_id=1',{'name':'المكتب الثاني','latitude':31,'longitude':32,'radius':100})[1][-1]
        self.assertEqual(self.admin('employees/2',{'allowed_site_ids':[a['id'],b['id']]})[0],200)
        self.assertEqual(len(self.employee('attendance-sites')[1]),2)
        gps={'latitude':30.1390381,'longitude':31.2558794,'accuracy':10}
        self.assertEqual(self.employee('attendance',gps)[0],400)
        self.assertEqual(self.employee('attendance',{**gps,'site_id':9999})[0],403)
        self.assertEqual(self.employee('attendance',{**gps,'site_id':b['id']})[0],400)
        self.assertEqual(self.employee('attendance',{**gps,'site_id':a['id']})[0],200)
        with server.conn() as c:
            self.assertEqual(c.execute('SELECT check_in_site FROM attendance WHERE employee_id=2').fetchone()[0],'المكتب الأول')
            c.execute("UPDATE attendance SET check_in_at='2020-01-01T00:00:00+00:00' WHERE employee_id=2")
        self.assertEqual(self.employee('attendance',{'site_id':b['id'],'latitude':31,'longitude':32,'accuracy':10})[0],200)
        self.assertEqual(self.admin('employees/2',{'allowed_site_ids':[9999]})[0],400)

    def test_public_application_stores_consent(self):
        code,out=self.admin('vacancies',{'company_id':1,'title':'اختبار الإقرار','closes_on':'2026-12-31','retention_until':'2027-01-31'})
        self.assertEqual(code,201,out)
        job=next(r for r in self.admin('vacancies')[1] if r['id']==out['id'])
        guest=self.client()
        data={'name':'متقدم اختبار','email':'consent@example.test','phone':'0501234567','note':'','consent':True,'cv':{'name':'cv.pdf','data':base64.b64encode(b'%PDF-1.4\n%%EOF').decode()}}
        self.assertEqual(guest('recruitment/'+job['public_token'],data)[0],201)
        with server.conn() as c:self.assertEqual(c.execute('SELECT consent_text FROM candidates WHERE email=?',(data['email'],)).fetchone()[0],delivery_updates.CONSENT)

    def test_notification_body_and_mock_channels(self):
        self.assertEqual(self.admin('employees/2',{'email':'demo@example.test','phone_country':'+966','phone_local':'0500000000'})[0],200)
        with patch.dict(server.os.environ,{'EHKAM_PUBLIC_URL':'https://systemehkam.online'}):rid=self.create()
        with server.conn() as c:
            body=c.execute('SELECT body FROM email_outbox WHERE request_id=?',(rid,)).fetchone()[0]
            self.assertIn('قيد المراجعة',body);self.assertIn('إحكام المسار',body)
            with patch.dict(server.os.environ,{'RESEND_API_KEY':'test','RESEND_FROM_EMAIL':'test@example.test'}),patch.object(notification_worker,'send_email'):
                self.assertEqual(notification_worker.deliver(c,True,fresh_only=True)['sent'],1)
                self.assertEqual(notification_worker.deliver(c,True,fresh_only=True)['sent'],0)
            with patch.dict(server.os.environ,{'WHATSAPP_ACCESS_TOKEN':'test','WHATSAPP_PHONE_NUMBER_ID':'123','WHATSAPP_TEMPLATE_NAME':'request_status'}),patch.object(notification_worker,'send_whatsapp'):
                self.assertEqual(notification_worker.deliver_whatsapp(c,True)['sent'],1)
                self.assertEqual(notification_worker.deliver_whatsapp(c,True)['sent'],0)


if __name__=='__main__':unittest.main()

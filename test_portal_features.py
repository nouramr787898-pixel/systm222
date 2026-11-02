"""Regression coverage for employee answers, selected sites, applications and delivery."""
import base64
import json
import unittest
from unittest.mock import patch
import server
import portal_features
import notification_worker
import test_workflow as fixture


class PortalFeatureTests(unittest.TestCase):
    setUp=fixture.WorkflowTests.setUp
    tearDown=fixture.WorkflowTests.tearDown
    client=fixture.WorkflowTests.client
    create=fixture.WorkflowTests.create

    def test_assistant_attendance_name_id_and_missing_punch(self):
        code,out=self.admin('assistant',{'question':'سارة خالد حضرت النهارده؟'})
        self.assertEqual(code,200);self.assertIn('لا توجد بصمة',out['answer'])
        self.employee('attendance',{'latitude':30.1390381,'longitude':31.2558794,'accuracy':10})
        code,out=self.admin('assistant',{'question':'الموظف EH-0002 حاضر النهارده؟'})
        self.assertEqual(code,200);self.assertIn('نعم، مسجل له حضور',out['answer']);self.assertIn('شركة الأفق',out['answer'])
        with server.conn() as c:c.execute("UPDATE attendance SET check_in='09:00:01',check_out='10:02:04' WHERE employee_id=2")
        out=self.admin('assistant',{'question':'عدد حضور سارة خالد الشهر ده'})[1]
        self.assertIn('01:02:03',out['answer']);self.assertIn('الغياب المعتمد',out['answer'])

    def test_assistant_scope_and_ambiguity(self):
        code,out=self.employee('assistant',{'question':'راتب أحمد محمد','employee_id':1})
        self.assertEqual(code,200);self.assertNotIn('5200',out['answer']);self.assertIn('لم أجد',out['answer'])
        with server.conn() as c:c.execute("UPDATE employees SET name='سارة أخرى' WHERE id=3")
        out=self.admin('assistant',{'question':'هل سارة حاضرة؟'})[1]
        self.assertEqual(len(out['choices']),2)
        out=self.admin('assistant',{'question':'هل سارة حاضرة؟','employee_id':2})[1]
        self.assertEqual(out['employee_id'],2)

    def test_selected_sites_authorization_and_both_punches(self):
        a=self.admin('sites?company_id=1',{'name':'المكتب الأول','latitude':30.1390381,'longitude':31.2558794,'radius':100})[1][0]
        b=self.admin('sites?company_id=1',{'name':'المكتب الثاني','latitude':31,'longitude':32,'radius':100})[1][-1]
        self.assertEqual(self.admin('employees/2',{'allowed_site_ids':[a['id'],b['id']]})[0],200)
        self.assertEqual(len(self.employee('attendance-sites')[1]),2)
        gps={'latitude':30.1390381,'longitude':31.2558794,'accuracy':10}
        self.assertEqual(self.employee('attendance',gps)[0],400)
        self.assertEqual(self.employee('attendance',{**gps,'site_id':99999})[0],403)
        self.assertEqual(self.employee('attendance',{**gps,'site_id':b['id']})[0],400)
        self.assertEqual(self.employee('attendance',{**gps,'site_id':a['id']})[0],200)
        self.assertEqual(self.employee('attendance',{'latitude':31,'longitude':32,'accuracy':10,'site_id':b['id']})[0],200)
        row=self.employee('attendance')[1][0]
        self.assertEqual(row['check_in_site'],'المكتب الأول');self.assertEqual(row['check_out_site'],'المكتب الثاني')
        self.assertEqual(self.admin('employees/2',{'allowed_site_ids':[99999]})[0],400)

    def test_public_application_consent_private_download_and_retention(self):
        self.assertEqual(self.employee('vacancies',{})[0],403)
        code,jobs=self.admin('vacancies',{'title':'محلل بيانات','description':'وصف اختباري','closes_on':'2027-01-01','retain_until':'2027-02-01'})
        self.assertEqual(code,200);job=jobs[0];guest=self.client()
        endpoint='apply/'+job['token']
        self.assertEqual(guest(endpoint)[1]['title'],'محلل بيانات')
        body={'name':'متقدم تجريبي','email':'test@example.test','phone':'0500000000','notes':'','consent':False,'cv':{'name':'cv.pdf','data':base64.b64encode(b'%PDF-1.4\n%%EOF').decode()}}
        self.assertEqual(guest(endpoint,body)[0],400)
        body['consent']=True;self.assertEqual(guest(endpoint,body)[0],201)
        rows=self.admin(f"vacancies/{job['id']}/applications")[1];self.assertEqual(len(rows),1)
        self.assertIn('إحكام المسار',rows[0]['consent_text'])
        aid=rows[0]['id'];self.assertEqual(self.employee(f'applications/{aid}/cv')[0],403)
        self.assertTrue(self.admin(f'applications/{aid}/cv',binary=True)[1].startswith(b'%PDF-'))
        with patch.object(server,'TODAY',return_value=server.datetime.date(2027,2,2)):
            self.assertEqual(self.admin(f"vacancies/{job['id']}/applications")[1],[])
            self.assertEqual(guest(endpoint)[0],410)

    def test_notification_body_state_and_mock_delivery(self):
        self.admin('employees/2',{'email':'demo@example.test','phone_country':'+966','phone_local':'500000000'})
        with patch.dict(server.os.environ,{'EHKAM_PUBLIC_URL':'https://systemehkam.online'}):rid=self.create()
        with server.conn() as c:
            body=c.execute('SELECT body FROM email_outbox WHERE request_id=?',(rid,)).fetchone()[0]
            self.assertIn('قيد المراجعة',body);self.assertIn(f'/#request-{rid}',body);self.assertIn('EH-0002',body)
            with patch.dict(server.os.environ,{'RESEND_API_KEY':'test','RESEND_FROM_EMAIL':'test@example.test'}),patch.object(notification_worker,'send_email') as send:
                self.assertEqual(notification_worker.deliver(c,True)['sent'],1)
                self.assertIn('إحكام المسار',send.call_args.args[2]);self.assertEqual(notification_worker.deliver(c,True)['sent'],0)
            with patch.dict(server.os.environ,{'WHATSAPP_ACCESS_TOKEN':'test','WHATSAPP_PHONE_NUMBER_ID':'123','WHATSAPP_TEMPLATE_NAME':'request_status','EHKAM_PUBLIC_URL':'https://systemehkam.online'}),patch.object(notification_worker,'send_whatsapp') as send:
                self.assertEqual(notification_worker.deliver_whatsapp(c,True)['sent'],1)
                self.assertEqual(send.call_args.args[1][3],'قيد المراجعة');self.assertEqual(notification_worker.deliver_whatsapp(c,True)['sent'],0)
        with patch.dict(server.os.environ,{'EHKAM_PUBLIC_URL':'https://systemehkam.online'}):self.manager(f'requests/{rid}',{'status':'rejected','note':'اختبار الرفض'})
        with server.conn() as c:
            self.assertIn('مرفوض',c.execute('SELECT body FROM email_outbox ORDER BY id DESC LIMIT 1').fetchone()[0])
            with patch.dict(server.os.environ,{'WHATSAPP_ACCESS_TOKEN':'test','WHATSAPP_PHONE_NUMBER_ID':'123','WHATSAPP_TEMPLATE_NAME':'request_status'}),patch.object(notification_worker,'send_whatsapp',side_effect=TimeoutError):
                self.assertEqual(notification_worker.deliver_whatsapp(c,True)['sent'],0)
                self.assertEqual(c.execute('SELECT status FROM whatsapp_outbox ORDER BY id DESC LIMIT 1').fetchone()[0],'needs_review')


if __name__=='__main__':unittest.main()

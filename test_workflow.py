"""Run: python3 -m unittest -v test_workflow (isolated temporary SQLite databases)."""
import base64
import concurrent.futures
import http.cookiejar
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

import server
import demo


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db, self.old_today = server.DB, server.TODAY
        server.DB = Path(self.temp.name) / 'test.sqlite3'
        server.TODAY = lambda: server.datetime.date(2026, 10, 27)
        server.init(seeder=demo.seed_fixture)
        server.SESSIONS.clear()
        self.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.http.server_port}/api/'
        self.admin = self.client('admin', 'Admin@123')
        self.employee = self.client('employee', 'Employee@123')
        self.manager = self.client('manager', 'Manager@123')

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        server.DB, server.TODAY = self.old_db, self.old_today
        server.SESSIONS.clear()
        self.temp.cleanup()

    def client(self, name=None, password=None, role=None):
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        def call(path, body=None, binary=False):
            req = urllib.request.Request(self.base + path,
                json.dumps(body).encode() if body is not None else None,
                {'Content-Type': 'application/json'} if body is not None else {})
            try:
                with opener.open(req, timeout=15) as response:
                    data = response.read()
                    return response.status, data if binary else json.loads(data)
            except urllib.error.HTTPError as error:
                return error.code, json.loads(error.read())
        if name:
            self.assertEqual(call('login', {'username': name, 'password': password, 'account_role': role or name})[0], 200)
        return call

    def create(self, typ='open', **extra):
        data = {'employee_id': 2, 'type': typ, 'notes': 'ملاحظة', **extra}
        status, result = self.employee('requests', data)
        self.assertEqual(status, 201, result)
        return result['id']

    def request(self, rid, client=None):
        return next(r for r in (client or self.employee)('requests')[1] if r['id'] == rid)

    def file(self, name='وثيقة.pdf', mime='application/pdf', data=b'%PDF-1.4\n%%EOF'):
        return {'name': name, 'mime': mime, 'data': base64.b64encode(data).decode()}

    def new_employee(self, index=1, **extra):
        return self.admin('employees', {'company_id': 1, 'name': f'موظف تجريبي {index}',
            'login_username': f'test{index}', 'login_password': 'Testing@12345', **extra})

    def test_employee_name_login_and_duplicate_rejection(self):
        self.assertEqual(self.new_employee()[0],201)
        self.client('موظف تجريبي 1','Testing@12345','employee')
        self.assertEqual(self.new_employee()[0],400)
        self.assertEqual(len(self.admin('employees')[1]),4)

    def test_company_without_location_does_not_allow_unverified_attendance(self):
        status,_=self.admin('companies',{'name':'بلا موقع','address':'عنوان'})
        self.assertEqual(status,201)
        with server.conn() as c:
            c.execute('UPDATE companies SET latitude=NULL,longitude=NULL WHERE id=1')
        self.assertEqual(self.employee('attendance',{'latitude':30,'longitude':31,'accuracy':10})[0],400)

    def test_actor_snapshot_and_notification_details(self):
        rid=self.create('annual_leave',start_date='02/11/2026',end_date='03/11/2026')
        self.assertEqual(self.manager(f'requests/{rid}',{'status':'rejected','note':'رفض موثق'})[0],200)
        notification=self.employee('notifications')[1][0]
        self.assertIn('سارة خالد',notification['message'])
        self.assertIn('مشرف',notification['message'])
        self.assertIn('إجازة سنوية',notification['message'])
        with server.conn() as c:c.execute("UPDATE users SET display_name='اسم جديد',role='employee' WHERE username='manager'")
        row=next(x for x in self.admin('audit')[1] if x['action']=='status_rejected')
        self.assertEqual(row['actor_role'],'manager')
        self.assertEqual(row['subject_name'],'سارة خالد')
        self.assertIn('رفض موثق',row['info'])
        self.assertNotEqual(row['actor_name'],'اسم جديد')

    def test_monthly_and_total_leave_preview(self):
        body={'employee_id':2,'start_date':'02/11/2026','end_date':'03/11/2026'}
        code,before=self.employee('leave/preview',body)
        self.assertEqual(code,200)
        self.assertEqual(before['monthly_available'],round(21/12,4))
        self.assertEqual(before['monthly_after_approval'],before['monthly_available'])  # Existing accumulated leave covers these two days.
        self.assertEqual(before['after_approval'],before['available']-2)
        rid=self.create('annual_leave',start_date='26/10/2026',end_date='26/10/2026')
        self.assertEqual(self.admin(f'requests/{rid}',{'status':'approved'})[0],200)
        self.assertEqual(self.employee('leave/preview',body)[1]['monthly_available'],round(21/12,4))

    def test_hr_contact_notes_without_title(self):
        rid=self.create('hr_contact',notes='تفاصيل تواصل بدون عنوان')
        request=self.request(rid)
        self.assertEqual(request['details']['title'],'')
        self.assertEqual(request['notes'],'تفاصيل تواصل بدون عنوان')

    def test_numbering_and_concurrent_creation(self):
        self.assertEqual(sorted(e['employee_no'] for e in self.admin('employees')[1]), ['EH-0001','EH-0002','EH-0003'])
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda i: self.new_employee(i, employee_no='EH-0001'), range(4)))
        self.assertTrue(all(status == 201 for status, _ in results), results)
        self.assertEqual(sorted(r['employee_no'] for _, r in results), ['EH-0004','EH-0005','EH-0006','EH-0007'])
        self.assertEqual(self.admin('employees/2', {'employee_no':'EH-9999'})[0],400)
        with server.conn() as c:
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("UPDATE employees SET employee_no='EH-0001' WHERE id=2")

    def test_migration_preserves_old_data_and_is_repeatable(self):
        with server.conn() as c:
            c.execute('DROP INDEX employees_number_global')
            for table in ['leave_accrual_cutover','email_outbox','employee_files','overtime_reviews','notifications','request_comments','settlements','absences','contracts','schedules','work_sites','holidays','employee_number_history','employee_number_sequence','request_attachments','request_decisions','schema_migrations']:
                c.execute('DROP TABLE '+table)
            c.execute("UPDATE employees SET employee_no='OLD-' || id")
            c.execute("UPDATE employees SET employee_no=CASE id WHEN 1 THEN 'EH-0002' WHEN 2 THEN 'A-002' ELSE 'EH-0001' END")
            c.execute("INSERT INTO companies(id,name) VALUES(2,'شركة ثانية')")
            c.execute("INSERT INTO employees(company_id,name,employee_no) VALUES(2,'مكرر','EH-0002')")
            c.execute("ALTER TABLE requests ADD COLUMN reason TEXT DEFAULT ''")
            c.execute("INSERT INTO requests(company_id,employee_id,type,reason,notes,created_by,created_at,updated_at) VALUES(1,2,'open','سبب قديم','ملاحظة قديمة',1,'2026-10-01','2026-10-01')")
            c.execute("INSERT INTO audit(user_id,username,action,entity,entity_id,info,timestamp) VALUES(1,'admin','status_rejected','request',1,'رفض سابق','2026-10-02')")
        server.init()
        with server.conn() as c:
            numbers = [r[0] for r in c.execute('SELECT employee_no FROM employees ORDER BY id')]
            self.assertEqual(numbers,['EH-0002','EH-0003','EH-0001','EH-0004'])
            self.assertEqual(c.execute('SELECT notes FROM requests').fetchone()[0],'ملاحظة قديمة\nسبب قديم')
            self.assertNotIn('reason', [row[1] for row in c.execute('PRAGMA table_info(requests)')])
            self.assertEqual(c.execute('SELECT old_number FROM employee_number_history WHERE employee_id=2').fetchone()[0],'A-002')
            self.assertEqual(c.execute('SELECT username,status,note FROM request_decisions').fetchone()[:],('admin','rejected','رفض سابق'))
        backup = server.DB.with_name(server.DB.name+'.before-workflow-v1.bak')
        with sqlite3.connect(backup) as c:
            self.assertEqual(c.execute('SELECT employee_no FROM employees WHERE id=2').fetchone()[0],'A-002')
        server.init()
        with server.conn() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM employee_number_history').fetchone()[0],4)
            self.assertEqual(c.execute('SELECT count(*) FROM request_decisions').fetchone()[0],1)
        self.assertEqual(self.new_employee()[1]['employee_no'],'EH-0005')

    def test_attachments_for_every_request_type(self):
        files=[self.file(), self.file('صورة.PNG','image/png',(server.BASE/'favicon.png').read_bytes()), self.file('photo.jpg','image/jpeg',b'\xff\xd8\xff\xe0test\xff\xd9')]
        for typ in ['annual_leave','sick_leave','unpaid_leave','advance','permission','punch_fix','hr_contact','investigation','termination','open','delegation']:
            data={'employee_id':2,'type':typ,'notes':'تفاصيل','start_date':'02/11/2026','end_date':'03/11/2026','amount':100,'installment':50,'attachments':files,'time_from':'10:00','time_to':'11:00','incident_description':'واقعة اختبار','incident_date':'01/10/2026','last_work_day':'31/12/2026'}
            if typ=='punch_fix':data.update(start_date='26/10/2026',end_date='26/10/2026')
            client=self.manager if typ=='delegation' else self.employee
            status,result=client('requests',data)
            self.assertEqual(status,201,(typ,result))
            r=self.request(result['id'])
            self.assertEqual(len(r['attachments']),3)
            for attachment, original in zip(r['attachments'], files):
                self.assertEqual(self.employee('attachments/'+str(attachment['id']),binary=True),(200,base64.b64decode(original['data'])))
            self.assertNotIn('reason',r)
            if typ!='advance':self.assertEqual((r['amount'],r['installment']),(0,0))

    def test_attachment_validation_and_atomicity(self):
        bad=[self.file('x.html','text/html',b'<html>'),self.file(data=b'fake PDF'),self.file('../x.pdf'),self.file('x\t.pdf'),{'name':'x.pdf','mime':'application/pdf','data':'!'},self.file(data=b'%PDF-'+b'a'*(2*1024*1024)),self.file('x.jpg','application/pdf')]
        for attachment in bad:
            self.assertEqual(self.employee('requests',{'type':'open','attachments':[attachment]})[0],400)
        self.assertEqual(self.employee('requests',{'type':'open','attachments':[self.file()]*4})[0],400)
        self.assertEqual(self.employee('requests',{'type':'open','attachments':{}})[0],400)
        self.assertEqual(self.employee('requests')[1],[])

    def test_hr_privacy_and_download_permissions(self):
        rid=self.create('hr_contact',attachments=[self.file()])
        aid=self.request(rid)['attachments'][0]['id']
        self.assertFalse(any(r['id']==rid for r in self.manager('requests')[1]))
        self.assertEqual(self.manager('attachments/'+str(aid),binary=True)[0],403)
        self.assertEqual(self.manager('requests/'+str(rid),{'status':'approved'})[0],403)
        self.assertEqual(self.admin('attachments/'+str(aid),binary=True)[0],200)
        self.assertEqual(self.client()('attachments/'+str(aid),binary=True)[0],401)
        self.assertEqual(self.admin('requests/'+str(rid),{'status':'approved'})[0],200)
        self.assertEqual(self.manager('requests',{'type':'hr_contact','employee_id':2})[0],403)
        self.assertEqual(self.manager('requests',{'type':'hr_contact','employee_id':1,'notes':'مراسلة'})[0],201)

    def test_decisions_rejection_and_audit(self):
        rid=self.create()
        self.assertEqual(self.employee(f'requests/{rid}',{'status':'approved'})[0],403)
        self.assertEqual(self.manager(f'requests/{rid}',{'status':'rejected','note':' '})[0],400)
        self.assertEqual(self.manager(f'requests/{rid}',{'status':'review','note':'مراجعة'})[0],200)
        self.assertEqual(self.manager(f'requests/{rid}',{'status':'rejected','note':'مستند ناقص'})[0],200)
        r=self.request(rid)
        self.assertEqual([d['status'] for d in r['decisions']],['review','rejected'])
        self.assertEqual(r['decisions'][-1]['username'],'manager')
        self.assertEqual(r['decisions'][-1]['note'],'مستند ناقص')
        self.assertTrue(r['decisions'][-1]['decided_at'].endswith('+00:00'))
        self.assertEqual(self.admin(f'requests/{rid}',{'status':'approved'})[0],400)
        self.assertTrue(any(a['action']=='status_rejected' and a['username']=='manager' for a in self.admin('audit')[1]))

    def test_leave_approval_cancellation_and_concurrent_decision(self):
        rid=self.create('annual_leave',start_date='02/11/2026',end_date='04/11/2026')
        before=self.employee('leave/2')[1]['available']
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            codes=list(pool.map(lambda _:self.manager(f'requests/{rid}',{'status':'approved'})[0],range(2)))
        self.assertEqual(sorted(codes),[200,400])
        self.assertEqual(self.employee('leave/2')[1]['available'],before-3)
        self.assertEqual(self.admin(f'requests/{rid}',{'status':'cancelled','note':'إلغاء'})[0],200)
        self.assertEqual(self.employee('leave/2')[1]['available'],before)
        self.assertEqual(len(self.request(rid)['decisions']),2)

    def test_delegation_and_role_boundaries(self):
        self.assertEqual(self.employee('requests',{'type':'delegation'})[0],403)
        self.assertEqual(self.manager('requests',{'type':'open','employee_id':2})[0],403)
        status,result=self.manager('requests',{'type':'delegation','employee_id':2,'start_date':'02/11/2026','end_date':'04/11/2026'})
        self.assertEqual(status,201,result)
        rid=result['id']
        self.assertEqual(self.admin(f'requests/{rid}',{'status':'approved'})[0],400)
        self.assertEqual(self.employee(f'requests/{rid}',{'status':'declined'})[0],400)
        self.assertEqual(self.employee(f'requests/{rid}',{'status':'declined','note':'تعذر السفر'})[0],200)
        self.assertEqual(self.request(rid)['decisions'][0]['username'],'employee')

    def test_other_employee_and_company_isolation(self):
        status,new=self.new_employee(manager_id=1)
        self.assertEqual(status,201)
        other=self.client('موظف تجريبي 1','Testing@12345','employee')
        rid=self.create(attachments=[self.file()])
        aid=self.request(rid)['attachments'][0]['id']
        self.assertEqual(other('attachments/'+str(aid),binary=True)[0],403)
        self.assertEqual(other(f'requests/{rid}',{'status':'rejected','note':'x'})[0],403)
        with server.conn() as c:
            c.execute("INSERT INTO companies(id,name) VALUES(2,'أخرى')")
            c.execute('UPDATE employees SET company_id=2 WHERE id=?',(new['id'],))
        self.assertEqual(self.manager('leave/'+str(new['id']))[0],403)

    def test_dates_profile_and_workweek(self):
        self.assertEqual(self.employee('employees/2',{'name':'تغيير'})[0],403)
        self.assertEqual(self.admin('employees/2',{'contract_start':'01/01/2026','contract_end':'31/12/2027','workweek':'6,0,2'})[0],200)
        e=self.employee('employees')[1][0]
        self.assertEqual((e['contract_end'],e['workweek']),('2027-12-31','0,2,6'))
        for date in ['31/02/2026','12/31/2026','broken']:
            self.assertEqual(self.employee('requests',{'type':'open','start_date':date})[0],400)
        self.assertEqual(self.admin('employees/2',{'workweek':'7'})[0],400)
        self.assertEqual(self.admin('employees/2',{'contract_end':'01/01/2025'})[0],400)
        self.assertEqual(self.employee('attendance?from=bad')[0],400)
        self.assertEqual(server.normalize_date('٠٢/١١/٢٠٢٦'),'2026-11-02')
        self.assertEqual(self.employee('requests',{'type':'open','start_date':'03/11/2026','end_date':'02/11/2026'})[0],400)

    def test_advance_validation(self):
        for amount,inst in [(9000,1000),(-1,100),(100,9999),(float('nan'),100),(100,float('inf')),('bad',10)]:
            self.assertEqual(self.employee('requests',{'type':'advance','amount':amount,'installment':inst})[0],400)
        rid=self.create('advance',amount=100,installment=50)
        self.assertEqual(self.request(rid)['amount'],100)
        self.assertGreater(self.employee('advance/preview')[1]['max_advance'],0)

    def test_geofence_on_both_checkin_and_checkout(self):
        good={'latitude':24.7136,'longitude':46.6753,'accuracy':10}
        bad=[{**good,'latitude':0},{**good,'accuracy':101},{**good,'accuracy':float('nan')},{**good,'longitude':float('nan')}]
        for point in bad:self.assertEqual(self.employee('attendance',point)[0],400)
        self.assertEqual(self.manager('attendance',good)[0],403)
        self.assertEqual(self.employee('attendance',good)[1]['action'],'check_in')
        for point in bad:self.assertEqual(self.employee('attendance',point)[0],400)
        self.assertIsNone(self.employee('attendance')[1][0]['check_out'])
        with server.conn() as c:c.execute("UPDATE attendance SET check_in_at=?",((server.datetime.datetime.now(server.datetime.timezone.utc)-server.datetime.timedelta(minutes=2)).isoformat(),))
        self.assertEqual(self.employee('attendance',good)[1]['action'],'check_out')
        self.assertEqual(self.employee('attendance',good)[0],400)
        self.assertEqual(self.employee('attendance')[1][0]['source'],'gps_geofence')


if __name__ == '__main__':
    unittest.main()

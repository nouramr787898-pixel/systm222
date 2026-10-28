"""Request image delivery checks; all data is created in temporary fixtures."""
import base64
import http.cookiejar
import json
import urllib.request
import unittest
import server
import test_workflow as fixture

class AttachmentPreviewTests(unittest.TestCase):
    setUp=fixture.WorkflowTests.setUp
    tearDown=fixture.WorkflowTests.tearDown
    client=fixture.WorkflowTests.client
    create=fixture.WorkflowTests.create
    request=fixture.WorkflowTests.request
    file=fixture.WorkflowTests.file
    new_employee=fixture.WorkflowTests.new_employee

    def headers(self,username,password,role,route):
        opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        request=urllib.request.Request(self.base+'login',json.dumps({'username':username,'password':password,'account_role':role}).encode(),{'Content-Type':'application/json'})
        with opener.open(request):pass
        with opener.open(self.base+route) as response:return response.headers,response.read()

    def test_inline_images_original_bytes_and_pdf_stays_download(self):
        raw=(server.BASE/'ehkam-icon.png').read_bytes()
        rid=self.create(attachments=[self.file('صورة.png','image/png',raw),self.file()])
        image,pdf=self.request(rid)['attachments']
        for username,password,role in [('employee','Employee@123','employee'),('admin','Admin@123','admin'),('manager','Manager@123','manager')]:
            with self.subTest(role=role):
                headers,body=self.headers(username,password,role,'attachments/'+str(image['id'])+'?preview=1')
                self.assertEqual(body,raw);self.assertEqual(headers['Content-Type'],'image/png')
                self.assertTrue(headers['Content-Disposition'].startswith('inline;'))
                self.assertEqual(headers['Cache-Control'],'no-store')
                self.assertEqual(headers['X-Content-Type-Options'],'nosniff')
        headers,_=self.headers('employee','Employee@123','employee','attachments/'+str(image['id']))
        self.assertTrue(headers['Content-Disposition'].startswith('attachment;'))
        headers,_=self.headers('employee','Employee@123','employee','attachments/'+str(pdf['id'])+'?preview=1')
        self.assertTrue(headers['Content-Disposition'].startswith('attachment;'))

    def test_preview_does_not_bypass_private_requests_or_authentication(self):
        for typ in ('sick_leave','advance','hr_contact','investigation','termination'):
            extra={'start_date':'2026-11-02','end_date':'2026-11-02','amount':100,'installment':50,'incident_description':'private','incident_date':'2026-10-20','last_work_day':'2026-12-31'}
            rid=self.create(typ,attachments=[self.file('test.png','image/png',(server.BASE/'favicon.png').read_bytes())],**extra)
            aid=self.request(rid)['attachments'][0]['id']
            self.assertEqual(self.manager('attachments/'+str(aid)+'?preview=1',binary=True)[0],403)
            self.assertEqual(self.client()('attachments/'+str(aid)+'?preview=1',binary=True)[0],401)
            self.assertEqual(self.employee('attachments/'+str(aid)+'?preview=1',binary=True)[0],200)

    def test_other_employee_and_restricted_admin_profiles(self):
        rid=self.create(attachments=[self.file('test.png','image/png',(server.BASE/'favicon.png').read_bytes())])
        normal=self.request(rid)['attachments'][0]['id']
        self.assertEqual(self.new_employee(manager_id=1)[0],201)
        other=self.client('موظف تجريبي 1','Testing@12345','employee')
        self.assertEqual(other('attachments/'+str(normal)+'?preview=1',binary=True)[0],403)
        medical=self.create('sick_leave',start_date='2026-11-02',end_date='2026-11-02',attachments=[self.file()])
        private=self.request(medical)['attachments'][0]['id']
        with server.conn() as c:
            uid=c.execute("INSERT INTO users(username,password_hash,role) VALUES('attachment_auditor',?,'admin')",(server.hashpw('AuditPreview!2026'),)).lastrowid
            c.execute("INSERT INTO admin_profiles(user_id,profile,company_id) VALUES(?,'auditor',1)",(uid,))
        auditor=self.client('attachment_auditor','AuditPreview!2026','admin')
        self.assertEqual(auditor('attachments/'+str(normal)+'?preview=1',binary=True)[0],200)
        self.assertEqual(auditor('attachments/'+str(private)+'?preview=1',binary=True)[0],403)

if __name__=='__main__':unittest.main()

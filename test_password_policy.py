"""Password policy across real API entry points, with isolated data and mocked OTP."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import server
import security
import review_demo
import test_workflow as fixture


class PasswordPolicyTests(unittest.TestCase):
    setUp=fixture.WorkflowTests.setUp
    tearDown=fixture.WorkflowTests.tearDown
    client=fixture.WorkflowTests.client

    def test_eight_character_letters_arabic_and_digits_are_allowed(self):
        for value in ['abcdefgh','abcd1234','abcdefghijk','ابتثجحخد','12345678']:
            self.assertTrue(security.valid_password(value))
        for value in ['abcdefg','x'*129,None]:self.assertFalse(security.valid_password(value))
        self.assertEqual(self.admin('security/password',{'current_password':'Admin@123','new_password':'abcdefg'})[0],400)
        self.assertEqual(self.admin('security/password',{'current_password':'Admin@123','new_password':'abcdefgh'})[0],200)
        self.client('admin','abcdefgh','admin')
        self.assertEqual(self.manager('security/password',{'current_password':'Manager@123','new_password':'abcdefgh'})[0],200)
        self.client('manager','abcdefgh','manager')

    def test_admin_supervisor_creation_and_employee_reset_allow_eight(self):
        body={'password':'Admin@123','username':'review_hr','display_name':'تجربة','new_password':'abcdefgh','profile':'hr'}
        self.assertEqual(self.admin('admin/accounts',body)[0],201)
        self.client('review_hr','abcdefgh','admin')
        body={'company_id':1,'employee_id':3,'username':'supervisor_eight','new_password':'abcd1234','password':'Admin@123'}
        self.assertEqual(self.admin('supervisor-accounts',body)[0],201)
        self.client('supervisor_eight','abcd1234','manager')
        uid=self.admin('security/employee-passwords')[1][0]['user_id']
        self.assertEqual(self.admin('security/employee-passwords',{'user_id':uid,'current_password':'Admin@123','new_password':'abcdefgh'})[0],200)

    def test_employee_creation_and_recovery_use_same_minimum(self):
        body={'company_id':1,'name':'اختبار الثمانية','username':'eight_employee','login_password':'abcdefgh'}
        self.assertEqual(self.admin('employees',body)[0],201)
        self.client('eight_employee','abcdefgh','employee')
        self.assertEqual(self.admin('employees/2',{'email':'review@example.test'})[0],200)
        codes=[]
        with patch.object(server,'resend_send_reset',side_effect=lambda dest,code:codes.append(code) or True):
            self.assertEqual(self.employee('password/request',{'username':'employee','channel':'email'})[0],200)
        result=self.employee('password/verify',{'username':'employee','channel':'email','code':codes[0]})
        self.assertEqual(result[0],200)
        body={'username':'employee','reset_token':result[1]['reset_token'],'new_password':'abcdefg'}
        self.assertEqual(self.employee('password/confirm',body)[0],400)
        body['new_password']='abcdefgh'
        self.assertEqual(self.employee('password/confirm',body)[0],200)
        self.client('employee','abcdefgh','employee')

    def test_review_accounts_use_arabic_names_and_never_overwrite_data(self):
        original=server.DB
        with tempfile.TemporaryDirectory() as temp:
            accounts={r:{'username':'تجربة '+r,'password':'abcdefgh'} for r in ('admin','manager','employee')}
            try:
                database=review_demo.prepare(temp,accounts)
                with server.conn() as c:
                    self.assertEqual(c.execute('SELECT count(*) FROM users WHERE disabled=0').fetchone()[0],3)
                    self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok')
                    self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
                for role,data in accounts.items():self.client(data['username'],data['password'],role)
                before=Path(database).read_bytes()
                with self.assertRaises(ValueError):review_demo.prepare(temp,accounts)
                self.assertEqual(Path(database).read_bytes(),before)
            finally:server.DB=original


if __name__=='__main__':unittest.main()

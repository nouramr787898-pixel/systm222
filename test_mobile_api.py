"""Native sessions share existing permission, revocation and MFA rules."""
import json
import time
import urllib.request
import urllib.error
import unittest
import server
import test_workflow as fixture


class MobileApiTests(unittest.TestCase):
    setUp=fixture.WorkflowTests.setUp
    tearDown=fixture.WorkflowTests.tearDown
    client=fixture.WorkflowTests.client

    def call(self,path,body=None,token=None,headers=None):
        request=urllib.request.Request(self.base+path,None if body is None else json.dumps(body).encode(),{'Content-Type':'application/json',**({'Authorization':'Bearer '+token} if token is not None else {}),**(headers or {})})
        try:
            with urllib.request.urlopen(request,timeout=15) as r:return r.status,json.load(r),r.headers
        except urllib.error.HTTPError as r:return r.code,json.load(r),r.headers

    def mobile(self,username='employee',password='Employee@123',role='employee',**extra):
        code,out,headers=self.call('mobile/login',{'username':username,'password':password,'account_role':role,**extra})
        self.assertEqual(code,200,out);self.assertNotIn('Set-Cookie',headers);return out['session_token']

    def test_native_and_browser_sessions_preserve_separate_login_contracts(self):
        token=self.mobile()
        code,out,_=self.call('me',token=token);self.assertEqual(code,200);self.assertEqual(out['role'],'employee')
        self.assertEqual(self.call('me')[0],401)
        code,rows,_=self.call('employees',token=token);self.assertEqual([r['id'] for r in rows],[2])
        self.assertEqual(self.call('attendance?employee_id=1',token=token)[0],403)
        web=self.call('login',{'username':'employee','password':'Employee@123','account_role':'employee'})
        self.assertEqual(web[0],200);self.assertNotIn('session_token',web[1]);self.assertIn('HttpOnly',web[2]['Set-Cookie'])
        with server.conn() as c:self.assertIsNone(c.execute('SELECT 1 FROM auth_sessions WHERE token_hash=?',(token,)).fetchone())

    def test_logout_revokes_native_session_without_logging_out_other_device(self):
        token=self.mobile();other=self.mobile()
        self.assertEqual(self.call('logout',{},token)[0],200)
        self.assertEqual(self.call('me',token=token)[0],401)
        self.assertEqual(self.call('me',token=other)[0],200)

    def test_idle_expiry_and_malformed_bearer_do_not_fall_back_to_browser_cookie(self):
        token=self.mobile()
        with server.conn() as c:c.execute('UPDATE auth_sessions SET last_seen=? WHERE token_hash=?',(int(time.time())-1900,server.security.digest(token)))
        self.assertEqual(self.call('me',token=token)[0],401)
        web=self.call('login',{'username':'employee','password':'Employee@123','account_role':'employee'})
        cookie=web[2]['Set-Cookie'].split(';')[0]
        for header in ['Bearer wrong','Basic wrong','Bearer '+('x'*64),'Bearer '+(' '*64)]:
            self.assertEqual(self.call('me',headers={'Cookie':cookie,'Authorization':header})[0],401)
        self.assertEqual(self.call('me',headers={'Cookie':cookie})[0],200)

    def test_native_login_keeps_mfa_and_origin_checks(self):
        secret='JBSWY3DPEHPK3PXP'
        with server.conn() as c:c.execute('INSERT INTO user_mfa(user_id,secret,enabled,last_step) VALUES(1,?,1,0)',(secret,))
        body={'username':'admin','password':'Admin@123','account_role':'admin'}
        self.assertEqual(self.call('mobile/login',body)[0],401)
        otp=server.security.totp(secret,int(time.time())//30)
        self.assertEqual(self.call('mobile/login',{**body,'otp':otp})[0],200)
        self.assertEqual(self.call('mobile/login',{'username':'employee','password':'Employee@123','account_role':'employee'},headers={'Origin':'https://outside.invalid'})[0],403)


if __name__=='__main__':unittest.main()

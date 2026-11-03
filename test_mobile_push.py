"""All delivery tests inject a fake provider; never contact Firebase or real phones."""
import time
import unittest
from unittest.mock import patch
import mobile_push
import server
import test_mobile_api as fixture


class PushTests(unittest.TestCase):
    setUp=fixture.MobileApiTests.setUp
    tearDown=fixture.MobileApiTests.tearDown
    client=fixture.MobileApiTests.client
    call=fixture.MobileApiTests.call
    mobile=fixture.MobileApiTests.mobile
    token='fake-device-token-with-no-external-recipient'

    def register(self,session,**extra):
        code,data,_=self.call('mobile/devices',{'token':self.token,'platform':'android',**extra},session)
        self.assertEqual(code,200,data)

    def event(self,c):
        uid=c.execute("SELECT id FROM users WHERE username='employee'").fetchone()[0]
        c.execute('INSERT INTO notifications(user_id,message,created_at) VALUES(?,?,?)',(uid,'حدث اختبار',server.now()));c.commit()

    def test_request_creation_decisions_comments_and_delivery_once(self):
        self.register(self.mobile())
        code,result=self.employee('requests',{'employee_id':2,'type':'open','notes':'اختبار'})
        self.assertEqual(code,201,result);rid=result['id']
        self.assertEqual(self.admin(f'requests/{rid}',{'status':'approved'})[0],200)
        self.assertEqual(self.employee(f'requests/{rid}/comments',{'message':'متابعة'})[0],200)
        delivered=[]
        with server.conn() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM push_outbox').fetchone()[0],3)
            self.assertEqual(mobile_push.deliver(c,lambda *r:delivered.append(r))['sent'],3)
            self.assertEqual(mobile_push.deliver(c,lambda *r:delivered.append(r))['sent'],0)
        self.assertEqual(len(delivered),3)
        self.assertTrue(all(row[0]==self.token and row[2]==rid for row in delivered))

    def test_attendance_success_enqueues_but_failed_action_does_not(self):
        self.register(self.mobile())
        site=self.admin('sites?company_id=1',{'name':'المكتب','latitude':30,'longitude':31,'radius':100})[1][0]
        self.assertEqual(self.admin('employees/2',{'allowed_site_ids':[site['id']]})[0],200)
        payload={'latitude':30,'longitude':31,'accuracy':10,'site_id':site['id']}
        self.assertEqual(self.employee('attendance',payload)[0],200)
        # Failed immediate checkout must not emit a second notification.
        self.assertEqual(self.employee('attendance',payload)[0],400)
        with server.conn() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM push_outbox').fetchone()[0],1)
            c.execute("UPDATE attendance SET check_in_at='2020-01-01T00:00:00+00:00' WHERE employee_id=2");c.commit()
        self.assertEqual(self.employee('attendance',payload)[0],200)
        with server.conn() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM push_outbox').fetchone()[0],2)
            self.assertEqual(mobile_push.deliver(c,lambda *r:None)['sent'],2)

    def test_missing_credentials_do_not_claim_queue_and_payload_is_private(self):
        import io,json
        from types import SimpleNamespace
        self.register(self.mobile())
        with server.conn() as c:
            self.event(c)
            with patch.dict('os.environ',{'FIREBASE_PROJECT_ID':'owned-project'}),patch.object(mobile_push,'credentials',side_effect=RuntimeError()):
                self.assertFalse(mobile_push.deliver(c)['configured'])
            self.assertEqual(c.execute('SELECT status FROM push_outbox').fetchone()[0],'pending')
        captured=[]
        def transport(request,timeout):
            captured.append(json.loads(request.data));return io.BytesIO(b'{"name":"confirmed"}')
        with patch.dict('os.environ',{'FIREBASE_PROJECT_ID':'owned-project'}),patch.object(mobile_push.urllib.request,'urlopen',transport):
            mobile_push.send(self.token,8,9,SimpleNamespace(token='fake-auth'))
        self.assertEqual(captured[0]['message']['data'],{'notification_id':'8','request_id':'9'})
        self.assertNotIn('employee',str(captured))
        self.assertEqual(captured[0]['message']['android']['ttl'],'3600s')

    def test_logout_and_owner_change_cancel_previous_queue(self):
        session=self.mobile();self.register(session,user_id=1)
        with server.conn() as c:
            self.event(c)
            self.assertEqual(c.execute('SELECT user_id FROM push_devices').fetchone()[0],c.execute("SELECT id FROM users WHERE username='employee'").fetchone()[0])
        self.assertEqual(self.call('logout',{},session)[0],200)
        sent=[]
        with server.conn() as c:self.assertEqual(mobile_push.deliver(c,lambda *r:sent.append(r))['sent'],0)
        self.register(self.mobile())
        with server.conn() as c:self.event(c)
        self.register(self.mobile('manager','Manager@123','manager'))
        with server.conn() as c:self.assertEqual(mobile_push.deliver(c,lambda *r:sent.append(r))['sent'],0)
        self.assertEqual(sent,[])

    def test_revoked_disabled_expired_read_and_old_events_are_not_sent(self):
        session=self.mobile();self.register(session)
        with server.conn() as c:
            self.event(c)
            c.execute('UPDATE notifications SET read_at=?',(server.now(),));c.commit()
            self.assertEqual(mobile_push.deliver(c,lambda *r:self.fail('read notification sent'))['sent'],0)
            self.event(c)
            c.execute("UPDATE push_outbox SET created_at='2000-01-01' WHERE status='pending'");c.commit()
            self.assertEqual(mobile_push.deliver(c,lambda *r:self.fail('old notification sent'))['sent'],0)
            self.event(c)
            c.execute('UPDATE auth_sessions SET expires_at=? WHERE token_hash=?',(int(time.time())-1,server.security.digest(session)));c.commit()
            self.assertEqual(mobile_push.deliver(c,lambda *r:self.fail('expired session sent'))['sent'],0)
        self.register(self.mobile())
        with server.conn() as c:
            self.event(c);c.execute("UPDATE users SET disabled=1 WHERE username='employee'");c.commit()
            self.assertEqual(mobile_push.deliver(c,lambda *r:self.fail('disabled account sent'))['sent'],0)

    def test_invalid_device_and_unknown_delivery_are_not_retried(self):
        self.register(self.mobile())
        with server.conn() as c:
            self.event(c)
            def uncertain(*args):raise TimeoutError()
            mobile_push.deliver(c,uncertain)
            self.assertEqual(c.execute('SELECT status FROM push_outbox').fetchone()[0],'needs_review')
            self.event(c)
            def invalid(*args):raise mobile_push.InvalidDevice()
            mobile_push.deliver(c,invalid)
            self.assertEqual(c.execute('SELECT enabled FROM push_devices').fetchone()[0],0)
            self.assertEqual(mobile_push.deliver(c,lambda *r:self.fail('duplicate delivery'))['sent'],0)

    def test_opt_out_validation_unauthenticated_and_provider_not_configured(self):
        self.assertEqual(self.call('mobile/devices',{'token':self.token,'platform':'ios'})[0],401)
        session=self.mobile()
        self.assertEqual(self.call('mobile/devices',{'token':'bad','platform':'android'},session)[0],400)
        self.register(session)
        with server.conn() as c:
            self.event(c)
            with patch.dict('os.environ',{'FIREBASE_PROJECT_ID':''}):
                self.assertFalse(mobile_push.deliver(c)['configured'])
            self.assertEqual(c.execute('SELECT status FROM push_outbox').fetchone()[0],'pending')
        self.assertEqual(self.call('mobile/devices/remove',{'token':self.token},session)[0],200)
        with server.conn() as c:self.assertEqual(mobile_push.deliver(c,lambda *r:self.fail('opt-out device sent'))['sent'],0)


if __name__=='__main__':unittest.main()

"""Provider diagnostics and worker isolation; no live credentials, network, or production DB."""
import contextlib
import io
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from urllib.error import HTTPError
import mobile_push
import notification_worker


class StopLoop(BaseException):pass


class PushDiagnosticsTests(unittest.TestCase):
    def test_provider_errors_expose_only_allowlisted_codes(self):
        cases=[
            ({'status':'PERMISSION_DENIED'},403,'PERMISSION_DENIED'),
            ({'status':'PERMISSION_DENIED','details':[{'errorCode':'SENDER_ID_MISMATCH'}]},403,'SENDER_ID_MISMATCH'),
            ({'status':'UNAUTHENTICATED','details':[{'errorCode':'THIRD_PARTY_AUTH_ERROR'}]},401,'THIRD_PARTY_AUTH_ERROR'),
            ({'status':'NOT_FOUND','details':[{'errorCode':'UNREGISTERED'}]},404,'UNREGISTERED'),
            ({'status':'SECRET-TOKEN-IN-STATUS'},400,'UNKNOWN'),
            ({'status':{'nested':'SECRET-TOKEN-IN-STATUS'}},400,'UNKNOWN'),
        ]
        for info,status,expected in cases:
            with self.subTest(expected=expected):
                info={**info,'message':'DEVICE-TOKEN-AND-PRIVATE-CONTENT'}
                error=HTTPError('https://example.test',status,'error',{},io.BytesIO(json.dumps({'error':info}).encode()))
                with patch.dict(os.environ,{'FIREBASE_PROJECT_ID':'ehkam-almasar'}),patch.object(mobile_push.urllib.request,'urlopen',side_effect=error):
                    with self.assertRaises(mobile_push.PushProviderError) as result:
                        mobile_push.send('secret-device-token',1,2,SimpleNamespace(token='secret-auth-token'))
                self.assertEqual(result.exception.code,expected);self.assertEqual(result.exception.status,status)
                self.assertNotIn('PRIVATE',str(result.exception));self.assertNotIn('secret-',str(result.exception))
                if expected=='UNREGISTERED':self.assertIsInstance(result.exception,mobile_push.InvalidDevice)

    def test_malformed_provider_response_is_redacted(self):
        error=HTTPError('https://example.test',503,'error',{},io.BytesIO(b'<html>SECRET PROVIDER BODY</html>'))
        with patch.dict(os.environ,{'FIREBASE_PROJECT_ID':'ehkam-almasar'}),patch.object(mobile_push.urllib.request,'urlopen',side_effect=error):
            with self.assertRaises(mobile_push.PushProviderError) as result:mobile_push.send('fake-token',1,None,SimpleNamespace(token='fake-auth'))
        self.assertEqual(str(result.exception),'FCM 503 UNKNOWN')
        with patch.dict(os.environ,{'FIREBASE_PROJECT_ID':'ehkam-almasar'}),patch.object(mobile_push.urllib.request,'urlopen',return_value=io.BytesIO(b'{"ok":true}')):
            with self.assertRaises(mobile_push.PushProviderError) as result:mobile_push.send('fake-token',1,None,SimpleNamespace(token='fake-auth'))
        self.assertEqual(result.exception.code,'INVALID_RESPONSE')

    def test_push_worker_does_not_wait_for_or_call_email_provider(self):
        started={};connection=Mock()
        def thread(*,target,name,daemon):
            started[name]=target;return Mock()
        with patch.dict(os.environ,{'FIREBASE_PROJECT_ID':'ehkam-almasar','RESEND_API_KEY':'fake-mail-key','RESEND_FROM_EMAIL':'test@example.test'},clear=True),patch('threading.Thread',side_effect=thread):
            notification_worker.start_background(lambda:connection)
        self.assertEqual(set(started),{'notification-delivery','notification-push'})
        output=io.StringIO()
        with patch('mobile_push.deliver',return_value={'configured':True,'sent':0}) as push,patch.object(notification_worker,'deliver',side_effect=AssertionError('email must be isolated')) as mail,patch.object(notification_worker,'deliver_whatsapp') as whatsapp,patch('time.sleep',side_effect=StopLoop),contextlib.redirect_stdout(output):
            with self.assertRaises(StopLoop):started['notification-push']()
        push.assert_called_once_with(connection);mail.assert_not_called();whatsapp.assert_not_called();connection.close.assert_called_once()
        self.assertIn('credential refresh succeeded',output.getvalue())

    def test_only_configured_channel_workers_start(self):
        for variables,expected in [({},set()),({'FIREBASE_PROJECT_ID':'ehkam-almasar'},{'notification-push'}),({'RESEND_API_KEY':'fake','RESEND_FROM_EMAIL':'fake@example.test'},{'notification-delivery'})]:
            names=[]
            def thread(*,target,name,daemon):names.append(name);return Mock()
            with patch.dict(os.environ,variables,clear=True),patch('threading.Thread',side_effect=thread):notification_worker.start_background(Mock())
            self.assertEqual(set(names),expected)


if __name__=='__main__':unittest.main()

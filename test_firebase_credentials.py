"""Server credentials tests mock SDK parsing/refresh and never use actual keys or network."""
import json
import os
import unittest
from unittest.mock import Mock,patch
import mobile_push

SCOPES=['https://www.googleapis.com/auth/firebase.messaging']


def fake_info(**overrides):
    return {'type':'service_account','project_id':'ehkam-almasar','client_email':'test-only@ehkam-almasar.iam.gserviceaccount.com','private_key':'FAKE-KEY-NEVER-SEND','token_uri':'https://oauth2.googleapis.com/token',**overrides}


class FirebaseCredentialsTests(unittest.TestCase):
    def test_railway_json_is_loaded_in_memory_and_precedes_adc(self):
        info=fake_info();credential=Mock()
        with patch.dict(os.environ,{'FIREBASE_PROJECT_ID':'ehkam-almasar','FIREBASE_SERVICE_ACCOUNT_JSON':json.dumps(info)},clear=True),patch('google.oauth2.service_account.Credentials.from_service_account_info',return_value=credential) as constructor,patch('google.auth.default') as adc,patch('builtins.open') as opened:
            self.assertIs(mobile_push.credentials(),credential)
        constructor.assert_called_once_with(info,scopes=SCOPES)
        credential.refresh.assert_called_once();adc.assert_not_called();opened.assert_not_called()

    def test_adc_file_or_workload_identity_still_supported_without_json_variable(self):
        credential=Mock()
        with patch.dict(os.environ,{'FIREBASE_PROJECT_ID':'ehkam-almasar'},clear=True),patch('google.auth.default',return_value=(credential,'ehkam-almasar')) as adc,patch('google.oauth2.service_account.Credentials.from_service_account_info') as constructor:
            self.assertIs(mobile_push.credentials(),credential)
        adc.assert_called_once_with(scopes=SCOPES);credential.refresh.assert_called_once();constructor.assert_not_called()

    def test_invalid_explicit_json_is_sanitized_and_never_falls_back(self):
        bad_values=['','not-json','"double-encoded-json"','[]',json.dumps(fake_info(type='other')),json.dumps(fake_info(project_id='another-project')),json.dumps(fake_info(token_uri='https://example.test/not-google')), 'X'*65537]
        for raw in bad_values:
            with self.subTest(value_type=type(raw).__name__,length=len(raw)),patch.dict(os.environ,{'FIREBASE_PROJECT_ID':'ehkam-almasar','FIREBASE_SERVICE_ACCOUNT_JSON':raw},clear=True),patch('google.auth.default') as adc,patch('google.oauth2.service_account.Credentials.from_service_account_info') as constructor:
                with self.assertRaisesRegex(RuntimeError,'^firebase_sender_credentials_unavailable$') as error:mobile_push.credentials()
                self.assertTrue(error.exception.__suppress_context__)
                self.assertNotIn('FAKE-KEY-NEVER-SEND',str(error.exception))
                adc.assert_not_called();constructor.assert_not_called()

    def test_invalid_key_and_refresh_failures_do_not_leak_provider_errors(self):
        for stage in ('parse','refresh'):
            credential=Mock()
            if stage=='refresh':credential.refresh.side_effect=RuntimeError('SECRET-CONTENT-MUST-NOT-APPEAR')
            with self.subTest(stage=stage),patch.dict(os.environ,{'FIREBASE_PROJECT_ID':'ehkam-almasar','FIREBASE_SERVICE_ACCOUNT_JSON':json.dumps(fake_info())},clear=True),patch('google.oauth2.service_account.Credentials.from_service_account_info',return_value=credential,side_effect=RuntimeError('SECRET-CONTENT-MUST-NOT-APPEAR') if stage=='parse' else None),patch('google.auth.default') as adc:
                with self.assertRaises(RuntimeError) as error:mobile_push.credentials()
                self.assertEqual(str(error.exception),'firebase_sender_credentials_unavailable');self.assertTrue(error.exception.__suppress_context__)
                adc.assert_not_called()


if __name__=='__main__':unittest.main()

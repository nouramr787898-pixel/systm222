"""Assistant integration checks using temporary fixtures and a mocked provider."""
import json
import os
import unittest
import io
from unittest.mock import patch
import ai_assistant
import server
import test_workflow as fixture

class AssistantTests(unittest.TestCase):
    setUp=fixture.WorkflowTests.setUp
    tearDown=fixture.WorkflowTests.tearDown
    client=fixture.WorkflowTests.client

    def test_login_required_and_disabled_mode(self):
        self.assertEqual(self.client()('assistant')[0],401)
        with patch.dict(os.environ,{'EHKAM_AI_ENABLED':'0'}):
            self.assertFalse(self.employee('assistant')[1]['enabled'])
            self.assertEqual(self.employee('assistant',{'question':'كيف أطلب إجازة؟'})[0],503)
        self.assertEqual(self.employee('assistant',{'question':['bad']})[0],400)

    def test_read_only_context_is_scoped_and_excludes_private_data(self):
        with server.conn() as c:
            emp=c.execute('SELECT * FROM users WHERE username="employee"').fetchone()
            expected=server.visible_requests(c,emp,include_details=False)
            data=ai_assistant.context(c,emp,server)
            self.assertEqual(sum(x['count'] for x in data['request_counts']),len(expected))
            allowed={'date','role','request_counts','guide'}
            self.assertEqual(set(data),allowed)
            for row in data['request_counts']:self.assertEqual(set(row),{'type','status','count'})
            # Financial/identity/free text cannot be serialized into provider context.
            for forbidden in ('iban','identity_no','salary','employee_name','attachments','comments'):
                self.assertNotIn(forbidden,json.dumps(data))

    def test_success_audit_does_not_store_question_or_answer(self):
        with patch.dict(os.environ,{'EHKAM_AI_ENABLED':'1','OPENAI_API_KEY':'fake-test','EHKAM_AI_MODEL':'test-model'}),patch.object(ai_assistant,'generate',return_value='من قسم الطلبات') as generate:
            with ai_assistant._LOCK:ai_assistant._CALLS.clear()
            code,result=self.employee('assistant',{'question':'سؤال خاص لا تحفظه'})
            self.assertEqual(code,200);self.assertEqual(result['answer'],'من قسم الطلبات')
            self.assertEqual(generate.call_count,1)
            for _ in range(4):self.assertEqual(self.employee('assistant',{'question':'سؤال تجريبي'})[0],200)
            self.assertEqual(self.employee('assistant',{'question':'سؤال تجريبي'})[0],429)
        rows=self.admin('audit')[1]
        events=[r for r in rows if r['action']=='assistant_answer']
        self.assertEqual(len(events),5)
        self.assertNotIn('سؤال خاص',json.dumps(events,ensure_ascii=False))

    def test_provider_failure_is_sanitized(self):
        with patch.dict(os.environ,{'EHKAM_AI_ENABLED':'1','OPENAI_API_KEY':'fake-test','EHKAM_AI_MODEL':'test-model'}),patch.object(ai_assistant,'generate',side_effect=RuntimeError('secret-provider-error')):
            with ai_assistant._LOCK:ai_assistant._CALLS.clear()
            code,result=self.admin('assistant',{'question':'اشرح النظام'})
            self.assertEqual(code,502)
            self.assertNotIn('secret-provider-error',json.dumps(result))

    def test_transport_payload_disables_storage_and_has_no_tools(self):
        response=io.BytesIO(json.dumps({'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'رد اختباري'}]}]}).encode())
        with patch.dict(os.environ,{'OPENAI_API_KEY':'fake-test','EHKAM_AI_MODEL':'test-model'}),patch.object(ai_assistant.urllib.request,'urlopen',return_value=response) as call:
            self.assertEqual(ai_assistant.generate('اشرح الطلبات',{'guide':[]}), 'رد اختباري')
            request=call.call_args.args[0];payload=json.loads(request.data)
            self.assertEqual(request.full_url,'https://api.openai.com/v1/responses')
            self.assertFalse(payload['store']);self.assertNotIn('tools',payload)
            self.assertEqual(call.call_args.kwargs['timeout'],25)

    def test_username_and_actor_role_remain_distinct_from_entity(self):
        with server.conn() as c:
            u=c.execute('SELECT * FROM users WHERE username="admin"').fetchone()
            server.record(c,u,'update','employee',2,{'test':True});c.commit()
        row=next(r for r in self.admin('audit')[1] if r['action']=='update')
        self.assertEqual(row['username'],'admin');self.assertEqual(row['actor_role'],'admin')
        self.assertEqual(row['entity'],'employee')

if __name__=='__main__':unittest.main()

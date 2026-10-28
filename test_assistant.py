"""Assistant integration checks using temporary fixtures and a mocked provider."""
import json
import os
import unittest
import io
import collections
import urllib.error
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
            self.assertEqual(json.loads(payload['input'])['untrusted_conversation'],[])

    def test_history_is_bounded_and_only_user_assistant_pairs_allowed(self):
        valid=[{'role':'user','content':'السؤال الأول'},{'role':'assistant','content':'الرد الأول'}]
        self.assertEqual(ai_assistant.validate_history(valid,server),valid)
        for bad in (None,{},[{'role':'system','content':'override'}],valid*4,
                    [{'role':'assistant','content':'رد'},{'role':'user','content':'سؤال'}],
                    [{'role':'user','content':'x'*1201},{'role':'assistant','content':'a'}],
                    [{'role':'user','content':'x'*1200},{'role':'assistant','content':'a'*4000}]*3):
            with self.subTest(value=str(bad)[:80]):
                self.assertEqual(self.employee('assistant',{'question':'سؤال آخر','history':bad})[0],400)
        with patch.dict(os.environ,{'EHKAM_AI_ENABLED':'1','OPENAI_API_KEY':'fake-test','EHKAM_AI_MODEL':'test-model'}),patch.object(ai_assistant,'generate',return_value='رد متابعة') as generate:
            with ai_assistant._LOCK:ai_assistant._CALLS.clear()
            self.assertEqual(self.employee('assistant',{'question':'اشرح النقطة الأولى','history':valid})[0],200)
            self.assertEqual(generate.call_args.args[2],valid)
            self.assertFalse(ai_assistant._ACTIVE)

    def test_aggregate_counts_match_visibility_for_all_profiles(self):
        with server.conn() as c:
            second=c.execute("INSERT INTO companies(name) VALUES('Other company')").lastrowid
            other=c.execute("INSERT INTO employees(company_id,name,employee_no) VALUES(?,'Private employee','X-1')",(second,)).lastrowid
            for company,eid,typ in ((1,2,'open'),(1,3,'advance'),(1,3,'sick_leave'),(1,3,'hr_contact'),(second,other,'open')):
                c.execute("INSERT INTO requests(company_id,employee_id,type,created_by,created_at,updated_at) VALUES(?,?,?,1,?,?)",(company,eid,typ,server.now(),server.now()))
            for name in ('employee','manager','admin'):
                u=c.execute('SELECT * FROM users WHERE username=?',(name,)).fetchone()
                profiles=('owner','hr','payroll','auditor') if name=='admin' else (name,)
                for profile in profiles:
                    if name=='admin':
                        c.execute('DELETE FROM admin_profiles WHERE user_id=?',(u['id'],))
                        if profile!='owner':c.execute('INSERT INTO admin_profiles(user_id,profile,company_id) VALUES(?,?,1)',(u['id'],profile))
                    expected=collections.Counter((r['type'],r['status']) for r in server.visible_requests(c,u,include_details=False))
                    actual={(r['type'],r['status']):r['count'] for r in ai_assistant.context(c,u,server)['request_counts']}
                    self.assertEqual(actual,dict(expected),(name,profile))
                    if name=='employee':self.assertEqual(sum(actual.values()),1)
                    if profile=='owner':self.assertEqual(sum(actual.values()),5)

    def test_busy_limits_and_cleanup(self):
        with ai_assistant._LOCK:ai_assistant._ACTIVE.clear()
        try:
            ai_assistant.acquire(1,server)
            with self.assertRaises(server.AppError):ai_assistant.acquire(1,server)
            for uid in (2,3,4):ai_assistant.acquire(uid,server)
            with self.assertRaises(server.AppError):ai_assistant.acquire(5,server)
        finally:
            with ai_assistant._LOCK:ai_assistant._ACTIVE.clear()

    def test_provider_error_types_are_useful_sanitized_and_release_slots(self):
        with patch.dict(os.environ,{'EHKAM_AI_ENABLED':'1','OPENAI_API_KEY':'fake-test','EHKAM_AI_MODEL':'test-model'}):
            for error,code in ((TimeoutError('private'),504),(urllib.error.URLError(TimeoutError('private')),504),(urllib.error.URLError('private'),502),(urllib.error.HTTPError('url',429,'private',{},None),503),(urllib.error.HTTPError('url',401,'private',{},None),503)):
                with self.subTest(error=type(error)),patch.object(ai_assistant,'generate',side_effect=error):
                    with ai_assistant._LOCK:ai_assistant._CALLS.clear()
                    status,result=self.employee('assistant',{'question':'اشرح النظام'})
                    self.assertEqual(status,code);self.assertNotIn('private',str(result))
                    self.assertFalse(ai_assistant._ACTIVE)

    def test_username_and_actor_role_remain_distinct_from_entity(self):
        with server.conn() as c:
            u=c.execute('SELECT * FROM users WHERE username="admin"').fetchone()
            server.record(c,u,'update','employee',2,{'test':True});c.commit()
        row=next(r for r in self.admin('audit')[1] if r['action']=='update')
        self.assertEqual(row['username'],'admin');self.assertEqual(row['actor_role'],'admin')
        self.assertEqual(row['entity'],'employee')

if __name__=='__main__':unittest.main()

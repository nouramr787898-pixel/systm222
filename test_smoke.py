"""Smoke tests for EHKAM prototype. Start server on 8765 first."""
import urllib.request,urllib.error,http.cookiejar,json
base='http://127.0.0.1:8765/api/'
def client(name,pw):
 jar=http.cookiejar.CookieJar();op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
 def call(path,body=None):
  data=json.dumps(body).encode() if body is not None else None
  r=urllib.request.Request(base+path,data,{'Content-Type':'application/json'} if data else {})
  try:
   with op.open(r) as x:return x.status,json.loads(x.read())
  except urllib.error.HTTPError as e:return e.code,json.loads(e.read())
 assert call('login',{'username':name,'password':pw})[0]==200
 return call
admin=client('admin','Admin@123');employee=client('employee','Employee@123');manager=client('manager','Manager@123')
assert employee('employees')[1][0]['id']==2
assert employee('leave/1')[0]==403
assert manager('audit')[0]==403
assert employee('archive',{'target':'employee','id':2,'archived':True,'confirm':True,'password':'Employee@123'})[0]==403
assert admin('requests',{'employee_id':2,'type':'advance','amount':9000,'installment':1000})[0]==400
assert admin('requests',{'employee_id':2,'type':'annual_leave','start_date':'2026-11-02','end_date':'2026-11-04','reason':'اختبار'})[0]==201
rid=admin('requests')[1][0]['id']
before=admin('leave/2')[1]['available']
assert manager('requests/'+str(rid),{'status':'approved','note':'تمت الموافقة'})[0]==200
assert admin('leave/2')[1]['available']==before-3
assert admin('requests/'+str(rid),{'status':'cancelled','note':'تجربة إلغاء'})[0]==200
assert admin('leave/2')[1]['available']==before
assert manager('requests',{'employee_id':2,'type':'delegation','start_date':'2026-11-02','end_date':'2026-11-05'})[0]==201
rid=employee('requests')[1][0]['id'];assert employee('requests/'+str(rid),{'status':'accepted','note':'موافق'})[0]==200
print('PASS: role isolation; advance validation; leave approval/reversal; delegation workflow')

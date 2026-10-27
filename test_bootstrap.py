"""Bootstrap checks run locally only, with private temporary volumes and fake credentials."""
import contextlib
import getpass
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import warnings

import production_db
import railway_start


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='ehkam-bootstrap-test-')
        self.volume=Path(self.temp.name)/'volume';self.volume.mkdir()
        self.database=self.volume/'ehkam_data.sqlite3'
        self.processes=[]

    def tearDown(self):
        for process in self.processes:
            if process.poll() is None:process.terminate()
            process.wait(timeout=5)
            if process.stdout:process.stdout.close()
            if process.stderr:process.stderr.close()
        self.temp.cleanup()

    def launch(self,mode='storage-setup',identity='',extra=None):
        sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
        env={**os.environ,'RAILWAY_SERVICE_ID':'test-only','RAILWAY_VOLUME_MOUNT_PATH':str(self.volume),'EHKAM_DB_PATH':str(self.database),'EHKAM_REQUIRE_EXISTING_DB':'1','EHKAM_DB_ID':identity,'PORT':str(port)}
        if mode is not None:env['EHKAM_START_MODE']=mode
        else:env.pop('EHKAM_START_MODE',None)
        if extra:env.update(extra)
        process=subprocess.Popen([sys.executable,'-u','railway_start.py'],cwd=Path(__file__).parent,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.processes.append(process)
        return process,f'http://127.0.0.1:{port}'

    def ready(self,process,base):
        for attempt in range(80):
            if process.poll() is not None:self.fail(process.stderr.read().decode())
            try:
                with urllib.request.urlopen(base+'/api/health',timeout=1) as response:return json.load(response)
            except urllib.error.URLError:time.sleep(.05)
        self.fail('Test process did not become healthy')

    def test_maintenance_health_no_database_or_file_exposure(self):
        process,base=self.launch();health=self.ready(process,base)
        self.assertEqual(health,{'status':'storage-setup','mode':'storage-setup','application_ready':False,'database_access':'disabled'})
        for path,body in [('/',None),('/server.py',None),('/production_db.py',None),('/api/login',b'{"password":"fixture-not-to-be-logged"}'),('/api/requests',b'{}'),('/api/health',b'{}')]:
            request=urllib.request.Request(base+path,data=body)
            with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(request,timeout=2)
            self.assertEqual(error.exception.code,503)
        with urllib.request.urlopen(urllib.request.Request(base+'/api/health',method='HEAD')) as response:
            self.assertEqual(response.status,200);self.assertEqual(response.read(),b'')
        self.assertEqual(list(self.volume.iterdir()),[])
        process.terminate();process.wait(timeout=5)
        output=process.stdout.read()+process.stderr.read()
        self.assertNotIn(b'fixture-not-to-be-logged',output)

    def test_maintenance_does_not_touch_existing_file(self):
        self.database.write_bytes(b'not-even-a-sqlite-database')
        digest=hashlib.sha256(self.database.read_bytes()).hexdigest()
        process,base=self.launch();self.ready(process,base)
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(),digest)
        self.assertEqual(len(list(self.volume.iterdir())),1)

    def test_no_automatic_switch_after_explicit_initialization(self):
        process,base=self.launch();self.ready(process,base)
        info=production_db.initialize(self.database,'bootstrap_owner','FixtureOnly!2026!!','مسؤول الاختبار')
        with urllib.request.urlopen(base+'/api/health') as response:self.assertEqual(json.load(response)['mode'],'storage-setup')
        process.terminate();process.wait(timeout=5)
        app,app_base=self.launch('app',info['database_id'])
        self.assertEqual(self.ready(app,app_base)['status'],'ok')
        self.assertEqual(production_db.inspect_database(self.database,info['database_id'])['counts']['users'],1)

    def test_app_is_default_and_never_creates_missing_database(self):
        for mode in ('app',None):
            process,_=self.launch(mode)
            self.assertNotEqual(process.wait(timeout=5),0)
            self.assertFalse(self.database.exists())
            self.assertEqual(list(self.volume.iterdir()),[])

    def test_app_guard_cannot_be_disabled_without_railway_variables(self):
        for guard in (None,'0'):
            env={k:v for k,v in os.environ.items() if not k.startswith('RAILWAY_') and k not in ('EHKAM_REQUIRE_EXISTING_DB','EHKAM_DB_ID')}
            env.update(EHKAM_START_MODE='app',EHKAM_DB_PATH=str(self.database))
            if guard is not None:env['EHKAM_REQUIRE_EXISTING_DB']=guard
            result=subprocess.run([sys.executable,'railway_start.py'],cwd=Path(__file__).parent,env=env,capture_output=True,text=True,timeout=5)
            self.assertNotEqual(result.returncode,0)
            self.assertFalse(self.database.exists())

    def test_app_refuses_wrong_identity(self):
        production_db.initialize(self.database,'bootstrap_owner','FixtureOnly!2026!!','مسؤول الاختبار')
        original=self.database.read_bytes()
        process,_=self.launch('app','wrong-identity')
        self.assertNotEqual(process.wait(timeout=5),0)
        self.assertEqual(self.database.read_bytes(),original)

    def test_invalid_mode_volume_and_path_fail_closed(self):
        cases=[('unknown',{}),('storage-setup',{'RAILWAY_VOLUME_MOUNT_PATH':''}),('storage-setup',{'EHKAM_DB_PATH':'relative.sqlite3'}),('storage-setup',{'EHKAM_DB_PATH':str(Path(self.temp.name)/'outside.sqlite3')})]
        for mode,extra in cases:
            process,_=self.launch(mode,extra=extra)
            self.assertNotEqual(process.wait(timeout=5),0)
            self.assertEqual(list(self.volume.iterdir()),[])

    def test_cli_noninteractive_initialization_refuses_before_reading_credentials(self):
        result=subprocess.run([sys.executable,'production_db.py','initialize','--database',str(self.database)],cwd=Path(__file__).parent,input='unused-credentials\n',text=True,capture_output=True,timeout=5)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('interactive terminal',result.stderr)
        self.assertNotIn('Production admin username:',result.stdout)
        self.assertNotIn('unused-credentials',result.stdout+result.stderr)
        self.assertFalse(self.database.exists())

    def test_password_echo_fallback_is_refused(self):
        def insecure_getpass(*args,**kwargs):
            warnings.warn('Cannot control echo',getpass.GetPassWarning)
            self.fail('Fallback must not continue')
        with patch.object(sys.stdin,'isatty',return_value=True),patch.object(sys.stdout,'isatty',return_value=True),patch.object(getpass,'getpass',side_effect=insecure_getpass):
            with self.assertRaisesRegex(ValueError,'hidden terminal input'):production_db.interactive_password()

    def test_password_confirmation_and_hidden_input(self):
        with patch.object(sys.stdin,'isatty',return_value=True),patch.object(sys.stdout,'isatty',return_value=True),patch.object(getpass,'getpass',side_effect=['FixtureOnly!2026!!','FixtureOnly!2026!!']):
            self.assertEqual(production_db.interactive_password(),'FixtureOnly!2026!!')
        with patch.object(sys.stdin,'isatty',return_value=True),patch.object(sys.stdout,'isatty',return_value=True),patch.object(getpass,'getpass',side_effect=['first','second']):
            with self.assertRaisesRegex(ValueError,'do not match'):production_db.interactive_password()

    def test_maintenance_does_not_import_application_or_sqlite(self):
        self.assertNotIn('server',railway_start.__dict__)
        self.assertNotIn('sqlite3',railway_start.__dict__)


if __name__=='__main__':unittest.main()

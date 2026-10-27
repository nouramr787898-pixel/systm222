"""Checks the demo does not open unmarked databases and keeps user sessions isolated."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import demo
import server


class DemoTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.old=server.DB

    def tearDown(self):
        server.DB=self.old
        self.temp.cleanup()

    def test_seed_and_reuse(self):
        database=demo.prepare_demo(self.temp.name)
        with sqlite3.connect(database) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM users').fetchone()[0],4)
            self.assertEqual(c.execute('SELECT count(*) FROM requests').fetchone()[0],5)
            self.assertEqual(c.execute('SELECT count(*) FROM request_attachments').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT count(*) FROM attendance WHERE day=?',(server.TODAY().isoformat(),)).fetchone()[0],0)
            for username,password,role,eid,label in demo.ACCOUNTS:
                row=c.execute('SELECT password_hash,role,employee_id FROM users WHERE username=?',(username,)).fetchone()
                self.assertTrue(server.verifypw(password,row[0]))
                self.assertEqual(row[1:],(role,eid))
            c.execute("UPDATE requests SET notes='preserve my changes' WHERE id=1")
        self.assertEqual(demo.prepare_demo(self.temp.name),database)
        with sqlite3.connect(database) as c:
            self.assertEqual(c.execute('SELECT notes FROM requests WHERE id=1').fetchone()[0],'preserve my changes')
            self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_refuses_unmarked_existing_database(self):
        database=Path(self.temp.name)/'demo.sqlite3'
        with sqlite3.connect(database) as c:c.execute('CREATE TABLE important_data(value)')
        original=database.read_bytes()
        with self.assertRaises(RuntimeError):demo.prepare_demo(self.temp.name)
        self.assertEqual(database.read_bytes(),original)
        self.assertEqual(server.DB,self.old)

    def test_marker_file_alone_does_not_authorize_existing_database(self):
        database=Path(self.temp.name)/'demo.sqlite3'
        with sqlite3.connect(database) as c:c.execute('CREATE TABLE important_data(value)')
        original=database.read_bytes()
        (Path(self.temp.name)/'ehkam-demo.json').write_text(json.dumps({'kind':'ehkam-isolated-demo-v1'}))
        with self.assertRaises(sqlite3.DatabaseError):demo.prepare_demo(self.temp.name)
        self.assertEqual(database.read_bytes(),original)

    def test_production_missing_database_is_never_created(self):
        server.DB=Path(self.temp.name)/'missing-production.sqlite3'
        with patch.dict(server.os.environ,{'RAILWAY_SERVICE_ID':'test-service'}):
            with self.assertRaises(RuntimeError):server.validate_runtime_database()
        self.assertFalse(server.DB.exists())

    def test_regular_initialization_does_not_create_demo_accounts(self):
        server.DB=Path(self.temp.name)/'empty.sqlite3'
        server.init()
        with sqlite3.connect(server.DB) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM users').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT count(*) FROM employees').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT count(*) FROM companies').fetchone()[0],0)
        with patch.dict(server.os.environ,{'RAILWAY_SERVICE_ID':'test-service'}):
            with self.assertRaises(RuntimeError):server.validate_runtime_database()

    def test_existing_production_database_is_checked_read_only(self):
        server.DB=Path(self.temp.name)/'existing.sqlite3'
        server.init(seeder=demo.seed_fixture)
        original=server.DB.read_bytes()
        with patch.dict(server.os.environ,{'RAILWAY_SERVICE_ID':'test-service'}):server.validate_runtime_database()
        self.assertEqual(server.DB.read_bytes(),original)


if __name__=='__main__':unittest.main()

"""GPS parsing, restricted redirects and isolated SQLite/attendance integration."""
import unittest
from unittest.mock import patch,MagicMock
import urllib.request
import gps_location as gps
import server
import test_workflow as fixture

class MapsTests(unittest.TestCase):
    def test_coordinate_formats_and_pin_priority(self):
        cases={
            'https://www.google.com/maps?q=30.1390381,31.2558794':(30.1390381,31.2558794),
            'https://www.google.com/maps/search/?api=1&query=30.1%2C31.2':(30.1,31.2),
            'https://www.google.com/maps/@30.1,31.2,18z':(30.1,31.2),
            'https://www.google.com/maps/place/office/@30,31,15z/data=!3d30.1390381!4d31.2558794':(30.1390381,31.2558794),
        }
        with patch('gps_location.urllib.request.build_opener',side_effect=AssertionError('No network needed')):
            for value,point in cases.items():self.assertEqual(gps.resolve(value),point)

    def test_disallowed_urls_and_coordinates(self):
        for value in ['http://google.com/maps?q=1,2','https://127.0.0.1/maps','https://www.google.com.evil.test/maps','https://evil.test/maps?q=1,2','https://user@google.com/maps','https://google.com:8443/maps','https://goo.gl/not-maps','https://google.com/maps?q=91,2']:
            with self.subTest(value=value),self.assertRaises(gps.LocationError):gps.resolve(value)

    def test_short_url_resolution(self):
        response=MagicMock();response.__enter__.return_value=response
        response.geturl.return_value='https://www.google.com/maps?q=30.1390381,31.2558794'
        with patch('gps_location.urllib.request.build_opener') as factory:
            factory.return_value.open.return_value=response
            self.assertEqual(gps.resolve('https://maps.app.goo.gl/example?g_st=ic'),(30.1390381,31.2558794))

    def test_redirect_cannot_fetch_arbitrary_host(self):
        request=urllib.request.Request('https://maps.app.goo.gl/example')
        for target in ['https://127.0.0.1/secret','file:///etc/passwd','https://example.org/']:
            with self.assertRaises(gps.LocationError):gps.SafeRedirect().redirect_request(request,None,302,'Found',{},target)

    def test_failed_or_ambiguous_link(self):
        with patch('gps_location.urllib.request.build_opener') as factory:
            factory.return_value.open.side_effect=OSError('offline')
            with self.assertRaises(gps.LocationError):gps.resolve('https://maps.app.goo.gl/example')
        response=MagicMock();response.__enter__.return_value=response
        response.geturl.return_value='https://www.google.com/maps/place/office'
        response.read.return_value=b'<a href="https://google.com/maps?q=10,20">unrelated link</a>'
        with patch('gps_location.urllib.request.build_opener') as factory:
            factory.return_value.open.return_value=response
            with self.assertRaises(gps.LocationError):gps.resolve('https://maps.app.goo.gl/example')

class CompanyGPSTests(unittest.TestCase):
    setUp=fixture.WorkflowTests.setUp
    tearDown=fixture.WorkflowTests.tearDown
    client=fixture.WorkflowTests.client

    def test_create_and_idempotent_schema(self):
        link='https://www.google.com/maps?q=30.1390381,31.2558794'
        code,out=self.admin('companies',{'name':'شركة GPS','gps_url':link})
        self.assertEqual(code,201,out)
        server.init()
        with server.conn() as c:
            row=c.execute("SELECT * FROM companies WHERE name='شركة GPS'").fetchone()
            self.assertEqual((row['gps_url'],row['latitude'],row['longitude']),(link,30.1390381,31.2558794))
            self.assertEqual(c.execute('SELECT count(*) FROM employees').fetchone()[0],3)

    def test_edit_preserves_legacy_location_and_uses_new_pin(self):
        self.assertEqual(self.admin('companies/1',{'name':'الشركة المعدلة','gps_url':''})[0],200)
        with server.conn() as c:self.assertEqual(c.execute('SELECT latitude FROM companies WHERE id=1').fetchone()[0],30.1390381)
        self.assertEqual(self.admin('companies/1',{'name':'موقع جديد','gps_url':'https://google.com/maps?q=20,20'})[0],200)
        self.assertEqual(self.employee('attendance',{'latitude':30.1390381,'longitude':31.2558794,'accuracy':10})[0],400)
        self.assertEqual(self.employee('attendance',{'latitude':20,'longitude':20,'accuracy':10})[0],200)
        self.assertEqual(self.employee('attendance',{'latitude':20,'longitude':20,'accuracy':10})[0],200)

    def test_reject_bad_link_without_changing_location(self):
        self.assertEqual(self.admin('companies/1',{'name':'bad','gps_url':'https://evil.test/'})[0],400)
        with server.conn() as c:self.assertEqual(c.execute('SELECT latitude FROM companies WHERE id=1').fetchone()[0],30.1390381)

    def test_gps_changes_are_admin_only(self):
        self.assertEqual(self.employee('companies/1',{'name':'x','gps_url':'https://google.com/maps?q=20,20'})[0],403)

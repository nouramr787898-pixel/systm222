"""Firebase configuration tests use disposable hosts; no network or delivery calls."""
import importlib.util
import json
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE=Path(__file__).resolve().parent
SPEC=importlib.util.spec_from_file_location('firebase_config',HERE/'firebase_config.py')
module=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(module)

PBX='''// !$*UTF8*$!
{
objects = {
/* Begin PBXBuildFile section */
/* End PBXBuildFile section */
/* Begin PBXFileReference section */
/* End PBXFileReference section */
AAAAAAAAAAAAAAAAAAAAAAAA /* Runner */ = {
    isa = PBXGroup;
    children = (
    );
    path = Runner;
};
BBBBBBBBBBBBBBBBBBBBBBBB /* Runner */ = {
    isa = PBXNativeTarget;
    buildPhases = (
        CCCCCCCCCCCCCCCCCCCCCCCC /* Resources */,
    );
    name = Runner;
};
CCCCCCCCCCCCCCCCCCCCCCCC /* Resources */ = {
    isa = PBXResourcesBuildPhase;
    files = (
    );
};
DDDDDDDDDDDDDDDDDDDDDDDD /* Resources */ = {
    isa = PBXResourcesBuildPhase;
    files = (
    );
};
'''+''.join('''CONFIG_%s = {
    buildSettings = {
        PRODUCT_BUNDLE_IDENTIFIER = com.ehkam.portal;
        EXISTING_CUSTOM_SETTING = keep;
    };
    name = %s;
};
'''%(name,name) for name in ('Debug','Release','Profile'))+'''
TEST_CONFIGURATION = {
    buildSettings = {
        PRODUCT_BUNDLE_IDENTIFIER = com.ehkam.portal.RunnerTests;
    };
    name = Debug;
};
};
}
'''


def clients(root):
    for name in ['android/app/google-services.json','ios/Runner/GoogleService-Info.plist']:
        target=root/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(HERE.parent/name,target)


def hosts(root):
    (root/'ios/Podfile').write_text("platform :ios, '15.0'\n")
    (root/'ios/Flutter').mkdir(parents=True,exist_ok=True)
    for mode in ('Debug','Release'):(root/'ios/Flutter'/f'{mode}.xcconfig').write_text('#include \"Generated.xcconfig\"\n')
    settings=root/'android/settings.gradle.kts'
    settings.write_text('pluginManagement { repositories { google(); mavenCentral(); gradlePluginPortal() } }\nplugins { id("dev.flutter.flutter-plugin-loader") version "1.0.0" }\n')
    app=root/'android/app/build.gradle.kts'
    app.write_text('plugins { id("com.android.application") }\nandroid { namespace = "com.ehkam.portal"\n defaultConfig { applicationId = "com.ehkam.portal"; minSdk = flutter.minSdkVersion }\n buildTypes { release { signingConfig = signingConfigs.getByName("debug") } } }\n')
    manifest=root/'android/app/src/main/AndroidManifest.xml';manifest.parent.mkdir(parents=True)
    manifest.write_text('<manifest xmlns:android="http://schemas.android.com/apk/res/android"><application/></manifest>')
    pbx=root/'ios/Runner.xcodeproj/project.pbxproj';pbx.parent.mkdir(parents=True);pbx.write_text(PBX)
    (root/'ios/Runner/Info.plist').write_bytes(plistlib.dumps({'CFBundleDisplayName':'Existing Name','UIBackgroundModes':['fetch']}))


class FirebaseConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);clients(self.root)
    def tearDown(self):self.temp.cleanup()

    def test_supplied_clients_and_explicit_options_match_without_host_claim(self):
        before={p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        config,pending=module.configure(self.root)
        self.assertEqual(config['project'],'ehkam-almasar')
        self.assertEqual(config['android_id'],'com.ehkam.portal');self.assertEqual(config['ios_id'],'com.ehkam.portal')
        self.assertEqual(len(pending),2)
        source=(self.root/'lib/firebase_options.dart').read_text()
        self.assertIn(config['options']['android']['appId'],source);self.assertIn(config['options']['ios']['appId'],source)
        for p,raw in before.items():self.assertEqual(p.read_bytes(),raw)
        module.configure(self.root,check=True)
        with self.assertRaisesRegex(ValueError,'Native hosts are required'):module.configure(self.root,check=True,require_hosts=True)

    def test_mismatched_firebase_project_does_not_modify_anything(self):
        path=self.root/'ios/Runner/GoogleService-Info.plist';data=plistlib.loads(path.read_bytes());data['PROJECT_ID']='another-project';path.write_bytes(plistlib.dumps(data))
        with self.assertRaisesRegex(ValueError,'mismatch'):module.configure(self.root)
        self.assertFalse((self.root/'lib/firebase_options.dart').exists())

    def test_platform_app_id_and_gcm_flag_are_validated(self):
        path=self.root/'ios/Runner/GoogleService-Info.plist';original=plistlib.loads(path.read_bytes())
        for field,value in [('IS_GCM_ENABLED',False),('GOOGLE_APP_ID','1:000:android:wrong')]:
            data={**original,field:value};path.write_bytes(plistlib.dumps(data))
            with self.assertRaises(ValueError):module.configure(self.root)

    def test_dart_format_whitespace_does_not_invalidate_derived_options(self):
        module.configure(self.root)
        path=self.root/'lib/firebase_options.dart'
        path.write_text(path.read_text().replace('return android;','\n      return android;'))
        module.configure(self.root,check=True)
        before=path.read_bytes();module.configure(self.root)
        self.assertEqual(path.read_bytes(),before)

    def test_custom_options_are_not_overwritten(self):
        path=self.root/'lib/firebase_options.dart';path.parent.mkdir();path.write_text('// Custom implementation')
        with self.assertRaisesRegex(ValueError,'refusing to overwrite'):module.configure(self.root)
        self.assertEqual(path.read_text(),'// Custom implementation')

    def test_native_configuration_is_repeatable_and_preserves_unrelated_settings(self):
        hosts(self.root);originals={p:p.read_bytes() for p in self.root.rglob('*') if p.name in ('google-services.json','GoogleService-Info.plist')}
        _,pending=module.configure(self.root,require_hosts=True);self.assertEqual(pending,[])
        snapshot={p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        module.configure(self.root,require_hosts=True)
        for p,raw in snapshot.items():self.assertEqual(p.read_bytes(),raw,p)
        for p,raw in originals.items():self.assertEqual(p.read_bytes(),raw)
        settings=(self.root/'android/settings.gradle.kts').read_text();app=(self.root/'android/app/build.gradle.kts').read_text()
        self.assertEqual(settings.count('com.google.gms.google-services'),1);self.assertIn('apply false',settings)
        self.assertEqual(app.count('com.google.gms.google-services'),1)
        project=(self.root/'ios/Runner.xcodeproj/project.pbxproj').read_text()
        self.assertEqual(project.count('GoogleService-Info.plist in Resources'),2)
        tests_phase=project.split('DDDDDDDDDDDDDDDDDDDDDDDD /* Resources */ = {',1)[1].split('};',1)[0]
        self.assertNotIn('GoogleService-Info',tests_phase)
        self.assertEqual(project.count('EXISTING_CUSTOM_SETTING = keep;'),3)
        self.assertEqual(project.count('APS_ENVIRONMENT = development;'),1);self.assertEqual(project.count('APS_ENVIRONMENT = production;'),2)
        info=plistlib.loads((self.root/'ios/Runner/Info.plist').read_bytes())
        self.assertEqual(info['UIBackgroundModes'],['fetch','remote-notification']);self.assertEqual(info['CFBundleDisplayName'],'Existing Name')
        ent=plistlib.loads((self.root/'ios/Runner/Ehkam.entitlements').read_bytes());self.assertEqual(ent['aps-environment'],'$(APS_ENVIRONMENT)')
        module.configure(self.root,check=True,require_hosts=True)

    def test_android_id_mismatch_and_disabled_ios_proxy_leave_hosts_untouched(self):
        hosts(self.root);path=self.root/'android/app/build.gradle.kts';path.write_text(path.read_text().replace('applicationId = "com.ehkam.portal"','applicationId = "com.another.app"'))
        before=path.read_bytes()
        with self.assertRaisesRegex(ValueError,'applicationId'):module.configure(self.root)
        self.assertEqual(path.read_bytes(),before);self.assertFalse((self.root/'lib/firebase_options.dart').exists())
        path.write_text(path.read_text().replace('com.another.app','com.ehkam.portal'))
        info=self.root/'ios/Runner/Info.plist';data=plistlib.loads(info.read_bytes());data['FirebaseAppDelegateProxyEnabled']=False;info.write_bytes(plistlib.dumps(data))
        before=path.read_bytes()
        with self.assertRaisesRegex(ValueError,'delegate proxy'):module.configure(self.root)
        self.assertEqual(path.read_bytes(),before)

    def test_bootstrap_configures_existing_hosts_without_flutter_create(self):
        hosts(self.root);tool=self.root/'tool';tool.mkdir()
        for name in ['bootstrap.py','firebase_config.py']:shutil.copyfile(HERE/name,tool/name)
        proc=subprocess.run([sys.executable,str(tool/'bootstrap.py'),'--configure-only'],capture_output=True,text=True)
        self.assertEqual(proc.returncode,0,proc.stderr)
        module.configure(self.root,check=True,require_hosts=True)
        proc=subprocess.run([sys.executable,str(tool/'bootstrap.py'),'--configure-only','--android-id','com.wrong.app'],capture_output=True,text=True)
        self.assertNotEqual(proc.returncode,0);self.assertIn('No files were changed',proc.stderr)

    def test_plist_only_in_tests_resources_is_not_accepted_for_runner(self):
        text=module.ios_project(PBX)
        build_id=module.hashlib.sha256(b'ehkam.firebase.resource').hexdigest()[:24].upper()
        entry='\n\t\t\t\t'+build_id+' /* GoogleService-Info.plist in Resources */,'
        text=text.replace(entry,'',1).replace('DDDDDDDDDDDDDDDDDDDDDDDD /* Resources */ = {\n    isa = PBXResourcesBuildPhase;\n    files = (','DDDDDDDDDDDDDDDDDDDDDDDD /* Resources */ = {\n    isa = PBXResourcesBuildPhase;\n    files = ('+entry)
        with self.assertRaisesRegex(ValueError,'incomplete'):module.ios_project(text)

    def test_partial_ios_resource_reference_requires_review(self):
        with self.assertRaisesRegex(ValueError,'incomplete'):module.ios_project(PBX+'// GoogleService-Info.plist')


if __name__=='__main__':unittest.main()

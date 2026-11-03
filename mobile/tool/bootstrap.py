#!/usr/bin/env python3
"""Generate official Flutter Android/iOS hosts, then add reviewed app settings.

Run with an installed stable Flutter SDK. Never edits signing secrets.
"""
import argparse
import plistlib
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ANDROID='http://schemas.android.com/apk/res/android'
TOOLS='http://schemas.android.com/tools'
ET.register_namespace('android',ANDROID)
ET.register_namespace('tools',TOOLS)
NAME='إحكام المسار'
LOCATION='يستخدم إحكام موقعك عند طلب تسجيل الحضور أو الانصراف فقط للتحقق من موقع العمل.'
SIGNING_MARKER='// EHKAM_RELEASE_SIGNING'
SIGNING='''// EHKAM_RELEASE_SIGNING
val ehkamSigning = Properties()
val ehkamKeyFile = rootProject.file("key.properties")
if (ehkamKeyFile.exists()) FileInputStream(ehkamKeyFile).use { ehkamSigning.load(it) }
if (gradle.startParameter.taskNames.any { it.contains("release", ignoreCase = true) } && !ehkamKeyFile.exists()) {
    throw GradleException("Configure android/key.properties with your upload keystore before a release build.")
}

'''
SIGNING_BLOCK='''    signingConfigs {
        create("release") {
            if (ehkamKeyFile.exists()) {
                keyAlias = ehkamSigning.getProperty("keyAlias")
                keyPassword = ehkamSigning.getProperty("keyPassword")
                storeFile = file(ehkamSigning.getProperty("storeFile"))
                storePassword = ehkamSigning.getProperty("storePassword")
            }
        }
    }

'''


def identifier(value):
    if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9]*(?:\.[a-zA-Z][a-zA-Z0-9]*){2,}',value):
        raise ValueError('Use a valid reverse-domain application identifier.')
    return value


def configure(root,android_id,ios_id):
    android_id=identifier(android_id);ios_id=identifier(ios_id)
    manifest=root/'android/app/src/main/AndroidManifest.xml'
    tree=ET.parse(manifest);document=tree.getroot()
    existing={p.get('{'+ANDROID+'}name') for p in document.findall('uses-permission')}
    for permission in ('INTERNET','ACCESS_COARSE_LOCATION','ACCESS_FINE_LOCATION'):
        name='android.permission.'+permission
        if name not in existing:document.insert(0,ET.Element('uses-permission',{'{'+ANDROID+'}name':name}))
    app=document.find('application')
    if app is None:raise ValueError('Generated Android application element is missing.')
    app.set('{'+ANDROID+'}label',NAME);app.set('{'+ANDROID+'}allowBackup','false');app.set('{'+ANDROID+'}usesCleartextTraffic','false')
    tree.write(manifest,encoding='utf-8',xml_declaration=True)
    debug=root/'android/app/src/debug/AndroidManifest.xml'
    debug.parent.mkdir(parents=True,exist_ok=True)
    debug_tree=ET.parse(debug) if debug.exists() else ET.ElementTree(ET.Element('manifest'))
    debug_app=debug_tree.getroot().find('application')
    if debug_app is None:debug_app=ET.SubElement(debug_tree.getroot(),'application')
    debug_app.set('{'+ANDROID+'}usesCleartextTraffic','true');debug_app.set('{'+TOOLS+'}replace','android:usesCleartextTraffic')
    debug_tree.write(debug,encoding='utf-8',xml_declaration=True)
    gradle=root/'android/app/build.gradle.kts';text=gradle.read_text()
    old=re.search(r'namespace\s*=\s*"([^"]+)"',text)
    if not old:raise ValueError('Expected the official Flutter Kotlin Gradle template.')
    old_package=old[1]
    text=re.sub(r'(namespace\s*=\s*)"[^"]+"',lambda m:m[1]+'"'+android_id+'"',text)
    text=re.sub(r'(applicationId\s*=\s*)"[^"]+"',lambda m:m[1]+'"'+android_id+'"',text)
    text=text.replace('minSdk = flutter.minSdkVersion','minSdk = maxOf(flutter.minSdkVersion, 23)')
    if SIGNING_MARKER not in text:
        expected='signingConfig = signingConfigs.getByName("debug")'
        if expected not in text:raise ValueError('Custom signing already exists; review it manually rather than replacing it.')
        text='import java.util.Properties\nimport java.io.FileInputStream\n\n'+text.replace('android {',SIGNING+'android {',1).replace('    buildTypes {',SIGNING_BLOCK+'    buildTypes {',1).replace(expected,'signingConfig = signingConfigs.getByName("release")')
    gradle.write_text(text)
    for source in list((root/'android/app/src').rglob('MainActivity.kt')):
        value=source.read_text().replace('package '+old_package,'package '+android_id)
        # Kotlin source root is found explicitly to support arbitrary namespace depth.
        base=next((parent for parent in source.parents if parent.name=='kotlin'),None)
        if base is None:raise ValueError('Kotlin source root is missing.')
        target=base/Path(*android_id.split('.'))/'MainActivity.kt';target.parent.mkdir(parents=True,exist_ok=True);target.write_text(value)
        if target!=source:source.unlink()
    info=root/'ios/Runner/Info.plist'
    with info.open('rb') as f:values=plistlib.load(f)
    values['CFBundleDisplayName']=NAME;values['NSLocationWhenInUseUsageDescription']=LOCATION
    with info.open('wb') as f:plistlib.dump(values,f,sort_keys=False)
    entitlements=root/'ios/Runner/Ehkam.entitlements'
    if not entitlements.exists():
        with entitlements.open('wb') as f:plistlib.dump({'keychain-access-groups':[]},f)
    pbx=root/'ios/Runner.xcodeproj/project.pbxproj';project=pbx.read_text()
    project=re.sub(r'PRODUCT_BUNDLE_IDENTIFIER = ([^;]+);',lambda m:'PRODUCT_BUNDLE_IDENTIFIER = '+ios_id+('.RunnerTests' if m[1].strip().endswith('.RunnerTests') else '')+';',project)
    def build_settings(match):
        block=match[0]
        if 'PRODUCT_BUNDLE_IDENTIFIER' in block and '.RunnerTests' not in block and 'CODE_SIGN_ENTITLEMENTS' not in block:
            block=block.replace('buildSettings = {','buildSettings = {\n\t\t\t\tCODE_SIGN_ENTITLEMENTS = Runner/Ehkam.entitlements;',1)
        return block
    project=re.sub(r'buildSettings = \{.*?\n\t\t\t\};',build_settings,project,flags=re.S)
    pbx.write_text(project)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--android-id',default='com.ehkam.portal')
    parser.add_argument('--ios-id',default='com.ehkam.portal')
    parser.add_argument('--configure-only',action='store_true')
    args=parser.parse_args();root=Path(__file__).resolve().parents[1]
    identifier(args.android_id);identifier(args.ios_id)
    if not args.configure_only:
        flutter=shutil.which('flutter')
        if not flutter:parser.error('Install Flutter stable (3.41+ / Dart 3.11+) and add flutter to PATH first.')
        subprocess.run([flutter,'create','--platforms','android,ios','--project-name','ehkam_mobile','--org','com.ehkam','--no-pub','.'],cwd=root,check=True)
    configure(root,args.android_id,args.ios_id)
    if not args.configure_only:subprocess.run(['flutter','pub','get'],cwd=root,check=True)
    print('Android/iOS hosts configured. Review bundle IDs, signing, icons and privacy before release.')


if __name__=='__main__':main()

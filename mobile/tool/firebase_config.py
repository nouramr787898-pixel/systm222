#!/usr/bin/env python3
"""Validate supplied Firebase clients and configure existing Flutter hosts in place.

This tool never creates a Firebase project, native host, signing key or service account.
"""
import argparse
import hashlib
import json
import plistlib
import re
from pathlib import Path

MARKER='// Derived from the supplied Firebase client configuration by tool/firebase_config.py.'
GOOGLE_SERVICES_VERSION='4.4.4'


def load(root):
    android=json.loads((root/'android/app/google-services.json').read_text())
    ios=plistlib.loads((root/'ios/Runner/GoogleService-Info.plist').read_bytes())
    info=android['project_info'];clients=android['client']
    if len(clients)!=1:raise ValueError('Expected one Android client; select the intended client explicitly before configuration.')
    client=clients[0];package=client['client_info']['android_client_info']['package_name']
    project=info['project_id'];sender=str(info['project_number'])
    if project!=ios.get('PROJECT_ID') or sender!=str(ios.get('GCM_SENDER_ID')):raise ValueError('Android and iOS Firebase project/sender mismatch.')
    if ios.get('IS_GCM_ENABLED') is not True:raise ValueError('The supplied iOS client does not enable GCM/FCM.')
    options={
        'android':{'apiKey':client['api_key'][0]['current_key'],'appId':client['client_info']['mobilesdk_app_id'],'messagingSenderId':sender,'projectId':project},
        'ios':{'apiKey':ios['API_KEY'],'appId':ios['GOOGLE_APP_ID'],'messagingSenderId':sender,'projectId':project,'iosBundleId':ios['BUNDLE_ID']},
    }
    for platform,data in options.items():
        if not all(isinstance(value,str) and value for value in data.values()):raise ValueError('Missing Firebase client fields.')
        if not re.fullmatch(r'1:'+re.escape(sender)+':'+platform+r':[A-Za-z0-9]+',data['appId']):raise ValueError('Firebase app ID does not match its platform/sender.')
    if info.get('storage_bucket'):options['android']['storageBucket']=info['storage_bucket']
    if ios.get('STORAGE_BUCKET'):options['ios']['storageBucket']=ios['STORAGE_BUCKET']
    return {'project':project,'android_id':package,'ios_id':ios['BUNDLE_ID'],'options':options}


def dart_options(config):
    # JSON double-quoted strings also work in Dart after escaping interpolation.
    quote=lambda value:json.dumps(value,ensure_ascii=True).replace('$',r'\$')
    platforms=[]
    for name,data in config['options'].items():
        fields='\n'.join('    '+key+': '+quote(value)+',' for key,value in data.items())
        platforms.append('  static const '+name+' = FirebaseOptions(\n'+fields+'\n  );')
    return MARKER+'''
import 'package:firebase_core/firebase_core.dart';
import 'package:flutter/foundation.dart' show defaultTargetPlatform, kIsWeb, TargetPlatform;

class DefaultFirebaseOptions {
  static FirebaseOptions get currentPlatform {
    if (!kIsWeb && defaultTargetPlatform == TargetPlatform.android) return android;
    if (!kIsWeb && defaultTargetPlatform == TargetPlatform.iOS) return ios;
    throw UnsupportedError('Firebase is configured for Android and iOS only.');
  }

'''+ '\n\n'.join(platforms)+'\n}\n'


def add_plugin(text,line):
    if 'plugins {' not in text:raise ValueError('Expected the official Flutter Kotlin plugins block.')
    return text.replace('plugins {','plugins {\n    '+line,1)


def ios_project(text):
    name='GoogleService-Info.plist'
    ids=[hashlib.sha256(('ehkam.firebase.'+kind).encode()).hexdigest()[:24].upper() for kind in ('file','resource')]
    file_id,build_id=ids
    group=re.search(r'([A-F0-9]{24}) /\* Runner \*/ = \{\s*isa = PBXGroup;.*?\n\s*\};',text,re.S)
    target=re.search(r'([A-F0-9]{24}) /\* Runner \*/ = \{\s*isa = PBXNativeTarget;.*?\n\s*\};',text,re.S)
    if not group or not target:raise ValueError('Expected the official Runner group and application target.')
    phase=re.search(r'([A-F0-9]{24}) /\* Resources \*/',target[0])
    if not phase:raise ValueError('Runner resources phase is missing.')
    resource=re.search(re.escape(phase[1])+r' /\* Resources \*/ = \{\s*isa = PBXResourcesBuildPhase;.*?\n\s*\};',text,re.S)
    if not resource or 'files = (' not in resource[0] or 'children = (' not in group[0]:raise ValueError('Unsupported Xcode resource structure.')
    if name in text:
        references=re.findall(r'([A-F0-9]{24}) /\* GoogleService-Info\.plist \*/ = \{[^}]*isa = PBXFileReference;[^}]*\};',text)
        for reference in references:
            build=re.search(r'([A-F0-9]{24}) /\* GoogleService-Info\.plist in Resources \*/ = \{[^}]*fileRef = '+reference+r'[^}]*\};',text)
            if build and build[1] in resource[0]:return text
        raise ValueError('Existing Firebase plist reference is incomplete; review Runner resources.')
    if any(value in text for value in ids):raise ValueError('Xcode resource identifier collision.')
    group_new=group[0].replace('children = (','children = (\n\t\t\t\t'+file_id+' /* '+name+' */,',1)
    resource_new=resource[0].replace('files = (','files = (\n\t\t\t\t'+build_id+' /* '+name+' in Resources */,',1)
    text=text.replace(group[0],group_new,1).replace(resource[0],resource_new,1)
    for section,entry in [
        ('PBXFileReference',f'\t\t{file_id} /* {name} */ = {{isa = PBXFileReference; lastKnownFileType = text.plist.xml; path = "{name}"; sourceTree = "<group>"; }};\n'),
        ('PBXBuildFile',f'\t\t{build_id} /* {name} in Resources */ = {{isa = PBXBuildFile; fileRef = {file_id} /* {name} */; }};\n'),
    ]:
        marker='/* End '+section+' section */'
        if marker not in text:raise ValueError('Missing Xcode '+section+' section.')
        text=text.replace(marker,entry+marker,1)
    return text


def plan(root,config):
    """Validate everything first; return planned writes without mutating any file."""
    writes={};pending=[]
    output=root/'lib/firebase_options.dart'
    if output.exists() and not output.read_text().startswith(MARKER):raise ValueError('Custom firebase_options.dart exists; refusing to overwrite it.')
    writes[output]=dart_options(config).encode()
    gradle=root/'android/app/build.gradle.kts';settings=root/'android/settings.gradle.kts'
    if gradle.exists():
        if not settings.exists() or not (root/'android/app/src/main/AndroidManifest.xml').exists():raise ValueError('Android host is incomplete: settings or manifest is missing.')
        app=gradle.read_text();setting=settings.read_text()
        actual=re.search(r'applicationId\s*=\s*"([^"]+)"',app)
        if not actual or actual[1]!=config['android_id']:raise ValueError('Android applicationId differs from the supplied Firebase client.')
        if 'com.google.gms.google-services' not in setting:
            setting=add_plugin(setting,'id("com.google.gms.google-services") version "'+GOOGLE_SERVICES_VERSION+'" apply false')
        if 'com.google.gms.google-services' not in app:app=add_plugin(app,'id("com.google.gms.google-services")')
        writes[settings]=setting.encode();writes[gradle]=app.encode()
    else:
        if settings.exists() or (root/'android/app/src/main/AndroidManifest.xml').exists():raise ValueError('Android host is incomplete: app Gradle file is missing.')
        pending.append('Android host not generated (Gradle and manifest absent).')
    pbx=root/'ios/Runner.xcodeproj/project.pbxproj';info=root/'ios/Runner/Info.plist'
    if pbx.exists():
        if not info.exists():raise ValueError('iOS host is incomplete: Info.plist is missing.')
        project=pbx.read_text();values=plistlib.loads(info.read_bytes())
        identifiers=re.findall(r'PRODUCT_BUNDLE_IDENTIFIER = ([^;]+);',project)
        if not identifiers or any(value.strip().strip('"') not in (config['ios_id'],config['ios_id']+'.RunnerTests') for value in identifiers):raise ValueError('iOS bundle ID differs from the supplied Firebase client.')
        if values.get('FirebaseAppDelegateProxyEnabled') is False:raise ValueError('Custom Firebase delegate proxy is disabled; manual APNs forwarding must be reviewed.')
        project=ios_project(project)
        modes=values.setdefault('UIBackgroundModes',[])
        if 'remote-notification' not in modes:modes.append('remote-notification')
        entitlements=root/'ios/Runner/Ehkam.entitlements'
        ent=plistlib.loads(entitlements.read_bytes()) if entitlements.exists() else {}
        # Xcode resolves the build setting per configuration; signing credentials are not generated here.
        if ent.get('aps-environment') not in (None,'$(APS_ENVIRONMENT)'):raise ValueError('Custom APNs environment exists; review it before automated configuration.')
        ent['aps-environment']='$(APS_ENVIRONMENT)'
        def build_config(match):
            block=match[0]
            if 'PRODUCT_BUNDLE_IDENTIFIER' not in block or '.RunnerTests' in block:return block
            mode='development' if re.search(r'name = Debug;',block) else 'production'
            if 'APS_ENVIRONMENT =' not in block:block=block.replace('buildSettings = {','buildSettings = {\n\t\t\t\tAPS_ENVIRONMENT = '+mode+';',1)
            if 'CODE_SIGN_ENTITLEMENTS =' not in block:block=block.replace('buildSettings = {','buildSettings = {\n\t\t\t\tCODE_SIGN_ENTITLEMENTS = Runner/Ehkam.entitlements;',1)
            return block
        project=re.sub(r'buildSettings = \{.*?\n\s*\};\s*name = [^;]+;',build_config,project,flags=re.S)
        if 'APS_ENVIRONMENT =' not in project:raise ValueError('Could not identify Runner signing configurations.')
        writes[pbx]=project.encode();writes[info]=plistlib.dumps(values,sort_keys=False);writes[entitlements]=plistlib.dumps(ent,sort_keys=False)
    else:
        if info.exists():raise ValueError('iOS host is incomplete: Xcode project is missing.')
        pending.append('iOS host not generated (Xcode project and Info.plist absent).')
    return writes,pending


def configure(root,check=False,require_hosts=False):
    config=load(root);writes,pending=plan(root,config)
    if require_hosts and pending:raise ValueError('Native hosts are required: '+' '.join(pending))
    if check:
        if any(not path.exists() or path.read_bytes()!=data for path,data in writes.items()):raise ValueError('Firebase configuration is not applied; run tool/firebase_config.py first.')
    else:
        for path,data in writes.items():
            if not path.exists() or path.read_bytes()!=data:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    return config,pending


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true');parser.add_argument('--require-hosts',action='store_true')
    args=parser.parse_args();root=Path(__file__).resolve().parents[1]
    try:config,pending=configure(root,args.check,args.require_hosts)
    except (ValueError,KeyError,OSError,plistlib.InvalidFileException) as error:parser.error(str(error))
    print('Firebase client configuration verified for project '+config['project']+'.')
    for message in pending:print('PENDING: '+message)
    print('No device delivery test was performed. APNs and server sending credentials are separate prerequisites.')


if __name__=='__main__':main()

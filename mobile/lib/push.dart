import 'dart:async';
import 'dart:io';
import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'api.dart';

/// Notification payloads never grant access: details always come from the authenticated API.
class PhoneNotifications {
  final ApiClient api;
  final void Function(bool opened) updated;
  final List<StreamSubscription<dynamic>> _subscriptions=[];
  bool _disposed=false;
  bool _ready=false;
  Future<void>? _initializing;
  bool _allowed=false;
  String get _preferenceKey=>'ehkam.push.disabled.${api.origin.host}';
  String _token='';
  PhoneNotifications(this.api,this.updated);

  Future<void> _initialize()=>_initializing??=_setup().catchError((Object e){_initializing=null;throw e;});

  Future<void> _setup() async {
    if(_ready)return;
    if(Firebase.apps.isEmpty)await Firebase.initializeApp();
    if(_disposed)return;
    _ready=true;
    _subscriptions.add(FirebaseMessaging.instance.onTokenRefresh.listen((token){
      if(!_disposed&&_allowed)unawaited(_register(token).catchError((Object _){}));
    }));
    _subscriptions.add(FirebaseMessaging.onMessage.listen((message){
      if(!_disposed&&_allowed)updated(false);
    }));
    _subscriptions.add(FirebaseMessaging.onMessageOpenedApp.listen((message){
      if(!_disposed)updated(true);
    }));
    // In foreground Android uses an in-app confirmation; iOS uses the same UI to avoid duplicates.
    await FirebaseMessaging.instance.setForegroundNotificationPresentationOptions(alert:false,badge:false,sound:false);
    final initial=await FirebaseMessaging.instance.getInitialMessage();
    if(initial!=null&&!_disposed)updated(true);
  }

  Future<void> _register(String token) async {
    if(_disposed||!_allowed)return;
    await api.call('/mobile/devices',body:{'token':token,'platform':Platform.isIOS?'ios':'android'});
    if(!_disposed)_token=token;
  }

  Future<void> restore() async {
    try {
      if(await api.storage.read(key:_preferenceKey)=='true')return;
      await _initialize();
      final settings=await FirebaseMessaging.instance.getNotificationSettings();
      if(settings.authorizationStatus==AuthorizationStatus.authorized||settings.authorizationStatus==AuthorizationStatus.provisional){
        _allowed=true;
        final token=await FirebaseMessaging.instance.getToken();
        if(token!=null)await _register(token);
      }
    } catch(_) {/* Missing provider configuration must not prevent signing in. */}
  }

  Future<String> enable() async {
    try {
      await _initialize();
      final settings=await FirebaseMessaging.instance.requestPermission(alert:true,badge:true,sound:true);
      if(settings.authorizationStatus!=AuthorizationStatus.authorized&&settings.authorizationStatus!=AuthorizationStatus.provisional){
        return 'الإشعارات غير مسموحة. يمكنك السماح بها من إعدادات الهاتف.';
      }
      _allowed=true;
      await api.storage.delete(key:_preferenceKey);
      final token=await FirebaseMessaging.instance.getToken();
      if(token==null)return 'تعذر تجهيز إشعارات الهاتف. جرّب مرة أخرى بعد اكتمال إعداد الخدمة.';
      await _register(token);
      return 'تم تسجيل الجهاز للإشعارات. وصولها يتطلب تفعيل خدمة الإرسال على السيرفر.';
    } catch(_) {return 'إشعارات الهاتف لم تكتمل تهيئتها بعد. إشعارات حسابك متاحة داخل التطبيق.';}
  }

  Future<void> disable() async {
    _allowed=false;
    await api.storage.write(key:_preferenceKey,value:'true');
    try {
      final token=_token.isNotEmpty?_token:(_ready?await FirebaseMessaging.instance.getToken():null);
      if(token!=null)await api.call('/mobile/devices/remove',body:{'token':token});
    } finally {
      if(_ready)await FirebaseMessaging.instance.deleteToken();
      _token='';
    }
  }

  void dispose(){_disposed=true;for(final subscription in _subscriptions){unawaited(subscription.cancel());}_subscriptions.clear();}
}

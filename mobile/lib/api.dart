import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;
import 'domain.dart';

class ApiError implements Exception {
  final String message;final int status;
  const ApiError(this.message,this.status);
  @override String toString()=>message;
}
class ApiClient {
  static const key='ehkam.flutter.session';
  final Uri origin;
  final http.Client client;
  final FlutterSecureStorage storage;
  String _token='';
  VoidCallback? onExpired;
  ApiClient({Uri? origin,http.Client? client,FlutterSecureStorage? storage}):origin=origin??apiOrigin(const String.fromEnvironment('EHKAM_API_URL',defaultValue:'https://systemehkam.online'),development:!kReleaseMode),client=client??http.Client(),storage=storage??const FlutterSecureStorage(iOptions:IOSOptions(accessibility:KeychainAccessibility.unlocked_this_device));
  Future<bool> restore() async {
    final saved=await storage.read(key:key);if(saved==null)return false;
    try {final data=jsonDecode(saved);if(data is Map&&data['origin']==origin.toString()&&data['token'] is String){_token=data['token'];return _token.isNotEmpty;}}catch(_){}
    await clear();return false;
  }
  Future<void> save(String token) async {if(token.length<32)throw const ApiError('رد تسجيل الدخول غير صالح.',0);await storage.write(key:key,value:jsonEncode({'origin':origin.toString(),'token':token}));_token=token;}
  Future<void> clear() async {_token='';await storage.delete(key:key);}
  Future<http.Response> _send(String path,{Object? body}) async {
    if(!path.startsWith('/')||path.startsWith('//'))throw const ApiError('مسار غير صالح.',0);
    final request=http.Request(body==null?'GET':'POST',origin.resolve('/api$path'))..followRedirects=false;
    request.headers['Content-Type']='application/json';
    if(_token.isNotEmpty)request.headers['Authorization']='Bearer $_token';
    if(body!=null)request.body=jsonEncode(body);
    try{return await (() async => http.Response.fromStream(await client.send(request)))().timeout(const Duration(seconds:25));}
    on TimeoutException {throw const ApiError('انتهت مهلة الاتصال. حدّث السجل قبل إعادة الحفظ لتجنب التكرار.',0);}
    on SocketException {throw const ApiError('تعذر الاتصال. تحقق من الإنترنت.',0);}
    on http.ClientException {throw const ApiError('تعذر الاتصال الآمن بالسيستم.',0);}
  }
  Future<void> _check(http.Response response,String path) async {
    if(response.statusCode>=200&&response.statusCode<300)return;
    if(response.statusCode==401&&path!='/mobile/login'){await clear();onExpired?.call();}
    String text='تعذر إكمال الطلب.';
    try{final data=jsonDecode(utf8.decode(response.bodyBytes));if(data is Map&&data['error'] is String)text=data['error'];}catch(_){}
    if(response.isRedirect)text='تحقق من عنوان الموقع الصحيح في إعداد البناء؛ تحويل العنوان غير متاح للجلسات.';
    throw ApiError(text,response.statusCode);
  }
  Future<dynamic> call(String path,{Object? body}) async {final r=await _send(path,body:body);await _check(r,path);try{return jsonDecode(utf8.decode(r.bodyBytes));}catch(_){throw const ApiError('استجابة السيستم غير مكتملة؛ تحقق من نشر تحديث الخادم.',0);}}
  Future<Uint8List> download(int id) async {final path='/attachments/$id';final r=await _send(path);await _check(r,path);return r.bodyBytes;}
  void close()=>client.close();
}

import 'dart:async';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:file_picker/file_picker.dart';
import 'package:geolocator/geolocator.dart';
import 'package:open_filex/open_filex.dart';
import 'package:path_provider/path_provider.dart';
import 'package:url_launcher/url_launcher.dart';
import 'api.dart';
import 'domain.dart';
import 'push.dart';
import 'recovery.dart';

const green=Color(0xff07513e);
final navigatorKey=GlobalKey<NavigatorState>();
void main(){WidgetsFlutterBinding.ensureInitialized();runApp(EhkamApp());}
class EhkamApp extends StatefulWidget {
  final ApiClient? api;
  const EhkamApp({super.key,this.api});
  @override State<EhkamApp> createState()=>_EhkamAppState();
}
class _EhkamAppState extends State<EhkamApp> with WidgetsBindingObserver {
  late final ApiClient api;Json? user;bool boot=true;String error='';
  @override void initState(){super.initState();api=widget.api??ApiClient();WidgetsBinding.instance.addObserver(this);api.onExpired=expire;start();}
  void expire(){navigatorKey.currentState?.popUntil((r)=>r.isFirst);if(mounted)setState((){user=null;error='انتهت الجلسة؛ سجل الدخول مجددًا.';});}
  Future<void> start() async {setState(()=>boot=true);try{if(await api.restore()){final data=await api.call('/me');if(mounted)setState(()=>user=Map<String,dynamic>.from(data as Map));}}catch(e){if(mounted)setState(()=>error=safeError(e));}finally{if(mounted)setState(()=>boot=false);}}
  Future<void> logout() async {try{await api.call('/logout',body:{});}catch(_){}await api.clear();try{final cache=Directory('${(await getTemporaryDirectory()).path}/ehkam-attachments');if(await cache.exists())await cache.delete(recursive:true);}catch(_){}navigatorKey.currentState?.popUntil((r)=>r.isFirst);if(mounted)setState((){user=null;error='';});}
  @override void didChangeAppLifecycleState(AppLifecycleState state){if(state==AppLifecycleState.resumed&&user!=null)api.call('/me').then((data){if(mounted)setState(()=>user=Map<String,dynamic>.from(data as Map));}).catchError((Object e){if(mounted)setState(()=>error=safeError(e));});}
  @override void dispose(){WidgetsBinding.instance.removeObserver(this);api.onExpired=null;api.close();super.dispose();}
  @override Widget build(BuildContext context)=>MaterialApp(navigatorKey:navigatorKey,debugShowCheckedModeBanner:false,title:'إحكام المسار',locale:const Locale('ar'),supportedLocales:const [Locale('ar'),Locale('en')],localizationsDelegates:GlobalMaterialLocalizations.delegates,theme:ThemeData(useMaterial3:true,colorScheme:ColorScheme.fromSeed(seedColor:green),scaffoldBackgroundColor:const Color(0xfff3f7f5),inputDecorationTheme:const InputDecorationTheme(border:OutlineInputBorder(),filled:true,fillColor:Colors.white),appBarTheme:const AppBarTheme(backgroundColor:Colors.white,foregroundColor:green)),home:boot?const Scaffold(body:Center(child:CircularProgressIndicator())):user==null?LoginPage(api:api,error:error,onLogin:(value){if(mounted)setState(()=>user=value);},retry:start):Workspace(api:api,user:user!,logout:logout));
}
Future<void> openWebsite(ApiClient api) async {if(!await launchUrl(api.origin,mode:LaunchMode.externalApplication))throw const ApiError('تعذر فتح الموقع.',0);}
Widget heading(String value)=>Padding(padding:const EdgeInsets.symmetric(vertical:8),child:Text(value,style:const TextStyle(fontSize:23,fontWeight:FontWeight.bold,color:green)));
Widget panel(List<Widget> children)=>Card(child:Padding(padding:const EdgeInsets.all(18),child:Column(crossAxisAlignment:CrossAxisAlignment.stretch,children:children)));
Widget gap()=>const SizedBox(height:12);
Widget errorBox(String error)=>error.isEmpty?const SizedBox.shrink():Container(margin:const EdgeInsets.symmetric(vertical:8),padding:const EdgeInsets.all(12),decoration:BoxDecoration(color:const Color(0xffffeeeb),borderRadius:BorderRadius.circular(10)),child:Text(error,style:const TextStyle(color:Color(0xffa23429))));
Widget action(String title,VoidCallback? onPressed,{bool secondary=false})=>Padding(padding:const EdgeInsets.symmetric(vertical:5),child:secondary?OutlinedButton(onPressed:onPressed,child:Text(title)):FilledButton(onPressed:onPressed,child:Text(title)));
Widget selectField(String title,String value,List<Json> options,ValueChanged<String>? change)=>Padding(padding:const EdgeInsets.symmetric(vertical:8),child:DropdownButtonFormField<String>(initialValue:options.any((o)=>str(o['value'])==value)?value:null,key:ValueKey('$title:$value:${options.length}'),isExpanded:true,decoration:InputDecoration(labelText:title),items:options.map((o)=>DropdownMenuItem(value:str(o['value']),child:Text(str(o['label']),overflow:TextOverflow.ellipsis))).toList(),onChanged:change==null?null:(v){if(v!=null)change(v);}));
Widget input(String label,TextEditingController controller,{bool secret=false,int lines=1,TextInputType? keyboard})=>Padding(padding:const EdgeInsets.symmetric(vertical:8),child:TextField(controller:controller,obscureText:secret,maxLines:lines,keyboardType:keyboard,autocorrect:false,decoration:InputDecoration(labelText:label)));
class LoginPage extends StatefulWidget {
  final ApiClient api;final String error;final ValueChanged<Json> onLogin;final Future<void> Function() retry;
  const LoginPage({super.key,required this.api,required this.error,required this.onLogin,required this.retry});
  @override State<LoginPage> createState()=>_LoginState();
}
class _LoginState extends State<LoginPage> {
  final username=TextEditingController(),password=TextEditingController(),otp=TextEditingController();String role='employee',error='';bool busy=false;
  Future<void> login() async {
    if(username.text.trim().isEmpty||password.text.isEmpty){setState(()=>error='اكتب اسم المستخدم وكلمة المرور.');return;}
    setState((){busy=true;error='';});
    try{final data=Map<String,dynamic>.from(await widget.api.call('/mobile/login',body:{'username':username.text.trim(),'password':password.text,'account_role':role,'otp':westernDigits(otp.text.trim())}) as Map);await widget.api.save(str(data['session_token']));final user=Map<String,dynamic>.from(await widget.api.call('/me') as Map);password.clear();otp.clear();if(mounted)widget.onLogin(user);}catch(e){if(mounted)setState(()=>error=safeError(e));}finally{if(mounted)setState(()=>busy=false);}
  }
  @override void dispose(){username.dispose();password.dispose();otp.dispose();super.dispose();}
  @override Widget build(BuildContext context)=>Scaffold(body:SafeArea(child:Center(child:ConstrainedBox(constraints:const BoxConstraints(maxWidth:520),child:ListView(padding:const EdgeInsets.all(24),shrinkWrap:true,children:[Image.asset('assets/logo.png',height:100),gap(),heading('أهلًا بك في إحكام المسار'),const Text('حسابك وطلباتك وحضورك في مكان واحد'),panel([selectField('نوع الحساب',role,[{'value':'employee','label':'الموظف'},{'value':'manager','label':'المشرف'},{'value':'admin','label':'الإدارة'}],busy?null:(v)=>setState(()=>role=v)),input('اسم المستخدم',username),input('كلمة المرور',password,secret:true),input('رمز المصادقة الثنائية إن كان مفعّلًا',otp,keyboard:TextInputType.number),errorBox(error.isEmpty?widget.error:error),action(busy?'جارٍ الدخول…':'تسجيل الدخول',busy?null:login),action('إعادة الاتصال',busy?null:widget.retry,secondary:true),action('استعادة كلمة المرور',busy?null:()=>Navigator.push(context,MaterialPageRoute<void>(builder:(_)=>RecoveryPage(api:widget.api))),secondary:true),action('فتح الموقع',()=>openWebsite(widget.api),secondary:true)]),Text('متصل بسيستم ${widget.api.origin.host}',textAlign:TextAlign.center)])))));
}

class Workspace extends StatefulWidget {
  final ApiClient api;final Json user;final Future<void> Function() logout;
  const Workspace({super.key,required this.api,required this.user,required this.logout});
  @override State<Workspace> createState()=>_WorkspaceState();
}
class _WorkspaceState extends State<Workspace> {
  late final PhoneNotifications phoneNotifications;
  ApiClient get api=>widget.api;Json get user=>widget.user;
  String tab='home',company='',error='',info='',attendancePerson='',site='';bool loading=true,busy=false;int attendanceVersion=0;
  List<Json> employees=[],companies=[],requests=[],notifications=[],punches=[],sites=[],messages=[],history=[];List<int> actionIds=[];Json dashboard={},leave={};
  final question=TextEditingController(),currentPassword=TextEditingController(),newPassword=TextEditingController();
  List<Json> get people=>scoped(employees,company);
  Json? get mine{for(final e in employees){if(e['id']==user['employee_id'])return e;}return null;}
  @override void initState(){super.initState();phoneNotifications=PhoneNotifications(api,(opened){if(!mounted)return;setState((){if(opened)tab='notifications';info='لديك تحديث جديد في حسابك.';});unawaited(refresh());});unawaited(phoneNotifications.restore());if(user['must_change_password']==true){loading=false;tab='account';}else{refresh();}}
  Future<void> run(Future<void> Function() work) async {if(busy)return;setState((){busy=true;error='';info='';});try{await work();}catch(e){if(mounted)setState(()=>error=safeError(e));}finally{if(mounted)setState(()=>busy=false);}}
  Future<void> refresh() async {
    if(!mounted)return;setState(()=>loading=true);
    try{
      final values=await Future.wait([api.call('/employees'),api.call('/requests?summary=1'),api.call('/dashboard'),api.call('/action-items'),api.call('/notifications'),user['role']=='admin'?api.call('/companies'):Future.value(<Json>[])]);
      final roster=rows(values[0]).where((e)=>integer(e['archived'])==0).toList();
      final cos=user['role']=='admin'?rows(values[5]).where((c)=>integer(c['archived'])==0).toList():{for(final e in roster)integer(e['company_id']):{'id':e['company_id'],'name':e['company_name']}}.values.toList();
      Json balance={};if(user['employee_id']!=null&&!['payroll','auditor'].contains(user['profile'])){try{balance=Map<String,dynamic>.from(await api.call('/leave/${user['employee_id']}') as Map);}catch(_){}}
      if(!mounted)return;setState((){employees=roster;companies=cos;requests=rows(values[1]);dashboard=Map<String,dynamic>.from(values[2] as Map);actionIds=rows(values[3]).map((r)=>integer(r['id'])).toList();notifications=rows(values[4]);leave=balance;if(!companies.any((c)=>str(c['id'])==company))company=companies.length==1?str(companies.first['id']):'';});
      if(tab=='attendance')await loadAttendance();
    }catch(e){if(mounted)setState(()=>error=safeError(e));}finally{if(mounted)setState(()=>loading=false);}
  }
  void changeCompany(String value){if(busy)return;attendanceVersion++;setState((){company=value;history=[];messages=[];question.clear();punches=[];attendancePerson='';error='';info='';});if(tab=='attendance')loadAttendance();}
  Future<void> loadAttendance() async {
    final version=++attendanceVersion;if(!canAttend(user))return;
    final roster=people;
    final id=user['role']=='employee'?integer(user['employee_id']):roster.any((e)=>str(e['id'])==attendancePerson)?integer(attendancePerson):roster.isEmpty?0:integer(roster.first['id']);
    if(id==0){if(mounted)setState(()=>punches=[]);return;}
    try{final data=rows(await api.call('/attendance?employee_id=$id'));final allowed=user['role']=='employee'?rows(await api.call('/attendance-sites')):<Json>[];if(!mounted||version!=attendanceVersion)return;setState((){punches=data;attendancePerson='$id';sites=allowed;if(!sites.any((e)=>str(e['id'])==site))site=sites.isEmpty?'':str(sites.first['id']);});}catch(e){if(mounted&&version==attendanceVersion)setState(()=>error=safeError(e));}
  }
  Future<void> openRequest(Json row) async {
    final changed=await Navigator.of(context).push<bool>(MaterialPageRoute(builder:(_)=>RequestDetail(api:api,user:user,id:integer(row['id']),actionIds:actionIds)));
    if(changed==true)await refresh();
  }
  Future<void> createRequest() async {
    final changed=await Navigator.of(context).push<bool>(MaterialPageRoute(builder:(_)=>RequestForm(api:api,user:user,employees:employees,company:company)));
    if(changed==true){await refresh();if(mounted)setState(()=>info='تم إرسال الطلب؛ تابع حالته من قائمة الطلبات.');}
  }
  Future<void> ask([String? supplied]) async {
    final text=(supplied??question.text).trim();if(text.isEmpty||company.isEmpty||text.length>1200)return;
    await run(() async{final data=Map<String,dynamic>.from(await api.call('/assistant',body:{'question':text,'history':history,'company_id':integer(company)}) as Map);if(!mounted)return;setState((){messages=[...messages,{'role':'user','text':text},{'role':'assistant','text':data['answer'],'choices':data['choices']}];history=recentHistory([...history,{'role':'user','content':text},{'role':'assistant','content':str(data['answer']).substring(0,str(data['answer']).length.clamp(0,4000).toInt())}]);question.clear();});});
  }
  Future<void> punch(String action) async {
    if(site.isEmpty){setState(()=>error='اختر موقع البصمة.');return;}
    final confirmed=await showDialog<bool>(context:context,builder:(c)=>AlertDialog(title:Text(action=='check_in'?'تسجيل الحضور':'تسجيل الانصراف'),content:const Text('سيُستخدم موقعك الحالي للتحقق من نطاق موقع العمل.'),actions:[TextButton(onPressed:()=>Navigator.pop(c,false),child:const Text('إلغاء')),FilledButton(onPressed:()=>Navigator.pop(c,true),child:const Text('تأكيد'))]));
    if(confirmed!=true)return;
    await run(() async{if(!await Geolocator.isLocationServiceEnabled())throw const ApiError('فعّل خدمات الموقع ثم حاول مجددًا.',0);var permission=await Geolocator.checkPermission();if(permission==LocationPermission.denied)permission=await Geolocator.requestPermission();if(permission==LocationPermission.denied||permission==LocationPermission.deniedForever)throw const ApiError('اسمح للتطبيق بالوصول إلى الموقع أثناء الاستخدام من إعدادات الجهاز.',0);final position=await Geolocator.getCurrentPosition(locationSettings:const LocationSettings(accuracy:LocationAccuracy.high,timeLimit:Duration(seconds:20)));if(!position.accuracy.isFinite||position.accuracy>100)throw const ApiError('دقة الموقع غير كافية. جرّب من مكان مفتوح.',0);final out=await api.call('/attendance',body:{'action':action,'site_id':integer(site),'latitude':position.latitude,'longitude':position.longitude,'accuracy':position.accuracy});if(mounted)setState(()=>info=str(out['message']));await loadAttendance();});
  }
  void switchTab(String value){if(busy)return;setState((){tab=value;error='';info='';});if(tab=='attendance')loadAttendance();}
  List<Widget> home(){final own=mine;return [heading('يوم عمل موفق'),if(own!=null)panel([heading(str(own['name'])),Text('${own['employee_no']} · ${own['company_name']}'),Text('${own['job']} · ${own['department']}'),if(leave.isNotEmpty)Text('رصيد الإجازات المتاح: ${leave['available']} يوم',style:const TextStyle(fontWeight:FontWeight.bold))]),Wrap(spacing:10,runSpacing:10,children:[for(final item in [['الموظفون المتاحون','employees'],['الطلبات','requests'],['قيد المراجعة','pending'],['الحضور اليوم','attendance_today']])SizedBox(width:155,child:panel([Text(item[0]),Text(str(dashboard[item[1]]),style:const TextStyle(fontSize:27,fontWeight:FontWeight.bold,color:green))]))]),panel([heading('متابعة اليوم'),Text('طلبات تحتاج إجراءً منك: ${actionIds.length}'),action('فتح الطلبات',()=>switchTab('requests')),action('الإشعارات',()=>switchTab('notifications'),secondary:true)])];}
  List<Widget> directory()=>[heading('موظفو الشركة'),if(company.isEmpty)const Text('اختر الشركة لعرض الموظفين المتاحين لحسابك.'),if(company.isNotEmpty&&people.isEmpty)const Text('لا يوجد موظفون متاحون في هذه الشركة.'),for(final e in people)panel([heading(str(e['name'])),Text('${e['employee_no']} · ${e['status']}'),Text('${e['job']} · ${e['department']}'),Text('العقد: ${dateText(e['contract_start'])} — ${dateText(e['contract_end'])}'),if(e.containsKey('gross_salary'))Text('إجمالي الراتب المسجل: ${e['gross_salary']} ريال')])];
  List<Widget> requestList()=>[heading('الطلبات'),if(creationTypes(user).isNotEmpty)action('طلب جديد',busy||company.isEmpty?null:createRequest),if(company.isEmpty)const Text('اختر الشركة أولًا.'),for(final r in scoped(requests,company))InkWell(onTap:()=>openRequest(r),child:panel([Row(children:[Expanded(child:Text(requestNames[r['type']]??str(r['type']),style:const TextStyle(fontWeight:FontWeight.bold,fontSize:17))),Chip(label:Text(statusNames[r['status']]??str(r['status'])))]),Text('${r['employee_name']} · ${r['employee_no']}'),Text('EH-${str(r['id']).padLeft(6,'0')} · ${dateText(str(r['created_at']).split('T').first)}'),const Text('عرض التفاصيل ←',style:TextStyle(color:green))])),if(company.isNotEmpty&&scoped(requests,company).isEmpty)const Text('لا توجد طلبات متاحة.')];
  List<Widget> attendance()=>[heading('الحضور والانصراف'),if(user['role']!='employee'&&company.isNotEmpty)selectField('الموظف',attendancePerson,people.map((e)=>{'value':str(e['id']),'label':e['name']}).toList(),busy?null:(v){setState(()=>attendancePerson=v);loadAttendance();}),if(user['role']=='employee')panel([selectField('موقع البصمة',site,sites.map((e)=>{'value':str(e['id']),'label':e['name']}).toList(),busy?null:(v)=>setState(()=>site=v)),const Text('يُستخدم موقعك عند طلب تسجيل البصمة فقط. لا يوجد تتبع في الخلفية.'),action('تسجيل الحضور',busy||site.isEmpty?null:()=>punch('check_in')),action('تسجيل الانصراف',busy||site.isEmpty?null:()=>punch('check_out'),secondary:true)]),panel([const Text('إجمالي مدة الحضور المكتملة'),Text(durationText(punches.fold<int>(0,(n,r)=>n+integer(r['duration_seconds']))),style:const TextStyle(fontSize:27,color:green,fontWeight:FontWeight.bold))]),for(final r in punches)panel([heading(dateText(r['day'])),Text('الحضور: ${timeText(r['check_in'])}'),Text('الانصراف: ${timeText(r['check_out'])}'),Text('المدة: ${durationText(integer(r['duration_seconds']))}'),if(r['check_in']!=null&&r['check_out']==null)const Text('لم يسجل الانصراف بعد؛ المدة غير مكتملة.'),if(r['check_in_site']!=null)Text('موقع الحضور: ${r['check_in_site']}')])];
  List<Widget> assistant()=>[heading('مساعد إحكام'),const Text('اسأل عن سجلات الشركة المختارة: الموظفين والحضور والطلبات والأعمال غير المكتملة.'),for(final m in messages)Container(margin:const EdgeInsets.symmetric(vertical:7),child:panel([Text(m['role']=='user'?'أنت':'مساعد إحكام',style:const TextStyle(fontWeight:FontWeight.bold)),SelectableText(assistantText(str(m['text']))),for(final e in rows(m['choices']))action('${e['name']} (${e['employee_no']})',busy?null:()=>ask(str(e['employee_no'])),secondary:true)])),input('سؤالك',question,lines:3),action(busy?'جارٍ قراءة السجلات…':'إرسال السؤال',busy||company.isEmpty?null:()=>ask()),action('محادثة جديدة',busy?null:()=>setState((){messages=[];history=[];question.clear();}),secondary:true)];
  List<Widget> notificationList()=>[heading('الإشعارات'),action('تعليم الكل كمقروء',busy?null:()=>run(() async{await api.call('/notifications/read',body:{});await refresh();}),secondary:true),if(notifications.isEmpty)const Text('لا توجد إشعارات.'),for(final n in notifications)panel([Text('${n['read_at']==null?'●':'✓'} ${n['message']}'),Text(dateText(str(n['created_at']).split('T').first)),if(n['request_id']!=null)action('تفاصيل الطلب',()=>openRequest({'id':n['request_id']}),secondary:true)])];
  List<Widget> account()=>[heading('حسابي'),panel([heading('إشعارات الهاتف'),const Text('تنبيهات الطلبات وتحديثاتها والحضور والانصراف. فتح الصفحات لا يرسل إشعارًا.'),action('تفعيل إشعارات الهاتف',busy?null:()=>run(() async{final result=await phoneNotifications.enable();if(mounted)setState(()=>info=result);})),action('إيقاف إشعارات هذا الجهاز',busy?null:()=>run(() async{await phoneNotifications.disable();if(mounted)setState(()=>info='تم إيقاف إشعارات هذا الجهاز.');}),secondary:true)]),panel([Text(str(user['username'])),const Text('تُحفظ جلسة الجهاز في التخزين الآمن، وتنتهي وفق سياسة السيستم.')]),if(user['must_change_password']==true)errorBox('غيّر كلمة المرور المطلوبة قبل استخدام بقية الأقسام.'),if(user['role']!='employee')panel([heading('تغيير كلمة المرور'),input('كلمة المرور الحالية',currentPassword,secret:true),input('كلمة المرور الجديدة (8 أحرف على الأقل)',newPassword,secret:true),action('تغيير كلمة المرور',busy?null:()=>run(() async{await api.call('/security/password',body:{'current_password':currentPassword.text,'new_password':newPassword.text});currentPassword.clear();newPassword.clear();await widget.logout();}))])else const Text('تغيير كلمة مرور الموظف بواسطة الإدارة أو المشرف المختص.'),action('فتح الموقع لجميع الإجراءات المتقدمة',()=>openWebsite(api),secondary:true),action('تسجيل الخروج من هذا الجهاز',busy?null:widget.logout),Text('إصدار تجريبي 0.1.1 · ${api.origin.host}')];
  @override void dispose(){phoneNotifications.dispose();question.dispose();currentPassword.dispose();newPassword.dispose();super.dispose();}
  @override Widget build(BuildContext context){
    final selected=user['must_change_password']==true?'account':tab;
    final content=switch(selected){'directory'=>directory(),'requests'=>requestList(),'attendance'=>attendance(),'assistant'=>assistant(),'notifications'=>notificationList(),'account'=>account(),_=>home()};
    final tabs=<String,String>{'home':'الرئيسية','directory':'الموظفون','requests':'الطلبات',if(canAttend(user))'attendance':'الحضور','assistant':'المساعد','notifications':'الإشعارات','account':'الحساب'};
    return Scaffold(appBar:AppBar(title:Text('إحكام · ${user['username']}'),actions:[IconButton(tooltip:'فتح الموقع',onPressed:()=>openWebsite(api),icon:const Icon(Icons.language))]),body:RefreshIndicator(onRefresh:refresh,child:ListView(padding:const EdgeInsets.all(18),children:[errorBox(error),if(info.isNotEmpty)Container(padding:const EdgeInsets.all(12),color:const Color(0xffe1f2e8),child:Text(info)),if(loading)const LinearProgressIndicator(),if(['directory','requests','attendance','assistant'].contains(selected)&&!(selected=='attendance'&&user['role']=='employee'))selectField('الشركة',company,[{'value':'','label':'اختر الشركة أولًا'},...companies.map((c)=>{'value':str(c['id']),'label':c['name']})],busy?null:changeCompany),...content,if(busy)const Padding(padding:EdgeInsets.all(14),child:Center(child:CircularProgressIndicator()))])),bottomNavigationBar:user['must_change_password']==true?null:SafeArea(child:SingleChildScrollView(scrollDirection:Axis.horizontal,child:Row(children:[for(final entry in tabs.entries)Padding(padding:const EdgeInsets.all(5),child:ChoiceChip(label:Text(entry.value),selected:tab==entry.key,onSelected:busy?null:(_)=>switchTab(entry.key)))]))));
  }
}

class RequestDetail extends StatefulWidget {
  final ApiClient api;final Json user;final int id;final List<int> actionIds;
  const RequestDetail({super.key,required this.api,required this.user,required this.id,required this.actionIds});
  @override State<RequestDetail> createState()=>_RequestDetailState();
}
class _RequestDetailState extends State<RequestDetail> {
  Json? detail;String responsible='',error='';bool busy=false;final note=TextEditingController();
  @override void initState(){super.initState();load();}
  Future<void> load() async {try{final values=await Future.wait([widget.api.call('/requests/${widget.id}'),widget.api.call('/request-progress?request_id=${widget.id}')]);if(mounted)setState((){detail=Map<String,dynamic>.from(values[0] as Map);responsible=str(values[1]['responsible']);});}catch(e){if(mounted)setState(()=>error=safeError(e));}}
  Future<void> decide(String status) async {
    if(busy)return;if(['rejected','declined'].contains(status)&&note.text.trim().isEmpty){setState(()=>error='اكتب سبب الرفض.');return;}
    final yes=await showDialog<bool>(context:context,builder:(c)=>AlertDialog(title:const Text('تأكيد القرار'),content:Text('هل تريد ${statusNames[status]??status} هذا الطلب؟'),actions:[TextButton(onPressed:()=>Navigator.pop(c,false),child:const Text('إلغاء')),FilledButton(onPressed:()=>Navigator.pop(c,true),child:const Text('تأكيد'))]));if(yes!=true||!mounted)return;
    setState(()=>busy=true);try{await widget.api.call('/requests/${widget.id}',body:{'status':status,'note':note.text,'attachments':[]});if(mounted)Navigator.pop(context,true);}catch(e){if(mounted)setState(()=>error=safeError(e));}finally{if(mounted)setState(()=>busy=false);}
  }
  Future<void> openAttachment(Json data) async {if(busy)return;setState(()=>busy=true);try{final bytes=await widget.api.download(integer(data['id']));final dir=Directory('${(await getTemporaryDirectory()).path}/ehkam-attachments');await dir.create(recursive:true);final name=str(data['name']).replaceAll(RegExp(r'[^a-zA-Z0-9._-]'),'_');final file=File('${dir.path}/${data['id']}-$name');await file.writeAsBytes(bytes,flush:true);final out=await OpenFilex.open(file.path);if(out.type!=ResultType.done)throw const ApiError('تعذر فتح المرفق على الجهاز.',0);}catch(e){if(mounted)setState(()=>error=safeError(e));}finally{if(mounted)setState(()=>busy=false);}}
  @override void dispose(){note.dispose();super.dispose();}
  @override Widget build(BuildContext context){final r=detail;final ownDelegation=r!=null&&widget.user['role']=='employee'&&widget.user['employee_id']==r['employee_id']&&r['type']=='delegation'&&['new','review'].contains(r['status']);return Scaffold(appBar:AppBar(title:const Text('تفاصيل الطلب')),body:ListView(padding:const EdgeInsets.all(18),children:[errorBox(error),if(r==null&&error.isEmpty)const Center(child:CircularProgressIndicator()),if(r!=null)...[panel([heading('${requestNames[r['type']]??r['type']} · ${statusNames[r['status']]??r['status']}'),Text('${r['employee_name']} · ${r['employee_no']}'),Text(str(r['company_name'])),Text('${dateText(r['start_date'])} — ${dateText(r['end_date'])}'),Text(str(r['notes'])),Text('الإجراء عند: $responsible')]),for(final f in rows(r['attachments']))action('فتح المرفق: ${f['name']}',busy?null:()=>openAttachment(f),secondary:true),for(final c in rows(r['comments']))panel([Text(str(c['username']),style:const TextStyle(fontWeight:FontWeight.bold)),Text(str(c['message']))]),if(canDecide(widget.user,r,widget.actionIds)||ownDelegation)panel([input('ملاحظات القرار (سبب الرفض إلزامي)',note,lines:3),action('قبول',busy?null:()=>decide(ownDelegation?'accepted':'approved')),action('رفض',busy?null:()=>decide(ownDelegation?'declined':'rejected'),secondary:true)])],if(busy)const Center(child:CircularProgressIndicator())]));}
}

class RequestForm extends StatefulWidget {
  final ApiClient api;final Json user;final List<Json> employees;final String company;
  const RequestForm({super.key,required this.api,required this.user,required this.employees,required this.company});
  @override State<RequestForm> createState()=>_RequestFormState();
}
class _RequestFormState extends State<RequestForm> {
  late Json draft;final fields=<String,TextEditingController>{};List<Json> attachments=[];String error='';bool busy=false;
  @override void initState(){super.initState();draft=blankRequest(widget.user);for(final key in ['title','notes','start_date','end_date','amount','installment','last_work_day','destination']){fields[key]=TextEditingController(text:str(draft[key]));}chooseType(str(draft['type']),initial:true);}
  List<Json> get targets {
    if(draft['type']=='hr_contact'&&widget.user['role']!='admin'||widget.user['role']=='employee')return widget.employees.where((e)=>e['id']==widget.user['employee_id']).toList();
    final people=scoped(widget.employees,widget.company);
    return widget.user['role']=='manager'?people.where((e)=>e['manager_id']==widget.user['employee_id']).toList():people;
  }
  void chooseType(String value,{bool initial=false}){void change(){draft['type']=value;final options=targets;if(!options.any((e)=>e['id']==draft['employee_id']))draft['employee_id']=options.isEmpty?0:options.first['id'];}if(initial){change();}else{setState(change);}}
  Widget field(String key,String label,{bool numeric=false,int lines=1})=>input(label,fields[key]!,lines:lines,keyboard:numeric?TextInputType.number:null);
  Future<void> pick() async {
    if(busy)return;setState(()=>busy=true);
    try{final files=await FilePicker.pickFiles(type:FileType.custom,allowedExtensions:['pdf','png','jpg','jpeg']);if(attachments.length+files.length>3)throw const FormatException('الحد الأقصى ثلاثة مرفقات.');final added=<Json>[];for(final file in files){final size=await file.length();if(size==null||size==0||size>2*1024*1024)throw const FormatException('كل مرفق يجب أن يكون حتى 2 ميجابايت.');final bytes=await file.readAsBytes();if(bytes.length>2*1024*1024)throw const FormatException('الملف يتجاوز الحد المسموح.');final name=file.name;final extension=name.split('.').last.toLowerCase();final mime=switch(extension){'pdf'=>'application/pdf','png'=>'image/png','jpg'||'jpeg'=>'image/jpeg',_=>throw const FormatException('صيغة الملف غير مدعومة.')};added.add({'name':name,'mime':mime,'data':attachmentData(bytes)});}if(mounted)setState(()=>attachments.addAll(added));}catch(e){if(mounted)setState(()=>error=safeError(e));}finally{if(mounted)setState(()=>busy=false);}
  }
  Future<void> pickTime(String key,String label) async {final parts=str(draft[key]).split(':');final time=await showTimePicker(context:context,helpText:label,initialTime:TimeOfDay(hour:integer(parts.first),minute:parts.length>1?integer(parts[1]):0),builder:(context,child)=>MediaQuery(data:MediaQuery.of(context).copyWith(alwaysUse24HourFormat:false),child:child!));if(time!=null&&mounted)setState(()=>draft[key]='${time.hour.toString().padLeft(2,'0')}:${time.minute.toString().padLeft(2,'0')}');}
  Future<void> send() async {
    if(busy)return;setState((){busy=true;error='';});
    try{for(final entry in fields.entries){draft[entry.key]=entry.value.text;}final target=integer(draft['employee_id']);if(!targets.any((e)=>integer(e['id'])==target))throw const FormatException('اختر الموظف من القائمة.');final employee=targets.firstWhere((e)=>integer(e['id'])==target);final data=requestPayload(draft,attachments);await widget.api.call('/requests',body:{...data,'selection_company_id':employee['company_id']});if(mounted)Navigator.pop(context,true);}catch(e){if(mounted)setState(()=>error=safeError(e));}finally{if(mounted)setState(()=>busy=false);}
  }
  @override void dispose(){for(final field in fields.values){field.dispose();}super.dispose();}
  @override Widget build(BuildContext context){final type=str(draft['type']);final dated=['annual_leave','sick_leave','unpaid_leave','permission','punch_fix','delegation'].contains(type);return Scaffold(appBar:AppBar(title:const Text('طلب جديد')),body:ListView(padding:const EdgeInsets.all(18),children:[selectField('نوع الطلب',type,creationTypes(widget.user).map((t)=>{'value':t,'label':requestNames[t]}).toList(),busy?null:chooseType),if(widget.user['role']!='employee')selectField('الموظف',str(draft['employee_id']),targets.map((e)=>{'value':str(e['id']),'label':e['name']}).toList(),busy?null:(v)=>setState(()=>draft['employee_id']=integer(v))),if(type=='open')field('title','عنوان الطلب'),if(dated)...[field('start_date','من تاريخ (يوم/شهر/سنة)'),field('end_date','إلى تاريخ (يوم/شهر/سنة)')],if(type=='advance')...[field('amount','مبلغ السلفة',numeric:true),field('installment','القسط المقترح',numeric:true)],if(type=='permission')...[action('بداية الاستئذان: ${timeText(draft['time_from'])}',busy?null:()=>pickTime('time_from','بداية الاستئذان'),secondary:true),action('نهاية الاستئذان: ${timeText(draft['time_to'])}',busy?null:()=>pickTime('time_to','نهاية الاستئذان'),secondary:true)],if(type=='delegation')field('destination','وجهة الانتداب'),if(type=='termination')field('last_work_day','آخر يوم عمل مقترح (يوم/شهر/سنة)'),field('notes','التفاصيل والملاحظات',lines:4),for(var i=0;i<attachments.length;i++)ListTile(title:Text(str(attachments[i]['name'])),trailing:IconButton(onPressed:busy?null:(){final index=i;setState(()=>attachments.removeAt(index));},icon:const Icon(Icons.close))),action('إضافة مرفقات PDF أو صور (حتى 2 ميجابايت لكل ملف)',busy?null:pick,secondary:true),errorBox(error),action(busy?'جارٍ الإرسال…':'إرسال الطلب',busy?null:send),action('إلغاء',busy?null:()=>Navigator.pop(context),secondary:true)]));}
}

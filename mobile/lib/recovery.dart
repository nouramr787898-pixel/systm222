import 'package:flutter/material.dart';
import 'api.dart';
import 'domain.dart';

class RecoveryPage extends StatefulWidget {
  final ApiClient api;
  const RecoveryPage({super.key, required this.api});
  @override
  State<RecoveryPage> createState() => _RecoveryState();
}

class _RecoveryState extends State<RecoveryPage> {
  final username = TextEditingController(),
      code = TextEditingController(),
      password = TextEditingController(),
      confirm = TextEditingController();
  String channel = 'email', grant = '', error = '', info = '';
  int step = 0;
  bool busy = false;
  Future<void> next() async {
    if (busy) return;
    setState(() {
      busy = true;
      error = '';
      info = '';
    });
    try {
      if (step == 0) {
        if (username.text.trim().isEmpty)
          throw const ApiError('اكتب اسم المستخدم.', 0);
        await widget.api.call(
          '/password/request',
          body: {'username': username.text.trim(), 'channel': channel},
        );
        if (mounted)
          setState(() {
            step = 1;
            info = 'إذا كان الحساب مسجلًا بوسيلة التحقق المختارة فسيصلك الرمز.';
          });
      } else if (step == 1) {
        final data = await widget.api.call(
          '/password/verify',
          body: {
            'username': username.text.trim(),
            'channel': channel,
            'code': westernDigits(code.text.trim()),
          },
        );
        grant = str(data['reset_token']);
        if (grant.isEmpty) throw const ApiError('تعذر التحقق من الرمز.', 0);
        if (mounted) setState(() => step = 2);
      } else {
        final length = password.text.runes.length;
        if (length < 8 || length > 128)
          throw const ApiError(
            'كلمة المرور من 8 إلى 128 حرفًا. الحروف وحدها أو الحروف والأرقام مسموحة.',
            0,
          );
        if (password.text != confirm.text)
          throw const ApiError('تأكيد كلمة المرور غير مطابق.', 0);
        await widget.api.call(
          '/password/confirm',
          body: {
            'username': username.text.trim(),
            'reset_token': grant,
            'new_password': password.text,
          },
        );
        password.clear();
        confirm.clear();
        grant = '';
        if (mounted) setState(() => step = 3);
      }
    } catch (e) {
      if (mounted) setState(() => error = safeError(e));
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  void dispose() {
    username.dispose();
    code.dispose();
    password.dispose();
    confirm.dispose();
    super.dispose();
  }

  Widget field(
    String label,
    TextEditingController control, {
    bool secret = false,
  }) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 8),
    child: TextField(
      controller: control,
      obscureText: secret,
      autocorrect: false,
      enableSuggestions: !secret,
      decoration: InputDecoration(labelText: label),
    ),
  );
  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('استعادة كلمة المرور')),
    body: SafeArea(
      child: ListView(
        padding: const EdgeInsets.all(24),
        children: [
          if (step == 0) ...[
            field('اسم المستخدم', username),
            DropdownButtonFormField<String>(
              initialValue: channel,
              decoration: const InputDecoration(
                labelText: 'وسيلة التحقق المسجلة في حسابك',
              ),
              items: const [
                DropdownMenuItem(
                  value: 'email',
                  child: Text('البريد الإلكتروني'),
                ),
                DropdownMenuItem(value: 'whatsapp', child: Text('واتساب')),
              ],
              onChanged: busy
                  ? null
                  : (value) {
                      if (value != null) setState(() => channel = value);
                    },
            ),
          ],
          if (step == 1) ...[
            const Text(
              'أدخل رمز التحقق الذي وصلك. إذا انتهت صلاحيته ارجع وابدأ الاستعادة مجددًا.',
            ),
            field('رمز التحقق', code),
          ],
          if (step == 2) ...[
            const Text(
              'اختر كلمة مرور من 8 أحرف على الأقل؛ يمكن استخدام حروف فقط أو حروف وأرقام، دون اشتراط رموز.',
            ),
            field('كلمة المرور الجديدة', password, secret: true),
            field('تأكيد كلمة المرور', confirm, secret: true),
          ],
          if (step == 3)
            const Text(
              'تم تغيير كلمة المرور. ارجع لتسجيل الدخول بكلمة المرور الجديدة.',
            ),
          if (info.isNotEmpty) Text(info),
          if (error.isNotEmpty)
            Text(error, style: const TextStyle(color: Colors.red)),
          const SizedBox(height: 16),
          if (step < 3)
            FilledButton(
              onPressed: busy ? null : next,
              child: Text(
                busy
                    ? 'جارٍ التحقق…'
                    : step == 0
                    ? 'إرسال رمز التحقق'
                    : step == 1
                    ? 'تحقق من الرمز'
                    : 'حفظ كلمة المرور',
              ),
            ),
          if (step == 3)
            FilledButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('العودة لتسجيل الدخول'),
            ),
        ],
      ),
    ),
  );
}

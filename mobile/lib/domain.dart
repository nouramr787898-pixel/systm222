import 'dart:convert';

typedef Json = Map<String, dynamic>;
String str(Object? value) => value?.toString() ?? '';
String westernDigits(String value) => value.replaceAllMapped(
  RegExp('[٠-٩]'),
  (m) => '${'٠١٢٣٤٥٦٧٨٩'.indexOf(m[0]!)}',
);
int integer(Object? value) =>
    value is num ? value.toInt() : int.tryParse(str(value)) ?? 0;
List<Json> rows(Object? value) => value is List
    ? value.map((e) => Map<String, dynamic>.from(e as Map)).toList()
    : [];
const requestNames = <String, String>{
  'open': 'طلب مفتوح',
  'annual_leave': 'إجازة سنوية',
  'sick_leave': 'إجازة مرضية',
  'unpaid_leave': 'إجازة غير مدفوعة',
  'advance': 'سلفة',
  'permission': 'استئذان',
  'punch_fix': 'تصحيح بصمة',
  'hr_contact': 'تواصل مع الموارد البشرية',
  'termination': 'إنهاء تعاقد',
  'delegation': 'انتداب',
  'investigation': 'تحقيق',
};
const statusNames = <String, String>{
  'new': 'قيد المراجعة',
  'review': 'تحت المراجعة',
  'approved': 'معتمد',
  'rejected': 'مرفوض',
  'manager_approved': 'بانتظار الإدارة',
  'cancelled': 'ملغي',
  'closed': 'مغلق',
  'accepted': 'مقبول',
  'declined': 'مرفوض',
  'withdrawn': 'مسحوب',
};
Uri apiOrigin(String value, {bool development = false}) {
  final uri = Uri.parse(value);
  final local = RegExp(
    r'^(localhost|127\.0\.0\.1|10\.(?:\d{1,3}\.){2}\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})$',
  ).hasMatch(uri.host);
  if (uri.host.isEmpty ||
      uri.userInfo.isNotEmpty ||
      uri.hasQuery ||
      uri.hasFragment ||
      !['', '/'].contains(uri.path) ||
      !(uri.scheme == 'https' || development && uri.scheme == 'http' && local))
    throw const FormatException(
      'عنوان السيستم يجب أن يكون HTTPS دون مسار أو بيانات دخول.',
    );
  return Uri.parse(uri.origin);
}

String timeText(Object? value) {
  final text = str(value);
  if (text.isEmpty) return '—';
  final m = RegExp(r'^(\d{2}):(\d{2})(?::(\d{2}))?$').firstMatch(text);
  if (m == null) return text;
  final hour = int.parse(m[1]!);
  return '${(hour % 12 == 0 ? 12 : hour % 12).toString().padLeft(2, '0')}:${m[2]}${m[3] == null ? '' : ':${m[3]}'} ${hour < 12 ? 'ص' : 'م'}';
}

String dateText(Object? value) {
  final text = str(value);
  if (text.isEmpty) return '—';
  return text.replaceFirstMapped(
    RegExp(r'^(\d{4})-(\d{2})-(\d{2})'),
    (m) => '${m[3]}/${m[2]}/${m[1]}',
  );
}

String durationText(num seconds) {
  final n = seconds.isFinite ? seconds.floor().clamp(0, 1 << 50) : 0;
  return [
    n ~/ 3600,
    n ~/ 60 % 60,
    n % 60,
  ].map((v) => '$v'.padLeft(2, '0')).join(':');
}

String isoDate(String value) {
  if (value.trim().isEmpty) return '';
  var text = westernDigits(value.trim());
  final m = RegExp(r'^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$').firstMatch(text);
  if (m != null)
    text = '${m[3]}-${m[2]!.padLeft(2, '0')}-${m[1]!.padLeft(2, '0')}';
  final date = DateTime.tryParse('${text}T00:00:00Z');
  if (!RegExp(r'^\d{4}-\d{2}-\d{2}$').hasMatch(text) ||
      date == null ||
      date.toIso8601String().substring(0, 10) != text)
    throw const FormatException('اكتب تاريخًا صحيحًا بصيغة يوم/شهر/سنة.');
  return text;
}

List<Json> scoped(List<Json> values, String company) => company.isEmpty
    ? []
    : values.where((r) => str(r['company_id']) == company).toList();
bool canAttend(Json u) => !['payroll', 'auditor'].contains(u['profile']);
List<String> creationTypes(Json u) {
  if (u['role'] == 'manager') return ['delegation', 'hr_contact'];
  if (['payroll', 'auditor'].contains(u['profile'])) return [];
  return [
    'open',
    'annual_leave',
    'sick_leave',
    'unpaid_leave',
    'advance',
    'permission',
    'punch_fix',
    'hr_contact',
    'termination',
  ].where((t) => u['profile'] != 'hr' || t != 'advance').toList();
}

bool canDecide(Json u, Json r, List<int> actions) =>
    ['new', 'review'].contains(r['status']) &&
    u['employee_id'] != r['employee_id'] &&
    actions.contains(integer(r['id'])) &&
    ['admin', 'manager'].contains(u['role']) &&
    u['profile'] != 'auditor';
List<Json> recentHistory(List<Json> input) {
  var history = input.length > 6
      ? input.sublist(input.length - 6)
      : List<Json>.from(input);
  while (history.fold<int>(0, (n, r) => n + str(r['content']).length) > 12000) {
    history = history.sublist(2);
  }
  return history;
}

Json blankRequest(Json u) => {
  'type': creationTypes(u).isEmpty ? 'open' : creationTypes(u).first,
  'employee_id': u['employee_id'] ?? 0,
  'title': '',
  'notes': '',
  'start_date': '',
  'end_date': '',
  'amount': '',
  'installment': '',
  'time_from': '09:00',
  'time_to': '10:00',
  'last_work_day': '',
  'destination': '',
};
Json requestPayload(Json draft, List<Json> attachments) {
  final type = str(draft['type']);
  if (attachments.length > 3)
    throw const FormatException('الحد الأقصى ثلاثة مرفقات.');
  if (str(draft['notes']).length > 4000)
    throw const FormatException('الملاحظات لا تتجاوز 4000 حرف.');
  final dated = [
    'annual_leave',
    'sick_leave',
    'unpaid_leave',
    'permission',
    'punch_fix',
    'delegation',
  ].contains(type);
  final start = dated ? isoDate(str(draft['start_date'])) : '';
  final end = dated
      ? isoDate(
          str(draft['end_date']).isEmpty
              ? str(draft['start_date'])
              : str(draft['end_date']),
        )
      : '';
  if (dated && (start.isEmpty || end.isEmpty))
    throw const FormatException('حدد البداية والنهاية.');
  if (start.isNotEmpty && end.compareTo(start) < 0)
    throw const FormatException('تاريخ النهاية يسبق البداية.');
  if (['open', 'hr_contact'].contains(type) &&
      str(draft['notes']).trim().isEmpty &&
      str(draft['title']).trim().isEmpty)
    throw const FormatException('اكتب تفاصيل الطلب.');
  if (type == 'sick_leave' && attachments.isEmpty)
    throw const FormatException('أرفق المستند الطبي.');
  final amount = num.tryParse(
    str(draft['amount']).isEmpty
        ? '0'
        : westernDigits(
            str(draft['amount']),
          ).replaceAll('٫', '.').replaceAll('٬', ''),
  );
  final installment = num.tryParse(
    str(draft['installment']).isEmpty
        ? '0'
        : westernDigits(
            str(draft['installment']),
          ).replaceAll('٫', '.').replaceAll('٬', ''),
  );
  if (type == 'advance' &&
      (amount == null ||
          !amount.isFinite ||
          amount <= 0 ||
          installment == null ||
          !installment.isFinite ||
          installment < 0))
    throw const FormatException('راجع مبلغ السلفة والقسط.');
  return {
    ...draft,
    'start_date': start,
    'end_date': end,
    'amount': amount ?? 0,
    'installment': installment ?? 0,
    'attachments': attachments,
    'last_day_fraction': 1,
    'correction_type': 'both',
    'termination_type': 'resignation',
    'last_work_day': type == 'termination'
        ? isoDate(str(draft['last_work_day']))
        : '',
  };
}

String safeError(Object error) => error is FormatException
    ? str(error.message)
    : error.toString().replaceFirst(RegExp(r'^Exception: '), '');
String attachmentData(List<int> bytes) => base64Encode(bytes);

String assistantText(String value) => value.replaceAllMapped(
  RegExp(r'(الساعة |الانصراف: |الدوام من | إلى )(\d{2}:\d{2}(?::\d{2})?)'),
  (m) => '${m[1]}${timeText(m[2])}',
);

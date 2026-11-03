import 'dart:convert';
import 'package:ehkam_mobile/api.dart';
import 'package:ehkam_mobile/main.dart';
import 'package:ehkam_mobile/recovery.dart';
import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

void main() {
  setUp(() => FlutterSecureStorage.setMockInitialValues({}));

  testWidgets(
    'Arabic login opens without a session and links to native recovery',
    (tester) async {
      final api = ApiClient(
        client: MockClient((request) async {
          fail('Login must not make a network request before submission.');
        }),
      );
      await tester.pumpWidget(EhkamApp(api: api));
      await tester.pumpAndSettle();
      expect(find.text('تسجيل الدخول'), findsOneWidget);
      expect(
        Directionality.of(tester.element(find.text('تسجيل الدخول'))),
        TextDirection.rtl,
      );
      await tester.ensureVisible(find.text('استعادة كلمة المرور'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('استعادة كلمة المرور'));
      await tester.pumpAndSettle();
      expect(find.text('إرسال رمز التحقق'), findsOneWidget);
      await tester.pumpWidget(const SizedBox.shrink());
    },
  );

  testWidgets(
    'Recovery requires OTP verification and matching eight-character passwords',
    (tester) async {
      final paths = <String>[];
      final api = ApiClient(
        client: MockClient((request) async {
          paths.add(request.url.path);
          if (request.url.path.endsWith('/password/verify')) {
            return http.Response(
              jsonEncode({'reset_token': 'test-grant'}),
              200,
            );
          }
          if (request.url.path.endsWith('/password/confirm')) {
            final body = jsonDecode(request.body) as Map;
            expect(body['reset_token'], 'test-grant');
            expect(body['new_password'], 'abcdefgh');
          }
          return http.Response('{"ok":true}', 200);
        }),
      );
      await tester.pumpWidget(MaterialApp(home: RecoveryPage(api: api)));
      await tester.enterText(find.byType(TextField).first, 'review_employee');
      await tester.tap(find.text('إرسال رمز التحقق'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField).first, '123456');
      await tester.tap(find.text('تحقق من الرمز'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField).at(0), 'abcdefg');
      await tester.enterText(find.byType(TextField).at(1), 'abcdefg');
      await tester.tap(find.text('حفظ كلمة المرور'));
      await tester.pumpAndSettle();
      expect(paths.where((p) => p.endsWith('/password/confirm')), isEmpty);
      await tester.enterText(find.byType(TextField).at(0), 'abcdefgh');
      await tester.enterText(find.byType(TextField).at(1), 'abcdefgh');
      await tester.tap(find.text('حفظ كلمة المرور'));
      await tester.pumpAndSettle();
      expect(paths, [
        '/api/password/request',
        '/api/password/verify',
        '/api/password/confirm',
      ]);
      expect(find.text('العودة لتسجيل الدخول'), findsOneWidget);
      await tester.pumpWidget(const SizedBox.shrink());
      api.close();
    },
  );
}

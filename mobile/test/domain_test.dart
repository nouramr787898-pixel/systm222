import 'package:flutter_test/flutter_test.dart';
import 'package:ehkam_mobile/domain.dart';

void main() {
  test('production origin rejects HTTP and credential-bearing URLs', () {
    expect(apiOrigin('https://example.com/').toString(), 'https://example.com');
    for (final value in [
      'http://example.com',
      'https://name:pass@example.com',
      'https://example.com/api',
      'https://example.com?token=a',
    ]) {
      expect(() => apiOrigin(value), throwsFormatException);
    }
    expect(
      apiOrigin('http://10.0.2.2:8765', development: true).host,
      '10.0.2.2',
    );
  });
  test('12-hour time, Arabic dates and precise durations', () {
    expect(timeText('20:19:01'), '08:19:01 م');
    expect(timeText('00:00:00'), '12:00:00 ص');
    expect(durationText(1), '00:00:01');
    expect(durationText(144001), '40:00:01');
    expect(isoDate('٠٣/١١/٢٠٢٦'), '2026-11-03');
    expect(() => isoDate('31/02/2026'), throwsFormatException);
  });
  test('company scope and approval affordances', () {
    expect(
      scoped([
        {'company_id': 1, 'id': 1},
        {'company_id': 2, 'id': 2},
      ], '1'),
      [
        {'company_id': 1, 'id': 1},
      ],
    );
    final u = <String, dynamic>{
      'id': 1,
      'role': 'admin',
      'profile': 'owner',
      'employee_id': null,
    };
    final r = <String, dynamic>{'id': 9, 'employee_id': 2, 'status': 'new'};
    expect(canDecide(u, r, [9]), isTrue);
    expect(canDecide({...u, 'employee_id': 2}, r, [9]), isFalse);
    expect(canDecide({...u, 'profile': 'auditor'}, r, [9]), isFalse);
  });
  test('history remains paired and bounded', () {
    final history = List<Json>.generate(
      8,
      (i) => {
        'role': i.isEven ? 'user' : 'assistant',
        'content': List.filled(i.isEven ? 1200 : 4000, 'x').join(),
      },
    );
    final result = recentHistory(history);
    expect(result.length <= 6, isTrue);
    expect(result.length.isEven, isTrue);
    expect(
      result.fold<int>(0, (n, r) => n + str(r['content']).length) <= 12000,
      isTrue,
    );
  });
  test('requests reject reversed dates and missing medical documents', () {
    final draft =
        blankRequest({
          'role': 'employee',
          'profile': 'employee',
          'employee_id': 2,
        })..addAll({
          'type': 'annual_leave',
          'start_date': '03/11/2026',
          'end_date': '04/11/2026',
        });
    expect(requestPayload(draft, [])['start_date'], '2026-11-03');
    expect(
      () => requestPayload({...draft, 'end_date': '02/11/2026'}, []),
      throwsFormatException,
    );
    expect(
      () => requestPayload({...draft, 'type': 'sick_leave'}, []),
      throwsFormatException,
    );
  });
}

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kite_clipper_flutter/time_entry.dart';

void main() {
  test('時刻をミリ秒へ変換し不正形式を拒否', () {
    expect(parseClipTime('00:02.500'), 2500);
    expect(parseClipTime('01:02.5'), 62500);
    expect(parseClipTime('100:00'), 6000000);
    for (final value in ['-01:00', '00:60', 'abc', '', '00:01.1234']) {
      expect(parseClipTime(value), isNull);
    }
  });
  test('境界・逆転・最短区間を検証', () {
    expect(
        validateClipTime(0, start: true, other: 100, duration: 1000), isNull);
    expect(validateClipTime(1000, start: false, other: 900, duration: 1000),
        isNull);
    expect(validateClipTime(950, start: false, other: 900, duration: 1000),
        isNotNull);
    expect(validateClipTime(1001, start: false, other: 0, duration: 1000),
        isNotNull);
    expect(validateClipTime(900, start: true, other: 800, duration: 1000),
        isNotNull);
  });
  testWidgets('不正入力は保持し、修正後の適用値を返す', (tester) async {
    int? result;
    await tester.pumpWidget(MaterialApp(
        theme: ThemeData(splashFactory: InkRipple.splashFactory),
        home: Builder(
            builder: (context) => Scaffold(
                body: TextButton(
                    onPressed: () async {
                      result = await showDialog<int>(
                          context: context,
                          builder: (_) => const TimeEntryDialog(
                              start: true,
                              initial: '00:00.000',
                              other: 5000,
                              duration: 10000));
                    },
                    child: const Text('開く'))))));
    await tester.tap(find.text('開く'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField), '00:06.000');
    await tester.tap(find.text('適用'));
    await tester.pumpAndSettle();
    expect(find.byType(TimeEntryDialog), findsOneWidget);
    expect(result, isNull);
    await tester.enterText(find.byType(TextField), '00:02.500');
    await tester.tap(find.text('適用'));
    await tester.pumpAndSettle();
    expect(result, 2500);
  });
}

import 'dart:async';
import 'package:flutter_test/flutter_test.dart';
import 'package:kite_clipper_flutter/preview_readiness.dart';

void main() {
  test('初回の描画準備完了より前に動画を開かない', () async {
    final ready = Completer<void>();
    var opened = false;
    final task = openPreparedPreview(ready: ready.future,
      isActive: () => true, open: () async { opened = true; });
    await Future<void>.delayed(Duration.zero);
    expect(opened, isFalse);
    ready.complete();
    await task;
    expect(opened, isTrue);
  });
  test('待機中に画面が破棄された場合は開かない', () async {
    var opened = false;
    await openPreparedPreview(ready: Future.value(), isActive: () => false,
      open: () async { opened = true; });
    expect(opened, isFalse);
  });
  test('準備失敗やタイムアウトを読み込み成功として扱わない', () async {
    var opened = false;
    await expectLater(openPreparedPreview(ready: Completer<void>().future,
      timeout: const Duration(milliseconds: 10), isActive: () => true,
      open: () async { opened = true; }), throwsA(isA<TimeoutException>()));
    expect(opened, isFalse);
  });
}

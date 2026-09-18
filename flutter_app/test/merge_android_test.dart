import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kite_clipper_flutter/batch_plan.dart';
import 'package:kite_clipper_flutter/clip_session.dart';
import 'package:kite_clipper_flutter/export_service.dart';
import 'package:kite_clipper_flutter/merge_service.dart';

/// Android側の呼び出しを差し替えて、渡す内容と結果の扱いだけを確かめる。
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  final messenger =
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;

  List<BatchEntry> entries() {
    final session = ClipSession();
    final items = session.add(['a.mp4', 'b.mp4']);
    for (final item in items) {
      item.setDuration(10000);
      item.setRange(1000, 3000);
    }
    return BatchPlan.from(session.items, includeSaved: true).entries;
  }

  tearDown(() =>
      messenger.setMockMethodCallHandler(ExportService.channel, null));

  test('区間を順に渡し、保存先のURIを返す', () async {
    Map<Object?, Object?>? sent;
    messenger.setMockMethodCallHandler(ExportService.channel, (call) async {
      expect(call.method, 'merge');
      sent = call.arguments as Map<Object?, Object?>;
      return 'content://out/kite.mp4';
    });
    final outcome = await MergeService().mergeAndroid(
        entries: entries(), now: DateTime(2026, 9, 18, 7, 8, 9));
    expect(outcome.output, 'content://out/kite.mp4');
    expect(outcome.lossless, isTrue);
    expect(sent!['name'], 'kite_merged_20260918_070809.mp4');
    expect(sent!['parts'], [
      {'source': 'a.mp4', 'startMs': 1000, 'endMs': 3000},
      {'source': 'b.mp4', 'startMs': 1000, 'endMs': 3000},
    ]);
  });

  test('保存先を選ばずに閉じたら中止として扱う', () async {
    messenger.setMockMethodCallHandler(
        ExportService.channel, (call) async => null);
    await expectLater(MergeService().mergeAndroid(entries: entries()),
        throwsA(isA<MergeCancelled>()));
  });

  test('形式がそろわない場合はネイティブの理由をそのまま伝える', () async {
    messenger.setMockMethodCallHandler(ExportService.channel,
        (call) async => throw PlatformException(
            code: 'merge', message: '形式がそろっていません：video/avc 1920x1080 と video/avc 1280x720'));
    await expectLater(
        MergeService().mergeAndroid(entries: entries()),
        throwsA(isA<MergeUnsupported>()
            .having((e) => e.message, 'message', contains('形式がそろっていません'))));
  });

  test('2区間未満は呼び出さない', () async {
    var called = false;
    messenger.setMockMethodCallHandler(ExportService.channel, (call) async {
      called = true;
      return null;
    });
    await expectLater(MergeService().mergeAndroid(entries: entries().take(1).toList()),
        throwsArgumentError);
    expect(called, isFalse);
  });
}

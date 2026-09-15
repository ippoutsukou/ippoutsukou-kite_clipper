import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kite_clipper_flutter/clip_session.dart';
import 'package:kite_clipper_flutter/clip_list.dart';

void main() {
  test('A→B→Aで各動画の範囲・保存結果を保持', () {
    final session = ClipSession();
    final clips = session.add(['A.mp4', 'B.mp4']);
    session.select(clips[0]);
    clips[0].setDuration(10000);
    clips[0].setRange(2000, 6000);
    clips[0].output = 'saved-A.mp4';
    session.select(clips[1]);
    clips[1].setDuration(20000);
    clips[1].setRange(3000, 14000);
    clips[1].error = 'Bのエラー';
    session.select(clips[0]);
    clips[0].setDuration(10000);
    expect(session.active!.startMs, 2000);
    expect(session.active!.endMs, 6000);
    expect(session.active!.output, 'saved-A.mp4');
    expect(session.active!.error, isNull);
    expect(clips[1].startMs, 3000);
  });
  test('重複を除いて追加順を維持し同名の別パスは残す', () {
    final session = ClipSession(caseInsensitivePaths: true);
    session.add(['C:\\a\\sample.mp4']);
    final added =
        session.add(['c:/A/sample.mp4', 'C:/b/sample.mp4', 'C:/b/sample.mp4']);
    expect(added.length, 1);
    expect(session.items.length, 2);
  });
  test('上限超過を部分追加せず取り消す・キャンセルは無変更', () {
    final session = ClipSession();
    session.add(List.generate(29, (i) => '$i.mp4'));
    session.select(session.items.first);
    expect(() => session.add(['new1.mp4', 'new2.mp4']), throwsStateError);
    expect(session.items.length, 29);
    expect(session.add([]), isEmpty);
    expect(session.active, same(session.items.first));
  });
  test('前の動画と画面破棄後の非同期結果を拒否', () {
    final session = ClipSession();
    final items = session.add(['a', 'b']);
    final old = session.select(items[0]);
    final current = session.select(items[1]);
    expect(session.accepts(old, items[0]), isFalse);
    expect(session.accepts(current, items[1]), isTrue);
    session.invalidate();
    expect(session.accepts(current, items[1]), isFalse);
  });
  test('未確認の長さを推測せず素材の短縮に安全に対応', () {
    final item = ClipItem('a');
    expect(item.durationMs, isNull);
    expect(item.startMs, isNull);
    item.setDuration(10000);
    item.setRange(2000, 9000);
    item.setDuration(1000);
    expect(item.startMs, 0);
    expect(item.endMs, 1000);
    expect(() => item.setRange(1000, 900), throwsArgumentError);
  });
  testWidgets('30件一覧はスクロール可能・読み込み中は切替不可', (tester) async {
    final session = ClipSession()
      ..add(List.generate(30, (i) => 'sample_$i.mp4'));
    ClipItem? selected;
    Widget ui(bool enabled) => MaterialApp(
        theme: ThemeData(splashFactory: InkRipple.splashFactory),
        home: Scaffold(
            body: SizedBox(
                width: 320,
                height: 400,
                child: ClipList(
                    items: session.items,
                    selected: session.items.first,
                    enabled: enabled,
                    onSelect: (item) => selected = item))));
    await tester.pumpWidget(ui(false));
    await tester.tap(find.text('1. sample_0.mp4'));
    expect(selected, isNull);
    await tester.pumpWidget(ui(true));
    await tester.tap(find.text('2. sample_1.mp4'));
    await tester.pumpAndSettle();
    expect(selected, same(session.items[1]));
    await tester.scrollUntilVisible(find.text('30. sample_29.mp4'), 250);
    expect(tester.takeException(), isNull);
  });
}

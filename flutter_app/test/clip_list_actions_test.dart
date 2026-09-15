import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kite_clipper_flutter/clip_session.dart';
import 'package:kite_clipper_flutter/clip_list.dart';

void main() {
  test('上下移動は選択・範囲・保存結果を維持し端では変化しない', () {
    final session = ClipSession();
    final clips = session.add(['a.mp4', 'b.mp4', 'c.mp4']);
    final ticket = session.select(clips[1]);
    clips[1].setDuration(5000); clips[1].setRange(1000, 3000);
    clips[1].output = 'saved.mp4';
    session.move(clips[1], -1);
    expect(session.items, [clips[1], clips[0], clips[2]]);
    session.move(clips[1], -1);
    expect(session.items.first, clips[1]);
    session.move(clips[1], 1);
    expect(session.items, clips);
    session.move(clips[2], 1);
    expect(session.items, clips);
    expect(session.active, clips[1]);
    expect(session.accepts(ticket, clips[1]), isTrue);
    expect(clips[1].startMs, 1000); expect(clips[1].endMs, 3000);
    expect(clips[1].output, 'saved.mp4');
  });
  test('非選択項目を外しても選択と世代を維持', () {
    final session = ClipSession();
    final items = session.add(['a', 'b', 'c']);
    final ticket = session.select(items[1]);
    session.remove(items[0]);
    expect(session.items, [items[1], items[2]]);
    expect(session.active, items[1]);
    expect(session.accepts(ticket, items[1]), isTrue);
  });
  test('選択中を外すと次→前→空へ遷移し旧イベントを拒否', () {
    final session = ClipSession();
    final items = session.add(['a', 'b', 'c']);
    final ticket = session.select(items[1]);
    session.remove(items[1]);
    expect(session.active, items[2]);
    expect(session.accepts(ticket, items[1]), isFalse);
    session.remove(items[2]);
    expect(session.active, items[0]);
    session.remove(items[0]);
    expect(session.active, isNull); expect(session.items, isEmpty);
    expect(session.add(['a']).single.startMs, isNull);
  });
  testWidgets('メニューで端の移動を無効化し移動・取り外しを通知', (tester) async {
    final session = ClipSession()..add(['a.mp4', 'b.mp4']);
    ClipItem? removed;
    Widget ui(bool enabled) => MaterialApp(theme: ThemeData(splashFactory: InkRipple.splashFactory),
      home: Scaffold(body: SizedBox(width: 320, height: 400, child: StatefulBuilder(
        builder: (context, update) => ClipList(items: session.items,
          selected: session.items.first, enabled: enabled, onSelect: (_) {},
          onMove: (item, delta) => update(() => session.move(item, delta)),
          onRemove: (item) => removed = item)))));
    await tester.pumpWidget(ui(true));
    await tester.tap(find.byTooltip('a.mp4 の操作')); await tester.pumpAndSettle();
    expect(tester.widget<PopupMenuItem<String>>(find.widgetWithText(PopupMenuItem<String>, '上へ')).enabled, isFalse);
    await tester.tap(find.text('下へ')); await tester.pumpAndSettle();
    expect(session.items.last.name, 'a.mp4');
    await tester.tap(find.byTooltip('a.mp4 の操作')); await tester.pumpAndSettle();
    await tester.tap(find.text('一覧から外す')); await tester.pumpAndSettle();
    expect(removed?.name, 'a.mp4');
    // メニューは依頼を通知するだけ。確認前にはモデルを変更しない。
    expect(session.items.length, 2);
    await tester.pumpWidget(ui(false));
    expect(tester.widgetList<PopupMenuButton<String>>(find.byType(PopupMenuButton<String>))
      .every((menu) => !menu.enabled), isTrue);
    expect(tester.takeException(), isNull);
  });
}

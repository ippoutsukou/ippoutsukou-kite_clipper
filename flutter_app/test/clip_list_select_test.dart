import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kite_clipper_flutter/clip_list.dart';
import 'package:kite_clipper_flutter/clip_session.dart';

Widget host(ClipSession session,
        {double height = 400,
        bool enabled = true,
        ValueChanged<List<ClipItem>>? onRemoveMany,
        ValueChanged<ClipItem>? onSelect}) =>
    MaterialApp(
        home: Scaffold(
            body: Align(
                alignment: Alignment.topLeft,
                child: SizedBox(
                    width: 360,
                    height: height,
                    child: ClipList(
                        items: session.items,
                        selected: session.items.firstOrNull,
                        enabled: enabled,
                        onSelect: onSelect ?? (_) {},
                        onMove: (_, __) {},
                        onRemove: (_) {},
                        onRemoveMany: onRemoveMany ?? (_) {})))));

void main() {
  testWidgets('上限30本でも見切れず、スクロールで最後まで届く', (tester) async {
    final session = ClipSession()
      ..add([for (var i = 1; i <= ClipSession.maximum; i++) 'v$i.mp4']);
    await tester.pumpWidget(host(session));
    expect(tester.takeException(), isNull);
    expect(find.text('30. v30.mp4'), findsNothing);
    await tester.scrollUntilVisible(find.text('30. v30.mp4'), 200,
        scrollable: find.byType(Scrollable).first);
    expect(find.text('30. v30.mp4'), findsOneWidget);
    // 最下部の案内文も見えたまま。
    expect(find.text('一覧と区間は次回起動時に復元します'), findsOneWidget);
  });

  testWidgets('選んだ動画だけを一覧の順で依頼し、選択中は動画を開かない', (tester) async {
    final session = ClipSession()..add(['a.mp4', 'b.mp4', 'c.mp4']);
    List<ClipItem>? requested;
    var opened = 0;
    await tester.pumpWidget(host(session,
        onRemoveMany: (items) => requested = items,
        onSelect: (_) => opened++));
    await tester.tap(find.text('選択'));
    await tester.pump();
    expect(tester.widget<FilledButton>(find.byType(FilledButton)).onPressed,
        isNull);
    await tester.tap(find.text('3. c.mp4'));
    await tester.tap(find.text('1. a.mp4'));
    await tester.pump();
    expect(opened, 0);
    await tester.tap(find.text('2本を外す'));
    expect(requested?.map((i) => i.name), ['a.mp4', 'c.mp4']);
    // 依頼するだけで一覧は変えない（確認は呼び出し側）。
    expect(session.items, hasLength(3));
  });

  testWidgets('すべて選択・解除と、選択をやめる', (tester) async {
    final session = ClipSession()..add(['a.mp4', 'b.mp4']);
    List<ClipItem>? requested;
    await tester.pumpWidget(
        host(session, onRemoveMany: (items) => requested = items));
    await tester.tap(find.text('選択'));
    await tester.pump();
    await tester.tap(find.byType(Checkbox).first);
    await tester.pump();
    await tester.tap(find.text('2本を外す'));
    expect(requested, hasLength(2));
    await tester.tap(find.byType(Checkbox).first);
    await tester.pump();
    expect(find.text('0本を外す'), findsOneWidget);
    await tester.tap(find.byTooltip('選択をやめる'));
    await tester.pump();
    expect(find.text('選択'), findsOneWidget);
    expect(find.byType(Checkbox), findsNothing);
  });

  test('開いている動画を含めて外すと、残った動画が選ばれる', () {
    final session = ClipSession();
    final items = session.add(['a', 'b', 'c', 'd']);
    session.select(items[1]);
    for (final item in [items[2]]) {
      session.remove(item);
    }
    session.remove(items[1]);
    expect(session.items, [items[0], items[3]]);
    expect(session.active, items[3]);
  });
}

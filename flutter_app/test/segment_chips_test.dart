import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kite_clipper_flutter/clip_session.dart';
import 'package:kite_clipper_flutter/segment_chips.dart';

List<ClipSegment> sample(int count) =>
    List.generate(count, (i) => ClipSegment(i * 1000, i * 1000 + 500));

Widget host(Widget child, {double width = 600}) => MaterialApp(
    theme: ThemeData(splashFactory: InkRipple.splashFactory),
    home: Scaffold(body: SizedBox(width: width, child: child)));

void main() {
  testWidgets('選択・追加・削除を通知し、保存済みの印と選択中だけのメニューを表示',
      (tester) async {
    final segments = sample(3)..[0].output = 'saved.mp4';
    int? chosen, removed;
    var added = 0;
    await tester.pumpWidget(host(SegmentChips(
        segments: segments,
        selected: 1,
        enabled: true,
        onSelect: (i) => chosen = i,
        onAdd: () => added++,
        onRemove: (i) => removed = i)));
    expect(find.byIcon(Icons.check_circle), findsOneWidget);
    expect(find.byTooltip('区間1 の操作'), findsNothing);
    await tester.tap(find.text('区間3'));
    expect(chosen, 2);
    await tester.tap(find.text('区間を追加'));
    expect(added, 1);
    await tester.tap(find.byTooltip('区間2 の操作'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('この区間を削除'));
    await tester.pumpAndSettle();
    // 通知だけで、区間リストは呼び出し側が確認後に変更する。
    expect(removed, 1);
    expect(segments.length, 3);
    expect(tester.takeException(), isNull);
  });

  testWidgets('最後の1区間は削除メニューを無効化', (tester) async {
    int? removed;
    await tester.pumpWidget(host(SegmentChips(
        segments: sample(1),
        selected: 0,
        enabled: true,
        onSelect: (_) {},
        onAdd: () {},
        onRemove: (i) => removed = i)));
    await tester.tap(find.byTooltip('区間1 の操作'));
    await tester.pumpAndSettle();
    expect(
        tester
            .widget<PopupMenuItem<String>>(
                find.widgetWithText(PopupMenuItem<String>, 'この区間を削除'))
            .enabled,
        isFalse);
    await tester.tap(find.text('この区間を削除'));
    await tester.pumpAndSettle();
    expect(removed, isNull);
  });

  testWidgets('20区間で追加を無効化し理由を表示・狭い画面でもはみ出さない', (tester) async {
    await tester.pumpWidget(host(
        SegmentChips(
            segments: sample(ClipItem.maxSegments),
            selected: 19,
            enabled: true,
            onSelect: (_) {},
            onAdd: () {},
            onRemove: (_) {}),
        width: 320));
    expect(tester.widget<ActionChip>(find.byType(ActionChip)).onPressed,
        isNull);
    expect(find.text('1動画につき最大20区間です'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('読み込み・保存中は選択・追加・削除を無効化', (tester) async {
    int? chosen;
    await tester.pumpWidget(host(SegmentChips(
        segments: sample(2),
        selected: 0,
        enabled: false,
        onSelect: (i) => chosen = i,
        onAdd: () {},
        onRemove: (_) {})));
    await tester.tap(find.text('区間2'));
    expect(chosen, isNull);
    expect(tester.widget<ActionChip>(find.byType(ActionChip)).onPressed,
        isNull);
    expect(
        tester
            .widget<PopupMenuButton<String>>(
                find.byType(PopupMenuButton<String>))
            .enabled,
        isFalse);
  });
}

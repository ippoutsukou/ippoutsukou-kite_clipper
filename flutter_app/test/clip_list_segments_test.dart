import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kite_clipper_flutter/clip_list.dart';
import 'package:kite_clipper_flutter/clip_session.dart';

void main() {
  testWidgets('一覧に区間数と合計秒数を表示し、長さ未確認は推測しない', (tester) async {
    final session = ClipSession();
    final items = session.add(['a.mp4', 'b.mp4']);
    items[0].setDuration(20000);
    items[0].setRange(0, 3000);
    items[0].addSegment(10000);
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: SizedBox(
                width: 320,
                height: 400,
                child: ClipList(
                    items: session.items,
                    selected: items[0],
                    enabled: true,
                    onSelect: (_) {})))));
    expect(find.text('区間2 ・ 合計8.0秒'), findsOneWidget);
    expect(find.text('長さ未確認'), findsOneWidget);
  });
}

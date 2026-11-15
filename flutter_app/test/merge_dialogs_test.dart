import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kite_clipper_flutter/clip_session.dart';
import 'package:kite_clipper_flutter/merge_dialogs.dart';
import 'package:kite_clipper_flutter/merge_plan.dart';
import 'package:kite_clipper_flutter/merge_service.dart';

StreamShape shape(int width, int height, String frameRate) => StreamShape(
    video: 'h264',
    width: width,
    height: height,
    frameRate: frameRate,
    audio: 'aac',
    sampleRate: 48000,
    channels: 2);

MergePlan planOf(int videos) {
  final session = ClipSession();
  final items =
      session.add([for (var i = 0; i < videos; i++) 'video$i.mp4']);
  for (final item in items) {
    item.setDuration(5000);
    item.setRange(0, 2000);
  }
  return MergePlan.from(session.items, exists: (_) => true);
}

/// 確認ダイアログを開き、閉じたときの戻り値を受け取る。
Future<List<MergeMode?>> open(WidgetTester tester, MergeFormats? formats,
    {int videos = 2}) async {
  // 選択肢まで一度に見える大きさにする。
  tester.view.physicalSize = const Size(1200, 2000);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  final result = <MergeMode?>[];
  await tester.pumpWidget(MaterialApp(
      home: Builder(
          builder: (context) => TextButton(
              onPressed: () async => result.add(await showDialog<MergeMode>(
                  context: context,
                  builder: (_) => MergeConfirmDialog(
                      plan: planOf(videos), formats: formats))),
              child: const Text('open')))));
  await tester.tap(find.text('open'));
  await tester.pumpAndSettle();
  return result;
}

void main() {
  testWidgets('形式がそろっていれば選択を出さず、そのままつなぐ', (tester) async {
    final same = shape(1920, 1080, '30/1');
    final result = await open(tester, MergeFormats([same, same]));
    expect(find.text('つなぎ方'), findsNothing);
    await tester.tap(find.text('保存先を選ぶ…'));
    await tester.pumpAndSettle();
    expect(result, [MergeMode.strict]);
  });

  testWidgets('大きさが違うと内訳と選択を出し、初期値は形式ごと（画質はそのまま）', (tester) async {
    final result = await open(
        tester,
        MergeFormats([
          shape(3840, 2160, '60/1'),
          shape(1920, 1080, '30/1'),
          shape(3840, 2160, '60/1'),
        ]),
        videos: 3);
    expect(find.textContaining('3840x2160・60fps'), findsOneWidget);
    expect(find.textContaining('区間2個'), findsOneWidget);
    expect(find.textContaining('合計2本できます'), findsOneWidget);
    expect(find.textContaining('3840x2160 にそろえて'), findsOneWidget);
    await tester.tap(find.text('保存先を選ぶ…'));
    await tester.pumpAndSettle();
    expect(result, [MergeMode.grouped]);
  });

  testWidgets('強制を選ぶと強制で実行する。フレームレートだけの違いは作り直さない案内', (tester) async {
    final result = await open(tester,
        MergeFormats([shape(1920, 1080, '30/1'), shape(1920, 1080, '60/1')]));
    expect(find.textContaining('作り直さずにつなぎます'), findsOneWidget);
    await tester.tap(find.text('強制的に1本にする'));
    await tester.pump();
    await tester.tap(find.text('保存先を選ぶ…'));
    await tester.pumpAndSettle();
    expect(result, [MergeMode.forced]);
  });

  testWidgets('作り直せない大きさが混ざると、強制は選べない', (tester) async {
    final result = await open(tester,
        MergeFormats([shape(5312, 4648, '30/1'), shape(1920, 1080, '30/1')]));
    expect(find.textContaining('選べません'), findsOneWidget);
    await tester.tap(find.text('強制的に1本にする'));
    await tester.pump();
    await tester.tap(find.text('保存先を選ぶ…'));
    await tester.pumpAndSettle();
    expect(result, [MergeMode.grouped]);
  });

  testWidgets('キャンセルは何も返さない', (tester) async {
    final result = await open(tester, null);
    await tester.tap(find.text('キャンセル'));
    await tester.pumpAndSettle();
    expect(result, [null]);
  });
}

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kite_clipper_flutter/batch_dialogs.dart';
import 'package:kite_clipper_flutter/batch_plan.dart';
import 'package:kite_clipper_flutter/batch_runner.dart';
import 'package:kite_clipper_flutter/clip_session.dart';

/// ダイアログを開くボタンだけの画面。戻り値を受け取る。
Widget opener<T>(Widget Function() dialog, void Function(T?) onClosed) =>
    MaterialApp(
        // 既存テストと同様、テスト環境にないシェーダーを読まない波紋にする。
        theme: ThemeData(splashFactory: InkRipple.splashFactory),
        home: Scaffold(
            body: Builder(
                builder: (context) => TextButton(
                    onPressed: () async => onClosed(await showDialog<T>(
                        context: context, builder: (_) => dialog())),
                    child: const Text('open')))));

List<ClipItem> items() {
  final session = ClipSession();
  final list = session.add(['A.mp4', 'B.mp4']);
  list[0].setDuration(20000);
  list[0].setRange(0, 3000);
  list[0].addSegment(10000);
  list[0].output = 'saved.mp4'; // 区間2は保存済み、Bは未確認
  return list;
}

void main() {
  testWidgets('対象・未確認・スキップを表示し、保存し直す指定で計画が変わる', (tester) async {
    BatchPlan? chosen;
    await tester.pumpWidget(opener<BatchPlan>(
        () => BatchConfirmDialog(items: items(), android: true),
        (plan) => chosen = plan));
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
    expect(find.text('動画1本・区間1個（合計3.0秒）'), findsOneWidget);
    expect(find.textContaining('まだ開いていない動画1本'), findsOneWidget);
    expect(find.text('保存済みの区間1個はスキップします。'), findsOneWidget);
    expect(find.textContaining('画面を消灯させません'), findsOneWidget);
    await tester.tap(find.text('保存済みの区間も保存し直す'));
    await tester.pumpAndSettle();
    expect(find.text('動画1本・区間2個（合計8.0秒）'), findsOneWidget);
    expect(find.textContaining('スキップします'), findsNothing);
    await tester.tap(find.text('保存先を選ぶ…'));
    await tester.pumpAndSettle();
    expect(chosen?.entries.map((e) => e.fileName),
        ['001_A_区間1.mp4', '001_A_区間2.mp4']);
  });

  testWidgets('対象0件は開始できず理由を表示、キャンセルは null', (tester) async {
    var closed = false;
    BatchPlan? chosen;
    final session = ClipSession()..add(['a.mp4']);
    await tester.pumpWidget(opener<BatchPlan>(
        () => BatchConfirmDialog(items: session.items), (plan) {
      closed = true;
      chosen = plan;
    }));
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
    expect(find.text('保存する区間がありません'), findsOneWidget);
    expect(find.textContaining('区間ができます'), findsOneWidget);
    expect(find.textContaining('画面を消灯させません'), findsNothing);
    expect(
        tester
            .widget<FilledButton>(find.widgetWithText(FilledButton, '保存先を選ぶ…'))
            .onPressed,
        isNull);
    await tester.tap(find.text('キャンセル'));
    await tester.pumpAndSettle();
    expect(closed, isTrue);
    expect(chosen, isNull);
  });

  testWidgets('すべて保存済みなら保存し直しの案内を表示', (tester) async {
    final session = ClipSession()..add(['a.mp4']);
    session.items.first
      ..setDuration(5000)
      ..output = 'saved.mp4';
    await tester.pumpWidget(opener<BatchPlan>(
        () => BatchConfirmDialog(items: session.items), (_) {}));
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
    expect(find.textContaining('すべて保存済みです'), findsOneWidget);
  });

  testWidgets('結果に件数・保存先・失敗理由・全体中止の理由を表示', (tester) async {
    final plan = BatchPlan.from(items(), includeSaved: true);
    final summary = BatchSummary([
      BatchResult(plan.entries[0], BatchStatus.saved, output: 'x.mp4'),
      BatchResult(plan.entries[1], BatchStatus.failed, error: '保存先フォルダが見つかりません'),
    ], skippedSaved: 0, abortReason: '保存先フォルダが見つかりません');
    await tester.pumpWidget(opener<void>(
        () => BatchResultDialog(summary: summary, folderLabel: 'Movies/kite_clips_20260916_010203'),
        (_) {}));
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
    expect(find.text('一括保存を中止しました'), findsOneWidget);
    expect(find.text('成功1件・失敗1件・スキップ0件・未実行0件'), findsOneWidget);
    expect(find.text('保存先：Movies/kite_clips_20260916_010203'), findsOneWidget);
    expect(find.text('中止の理由：保存先フォルダが見つかりません'), findsOneWidget);
    expect(find.text('A.mp4 区間2：保存先フォルダが見つかりません'), findsOneWidget);
    expect(find.textContaining('未完成のファイル'), findsOneWidget);
    await tester.tap(find.text('閉じる'));
    await tester.pumpAndSettle();
    expect(find.byType(BatchResultDialog), findsNothing);
  });

  test('結果の見出しは中止・一部失敗・全成功で切り替える', () {
    final plan = BatchPlan.from(items(), includeSaved: true);
    BatchResultDialog dialog(List<BatchStatus> statuses, {bool cancelled = false}) =>
        BatchResultDialog(
            summary: BatchSummary([
              for (var i = 0; i < statuses.length; i++)
                BatchResult(plan.entries[i], statuses[i])
            ], skippedSaved: 0, cancelled: cancelled),
            folderLabel: '');
    expect(dialog([BatchStatus.saved, BatchStatus.saved]).title, 'すべて保存しました');
    expect(dialog([BatchStatus.saved, BatchStatus.failed]).title, '一部を保存できませんでした');
    expect(dialog([BatchStatus.saved, BatchStatus.notRun], cancelled: true).title,
        '一括保存を中止しました');
  });
}

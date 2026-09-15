import 'dart:async';

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kite_clipper_flutter/batch_plan.dart';
import 'package:kite_clipper_flutter/batch_runner.dart';
import 'package:kite_clipper_flutter/clip_session.dart';

BatchPlan samplePlan() {
  final session = ClipSession();
  final items = session.add(['A.mp4', 'B.mp4']);
  items[0].setDuration(20000);
  items[0].setRange(0, 3000);
  items[0].addSegment(10000);
  items[1].setDuration(8000);
  return BatchPlan.from(session.items);
}

Future<void> settle() => Future<void>.delayed(Duration.zero);

void main() {
  test('一覧順に1件ずつ保存し、成功した区間の保存結果を更新', () async {
    final plan = samplePlan();
    final started = <String>[];
    final finished = <BatchStatus>[];
    final summary = await BatchRunner((e) async => 'out/${e.fileName}').run(
        plan,
        onStart: (i, e) => started.add('$i:${e.fileName}'),
        onResult: (r) => finished.add(r.status));
    expect(started,
        ['0:001_A_区間1.mp4', '1:001_A_区間2.mp4', '2:002_B_区間1.mp4']);
    expect(finished, List.filled(3, BatchStatus.saved));
    expect(summary.saved, 3);
    expect(summary.cancelled, isFalse);
    expect(plan.entries.map((e) => e.segment.output), [
      'out/001_A_区間1.mp4',
      'out/001_A_区間2.mp4',
      'out/002_B_区間1.mp4'
    ]);
  });

  test('1件失敗しても残りを続け、失敗した区間の以前の保存結果は残す', () async {
    final plan = samplePlan();
    plan.entries[1].segment.output = 'old.mp4';
    final summary = await BatchRunner((e) async {
      if (e.segmentNumber == 2) throw StateError('容量が足りません');
      if (e.item.name == 'B.mp4') {
        throw PlatformException(code: 'export', message: '段階：MP4最終処理');
      }
      return 'ok';
    }).run(plan);
    expect(summary.results.map((r) => r.status),
        [BatchStatus.saved, BatchStatus.failed, BatchStatus.failed]);
    expect(summary.failures.map((r) => r.error), ['容量が足りません', '段階：MP4最終処理']);
    expect(plan.entries[1].segment.output, 'old.mp4');
    expect(summary.saved, 1);
    expect(summary.failed, 2);
  });

  test('中止は保存中の1件の完了を待ち、残りを未実行にする', () async {
    final plan = samplePlan();
    final gates = <Completer<String>>[];
    final runner = BatchRunner((e) {
      final gate = Completer<String>();
      gates.add(gate);
      return gate.future;
    });
    final running = runner.run(plan);
    await settle();
    expect(gates.length, 1);
    runner.cancel();
    expect(runner.cancelRequested, isTrue);
    await settle();
    expect(gates.length, 1, reason: '保存中の件が終わるまで次へ進まない');
    gates.first.complete('first.mp4');
    final summary = await running;
    expect(summary.results.map((r) => r.status),
        [BatchStatus.saved, BatchStatus.notRun, BatchStatus.notRun]);
    expect(summary.cancelled, isTrue);
    expect(plan.entries.first.segment.output, 'first.mp4');
    expect(runner.running, isFalse);
  });

  test('保存先に書けない等の全体中止では以降を未実行にする', () async {
    var calls = 0;
    final summary = await BatchRunner((e) async {
      calls++;
      throw const BatchAbort('保存先フォルダが見つかりません');
    }).run(samplePlan());
    expect(calls, 1);
    expect(summary.results.map((r) => r.status),
        [BatchStatus.failed, BatchStatus.notRun, BatchStatus.notRun]);
    expect(summary.abortReason, '保存先フォルダが見つかりません');
    expect(summary.cancelled, isFalse);
  });

  test('実行中の二重開始を拒否し、終了後は中止指定を持ち越さず再実行できる', () async {
    final gate = Completer<String>();
    var first = true;
    final runner = BatchRunner((e) {
      if (first) {
        first = false;
        return gate.future;
      }
      return Future.value('again');
    });
    final running = runner.run(samplePlan());
    await settle();
    await expectLater(runner.run(samplePlan()), throwsStateError);
    runner.cancel();
    gate.complete('x');
    expect((await running).cancelled, isTrue);
    runner.cancel(); // 実行中でなければ無視
    final again = await runner.run(samplePlan());
    expect(again.saved, 3);
    expect(again.cancelled, isFalse);
  });

  test('空の保存先は失敗扱い・空の計画は何もしない・スキップ件数を引き継ぐ', () async {
    final summary = await BatchRunner((e) async => '').run(samplePlan());
    expect(summary.failed, 3);
    expect(summary.failures.first.error, '保存先が返されませんでした');
    final session = ClipSession()..add(['a.mp4']);
    final item = session.items.first..setDuration(5000);
    item.output = 'saved.mp4';
    final empty =
        await BatchRunner((e) async => fail('呼ばれない')).run(BatchPlan.from(session.items));
    expect(empty.results, isEmpty);
    expect(empty.skippedSaved, 1);
  });
}

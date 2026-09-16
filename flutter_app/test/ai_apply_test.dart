import 'package:flutter_test/flutter_test.dart';
import 'package:kite_clipper_flutter/ai_segments.dart';
import 'package:kite_clipper_flutter/analysis_service.dart';
import 'package:kite_clipper_flutter/clip_session.dart';

ClipItem video({int durationMs = 60000}) =>
    ClipItem('a.mp4')..setDuration(durationMs);

List<ProposedSegment> proposals(int count) => [
      for (var i = 0; i < count; i++)
        ProposedSegment(i * 2000, i * 2000 + 1000, 0.8),
    ];

List<List<int>> ranges(ClipItem item) =>
    item.segments.map((s) => [s.startMs, s.endMs]).toList();

void main() {
  test('範囲指定の追加は長さ・最短区間・上限を検証する', () {
    final item = video(durationMs: 10000);
    expect(item.addSegmentRange(1000, 3000), 1);
    expect(item.startMs, 1000);
    expect(() => item.addSegmentRange(-1, 500), throwsArgumentError);
    expect(() => item.addSegmentRange(0, 10001), throwsArgumentError);
    expect(() => item.addSegmentRange(0, 50), throwsArgumentError);
    expect(() => ClipItem('b.mp4').addSegmentRange(0, 1000), throwsStateError);
    while (item.segments.length < ClipItem.maxSegments) {
      item.addSegmentRange(0, 1000);
    }
    expect(() => item.addSegmentRange(0, 1000), throwsStateError);
  });

  test('触っていない初期区間の判定', () {
    final item = video();
    expect(item.isUntouchedDefault, isTrue);
    item.setRange(0, 5000);
    expect(item.isUntouchedDefault, isFalse);
    final saved = video();
    saved.output = 'saved.mp4';
    expect(saved.isUntouchedDefault, isFalse);
    expect(ClipItem('c.mp4').isUntouchedDefault, isFalse);
  });

  test('初期状態なら提案で置き換え、最初の区間を選ぶ', () {
    final item = video();
    final applied = applyProposedSegments(item, proposals(3));
    expect(applied.added, 3);
    expect(applied.skipped, 0);
    expect(applied.replaced, isTrue);
    expect(ranges(item), [
      [0, 1000],
      [2000, 3000],
      [4000, 5000],
    ]);
    expect(item.activeSegment, 0);
  });

  test('編集済みなら既存を残して後ろに足す', () {
    final item = video();
    item.setRange(10000, 12000);
    item.output = 'saved.mp4';
    final applied = applyProposedSegments(item, proposals(2));
    expect(applied.replaced, isFalse);
    expect(applied.added, 2);
    expect(ranges(item), [
      [10000, 12000],
      [0, 1000],
      [2000, 3000],
    ]);
    expect(item.segments.first.output, 'saved.mp4');
    expect(item.activeSegment, 1);
  });

  test('上限を超える提案は追加せず件数を返す', () {
    final item = video();
    item.setRange(30000, 31000); // 初期状態ではなくする
    for (var i = 0; i < 18; i++) {
      item.addSegmentRange(40000 + i * 10, 40000 + i * 10 + 200);
    }
    expect(item.segments.length, 19);
    final applied = applyProposedSegments(item, proposals(5));
    expect(applied.added, 1);
    expect(applied.skipped, 4);
    expect(item.segments.length, ClipItem.maxSegments);
  });

  test('提案が0個なら何も変えない', () {
    final item = video();
    final applied = applyProposedSegments(item, const []);
    expect(applied.added, 0);
    expect(applied.replaced, isFalse);
    expect(ranges(item), [
      [0, 60000]
    ]);
  });

  test('上限フレーム数を超える動画は間隔を広げる', () {
    // 1時間（3600枚）はそのまま。
    expect(
        resolveIntervalMs(durationMs: 3600000, wantedMs: 1000), 1000);
    // 2時間を1秒間隔にすると7200枚になるため2秒へ。
    expect(resolveIntervalMs(durationMs: 7200000, wantedMs: 1000), 2000);
    // 2秒指定でも上限内ならそのまま。
    expect(resolveIntervalMs(durationMs: 7200000, wantedMs: 2000), 2000);
    expect(resolveIntervalMs(durationMs: 0, wantedMs: 1000), 1000);
  });
}

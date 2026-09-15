import 'package:flutter_test/flutter_test.dart';
import 'package:kite_clipper_flutter/clip_session.dart';

List<List<int>> ranges(ClipItem item) =>
    item.segments.map((s) => [s.startMs, s.endMs]).toList();

void main() {
  test('長さ確定前は区間なし・確定後は全体の1区間', () {
    final item = ClipItem('a.mp4');
    expect(item.segments, isEmpty);
    expect(() => item.addSegment(0), throwsStateError);
    expect(() => item.startMs = 0, throwsStateError);
    item.setDuration(20000);
    expect(ranges(item), [
      [0, 20000]
    ]);
    expect(item.activeSegment, 0);
  });

  test('追加位置：再生位置から5秒・終端付近・短い動画', () {
    final item = ClipItem('a.mp4')..setDuration(20000);
    expect(item.addSegment(2000), 1);
    expect(item.activeSegment, 1);
    item.addSegment(18000);
    item.addSegment(19950);
    item.addSegment(-500);
    expect(ranges(item).skip(1), [
      [2000, 7000],
      [18000, 20000],
      [15000, 20000],
      [0, 5000],
    ]);
    final short = ClipItem('b.mp4')..setDuration(3000);
    short.addSegment(2000);
    expect(ranges(short).last, [0, 3000]);
  });

  test('1動画20区間を超える追加は拒否し件数を変えない', () {
    final item = ClipItem('a.mp4')..setDuration(60000);
    for (var i = 1; i < ClipItem.maxSegments; i++) {
      item.addSegment(i * 1000);
    }
    expect(item.segments.length, 20);
    expect(() => item.addSegment(0), throwsStateError);
    expect(item.segments.length, 20);
    expect(item.activeSegment, 19);
  });

  test('削除後の選択：次・末尾なら前・前方削除でも同じ区間を維持', () {
    final item = ClipItem('a.mp4')..setDuration(60000);
    item
      ..addSegment(10000)
      ..addSegment(20000)
      ..addSegment(30000);
    final third = item.segments[2];
    item.selectSegment(1);
    item.removeSegment(1);
    expect(item.current, same(third));
    item.removeSegment(0);
    expect(item.current, same(third));
    item.selectSegment(1);
    item.removeSegment(1);
    expect(item.current, same(third));
    expect(item.segments.length, 1);
    expect(() => item.removeSegment(0), throwsStateError);
    expect(() => item.removeSegment(5), throwsArgumentError);
    expect(() => item.selectSegment(-1), throwsArgumentError);
  });

  test('長さが縮んだら区間を切り詰め、0.1秒未満だけ全体へ戻す', () {
    final item = ClipItem('a.mp4')..setDuration(10000);
    item.setRange(1000, 4000);
    item.output = 'kept.mp4';
    item.addSegment(6000);
    item.setRange(6000, 9000);
    item.output = 'clamped.mp4';
    item.addSegment(8000);
    item.setRange(8000, 9500);
    item.output = 'reset.mp4';
    item.setDuration(7000);
    expect(ranges(item), [
      [1000, 4000],
      [6000, 7000],
      [0, 7000],
    ]);
    expect(item.segments.map((s) => s.output),
        ['kept.mp4', null, null]);
    expect(item.activeSegment, 2);
  });

  test('保存結果は区間ごとに保持し、範囲変更で該当区間だけ消える', () {
    final item = ClipItem('a.mp4')..setDuration(30000);
    item.setRange(0, 3000);
    item.output = 'first.mp4';
    item.addSegment(10000);
    expect(item.output, isNull);
    item.output = 'second.mp4';
    item.setRange(11000, 14000);
    expect(item.output, isNull);
    item.selectSegment(0);
    expect(item.output, 'first.mp4');
    expect(item.startMs, 0);
    expect(item.endMs, 3000);
  });
}

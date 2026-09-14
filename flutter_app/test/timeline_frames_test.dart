import 'dart:async';
import 'dart:typed_data';
import 'package:flutter_test/flutter_test.dart';
import 'package:kite_clipper_flutter/timeline_frames.dart';

void main() {
  test('8枚を動画内に等間隔配置し空動画は取得しない', () {
    expect(timelinePositions(8000), [500, 1500, 2500, 3500, 4500, 5500, 6500, 7500]);
    expect(timelinePositions(0), isEmpty);
    expect(timelinePositions(1), List.filled(8, 0));
  });
  test('動画切替時は古い結果を破棄し取得を直列化', () async {
    final gate = Completer<Uint8List?>();
    final calls = <String>[];
    final loader = TimelineLoader((source, _) async {
      calls.add(source);
      return source == 'old' ? gate.future : Uint8List.fromList([1]);
    });
    final oldResults = <int>[];
    final newResults = <int>[];
    final old = loader.load('old', 8000, (i, _) => oldResults.add(i));
    await Future<void>.delayed(Duration.zero);
    final next = loader.load('new', 8000, (i, _) => newResults.add(i));
    gate.complete(Uint8List.fromList([2]));
    await old;
    await next;
    expect(oldResults, isEmpty);
    expect(newResults.length, 8);
    expect(calls.where((s) => s == 'old').length, 1);
  });
  test('取得失敗と過大な画像は空欄で継続', () async {
    var count = 0;
    final loader = TimelineLoader((_, __) async {
      if (count++ == 0) throw StateError('失敗');
      return Uint8List(512 * 1024 + 1);
    });
    final results = <Uint8List?>[];
    await loader.load('sample', 8000, (_, bytes) => results.add(bytes));
    expect(results.length, 8);
    expect(results.every((bytes) => bytes == null), isTrue);
  });
}

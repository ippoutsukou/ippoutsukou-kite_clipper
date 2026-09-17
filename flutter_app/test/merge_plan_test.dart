import 'package:flutter_test/flutter_test.dart';
import 'package:kite_clipper_flutter/clip_session.dart';
import 'package:kite_clipper_flutter/merge_plan.dart';
import 'package:kite_clipper_flutter/merge_service.dart';

const _probe = '''
{"streams":[
  {"codec_type":"video","codec_name":"h264","width":320,"height":180,"r_frame_rate":"30/1"},
  {"codec_type":"audio","codec_name":"aac","sample_rate":"48000","channels":2}
]}''';

void main() {
  group('つなぐ順番', () {
    test('一覧順・区間番号順に並び、保存済みの区間も含む', () {
      final session = ClipSession();
      final items = session.add(['a.mp4', 'b.mp4']);
      items[0].setDuration(10000);
      items[0].setRange(0, 2000);
      items[0].addSegmentRange(4000, 6000);
      items[0].segments.first.output = 'saved.mp4';
      items[1].setDuration(8000);
      items[1].setRange(1000, 3000);
      final plan = MergePlan.from(session.items, exists: (_) => true);
      expect(plan.canMerge, isTrue);
      expect(
          plan.entries
              .map((e) => '${e.item.name}#${e.segmentNumber}')
              .toList(),
          ['a.mp4#1', 'a.mp4#2', 'b.mp4#1']);
      expect(plan.totalMs, 6000);
      expect(plan.videoCount, 2);
    });

    test('元動画が見つからない動画は外し、長さ未確認の動画は数える', () {
      final session = ClipSession();
      final items = session.add(['gone.mp4', 'here.mp4', 'unopened.mp4']);
      for (final item in items.take(2)) {
        item.setDuration(5000);
      }
      final plan = MergePlan.from(session.items,
          exists: (path) => path != 'gone.mp4');
      expect(plan.missing.map((i) => i.name), ['gone.mp4']);
      expect(plan.entries.map((e) => e.item.name), ['here.mp4']);
      expect(plan.unconfirmedVideos, 1);
      expect(plan.canMerge, isFalse);
    });
  });

  group('形式の照合', () {
    test('ffprobeの結果を読み、同じ形式は同じ印になる', () {
      final shape = StreamShape.fromProbe(_probe);
      expect(shape.video, 'h264');
      expect(shape.width, 320);
      expect(shape.sampleRate, 48000);
      expect(shape.channels, 2);
      expect(shape.key, StreamShape.fromProbe(_probe).key);
    });

    test('違いを日本語で並べる', () {
      final a = StreamShape.fromProbe(_probe);
      final b = StreamShape.fromProbe(_probe.replaceAll('"width":320', '"width":640')
          .replaceAll('"codec_name":"aac"', '"codec_name":"mp3"'));
      expect(a.key, isNot(b.key));
      final diff = a.differences(b);
      expect(diff.join(), contains('画面の大きさ'));
      expect(diff.join(), contains('音声の形式'));
    });

    test('音声がない動画も読める', () {
      final shape = StreamShape.fromProbe(
          '{"streams":[{"codec_type":"video","codec_name":"h264","width":8,"height":8,"r_frame_rate":"30/1"}]}');
      expect(shape.audio, isEmpty);
      expect(shape.channels, 0);
      expect(shape.differences(shape), isEmpty);
    });

    test('映像がない・形式が読めないものは例外', () {
      expect(() => StreamShape.fromProbe('{"streams":[]}'),
          throwsA(isA<FormatException>()));
      expect(() => StreamShape.fromProbe('{}'), throwsA(isA<FormatException>()));
    });
  });

  test('まとめた動画のファイル名は日時入り', () {
    expect(mergedFileName(DateTime(2026, 9, 18, 1, 2, 3)),
        'kite_merged_20260918_010203.mp4');
  });
}

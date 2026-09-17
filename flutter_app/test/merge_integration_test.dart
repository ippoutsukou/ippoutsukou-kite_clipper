import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:kite_clipper_flutter/batch_plan.dart';
import 'package:kite_clipper_flutter/clip_session.dart';
import 'package:kite_clipper_flutter/merge_service.dart';

/// 試験用の動画を作る。size を変えると形式の違う動画になる。
Future<String> makeVideo(Directory run, String name,
    {String size = '320x180', int seconds = 3}) async {
  final path = '${run.absolute.path}/$name';
  final result = await Process.run('ffmpeg', [
    '-hide_banner', '-loglevel', 'error', '-n',
    '-f', 'lavfi', '-i', 'testsrc2=size=$size:rate=30',
    '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
    '-t', '$seconds',
    '-c:v', 'libx264', '-g', '30', '-bf', '0', '-pix_fmt', 'yuv420p',
    '-c:a', 'aac', path,
  ]);
  expect(result.exitCode, 0, reason: result.stderr.toString());
  return path;
}

List<BatchEntry> entriesFor(List<String> paths, int startMs, int endMs) {
  final session = ClipSession();
  final items = session.add(paths);
  for (final item in items) {
    item.setDuration(3000);
    item.setRange(startMs, endMs);
  }
  return BatchPlan.from(session.items, includeSaved: true).entries;
}

Future<double> durationOf(String path) async {
  final result = await Process.run('ffprobe', [
    '-v', 'error', '-show_entries', 'format=duration',
    '-of', 'default=nw=1:nk=1', path,
  ]);
  expect(result.exitCode, 0, reason: result.stderr.toString());
  return double.parse((result.stdout as String).trim());
}

void main() {
  test('Windows: 2本の動画の区間を無劣化で1本につなぐ', () async {
    final base = await Directory('../out/flutter-proof').create(recursive: true);
    final run = await base.createTemp('merge_');
    final a = await makeVideo(run, 'a.mp4');
    final b = await makeVideo(run, 'b.mp4');
    final phases = <MergePhase>[];
    final outcome = await MergeService().mergeWindows(
      entries: entriesFor([a, b], 500, 2000),
      directory: run.absolute.path,
      now: DateTime(2026, 9, 18, 4, 5, 6),
      onProgress: (phase, _, __) => phases.add(phase),
    );
    expect(outcome.lossless, isTrue);
    expect(outcome.output, endsWith('kite_merged_20260918_040506.mp4'));
    expect(phases, contains(MergePhase.joining));
    // 1.5秒の区間2つ＝約3秒。キーフレーム単位のずれを見込んで幅を持たせる。
    expect(await durationOf(outcome.output), closeTo(3.0, 1.0));
    final streams = await Process.run('ffprobe', [
      '-v', 'error', '-show_entries', 'stream=codec_type,codec_name',
      '-of', 'json', outcome.output,
    ]);
    final types = [
      for (final s in (jsonDecode(streams.stdout as String)
          as Map)['streams'] as List)
        (s as Map)['codec_type']
    ];
    expect(types, containsAll(['video', 'audio']));
    // 一時ファイルは残さない。
    expect(
        Directory(Directory.systemTemp.path)
            .listSync()
            .whereType<Directory>()
            .where((d) => d.path.contains('kite_merge_'))
            .isEmpty,
        isTrue);
  }, timeout: const Timeout(Duration(minutes: 3)));

  test('Windows: 形式が違う動画は理由を示して中止する', () async {
    final base = await Directory('../out/flutter-proof').create(recursive: true);
    final run = await base.createTemp('merge_diff_');
    final a = await makeVideo(run, 'a.mp4');
    final b = await makeVideo(run, 'b.mp4', size: '640x360');
    await expectLater(
        MergeService().mergeWindows(
            entries: entriesFor([a, b], 500, 2000),
            directory: run.absolute.path),
        throwsA(isA<MergeUnsupported>().having(
            (e) => e.message, 'message', contains('画面の大きさ'))));
    // 出力は作らない。
    expect(
        run
            .listSync()
            .whereType<File>()
            .where((f) => f.path.contains('kite_merged_'))
            .isEmpty,
        isTrue);
  }, timeout: const Timeout(Duration(minutes: 3)));
}

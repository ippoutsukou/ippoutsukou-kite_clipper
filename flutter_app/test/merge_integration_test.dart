import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:kite_clipper_flutter/batch_plan.dart';
import 'package:kite_clipper_flutter/clip_session.dart';
import 'package:kite_clipper_flutter/merge_service.dart';

/// 試験用の動画を作る。size / rate を変えると形式の違う動画になる。
Future<String> makeVideo(Directory run, String name,
    {String size = '320x180',
    int seconds = 3,
    String rate = '30',
    bool audio = true}) async {
  final path = '${run.absolute.path}/$name';
  final result = await Process.run('ffmpeg', [
    '-hide_banner', '-loglevel', 'error', '-n',
    '-f', 'lavfi', '-i', 'testsrc2=size=$size:rate=$rate',
    if (audio) ...[
      '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
    ],
    '-t', '$seconds',
    '-c:v', 'libx264', '-g', '30', '-bf', '0', '-pix_fmt', 'yuv420p',
    if (audio) ...['-c:a', 'aac'],
    path,
  ]);
  expect(result.exitCode, 0, reason: result.stderr.toString());
  return path;
}

/// 映像・音声それぞれの長さと、映像の大きさ・フレーム数を読む。
Future<Map<String, Object?>> describe(String path) async {
  final result = await Process.run('ffprobe', [
    '-v', 'error', '-count_frames', '-show_entries',
    'stream=codec_type,width,height,duration,nb_read_frames',
    '-of', 'json', path,
  ]);
  expect(result.exitCode, 0, reason: result.stderr.toString());
  final streams = (jsonDecode(result.stdout as String) as Map)['streams'] as List;
  Map? find(String type) => streams
      .cast<Map>()
      .where((stream) => stream['codec_type'] == type)
      .firstOrNull;
  final video = find('video')!;
  final audio = find('audio');
  return {
    'width': video['width'],
    'height': video['height'],
    'frames': int.parse(video['nb_read_frames'] as String),
    'video': double.parse(video['duration'] as String),
    'audio': audio == null ? null : double.parse(audio['duration'] as String),
  };
}

/// 最後まで読めるか（壊れた動画でないか）を確かめる。
Future<void> expectDecodable(String path) async {
  final result = await Process.run('ffmpeg', [
    '-hide_banner', '-v', 'error', '-xerror', '-i', path,
    '-map', '0:v:0', '-f', 'null', '-',
  ]);
  expect(result.exitCode, 0, reason: result.stderr.toString());
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

  test('Windows: 形式ごとに分けて、それぞれ無劣化で1本にする', () async {
    final base = await Directory('../out/flutter-proof').create(recursive: true);
    final run = await base.createTemp('merge_grouped_');
    final a = await makeVideo(run, 'a.mp4');
    final b = await makeVideo(run, 'b.mp4', size: '640x360');
    final c = await makeVideo(run, 'c.mp4');
    final service = MergeService();
    final entries = entriesFor([a, b, c], 500, 2000);
    final formats = await service.inspectWindows(entries);
    expect(formats!.mixed, isTrue);
    expect(formats.groups.map((g) => g.indexes), [
      [0, 2],
      [1]
    ]);
    final outcome = await service.mergeWindows(
        entries: entries,
        directory: run.absolute.path,
        mode: MergeMode.grouped,
        now: DateTime(2026, 9, 18, 4, 5, 6));
    expect(outcome.lossless, isTrue);
    expect(outcome.outputs.map((path) => path.split(RegExp(r'[\\/]')).last), [
      'kite_merged_20260918_040506_320x180_30fps.mp4',
      'kite_merged_20260918_040506_640x360_30fps.mp4',
    ]);
    // a と c（各1.5秒）が1本目、b が2本目。キーフレーム単位のずれを見込む。
    final first = await describe(outcome.outputs[0]);
    final second = await describe(outcome.outputs[1]);
    expect(first['width'], 320);
    expect(first['video'], closeTo(3.0, 1.0));
    expect(second['width'], 640);
    expect(second['video'], closeTo(1.5, 0.6));
    await expectDecodable(outcome.outputs[0]);
    await expectDecodable(outcome.outputs[1]);
  }, timeout: const Timeout(Duration(minutes: 3)));

  test('Windows: フレームレートだけ違う動画は、強制でも作り直さずに1本にする', () async {
    final base = await Directory('../out/flutter-proof').create(recursive: true);
    final run = await base.createTemp('merge_fps_');
    final a = await makeVideo(run, 'a.mp4');
    final b = await makeVideo(run, 'b.mp4', rate: '60');
    final c = await makeVideo(run, 'c.mp4', rate: '30000/1001');
    final service = MergeService();
    final entries = entriesFor([a, b, c], 500, 2000);
    final formats = await service.inspectWindows(entries);
    expect(formats!.mixed, isTrue);
    expect(formats.frameRateOnly, isTrue);
    final outcome = await service.mergeWindows(
        entries: entries,
        directory: run.absolute.path,
        mode: MergeMode.forced);
    expect(outcome.lossless, isTrue);
    expect(outcome.outputs, hasLength(1));
    final info = await describe(outcome.output);
    // 時刻の刻みをそろえていないと、映像だけ長くなって音声とずれる。
    expect(info['video'], closeTo(4.5, 1.2));
    expect(info['video'], closeTo(info['audio'] as double, 0.2));
    await expectDecodable(outcome.output);
  }, timeout: const Timeout(Duration(minutes: 3)));

  test('Windows: 大きさが違う動画は、強制なら最大の大きさへ作り直して1本にする', () async {
    final base = await Directory('../out/flutter-proof').create(recursive: true);
    final run = await base.createTemp('merge_forced_');
    final a = await makeVideo(run, 'a.mp4');
    final b = await makeVideo(run, 'b.mp4', size: '640x360', rate: '60');
    // 縦横比が違い、音声もない動画（黒帯と無音で埋める）。
    final c = await makeVideo(run, 'c.mp4', size: '320x240', audio: false);
    final phases = <MergePhase>{};
    final outcome = await MergeService().mergeWindows(
        entries: entriesFor([a, b, c], 500, 2000),
        directory: run.absolute.path,
        mode: MergeMode.forced,
        onProgress: (phase, _, __) => phases.add(phase));
    expect(outcome.lossless, isFalse);
    expect(outcome.outputs, hasLength(1));
    expect(phases, contains(MergePhase.encoding));
    final info = await describe(outcome.output);
    expect(info['width'], 640);
    expect(info['height'], 360);
    // 作り直した区間はフレーム単位で正確（1.5秒×3）。元のフレームレートを保つ。
    expect(info['video'], closeTo(4.5, 0.3));
    expect(info['audio'] as double, closeTo(4.5, 0.3));
    expect(info['frames'], closeTo(45 + 90 + 45, 6));
    await expectDecodable(outcome.output);
  }, timeout: const Timeout(Duration(minutes: 5)));
}

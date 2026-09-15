import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:kite_clipper_flutter/batch_plan.dart';
import 'package:kite_clipper_flutter/batch_runner.dart';
import 'package:kite_clipper_flutter/clip_session.dart';
import 'package:kite_clipper_flutter/export_service.dart';

Future<String> synth(String path, int seconds) async {
  final result = await Process.run('ffmpeg', [
    '-v', 'error', '-n', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=30',
    '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
    '-t', '$seconds', '-c:v', 'libx264', '-g', '30', '-bf', '0', '-c:a', 'aac', path,
  ]);
  expect(result.exitCode, 0, reason: result.stderr.toString());
  return path;
}

Future<(double, Set<String>)> probe(String path) async {
  final result = await Process.run('ffprobe', [
    '-v', 'error', '-show_entries', 'format=duration:stream=codec_type',
    '-of', 'json', path,
  ]);
  expect(result.exitCode, 0, reason: result.stderr.toString());
  final data = jsonDecode(result.stdout as String) as Map<String, dynamic>;
  return (
    double.parse(data['format']['duration'] as String),
    (data['streams'] as List).map((s) => s['codec_type'] as String).toSet(),
  );
}

void main() {
  test('Windows: 2動画3区間を1フォルダへ音声付きで保存し、既存ファイル・原本を上書きしない',
      () async {
    final base = await Directory('../out/flutter-proof').create(recursive: true);
    final run = await base.createTemp('batch_');
    final first = await synth('${run.absolute.path}/動画 テスト.mp4', 6);
    final second = await synth('${run.absolute.path}/second.mp4', 4);
    final originals = [await File(first).readAsBytes(), await File(second).readAsBytes()];

    final session = ClipSession()..add([first, second]);
    final a = session.items[0]..setDuration(6000);
    a.setRange(0, 1000);
    a.addSegment(2000);
    a.setRange(2000, 4000);
    session.items[1]
      ..setDuration(4000)
      ..setRange(1000, 2500);
    final plan = BatchPlan.from(session.items);
    expect(plan.entries.length, 3);

    final service = ExportService();
    final time = DateTime(2026, 9, 16, 1, 2, 3);
    final folder = await service.createBatchFolder(run.absolute.path, time);
    expect(folder.location, endsWith('kite_clips_20260916_010203'));
    final again = await service.createBatchFolder(run.absolute.path, time);
    expect(again.location, endsWith('kite_clips_20260916_010203_2'));

    // 同名の既存ファイルは上書きせず _2 で保存する。
    final existing = File('${folder.location}/001_動画 テスト_区間1.mp4');
    await existing.writeAsString('既存');

    final summary = await BatchRunner((e) => service.exportInto(
        folder,
        e.fileName,
        e.item.path,
        ClipRange(e.segment.startMs, e.segment.endMs),
        e.item.durationMs!)).run(plan);
    expect(summary.failures.map((r) => r.error), isEmpty);
    expect(summary.saved, 3);

    final names = summary.results
        .map((r) => r.output!.split(RegExp(r'[/\\]')).last)
        .toList();
    expect(names, [
      '001_動画 テスト_区間1_2.mp4',
      '001_動画 テスト_区間2.mp4',
      '002_second_区間1.mp4',
    ]);
    expect(await existing.readAsString(), '既存');
    final expected = [1.0, 2.0, 1.5];
    for (var i = 0; i < 3; i++) {
      final (duration, types) = await probe(summary.results[i].output!);
      expect(types, containsAll(['video', 'audio']));
      expect(duration, closeTo(expected[i], 0.2), reason: names[i]);
      expect(plan.entries[i].segment.output, summary.results[i].output);
    }
    expect(await File(first).readAsBytes(), originals[0]);
    expect(await File(second).readAsBytes(), originals[1]);

    // 保存先フォルダがなくなった場合は全体中止として扱う。
    final missing = BatchFolder('${run.absolute.path}/missing', 'missing');
    await expectLater(
        service.exportInto(missing, 'x.mp4', first, const ClipRange(0, 1000), 6000),
        throwsA(isA<BatchAbort>()));
    expect(Directory(missing.location).existsSync(), isFalse);
  }, skip: !Platform.isWindows, timeout: const Timeout(Duration(minutes: 3)));
}

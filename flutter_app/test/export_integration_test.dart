import 'dart:convert';
import 'dart:io';
import 'package:flutter_test/flutter_test.dart';
import 'package:kite_clipper_flutter/export_service.dart';

void main() {
  test('Windows: 合成動画の1区間を映像・音声付きで保存する', () async {
    final base =
        await Directory('../out/flutter-proof').create(recursive: true);
    final run = await base.createTemp('export_');
    final input = '${run.absolute.path}/source.mp4';
    final generated = await Process.run('ffmpeg', [
      '-hide_banner',
      '-loglevel',
      'error',
      '-n',
      '-f',
      'lavfi',
      '-i',
      'testsrc2=size=320x180:rate=30',
      '-f',
      'lavfi',
      '-i',
      'sine=frequency=440:sample_rate=48000',
      '-t',
      '4',
      '-c:v',
      'libx264',
      '-g',
      '30',
      '-bf',
      '0',
      '-c:a',
      'aac',
      input,
    ]);
    expect(generated.exitCode, 0, reason: generated.stderr.toString());
    final original = await File(input).readAsBytes();
    final output = await ExportService().export(
        input, const ClipRange(1000, 3000), 4000,
        directory: run.absolute.path);
    expect(output, isNotNull);
    expect(await File(input).readAsBytes(), original);
    final probe = await Process.run('ffprobe', [
      '-v',
      'error',
      '-show_entries',
      'format=duration:stream=codec_type',
      '-of',
      'json',
      output!,
    ]);
    expect(probe.exitCode, 0);
    final data = jsonDecode(probe.stdout as String) as Map<String, dynamic>;
    final types = (data['streams'] as List).map((s) => s['codec_type']).toSet();
    expect(types, containsAll(['video', 'audio']));
    expect(
        double.parse(data['format']['duration'] as String), closeTo(2, 0.15));
    final decoded = await Process.run('ffmpeg', [
      '-v',
      'error',
      '-i',
      output,
      '-f',
      'null',
      '-',
    ]);
    expect(decoded.exitCode, 0, reason: decoded.stderr.toString());
  }, skip: !Platform.isWindows, timeout: const Timeout(Duration(minutes: 2)));
}

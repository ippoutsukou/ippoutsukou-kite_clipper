import 'dart:async';
import 'dart:io';
import 'package:flutter_test/flutter_test.dart';
import 'package:media_kit/media_kit.dart';
import 'package:kite_clipper_flutter/clip_session.dart';
import 'package:kite_clipper_flutter/export_service.dart';

void main() {
  final library = File('build/windows/x64/runner/Debug/libmpv-2.dll');
  test('Windows実エンジンでA→B→Aを停止状態で開き現在の範囲だけ保存', () async {
    final root = await Directory('../out/flutter-proof').create(recursive: true);
    final dir = await root.createTemp('multi_');
    final paths = <String>[];
    for (final seconds in [3, 5]) {
      final path = '${dir.absolute.path}/sample$seconds.mp4';
      final result = await Process.run('ffmpeg', ['-v', 'error', '-n', '-f', 'lavfi',
        '-i', 'testsrc2=size=320x180:rate=30', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
        '-t', '$seconds', '-c:v', 'libx264', '-g', '30', '-c:a', 'aac', path]);
      expect(result.exitCode, 0);
      paths.add(path);
    }
    final session = ClipSession()..add(paths);
    MediaKit.ensureInitialized(libmpv: library.absolute.path);
    final player = Player();
    try {
      await player.setVolume(0);
      for (final index in [0, 1, 0]) {
        final item = session.items[index];
        session.select(item);
        await player.stop();
        final length = Completer<Duration>();
        final subscription = player.stream.duration.listen((value) {
          if (value.inMilliseconds > 0 && !length.isCompleted) length.complete(value);
        });
        try {
          await player.open(Media(item.path), play: false);
          final duration = await length.future.timeout(const Duration(seconds: 20));
          expect(duration.inMilliseconds, closeTo(index == 0 ? 3000 : 5000, 150));
          item.setDuration(duration.inMilliseconds);
          if (item.startMs == 0) item.setRange(1000, index == 0 ? 2000 : 4000);
          await player.seek(Duration(milliseconds: item.startMs!));
          expect(player.state.playing, isFalse);
        } finally { await subscription.cancel(); }
      }
      final active = session.active!;
      expect(active.path, paths[0]);
      expect(active.startMs, 1000); expect(active.endMs, 2000);
      expect(session.items[1].endMs, 4000);
      final result = await ExportService().export(active.path,
        ClipRange(active.startMs!, active.endMs!), active.durationMs!, directory: dir.path);
      expect(result, isNotNull);
      final probe = await Process.run('ffprobe', ['-v', 'error', '-show_entries',
        'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', result!]);
      expect(probe.exitCode, 0);
      expect(double.parse((probe.stdout as String).trim()), closeTo(1.0, 0.2));
    } finally { await player.dispose(); }
  }, skip: !Platform.isWindows || !library.existsSync(),
    timeout: const Timeout(Duration(minutes: 2)));
}

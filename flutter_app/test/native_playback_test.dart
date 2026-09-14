import 'dart:io';
import 'package:flutter_test/flutter_test.dart';
import 'package:media_kit/media_kit.dart';

void main() {
  final library = File('build/windows/x64/runner/Debug/libmpv-2.dll');
  test('同梱Windows再生エンジンで合成動画を開き時刻が進む', () async {
    final root =
        await Directory('../out/flutter-proof').create(recursive: true);
    final dir = await root.createTemp('playback_');
    final source = '${dir.absolute.path}/synthetic.mp4';
    final result = await Process.run('ffmpeg', [
      '-v',
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
      '-c:a',
      'aac',
      source,
    ]);
    expect(result.exitCode, 0);
    MediaKit.ensureInitialized(libmpv: library.absolute.path);
    final player = Player();
    try {
      await player.setVolume(0);
      await player.open(Media(source));
      await player.stream.position
          .firstWhere((p) => p.inMilliseconds >= 500)
          .timeout(const Duration(seconds: 20));
      expect(
          player.state.duration.inMilliseconds, inInclusiveRange(3900, 4200));
      await player.pause();
      expect(player.state.playing, isFalse);
      await player.seek(const Duration(seconds: 2));
      await player.play();
      await player.stream.position
          .firstWhere((p) => p.inMilliseconds >= 2200)
          .timeout(const Duration(seconds: 20));
    } finally {
      await player.dispose();
    }
  },
      skip: !Platform.isWindows || !library.existsSync(),
      timeout: const Timeout(Duration(minutes: 1)));
}

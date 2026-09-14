import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter_test/flutter_test.dart';
import 'package:kite_clipper_flutter/timeline_frames.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  test('Windowsで実際に小さいJPEGを2時刻から取得する', () async {
    final root = await Directory('../out/flutter-proof').create(recursive: true);
    final folder = await root.createTemp('timeline_');
    final source = '${folder.absolute.path}/synthetic.mp4';
    final result = await Process.run('ffmpeg', ['-v', 'error', '-n', '-f', 'lavfi',
      '-i', 'testsrc2=size=320x180:rate=30', '-t', '3', '-c:v', 'libx264', source]);
    expect(result.exitCode, 0);
    final originalLength = await File(source).length();
    final first = await extractTimelineFrame(source, 500);
    final second = await extractTimelineFrame(source, 2000);
    expect(first, isNotNull);
    expect(second, isNotNull);
    expect(first, isNot(orderedEquals(second!)));
    final codec = await ui.instantiateImageCodec(first!);
    final frame = await codec.getNextFrame();
    expect(frame.image.width, lessThanOrEqualTo(160));
    expect(frame.image.height, lessThanOrEqualTo(90));
    frame.image.dispose();
    codec.dispose();
    expect(await File(source).length(), originalLength);
  }, skip: !Platform.isWindows, timeout: const Timeout(Duration(minutes: 1)));
}

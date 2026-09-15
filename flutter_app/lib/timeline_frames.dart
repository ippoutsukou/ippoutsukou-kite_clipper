import 'dart:io';
import 'dart:typed_data';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

List<int> timelinePositions(int durationMs, {int count = 8}) =>
    durationMs <= 0 || count <= 0
        ? []
        : List.generate(
            count,
            (i) => ((i + 0.5) * durationMs / count)
                .floor()
                .clamp(0, durationMs - 1));

/// 取得を直列化し、動画切り替え後の結果を通知しない。
class TimelineLoader {
  TimelineLoader(this.extract);
  final Future<Uint8List?> Function(String, int) extract;
  int generation = 0;
  Future<void> tail = Future.value();
  void cancel() => generation++;
  Future<void> load(
      String source, int durationMs, void Function(int, Uint8List?) onFrame) {
    final id = ++generation;
    tail = tail.then((_) async {
      final positions = timelinePositions(durationMs);
      for (var i = 0; i < positions.length; i++) {
        if (id != generation) return;
        Uint8List? bytes;
        try {
          bytes = await extract(source, positions[i]);
        } catch (_) {/* 空欄で継続 */}
        if (id != generation) return;
        onFrame(i, bytes != null && bytes.length <= 512 * 1024 ? bytes : null);
      }
    });
    return tail;
  }
}

Future<Uint8List?> extractTimelineFrame(String source, int positionMs) async {
  if (Platform.isAndroid) {
    return const MethodChannel('kite_clipper/thumbnails')
        .invokeMethod<Uint8List>(
            'frame', {'source': source, 'positionMs': positionMs});
  }
  if (!Platform.isWindows) return null;
  var ffmpeg = 'ffmpeg';
  for (final file in [
    File('${File(Platform.resolvedExecutable).parent.path}/ffmpeg.exe'),
    File('../vendor/ffmpeg/windows-x64/ffmpeg.exe'),
    File('vendor/ffmpeg/windows-x64/ffmpeg.exe'),
  ]) {
    if (await file.exists()) {
      ffmpeg = file.absolute.path;
      break;
    }
  }
  final process = await Process.start(ffmpeg, [
    '-v',
    'error',
    '-nostdin',
    '-ss',
    (positionMs / 1000).toStringAsFixed(3),
    '-i',
    source,
    '-frames:v',
    '1',
    '-an',
    '-vf',
    'scale=160:90:force_original_aspect_ratio=decrease',
    '-f',
    'image2pipe',
    '-vcodec',
    'mjpeg',
    'pipe:1'
  ]);
  final bytes = BytesBuilder(copy: false);
  var oversized = false;
  final output = process.stdout.listen((data) {
    if (bytes.length + data.length > 512 * 1024) {
      oversized = true;
      process.kill();
    } else {
      bytes.add(data);
    }
  });
  final errors = process.stderr.drain<void>();
  final outputDone = output.asFuture<void>();
  try {
    final code = await process.exitCode.timeout(const Duration(seconds: 15),
        onTimeout: () {
      process.kill();
      return -1;
    });
    await outputDone;
    await errors;
    return code == 0 && !oversized && bytes.length > 0
        ? bytes.takeBytes()
        : null;
  } finally {
    await output.cancel();
  }
}

class TimelineFrames extends StatelessWidget {
  const TimelineFrames({super.key, required this.frames});
  final List<Uint8List?> frames;
  @override
  Widget build(BuildContext context) => IgnorePointer(
          child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 6),
        child: ClipRRect(
            borderRadius: BorderRadius.circular(3),
            child: Row(
              children: frames
                  .map((bytes) => Expanded(
                      child: bytes == null
                          ? Container(
                              color: const Color(0xffdce5f1),
                              child: const Center(
                                  child: Icon(Icons.movie_outlined,
                                      size: 14, color: Colors.blueGrey)))
                          : Image.memory(bytes,
                              height: double.infinity,
                              fit: BoxFit.cover,
                              gaplessPlayback: false,
                              errorBuilder: (_, __, ___) =>
                                  const ColoredBox(color: Color(0xffdce5f1)))))
                  .toList(),
            )),
      ));
}

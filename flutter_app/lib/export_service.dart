import 'dart:io';

import 'package:flutter/services.dart';

class ClipRange {
  final int startMs;
  final int endMs;
  const ClipRange(this.startMs, this.endMs);
  void validate(int durationMs) {
    if (startMs < 0 || endMs <= startMs || endMs > durationMs) {
      throw ArgumentError('開始・終了範囲が不正です');
    }
  }
}

class ExportService {
  static const channel = MethodChannel('kite_clipper/export');

  Future<String?> export(
    String source,
    ClipRange range,
    int durationMs, {
    String? directory,
  }) async {
    range.validate(durationMs);
    if (!await File(source).exists()) throw StateError('元動画が見つかりません');
    if (Platform.isAndroid) {
      return channel.invokeMethod<String>('export', {
        'source': source,
        'startMs': range.startMs,
        'endMs': range.endMs,
      });
    }
    if (!Platform.isWindows || directory == null) {
      throw UnsupportedError('保存先を選択してください');
    }
    // 一意な専用ディレクトリで既存ファイルとの競合を防ぐ。
    final folder = await Directory(directory).createTemp('kite_clip_');
    final output = '${folder.path}${Platform.pathSeparator}clip.mp4';
    final executable = await _ffmpeg();
    final result = await Process.run(executable, [
      '-hide_banner',
      '-loglevel',
      'error',
      '-n',
      '-ss',
      (range.startMs / 1000).toStringAsFixed(3),
      '-i',
      source,
      '-t',
      ((range.endMs - range.startMs) / 1000).toStringAsFixed(3),
      '-map',
      '0:v:0',
      '-map',
      '0:a:0?',
      '-c',
      'copy',
      output,
    ]);
    if (result.exitCode != 0) {
      throw StateError(
        '動画の保存に失敗しました。形式・空き容量を確認してください。未完成ファイルは ${folder.path} に残ります。',
      );
    }
    if (!await File(output).exists() || await File(output).length() == 0) {
      throw StateError('出力が空です');
    }
    return output;
  }

  Future<String> _ffmpeg() async {
    final locations = [
      File('${File(Platform.resolvedExecutable).parent.path}/ffmpeg.exe'),
      File('../vendor/ffmpeg/windows-x64/ffmpeg.exe'),
      File('vendor/ffmpeg/windows-x64/ffmpeg.exe'),
    ];
    for (final file in locations) {
      if (await file.exists()) return file.absolute.path;
    }
    return 'ffmpeg';
  }
}

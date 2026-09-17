import 'dart:io';

import 'package:flutter/services.dart';

import 'batch_plan.dart';
import 'batch_runner.dart';

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

/// 一括保存の実行ごとのフォルダ。Windowsはパス、Androidはフォルダ選択で作ったフォルダのURI。
class BatchFolder {
  const BatchFolder(this.location, this.label);
  final String location;

  /// 利用者に見せる保存先の表示。AndroidではURIではなく「選んだフォルダ/kite_clips_…」。
  final String label;
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
    final result = await copyRange(source, range, output);
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

  /// Windows：選んだ親フォルダの中に実行ごとのフォルダを作る。作れなければ例外（一括保存を開始しない）。
  Future<BatchFolder> createBatchFolder(String parent, DateTime now) async {
    final base = Directory(parent);
    if (!await base.exists()) throw StateError('保存先フォルダが見つかりません');
    String path(String name) => '${base.path}${Platform.pathSeparator}$name';
    final name = uniqueFileName(batchFolderName(now), (n) => _exists(path(n)));
    final folder = await Directory(path(name)).create();
    return BatchFolder(folder.path, folder.path);
  }

  /// Android：システムのフォルダ選択画面で選んだ場所に実行ごとのフォルダを作る。キャンセルはnull。
  Future<BatchFolder?> pickAndroidBatchFolder(DateTime now) async {
    final result = await channel.invokeMapMethod<String, String>(
        'pickFolder', {'name': batchFolderName(now)});
    if (result == null) return null;
    final uri = result['uri'];
    if (uri == null || uri.isEmpty) throw StateError('保存先フォルダを作成できませんでした');
    return BatchFolder(uri, result['label'] ?? batchFolderName(now));
  }

  /// 一括保存の1件。同名があれば上書きせず別名にする。戻り値は保存先（WindowsはパスAndroidはURI）。
  Future<String> exportInto(BatchFolder folder, String fileName, String source,
      ClipRange range, int durationMs) async {
    range.validate(durationMs);
    if (!await File(source).exists()) throw StateError('元動画が見つかりません');
    if (Platform.isAndroid) {
      final String? uri;
      try {
        uri = await channel.invokeMethod<String>('exportTo', {
          'folder': folder.location,
          'name': fileName,
          'source': source,
          'startMs': range.startMs,
          'endMs': range.endMs,
        });
      } on PlatformException catch (e) {
        // ファイル自体を作れない＝フォルダの権限切れ・削除等。残りも成功しないため全体中止。
        if (e.code == 'folder') {
          throw BatchAbort('保存先フォルダに書き込めません。フォルダを選び直してください。\n${e.message ?? ''}'.trim());
        }
        rethrow;
      }
      if (uri == null || uri.isEmpty) throw StateError('保存先のファイルを作成できませんでした');
      return uri;
    }
    if (!Platform.isWindows) throw UnsupportedError('この環境では保存できません');
    final directory = Directory(folder.location);
    if (!await directory.exists()) {
      throw const BatchAbort('保存先フォルダが見つかりません。移動・削除されていないか確認してください。');
    }
    String path(String name) =>
        '${directory.path}${Platform.pathSeparator}$name';
    final output = path(uniqueFileName(fileName, (n) => _exists(path(n))));
    final result = await copyRange(source, range, output);
    if (result.exitCode != 0) {
      throw StateError(
          '動画の保存に失敗しました。形式・空き容量を確認してください。未完成ファイルが残る場合があります：$output');
    }
    if (!await File(output).exists() || await File(output).length() == 0) {
      throw StateError('出力が空です：$output');
    }
    return output;
  }

  /// Android：一括保存中だけ画面の消灯を防ぐ。他の環境では何もしない。
  Future<void> keepScreenOn(bool on) async {
    if (!Platform.isAndroid) return;
    await channel.invokeMethod<void>('keepScreenOn', {'on': on});
  }

  bool _exists(String path) =>
      FileSystemEntity.typeSync(path) != FileSystemEntityType.notFound;

  /// `-n` で既存ファイルを上書きしない高速コピー。1件保存・一括保存・結合で共通。
  Future<ProcessResult> copyRange(
      String source, ClipRange range, String output) async {
    final executable = await ffmpegPath();
    return Process.run(executable, [
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
  }

  /// 同梱・開発用の場所から ffmpeg を探す。見つからなければ PATH に任せる。
  Future<String> ffmpegPath() => _tool('ffmpeg');

  /// ffprobe も ffmpeg と同じ場所から探す。
  Future<String> ffprobePath() => _tool('ffprobe');

  Future<String> _tool(String name) async {
    final locations = [
      File('${File(Platform.resolvedExecutable).parent.path}/$name.exe'),
      File('../vendor/ffmpeg/windows-x64/$name.exe'),
      File('vendor/ffmpeg/windows-x64/$name.exe'),
    ];
    for (final file in locations) {
      if (await file.exists()) return file.absolute.path;
    }
    return name;
  }
}

import 'dart:convert';
import 'dart:io';

import 'batch_plan.dart';
import 'export_service.dart';
import 'merge_plan.dart';

/// 中止されたときに投げる。呼び出し側は結果を出さずに終える。
class MergeCancelled implements Exception {
  const MergeCancelled();
  @override
  String toString() => 'まとめる処理を中止しました';
}

/// つなげない理由が分かっている場合（形式が違う・上限を超える等）。
class MergeUnsupported implements Exception {
  const MergeUnsupported(this.message);
  final String message;
  @override
  String toString() => message;
}

/// 動画・音声の形式。これがそろっていれば無劣化でつなげる。
class StreamShape {
  const StreamShape(
      {required this.video,
      required this.width,
      required this.height,
      required this.frameRate,
      required this.audio,
      required this.sampleRate,
      required this.channels});
  final String video;
  final int width;
  final int height;
  final String frameRate;
  final String audio;
  final int sampleRate;
  final int channels;

  String get key => '$video/$width x$height/$frameRate/$audio/$sampleRate/$channels';

  /// 違いを利用者向けの言葉で並べる。
  List<String> differences(StreamShape other) => [
        if (video != other.video) '映像の形式（$video と ${other.video}）',
        if (width != other.width || height != other.height)
          '画面の大きさ（${width}x$height と ${other.width}x${other.height}）',
        if (frameRate != other.frameRate)
          'フレームレート（$frameRate と ${other.frameRate}）',
        if (audio != other.audio) '音声の形式（${_audioName(audio)} と ${_audioName(other.audio)}）',
        if (sampleRate != other.sampleRate)
          '音声のサンプリング（$sampleRate と ${other.sampleRate}）',
        if (channels != other.channels) '音声のチャンネル数（$channels と ${other.channels}）',
      ];

  static String _audioName(String codec) => codec.isEmpty ? '音声なし' : codec;

  /// ffprobe の JSON から読む。音声がない場合は空欄にする。
  factory StreamShape.fromProbe(String json) {
    final data = jsonDecode(json);
    final streams = (data is Map ? data['streams'] : null);
    if (streams is! List) throw const FormatException('形式を読み取れませんでした');
    Map<String, Object?>? find(String type) {
      for (final stream in streams) {
        if (stream is Map && stream['codec_type'] == type) {
          return stream.cast<String, Object?>();
        }
      }
      return null;
    }

    final video = find('video');
    if (video == null) throw const FormatException('映像が見つかりませんでした');
    final audio = find('audio');
    int number(Object? value) => (value as num?)?.toInt() ?? 0;
    return StreamShape(
      video: video['codec_name'] as String? ?? '',
      width: number(video['width']),
      height: number(video['height']),
      frameRate: video['r_frame_rate'] as String? ?? '',
      audio: audio?['codec_name'] as String? ?? '',
      sampleRate: number(audio?['sample_rate'] is String
          ? int.tryParse(audio!['sample_rate'] as String)
          : audio?['sample_rate']),
      channels: number(audio?['channels']),
    );
  }
}

/// まとめた結果。
class MergeOutcome {
  const MergeOutcome(
      {required this.output, required this.lossless, required this.elapsed});

  /// 保存先（Windowsはパス）。
  final String output;

  /// 無劣化（作り直しなし）でつないだか。
  final bool lossless;
  final Duration elapsed;
}

/// 進捗の段階。
enum MergePhase { cutting, checking, joining }

/// 複数の区間を1本につなぐ。Windowsは区間を一時ファイルへ切り出してから FFmpeg でつなぐ。
class MergeService {
  MergeService({ExportService? exporter})
      : exporter = exporter ?? ExportService();
  final ExportService exporter;

  bool _cancelled = false;
  Process? _running;

  /// 処理中の1件が終わった時点で止める。
  Future<void> cancel() async {
    _cancelled = true;
    _running?.kill();
  }

  void _checkCancelled() {
    if (_cancelled) throw const MergeCancelled();
  }

  Future<MergeOutcome> mergeWindows({
    required List<BatchEntry> entries,
    required String directory,
    DateTime? now,
    void Function(MergePhase phase, int done, int total)? onProgress,
  }) async {
    if (!Platform.isWindows) throw UnsupportedError('この環境ではまとめられません');
    if (entries.length < 2) throw ArgumentError('2区間以上を指定してください');
    _cancelled = false;
    final started = DateTime.now();
    final base = Directory(directory);
    if (!await base.exists()) throw StateError('保存先フォルダが見つかりません');
    final temp = await Directory.systemTemp.createTemp('kite_merge_');
    try {
      // 1) 区間ごとに一時ファイルへ切り出す（無劣化）。
      final parts = <String>[];
      for (var index = 0; index < entries.length; index++) {
        _checkCancelled();
        onProgress?.call(MergePhase.cutting, index, entries.length);
        final entry = entries[index];
        final part =
            '${temp.path}${Platform.pathSeparator}part_${index.toString().padLeft(3, '0')}.mp4';
        if (!await File(entry.item.path).exists()) {
          throw StateError('元動画が見つかりません：${entry.item.name}');
        }
        final result = await exporter.copyRange(entry.item.path,
            ClipRange(entry.segment.startMs, entry.segment.endMs), part);
        _checkCancelled();
        if (result.exitCode != 0 ||
            !await File(part).exists() ||
            await File(part).length() == 0) {
          throw StateError(
              '${entry.item.name} の区間${entry.segmentNumber}を切り出せませんでした。'
              '形式・空き容量を確認してください。');
        }
        parts.add(part);
      }
      onProgress?.call(MergePhase.cutting, entries.length, entries.length);

      // 2) 形式がそろっているか調べる。違えば無劣化ではつなげない。
      _checkCancelled();
      onProgress?.call(MergePhase.checking, 0, parts.length);
      final shapes = <StreamShape>[];
      for (final part in parts) {
        _checkCancelled();
        shapes.add(await _probe(part));
        onProgress?.call(MergePhase.checking, shapes.length, parts.length);
      }
      for (var index = 1; index < shapes.length; index++) {
        if (shapes[index].key == shapes.first.key) continue;
        final diff = shapes.first.differences(shapes[index]);
        throw MergeUnsupported('形式が違うため無劣化でつなげません：'
            '${entries.first.item.name} と ${entries[index].item.name} で'
            '${diff.join('、')}が異なります。');
      }

      // 3) つなぐ。
      _checkCancelled();
      onProgress?.call(MergePhase.joining, 0, 1);
      final listFile = File('${temp.path}${Platform.pathSeparator}list.txt');
      await listFile.writeAsString([
        // concat のリストでは ' を '\'' に置き換える。
        for (final part in parts)
          "file '${part.replaceAll(r'\', '/').replaceAll("'", r"'\''")}'",
      ].join('\n'));
      String path(String name) =>
          '${base.path}${Platform.pathSeparator}$name';
      final output = path(uniqueFileName(
          mergedFileName(now ?? DateTime.now()),
          (name) =>
              FileSystemEntity.typeSync(path(name)) !=
              FileSystemEntityType.notFound));
      final joined = await _run(await exporter.ffmpegPath(), [
        '-hide_banner', '-loglevel', 'error', '-n',
        '-f', 'concat', '-safe', '0', '-i', listFile.path,
        '-c', 'copy', '-movflags', '+faststart', output,
      ]);
      _checkCancelled();
      if (joined.exitCode != 0 ||
          !await File(output).exists() ||
          await File(output).length() == 0) {
        // 書きかけを残さない。
        if (await File(output).exists()) await File(output).delete();
        throw StateError('つなぐ処理に失敗しました。空き容量と保存先を確認してください。');
      }
      onProgress?.call(MergePhase.joining, 1, 1);
      return MergeOutcome(
          output: output,
          lossless: true,
          elapsed: DateTime.now().difference(started));
    } finally {
      // 一時ファイルは成功・失敗にかかわらず片付ける。
      if (await temp.exists()) await temp.delete(recursive: true);
    }
  }

  Future<StreamShape> _probe(String path) async {
    final result = await _run(await exporter.ffprobePath(), [
      '-v', 'error', '-show_entries',
      'stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels',
      '-of', 'json', path,
    ]);
    if (result.exitCode != 0) throw StateError('形式を調べられませんでした：$path');
    return StreamShape.fromProbe(result.stdout as String);
  }

  /// 中止で止められるよう、実行中のプロセスを覚えておく。
  Future<ProcessResult> _run(String executable, List<String> arguments) async {
    final process = await Process.start(executable, arguments);
    _running = process;
    try {
      final out = await process.stdout.transform(utf8.decoder).join();
      final err = await process.stderr.transform(utf8.decoder).join();
      final code = await process.exitCode;
      return ProcessResult(process.pid, code, out, err);
    } finally {
      _running = null;
    }
  }
}

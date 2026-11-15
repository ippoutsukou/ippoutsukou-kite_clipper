import 'dart:convert';
import 'dart:io';

import 'package:flutter/services.dart';

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

/// 形式が違う動画が混ざっているときの扱い（Windows）。
enum MergeMode {
  /// 形式がそろっているときだけつなぐ。違えば理由を出して中止する。
  strict,

  /// 形式ごとに分け、それぞれを無劣化で1本ずつ作る（複数ファイルになる）。
  grouped,

  /// 1本にする。フレームレートだけの違いは無劣化でつなぎ、それ以外は作り直す。
  forced,
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
      required this.channels,
      this.rotation = 0,
      this.bitRate = 0});
  final String video;
  final int width;
  final int height;
  final String frameRate;
  final String audio;
  final int sampleRate;
  final int channels;

  /// 再生時の回転（度）。スマホの縦撮りなどで 90 / -90 になる。
  final int rotation;

  /// 映像のビットレート（bps）。分からなければ 0。形式の照合には使わない。
  final int bitRate;

  String get key => '$joinKey/$frameRate';

  /// フレームレートを除いた印。これが同じなら、作り直さずに1本へつなげる。
  String get joinKey =>
      '$video/$width x$height/$rotation/$audio/$sampleRate/$channels';

  bool get _sideways => rotation.abs() % 180 == 90;

  /// 再生したときの大きさ（回転を反映）。
  int get displayWidth => _sideways ? height : width;
  int get displayHeight => _sideways ? width : height;

  /// フレームレートの表示（`30000/1001` → `29.97`）。読めなければ空。
  String get frameRateLabel {
    final parts = frameRate.split('/');
    final top = double.tryParse(parts.first);
    final bottom = parts.length > 1 ? double.tryParse(parts[1]) : 1.0;
    if (top == null || bottom == null || bottom == 0) return '';
    final value = top / bottom;
    return (value - value.round()).abs() < 0.005
        ? '${value.round()}'
        : value.toStringAsFixed(2);
  }

  /// 画面に出す名前（例 `1920x1080・30fps`）。
  String get label => '${displayWidth}x$displayHeight'
      '${frameRateLabel.isEmpty ? '' : '・${frameRateLabel}fps'}';

  /// ファイル名に付ける名前（例 `1920x1080_30fps`）。
  String get fileLabel => '${displayWidth}x$displayHeight'
      '${frameRateLabel.isEmpty ? '' : '_${frameRateLabel}fps'}';

  /// 違いを利用者向けの言葉で並べる。
  List<String> differences(StreamShape other) => [
        if (video != other.video) '映像の形式（$video と ${other.video}）',
        if (width != other.width || height != other.height)
          '画面の大きさ（${width}x$height と ${other.width}x${other.height}）',
        if (rotation != other.rotation)
          '映像の向き（$rotation° と ${other.rotation}°）',
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
    // ffprobe は数値を文字列で返す項目がある（sample_rate / bit_rate）。
    int number(Object? value) => switch (value) {
          num n => n.toInt(),
          String s => num.tryParse(s)?.toInt() ?? 0,
          _ => 0,
        };
    var rotation = 0;
    final sideData = video['side_data_list'];
    if (sideData is List) {
      for (final data in sideData) {
        if (data is Map && data['rotation'] != null) {
          rotation = number(data['rotation']);
        }
      }
    }
    return StreamShape(
      video: video['codec_name'] as String? ?? '',
      width: number(video['width']),
      height: number(video['height']),
      frameRate: video['r_frame_rate'] as String? ?? '',
      audio: audio?['codec_name'] as String? ?? '',
      sampleRate: number(audio?['sample_rate']),
      channels: number(audio?['channels']),
      rotation: rotation,
      bitRate: number(video['bit_rate']),
    );
  }
}

/// 同じ形式のまとまり。[indexes] は元の並びでの位置（小さい順）。
class ShapeGroup {
  ShapeGroup(this.shape);
  final StreamShape shape;
  final List<int> indexes = [];
}

/// 形式ごとにまとめる。まとまりの順は最初に出てきた順、中の順は元の並びのまま。
List<ShapeGroup> groupShapes(List<StreamShape> shapes) {
  final groups = <String, ShapeGroup>{};
  for (var index = 0; index < shapes.length; index++) {
    groups
        .putIfAbsent(shapes[index].key, () => ShapeGroup(shapes[index]))
        .indexes
        .add(index);
  }
  return groups.values.toList();
}

/// 作り直すときの映像の設定。
class EncodeTarget {
  const EncodeTarget(this.width, this.height, this.bitRate);
  final int width;
  final int height;
  final int bitRate;

  // H.264 の上限。一辺は実装側（Quick Sync 等）が 4096px で頭打ちになり、総画素は
  // Level 6.2 の 139264 マクロブロックまで（Python版 exceeds_h264_limit と同じ）。
  static const maxSide = 4096;
  static const maxPixels = 139264 * 16 * 16;
  static const _maxBitRate = 150000000;

  bool get exceedsH264 =>
      width > maxSide || height > maxSide || width * height > maxPixels;

  /// 一番大きい動画の大きさにそろえる（縮小による劣化を避ける）。
  /// ビットレートは、大きさに応じた目安と元動画の最大値のうち高いほうにする。
  factory EncodeTarget.from(List<StreamShape> shapes) {
    var best = shapes.first;
    var bitRate = 0;
    for (final shape in shapes) {
      if (shape.displayWidth * shape.displayHeight >
          best.displayWidth * best.displayHeight) {
        best = shape;
      }
      if (shape.bitRate > bitRate) bitRate = shape.bitRate;
    }
    // 4:2:0 では奇数の大きさを扱えないため、偶数へ切り上げる。
    final width = best.displayWidth + (best.displayWidth & 1);
    final height = best.displayHeight + (best.displayHeight & 1);
    final floor = _bitRateFor(width * height);
    final wanted = bitRate > floor ? bitRate : floor;
    return EncodeTarget(
        width, height, wanted > _maxBitRate ? _maxBitRate : wanted);
  }

  /// 大きさに応じたビットレートの目安（Python版 bitrate_for_resolution と同じ）。
  static int _bitRateFor(int pixels) {
    if (pixels >= 3840 * 2160 * 0.9) return 40000000;
    if (pixels >= 2560 * 1440 * 0.9) return 24000000;
    if (pixels >= 1920 * 1080 * 0.9) return 12000000;
    return 8000000;
  }
}

/// まとめる前に調べた、元動画の形式の内訳（Windows）。
class MergeFormats {
  MergeFormats(this.shapes) : groups = groupShapes(shapes);

  /// 区間ごとの形式（つなぐ順）。
  final List<StreamShape> shapes;
  final List<ShapeGroup> groups;

  /// 形式が2種類以上ある。
  bool get mixed => groups.length > 1;

  /// 違いがフレームレートだけ（作り直さずに1本へつなげる）。
  bool get frameRateOnly =>
      shapes.map((shape) => shape.joinKey).toSet().length == 1;

  EncodeTarget get target => EncodeTarget.from(shapes);

  /// 強制的に1本にできない理由。できるなら null。
  String? get forcedBlocked {
    if (frameRateOnly) return null;
    final size = target;
    return size.exceedsH264
        ? '${size.width}x${size.height} は作り直せる大きさの上限'
            '（一辺${EncodeTarget.maxSide}px）を超えています'
        : null;
  }
}

/// まとめた結果。
class MergeOutcome {
  const MergeOutcome(
      {required this.outputs, required this.lossless, required this.elapsed});

  /// 保存先（Windowsはパス）。形式ごとに分けた場合は複数になる。
  final List<String> outputs;
  String get output => outputs.first;

  /// 無劣化（作り直しなし）でつないだか。
  final bool lossless;
  final Duration elapsed;
}

/// 進捗の段階。
enum MergePhase { cutting, checking, encoding, joining }

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

  /// 結合用の映像の時刻の刻み。24/25/30/50/60/120/240fps と、その 1000/1001 倍を
  /// すべて割り切れる値にして、区間ごとに刻みが違っても時刻がずれないようにする。
  static const _timescale = 240000;

  /// まとめる前に元動画の形式を調べる（Windows）。調べられなければ null
  /// （その場合は選択を出さず、実行時の照合に任せる）。
  Future<MergeFormats?> inspectWindows(List<BatchEntry> entries) async {
    if (!Platform.isWindows) return null;
    try {
      final known = <String, StreamShape>{};
      final shapes = <StreamShape>[];
      for (final entry in entries) {
        shapes.add(known[entry.item.path] ??= await _probe(entry.item.path));
      }
      return MergeFormats(shapes);
    } catch (_) {
      return null;
    }
  }

  Future<MergeOutcome> mergeWindows({
    required List<BatchEntry> entries,
    required String directory,
    MergeMode mode = MergeMode.strict,
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
    // この実行で作った出力。失敗・中止のときは残さない。
    final created = <String>[];
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
            ClipRange(entry.segment.startMs, entry.segment.endMs), part,
            videoTimescale: _timescale);
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
      final groups = groupShapes(shapes);
      final stamp = now ?? DateTime.now();
      MergeOutcome done(bool lossless) => MergeOutcome(
          outputs: List.unmodifiable(created),
          lossless: lossless,
          elapsed: DateTime.now().difference(started));

      // 3) つなぐ。形式がそろっていれば、どの方法でも無劣化の1本になる。
      if (groups.length == 1) {
        onProgress?.call(MergePhase.joining, 0, 1);
        await _join(parts, base, mergedFileName(stamp), temp, created);
        onProgress?.call(MergePhase.joining, 1, 1);
        return done(true);
      }
      switch (mode) {
        case MergeMode.strict:
          final other = groups[1].indexes.first;
          final diff = shapes.first.differences(shapes[other]);
          throw MergeUnsupported('形式が違うため無劣化でつなげません：'
              '${entries.first.item.name} と ${entries[other].item.name} で'
              '${diff.join('、')}が異なります。');
        case MergeMode.grouped:
          // 形式ごとに1本ずつ。中の順番は一覧の順のまま。
          for (var index = 0; index < groups.length; index++) {
            onProgress?.call(MergePhase.joining, index, groups.length);
            final group = groups[index];
            await _join(
                [for (final i in group.indexes) parts[i]],
                base,
                mergedFileName(stamp, format: group.shape.fileLabel),
                temp,
                created);
          }
          onProgress?.call(
              MergePhase.joining, groups.length, groups.length);
          return done(true);
        case MergeMode.forced:
          final formats = MergeFormats(shapes);
          if (formats.frameRateOnly) {
            // フレームレートだけの違いは、作り直さずにつなげる（可変フレームレートになる）。
            onProgress?.call(MergePhase.joining, 0, 1);
            await _join(parts, base, mergedFileName(stamp), temp, created);
            onProgress?.call(MergePhase.joining, 1, 1);
            return done(true);
          }
          final blocked = formats.forcedBlocked;
          if (blocked != null) {
            throw MergeUnsupported('強制的に1本にできません：$blocked。'
                '「形式ごとに分けて保存」を選んでください。');
          }
          final target = formats.target;
          final encoder = await _videoEncoder();
          final withAudio = shapes.any((shape) => shape.audio.isNotEmpty);
          final encoded = <String>[];
          for (var index = 0; index < parts.length; index++) {
            _checkCancelled();
            onProgress?.call(MergePhase.encoding, index, parts.length);
            final output =
                '${temp.path}${Platform.pathSeparator}enc_${index.toString().padLeft(3, '0')}.mp4';
            final result = await _run(
                await exporter.ffmpegPath(),
                _encodeArguments(parts[index], output, target, encoder,
                    withAudio: withAudio,
                    hasAudio: shapes[index].audio.isNotEmpty));
            _checkCancelled();
            if (result.exitCode != 0 ||
                !await File(output).exists() ||
                await File(output).length() == 0) {
              throw StateError(
                  '${entries[index].item.name} の区間${entries[index].segmentNumber}を'
                  '作り直せませんでした（$encoder）。${_lastLine(result.stderr)}');
            }
            encoded.add(output);
          }
          onProgress?.call(MergePhase.encoding, parts.length, parts.length);
          onProgress?.call(MergePhase.joining, 0, 1);
          await _join(encoded, base, mergedFileName(stamp), temp, created);
          onProgress?.call(MergePhase.joining, 1, 1);
          return done(false);
      }
    } catch (_) {
      // 失敗・中止のときは、この実行で作った出力（書きかけを含む）を残さない。
      for (final output in created) {
        final file = File(output);
        if (await file.exists()) await file.delete();
      }
      rethrow;
    } finally {
      // 一時ファイルは成功・失敗にかかわらず片付ける。
      if (await temp.exists()) await temp.delete(recursive: true);
    }
  }

  /// 同じ形式の区間を、作り直さずに1本へつなぐ。出力のパスを [created] に足す。
  Future<void> _join(List<String> parts, Directory base, String name,
      Directory temp, List<String> created) async {
    _checkCancelled();
    final listFile = File(
        '${temp.path}${Platform.pathSeparator}list_${created.length}.txt');
    await listFile.writeAsString([
      // concat のリストでは ' を '\'' に置き換える。
      for (final part in parts)
        "file '${part.replaceAll(r'\', '/').replaceAll("'", r"'\''")}'",
    ].join('\n'));
    String path(String name) => '${base.path}${Platform.pathSeparator}$name';
    final output = path(uniqueFileName(
        name,
        (name) =>
            FileSystemEntity.typeSync(path(name)) !=
            FileSystemEntityType.notFound));
    // 書きかけも片付けられるよう、実行前に覚えておく（-n で既存ファイルは上書きしない）。
    created.add(output);
    final joined = await _run(await exporter.ffmpegPath(), [
      '-hide_banner', '-loglevel', 'error', '-n',
      '-f', 'concat', '-safe', '0', '-i', listFile.path,
      '-c', 'copy', '-movflags', '+faststart', output,
    ]);
    _checkCancelled();
    if (joined.exitCode != 0 ||
        !await File(output).exists() ||
        await File(output).length() == 0) {
      throw StateError('つなぐ処理に失敗しました。空き容量と保存先を確認してください。');
    }
  }

  /// 1区間を、決めた大きさ（余白は黒帯）へ作り直す引数。フレームレートは元のまま。
  /// 音声は、どれか1つにでもあれば全区間に付ける（ない区間は無音）。
  List<String> _encodeArguments(
          String input, String output, EncodeTarget target, String encoder,
          {required bool withAudio, required bool hasAudio}) =>
      [
        '-hide_banner', '-loglevel', 'error', '-n',
        '-i', input,
        if (withAudio && !hasAudio) ...[
          '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo',
        ],
        '-map', '0:v:0',
        if (withAudio) ...['-map', hasAudio ? '0:a:0' : '1:a:0'],
        '-vf',
        'scale=${target.width}:${target.height}'
            ':force_original_aspect_ratio=decrease:force_divisible_by=2'
            ':flags=lanczos,'
            'pad=${target.width}:${target.height}:(ow-iw)/2:(oh-ih)/2:black,'
            'setsar=1,format=yuv420p',
        '-c:v', encoder, '-b:v', '${target.bitRate}', '-pix_fmt', 'yuv420p',
        if (withAudio) ...[
          '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2',
        ] else
          '-an',
        if (withAudio && !hasAudio) '-shortest',
        '-video_track_timescale', '$_timescale',
        output,
      ];

  String? _encoder;

  /// 使えるエンコーダを選ぶ（Python版 detect_video_encoder と同じ順）。
  /// ハードウェア（Quick Sync / Media Foundation）を優先し、なければソフトウェア。
  Future<String> _videoEncoder() async {
    final known = _encoder;
    if (known != null) return known;
    final ffmpeg = await exporter.ffmpegPath();
    for (final candidate in const ['h264_qsv', 'h264_mf']) {
      _checkCancelled();
      final result = await _run(ffmpeg, [
        '-hide_banner', '-loglevel', 'error',
        '-f', 'lavfi', '-i', 'color=size=320x240:rate=10:duration=0.3',
        '-pix_fmt', 'yuv420p', '-c:v', candidate, '-f', 'null', '-',
      ]);
      if (result.exitCode == 0) return _encoder = candidate;
    }
    return _encoder = 'libopenh264';
  }

  static String _lastLine(Object? text) {
    final lines = '$text'
        .split('\n')
        .map((line) => line.trim())
        .where((line) => line.isNotEmpty)
        .toList();
    return lines.isEmpty ? '' : lines.last;
  }

  /// Android：保存先ファイルを選んでもらい、端末の仕組みで順につなぐ。
  /// 作り直し（再エンコード）はできないため、形式がそろっていない場合はネイティブ側が理由を返す。
  /// 保存先を選ばずに閉じた場合は中止として扱う。
  Future<MergeOutcome> mergeAndroid({
    required List<BatchEntry> entries,
    DateTime? now,
    void Function(MergePhase phase, int done, int total)? onProgress,
  }) async {
    // 環境の判定は呼び出し側で行う（ここはネイティブ側の応答だけを扱う）。
    if (entries.length < 2) throw ArgumentError('2区間以上を指定してください');
    _cancelled = false;
    final started = DateTime.now();
    ExportService.channel.setMethodCallHandler((call) async {
      if (call.method != 'mergeProgress') return null;
      final arguments = call.arguments;
      if (arguments is Map) {
        onProgress?.call(MergePhase.joining,
            (arguments['done'] as num?)?.toInt() ?? 0,
            (arguments['total'] as num?)?.toInt() ?? entries.length);
      }
      return null;
    });
    try {
      onProgress?.call(MergePhase.joining, 0, entries.length);
      final uri = await ExportService.channel.invokeMethod<String>('merge', {
        'name': mergedFileName(now ?? DateTime.now()),
        'parts': [
          for (final entry in entries)
            {
              'source': entry.item.path,
              'startMs': entry.segment.startMs,
              'endMs': entry.segment.endMs,
            }
        ],
      });
      if (uri == null || uri.isEmpty) throw const MergeCancelled();
      return MergeOutcome(
          outputs: [uri],
          lossless: true,
          elapsed: DateTime.now().difference(started));
    } on PlatformException catch (e) {
      if (e.code == 'merge' || e.code == 'arguments') {
        throw MergeUnsupported(e.message ?? '1本にまとめられませんでした');
      }
      rethrow;
    } finally {
      ExportService.channel.setMethodCallHandler(null);
    }
  }

  Future<StreamShape> _probe(String path) async {
    final result = await _run(await exporter.ffprobePath(), [
      '-v', 'error', '-show_entries',
      'stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate,'
          'channels,bit_rate:stream_side_data=rotation',
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

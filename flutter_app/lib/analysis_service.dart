import 'dart:async';
import 'dart:io';

import 'package:flutter/services.dart';

import 'ai_segments.dart';

/// 同梱している競技モデル（assets/models/<id>.json）。
const sportModelIds = ['kite', 'wing', 'windsurf', 'kite_static'];

/// 1回の解析で扱うフレームの上限（1秒間隔で1時間分）。超える場合は間隔を広げる。
const maxAnalysisFrames = 3600;

Future<SportModel> loadSportModel(String id) async =>
    SportModel.fromJson(await rootBundle.loadString('assets/models/$id.json'));

/// キーフレームだけで希望の粒度を保てるか。
///
/// キーフレームの間隔は動画によって違う（実測：GoPro 0.94秒、別の動画 2.0秒）。
/// 間隔が希望の3倍を超えて粗い場合は、キーフレーム方式では粒度が足りないため使わない。
bool keyframesUsable(List<int> timesMs, int intervalMs) {
  if (timesMs.length < 2 || intervalMs <= 0) return false;
  final gaps = [
    for (var i = 1; i < timesMs.length; i++) timesMs[i] - timesMs[i - 1]
  ]..sort();
  final median = gaps[gaps.length ~/ 2];
  return median <= intervalMs * 3;
}

/// 動画の長さと希望の間隔から、上限に収まる間隔を決める。
int resolveIntervalMs(
    {required int durationMs, required int wantedMs, int maxFrames = maxAnalysisFrames}) {
  if (durationMs <= 0 || wantedMs <= 0 || maxFrames <= 0) return wantedMs;
  final frames = (durationMs / wantedMs).ceil();
  if (frames <= maxFrames) return wantedMs;
  return (durationMs / maxFrames).ceil();
}

/// AI解析1回分の結果と所要時間。
class AnalysisResult {
  const AnalysisResult({
    required this.durationMs,
    required this.timesMs,
    required this.scores,
    this.scoresByModel = const {},
    required this.modelMs,
    required this.extractMs,
    required this.inferMs,
    required this.totalMs,
    required this.truncated,
    required this.cancelled,
  });

  final int durationMs;
  final List<int> timesMs;

  /// 選択した競技モデルの点数。
  final List<double> scores;

  /// 競技モデルごとの点数（キーは競技ID）。
  /// 保存済みの結果から作った場合は、選択したモデルのみを含む。
  final Map<String, List<double>> scoresByModel;

  /// モデルの読み込み（初回のみ時間がかかる）。
  final int modelMs;
  final int extractMs;
  final int inferMs;
  final int totalMs;

  /// 上限に達して打ち切ったか。
  final bool truncated;

  /// 中止で途中まで解析したか。
  final bool cancelled;

  int get frames => timesMs.length;

  /// 実際に採点したフレームの平均間隔（ミリ秒）。
  /// キーフレーム方式では動画のキーフレーム間隔に依存するため、希望どおりとは限らない。
  double get averageIntervalMs =>
      frames < 2 ? 0 : (timesMs.last - timesMs.first) / (frames - 1);

  /// 動画1分あたりの解析時間（秒）。解析できた範囲から換算する。
  double get secondsPerVideoMinute {
    final analyzedMs = frames <= 1 ? durationMs : timesMs.last + 1000;
    if (analyzedMs <= 0) return 0;
    return totalMs / analyzedMs * 60;
  }

  /// ネイティブ側からの結果を読む。点数はモデルごとに、渡した競技IDと同じ並びで返る。
  factory AnalysisResult.fromMap(
      Map<String, Object?> map, List<String> modelIds) {
    int number(String key) => (map[key] as num?)?.toInt() ?? 0;
    final times = (map['timesMs'] as List?) ?? const [];
    final raw = (map['scores'] as List?) ?? const [];
    if (raw.length != modelIds.length) {
      throw const FormatException('解析結果のモデル数が一致しません');
    }
    final byModel = <String, List<double>>{};
    for (var i = 0; i < modelIds.length; i++) {
      final column = (raw[i] as List?) ?? const [];
      if (column.length != times.length) {
        throw const FormatException('解析結果の件数が一致しません');
      }
      byModel[modelIds[i]] = [for (final v in column) (v as num).toDouble()];
    }
    return AnalysisResult(
      durationMs: number('durationMs'),
      timesMs: [for (final value in times) (value as num).toInt()],
      scores: byModel[modelIds.first]!,
      scoresByModel: byModel,
      modelMs: number('modelMs'),
      extractMs: number('extractMs'),
      inferMs: number('inferMs'),
      totalMs: number('totalMs'),
      truncated: map['truncated'] == true,
      cancelled: map['cancelled'] == true,
    );
  }
}

/// 端末内でCLIPを動かして候補を点数化する。
///
/// Android：フレーム取り出しから採点までネイティブ側で行う。
/// Windows：FFmpegで224x224のRGBを取り出し、CLIP推論だけをネイティブ側へ渡す。
class AnalysisService {
  static const channel = MethodChannel('kite_clipper/analyze');
  static const clipChannel = MethodChannel('kite_clipper/clip');
  static bool get supported => Platform.isAndroid || Platform.isWindows;

  /// Windowsの解析を中止するための印。
  bool _cancelRequested = false;

  /// [others] を渡すと、同じ取り出し結果を使って他の競技モデルでも採点する。
  /// CLIPの計算は共通のため、モデルを増やしても所要時間はほとんど変わらない。
  Future<AnalysisResult> analyze({
    required String source,
    required SportModel model,
    List<SportModel> others = const [],
    int? intervalMs,
    void Function(int done, int total)? onProgress,
  }) async {
    final interval = intervalMs ?? model.frameIntervalMs;
    final models = [model, ...others];
    if (Platform.isWindows) {
      return _analyzeWindows(
          source: source,
          models: models,
          intervalMs: interval,
          onProgress: onProgress);
    }
    if (!Platform.isAndroid) {
      throw UnsupportedError('この環境ではAI解析に対応していません');
    }
    if (onProgress != null) {
      channel.setMethodCallHandler((call) async {
        if (call.method == 'progress') {
          final arguments = call.arguments;
          if (arguments is Map) {
            onProgress((arguments['done'] as num?)?.toInt() ?? 0,
                (arguments['total'] as num?)?.toInt() ?? 0);
          }
        }
        return null;
      });
    }
    try {
      final result = await channel.invokeMapMethod<String, Object?>('analyze', {
        'source': source,
        'intervalMs': interval,
        'maxFrames': maxAnalysisFrames,
        'coefs': [for (final m in models) m.coefficients],
        'intercepts': [for (final m in models) m.intercept],
      });
      if (result == null) throw StateError('解析結果を受け取れませんでした');
      return AnalysisResult.fromMap(result, [for (final m in models) m.id]);
    } finally {
      channel.setMethodCallHandler(null);
    }
  }

  /// 処理中の1枚が終わった時点で止める。
  Future<void> cancel() async {
    _cancelRequested = true;
    if (Platform.isAndroid) {
      await channel.invokeMethod<void>('cancelAnalyze');
    }
  }

  /// キーフレームだけを取り出してCLIPに通す。
  ///
  /// 全フレームをデコードすると4K・HEVC・60fpsで極端に遅くなるため、`-skip_frame nokey` で
  /// キーフレームのみをデコードする（実測で約45分の1）。キーフレームの時刻は ffprobe で先に取得し、
  /// 希望の間隔より細かい分は読み飛ばす。前処理（短辺リサイズ→中央切り出し）はFFmpegに任せる。
  Future<AnalysisResult> _analyzeWindows({
    required String source,
    required List<SportModel> models,
    required int intervalMs,
    void Function(int done, int total)? onProgress,
  }) async {
    _cancelRequested = false;
    final started = DateTime.now();
    final durationMs = await _probeDurationMs(source);
    if (durationMs <= 0) throw StateError('動画の長さを取得できませんでした');
    final keyframes = await _probeKeyframeTimesMs(source);
    // キーフレームを取得できない動画や、間隔が粗すぎる動画では、
    // 間隔指定で取り出す方式に切り替える（遅いが希望の粒度を保てる）。
    final useKeyframes = keyframesUsable(keyframes, intervalMs);
    final wanted = <int>{};
    if (useKeyframes) {
      // 希望間隔に満たない間隔のキーフレームは採点しない（キーフレームより細かくはできない）。
      var last = -intervalMs;
      for (final time in keyframes) {
        if (time - last < intervalMs - 100) continue;
        if (wanted.length >= maxAnalysisFrames) break;
        wanted.add(time);
        last = time;
      }
    }
    final total = useKeyframes
        ? wanted.length
        : (durationMs / intervalMs).ceil().clamp(1, maxAnalysisFrames);
    final filter = 'scale=$clipImageSize:$clipImageSize:'
        'force_original_aspect_ratio=increase,crop=$clipImageSize:$clipImageSize';
    // 取り出しと採点を「時間的に」分ける。採点は画面と同じ流れで動くため、
    // 読み取りと同時に進めても順番待ちになり効果がなかった（実測で確認）。
    // 先に全フレームを一時ファイルへ書き出すと、取り出し中は採点が挟まらず本来の速度が出る。
    final temp = File('${Directory.systemTemp.path}${Platform.pathSeparator}'
        'kite_clipper_${DateTime.now().millisecondsSinceEpoch}.rgb');
    final timesMs = <int>[];
    // 競技モデルごとの点数。採点は係数との内積のみのため、モデルを増やしても負担は小さい。
    final byModel = {for (final m in models) m.id: <double>[]};
    var extractMs = 0;
    var inferMs = 0;
    var truncated = false;
    try {
      // 1) 取り出し：採点を挟まずに一気に書き出す。
      final extractStart = DateTime.now();
      final process = await Process.start(await _ffmpeg(), [
        '-v', 'error', '-nostdin',
        if (useKeyframes) ...['-skip_frame', 'nokey'],
        '-i', source,
        if (useKeyframes) ...['-fps_mode', 'passthrough'],
        '-vf',
        useKeyframes
            ? filter
            : 'fps=${(1000 / intervalMs).toStringAsFixed(6)},$filter',
        '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1',
      ]);
      final errors = process.stderr.drain<void>();
      // 書き出した大きさから取り出しの進捗を出す。中止もここで受ける。
      final ticker = Timer.periodic(const Duration(milliseconds: 200), (_) {
        if (_cancelRequested) {
          process.kill();
          return;
        }
        if (!temp.existsSync()) return;
        onProgress?.call(
            (temp.lengthSync() ~/ clipFrameBytes).clamp(0, total), total);
      });
      try {
        await process.stdout.pipe(temp.openWrite());
        await errors;
        await process.exitCode;
      } finally {
        ticker.cancel();
      }
      extractMs = DateTime.now().difference(extractStart).inMilliseconds;

      // 2) 採点：書き出した順番と ffprobe の時刻を対応させながら1枚ずつ。
      final file = await temp.open();
      try {
        final count = (await temp.length()) ~/ clipFrameBytes;
        for (var index = 0; index < count; index++) {
          if (_cancelRequested) break;
          final int time;
          if (useKeyframes) {
            if (index >= keyframes.length) {
              truncated = true;
              break;
            }
            time = keyframes[index];
            if (!wanted.contains(time)) continue;
          } else {
            if (timesMs.length >= maxAnalysisFrames) {
              truncated = true;
              break;
            }
            time = index * intervalMs;
          }
          await file.setPosition(index * clipFrameBytes);
          final Uint8List frame = await file.read(clipFrameBytes);
          final inferStart = DateTime.now();
          final embedding = await clipChannel
              .invokeListMethod<double>('embed', {'pixels': frame});
          inferMs += DateTime.now().difference(inferStart).inMilliseconds;
          if (embedding == null ||
              embedding.length != SportModel.embeddingSize) {
            throw StateError('CLIPの結果を受け取れませんでした');
          }
          timesMs.add(time);
          for (final m in models) {
            byModel[m.id]!.add(m.score(embedding));
          }
          onProgress?.call(timesMs.length, total);
        }
      } finally {
        await file.close();
      }
    } finally {
      // 自分で作った一時ファイルだけを消す。
      if (await temp.exists()) await temp.delete();
    }
    final totalMs = DateTime.now().difference(started).inMilliseconds;
    return AnalysisResult(
      durationMs: durationMs,
      timesMs: timesMs,
      scores: byModel[models.first.id]!,
      scoresByModel: byModel,
      modelMs: 0,
      extractMs: extractMs,
      inferMs: inferMs,
      totalMs: totalMs,
      truncated: truncated,
      cancelled: _cancelRequested,
    );
  }

  /// キーフレームの時刻（ミリ秒）を昇順で返す。デコードしないため高速。
  Future<List<int>> _probeKeyframeTimesMs(String source) async {
    final result = await Process.run(await _tool('ffprobe'), [
      '-v', 'error', '-select_streams', 'v:0', '-skip_frame', 'nokey',
      '-show_entries', 'frame=pts_time', '-of', 'csv=p=0', source,
    ]);
    final times = <int>[];
    for (final line in (result.stdout as String).split('\n')) {
      // 動画によっては "0.966667," のように区切りが付いて出力される。
      final seconds = double.tryParse(line.split(',').first.trim());
      if (seconds != null && seconds >= 0) times.add((seconds * 1000).round());
    }
    times.sort();
    return times;
  }

  Future<int> _probeDurationMs(String source) async {
    final result = await Process.run(await _tool('ffprobe'), [
      '-v', 'error', '-show_entries', 'format=duration',
      '-of', 'default=nw=1:nk=1', source,
    ]);
    final seconds = double.tryParse((result.stdout as String).trim());
    return seconds == null ? 0 : (seconds * 1000).round();
  }

  Future<String> _ffmpeg() => _tool('ffmpeg');

  /// ffmpeg / ffprobe を同じ場所から探す。見つからなければ名前だけ返しPATHに任せる。
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

  /// Android：一括保存中だけ画面の消灯を防ぐ。他の環境では何もしない。
  Future<void> keepScreenOn(bool on) async {
    if (!Platform.isAndroid) return;
    await channel.invokeMethod<void>('keepScreenOn', {'on': on});
  }
}

const clipImageSize = 224;
const clipFrameBytes = clipImageSize * clipImageSize * 3;

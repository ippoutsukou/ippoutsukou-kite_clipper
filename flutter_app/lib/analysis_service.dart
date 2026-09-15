import 'dart:io';

import 'package:flutter/services.dart';

import 'ai_segments.dart';

/// 同梱している競技モデル（assets/models/<id>.json）。
const sportModelIds = ['kite', 'wing', 'windsurf', 'kite_static'];

Future<SportModel> loadSportModel(String id) async =>
    SportModel.fromJson(await rootBundle.loadString('assets/models/$id.json'));

/// AI解析1回分の結果と所要時間（速度測定用）。
class AnalysisResult {
  const AnalysisResult({
    required this.durationMs,
    required this.timesMs,
    required this.scores,
    required this.modelMs,
    required this.extractMs,
    required this.inferMs,
    required this.totalMs,
    required this.truncated,
  });

  final int durationMs;
  final List<int> timesMs;
  final List<double> scores;

  /// モデルの読み込み（初回のみ時間がかかる）。
  final int modelMs;
  final int extractMs;
  final int inferMs;
  final int totalMs;

  /// 上限に達して途中で打ち切ったか。
  final bool truncated;

  int get frames => timesMs.length;

  /// 動画1分あたりの解析時間（秒）。解析できた範囲から換算する。
  double get secondsPerVideoMinute {
    final analyzedMs = frames <= 1 ? durationMs : timesMs.last + 1000;
    if (analyzedMs <= 0) return 0;
    return totalMs / analyzedMs * 60;
  }

  factory AnalysisResult.fromMap(Map<String, Object?> map) {
    int number(String key) => (map[key] as num?)?.toInt() ?? 0;
    final times = (map['timesMs'] as List?) ?? const [];
    final scores = (map['scores'] as List?) ?? const [];
    if (times.length != scores.length) {
      throw const FormatException('解析結果の件数が一致しません');
    }
    return AnalysisResult(
      durationMs: number('durationMs'),
      timesMs: [for (final value in times) (value as num).toInt()],
      scores: [for (final value in scores) (value as num).toDouble()],
      modelMs: number('modelMs'),
      extractMs: number('extractMs'),
      inferMs: number('inferMs'),
      totalMs: number('totalMs'),
      truncated: map['truncated'] == true,
    );
  }
}

/// 端末内でCLIPを動かして候補を点数化する。測定段階ではAndroidのみ。
class AnalysisService {
  static const channel = MethodChannel('kite_clipper/analyze');
  static bool get supported => Platform.isAndroid;

  Future<AnalysisResult> analyze(
      {required String source,
      required SportModel model,
      int maxFrames = 300}) async {
    if (!supported) {
      throw UnsupportedError('この測定用ビルドでは Android のみ解析できます');
    }
    final result = await channel.invokeMapMethod<String, Object?>('analyze', {
      'source': source,
      'intervalMs': model.frameIntervalMs,
      'maxFrames': maxFrames,
      'coef': model.coefficients,
      'intercept': model.intercept,
    });
    if (result == null) throw StateError('解析結果を受け取れませんでした');
    return AnalysisResult.fromMap(result);
  }
}

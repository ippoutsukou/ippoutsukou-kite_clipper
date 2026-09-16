import 'dart:convert';
import 'dart:math' as math;

import 'clip_session.dart';

/// 競技ごとの判定モデル。frame_scorer.py の学習結果（CLIP埋め込み＋ロジスティック回帰）を
/// 係数だけ書き出したもの。assets/models/<id>.json と対応する。
class SportModel {
  const SportModel({
    required this.id,
    required this.displayName,
    required this.enter,
    required this.exit,
    required this.smoothWindow,
    required this.frameIntervalMs,
    required this.preRollMs,
    required this.postRollMs,
    required this.intercept,
    required this.coefficients,
  });

  final String id;
  final String displayName;
  final double enter;
  final double exit;
  final int smoothWindow;
  final int frameIntervalMs;
  final int preRollMs;
  final int postRollMs;
  final double intercept;
  final List<double> coefficients;

  static const embeddingSize = 512;

  factory SportModel.fromJson(String text) {
    final data = jsonDecode(text);
    if (data is! Map || data['schema_version'] != 1) {
      throw const FormatException('判定モデルの形式が違います');
    }
    final coef = data['coef'];
    if (coef is! List || coef.length != embeddingSize) {
      throw const FormatException('判定モデルの係数が$embeddingSize個ではありません');
    }
    double number(Object? value, double fallback) =>
        value is num ? value.toDouble() : fallback;
    final enter = number(data['enter'], 0.5);
    final exit = number(data['exit'], 0.4);
    if (enter <= exit) {
      throw const FormatException('開始しきい値は終了しきい値より大きい必要があります');
    }
    return SportModel(
      id: data['id'] as String,
      displayName: data['display_name'] as String? ?? data['id'] as String,
      enter: enter,
      exit: exit,
      smoothWindow: (data['smooth_window'] as num?)?.toInt() ?? 3,
      frameIntervalMs:
          (number(data['frame_interval'], 1.0) * 1000).round().clamp(100, 10000),
      preRollMs: (number(data['pre_roll'], 1.0) * 1000).round().clamp(0, 10000),
      postRollMs:
          (number(data['post_roll'], 1.0) * 1000).round().clamp(0, 10000),
      intercept: number(data['intercept'], 0.0),
      coefficients: [for (final value in coef) (value as num).toDouble()],
    );
  }

  /// L2正規化済みのCLIP埋め込みから採用確率を求める（sklearn の predict_proba と同じ計算）。
  double score(List<double> embedding) {
    if (embedding.length != coefficients.length) {
      throw ArgumentError('埋め込みの次元が違います');
    }
    var sum = intercept;
    for (var i = 0; i < coefficients.length; i++) {
      sum += coefficients[i] * embedding[i];
    }
    return 1 / (1 + math.exp(-sum));
  }
}

/// 提案する区間。前後の余白（pre_roll / post_roll）を足した後の値。
class ProposedSegment {
  const ProposedSegment(this.startMs, this.endMs, this.meanScore);
  final int startMs;
  final int endMs;
  final double meanScore;
  int get lengthMs => endMs - startMs;
}

/// AIの提案を一覧へ反映した結果。
class AppliedSegments {
  const AppliedSegments(
      {required this.added, required this.skipped, required this.replaced});

  /// 実際に追加した区間数。
  final int added;

  /// 上限のため追加できなかった区間数。
  final int skipped;

  /// 触っていない初期区間を置き換えたか。
  final bool replaced;
}

/// 提案区間を動画へ反映する。初期状態なら置き換え、そうでなければ既存の後ろに足す。
/// 上限（20区間）を超える分は追加しない。最初に追加した区間を選んだ状態で返す。
AppliedSegments applyProposedSegments(
    ClipItem item, List<ProposedSegment> proposals) {
  if (proposals.isEmpty) {
    return const AppliedSegments(added: 0, skipped: 0, replaced: false);
  }
  final replace = item.isUntouchedDefault;
  var added = 0;
  var firstIndex = -1;
  for (final proposal in proposals) {
    if (item.segments.length >= ClipItem.maxSegments) break;
    final index = item.addSegmentRange(proposal.startMs, proposal.endMs);
    if (added == 0) {
      firstIndex = index;
      // 触っていない初期区間は、最初の提案を入れてから取り除く。
      if (replace) {
        item.removeSegment(0);
        firstIndex = 0;
      }
    }
    added++;
  }
  if (firstIndex >= 0) item.selectSegment(firstIndex);
  return AppliedSegments(
      added: added, skipped: proposals.length - added, replaced: replace);
}

/// 移動平均（窓＝window、中央寄せ）。segment_simulator.py の smooth_scores と同じ。
List<double> smoothScores(List<double> scores, int window) {
  if (window <= 1 || scores.isEmpty) return List.of(scores);
  final half = window ~/ 2;
  return [
    for (var i = 0; i < scores.length; i++)
      () {
        final low = math.max(0, i - half);
        final high = math.min(scores.length, i + half + 1);
        var sum = 0.0;
        for (var j = low; j < high; j++) {
          sum += scores[j];
        }
        return sum / (high - low);
      }()
  ];
}

/// ヒステリシスで区間を作る（平滑化後の点数が enter 以上で開始、exit 未満で終了）。
/// 平均点は平滑化前の点数から求める。Python 版 build_segments_for_video と同じ考え方。
List<ProposedSegment> buildProposedSegments({
  required List<int> timesMs,
  required List<double> scores,
  required SportModel model,
  required int durationMs,
  int minLengthMs = 100,
}) {
  if (timesMs.length != scores.length) {
    throw ArgumentError('時刻と点数の件数が違います');
  }
  if (timesMs.isEmpty || durationMs <= 0) return [];
  final smoothed = smoothScores(scores, model.smoothWindow);
  final segments = <ProposedSegment>[];
  var inSegment = false;
  var startTime = 0;
  var lastHighTime = 0;
  var startIndex = 0;

  void close(int endIndex) {
    final count = endIndex - startIndex;
    var sum = 0.0;
    for (var i = startIndex; i < endIndex; i++) {
      sum += scores[i];
    }
    final start = (startTime - model.preRollMs).clamp(0, durationMs);
    var end = (lastHighTime + model.postRollMs).clamp(0, durationMs);
    if (end - start < minLengthMs) {
      end = math.min(durationMs, start + minLengthMs);
    }
    if (end - start >= minLengthMs) {
      segments.add(ProposedSegment(start, end, sum / count));
    }
  }

  for (var index = 0; index < timesMs.length; index++) {
    final score = smoothed[index];
    if (!inSegment) {
      if (score >= model.enter) {
        inSegment = true;
        startTime = timesMs[index];
        lastHighTime = timesMs[index];
        startIndex = index;
      }
    } else if (score >= model.exit) {
      lastHighTime = timesMs[index];
    } else {
      close(index);
      inSegment = false;
    }
  }
  if (inSegment) close(timesMs.length);
  return segments;
}

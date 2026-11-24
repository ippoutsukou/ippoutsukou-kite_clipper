import 'ai_segments.dart';
import 'analysis_service.dart';
import 'clip_session.dart';

/// おまかせモードの設定。ONの間に追加した動画を、この条件で解析して区間を入れる。
class OmakaseSettings {
  const OmakaseSettings(
      {this.enabled = false,
      this.sportId = 'kite',
      this.intervalMs = 1000});
  final bool enabled;
  final String sportId;
  final int intervalMs;

  static const intervals = [1000, 2000];

  Map<String, Object?> toJson() =>
      {'enabled': enabled, 'sportId': sportId, 'intervalMs': intervalMs};

  /// 記録が不正な場合はOFF（初期値）に戻す。おまかせ設定だけの問題で作業記録全体は捨てない。
  factory OmakaseSettings.fromJson(Object? raw) {
    if (raw is! Map) return const OmakaseSettings();
    final enabled = raw['enabled'], sport = raw['sportId'], interval = raw['intervalMs'];
    if (enabled is! bool ||
        sport is! String ||
        !sportModelIds.contains(sport) ||
        interval is! int ||
        !intervals.contains(interval)) {
      return const OmakaseSettings();
    }
    return OmakaseSettings(enabled: enabled, sportId: sport, intervalMs: interval);
  }
}

enum OmakaseStatus {
  /// 区間を追加した。
  added,

  /// 解析したが提案が0個だった。
  noProposals,

  /// 解析に失敗した。
  failed,

  /// 中止したため処理していない。
  skipped,
}

/// おまかせで動画1本を処理した結果。
class OmakaseOutcome {
  const OmakaseOutcome(this.name, this.status,
      {this.added = 0, this.overLimit = 0, this.partial = false, this.message});
  final String name;
  final OmakaseStatus status;
  final int added;

  /// 上限（20区間）のため追加できなかった数。
  final int overLimit;

  /// 中止・打ち切りで途中までの結果から作ったか。
  final bool partial;
  final String? message;

  String describe() => switch (status) {
        OmakaseStatus.added => '区間$added個を追加'
            '${overLimit > 0 ? '（$overLimit個は上限のため追加せず）' : ''}'
            '${partial ? '（途中までの結果）' : ''}',
        OmakaseStatus.noProposals => partial
            ? '提案0個（区間は変更なし）（途中までの結果）'
            : '提案0個（保存・まとめる対象から外しました）',
        OmakaseStatus.failed => '失敗：${message ?? '原因不明'}',
        OmakaseStatus.skipped => '中止のため未処理',
      };
}

/// 解析結果を動画へ反映する。手動の「区間として追加」と同じ作り方・入れ方。
/// 解析結果は動画に保存し、手動のAI解析で「保存済みを使う」ができるようにする。
OmakaseOutcome applyOmakaseResult({
  required ClipItem item,
  required AnalysisResult result,
  required SportModel model,
  required String sportId,
  required int intervalMs,
}) {
  final partial = result.cancelled || result.truncated;
  // まだ開いていない動画は長さが未確定のため、解析で取得した長さを使う。
  if (item.durationMs == null && result.durationMs > 0) {
    item.setDuration(result.durationMs);
  }
  item.analysis = AnalysisRecord(
      sportId: sportId,
      intervalMs: intervalMs,
      durationMs: result.durationMs,
      timesMs: result.timesMs,
      scores: result.scores,
      partial: partial);
  final duration = item.durationMs ?? 0;
  final proposals = buildProposedSegments(
      timesMs: result.timesMs,
      scores: result.scores,
      model: model,
      durationMs: duration < result.durationMs ? duration : result.durationMs);
  // 途中までの結果で0個なら、残りに提案があるかもしれないため外さない。
  item.noProposals = proposals.isEmpty && !partial;
  if (proposals.isEmpty) {
    return OmakaseOutcome(item.name, OmakaseStatus.noProposals, partial: partial);
  }
  final applied = applyProposedSegments(item, proposals);
  return OmakaseOutcome(item.name, OmakaseStatus.added,
      added: applied.added, overLimit: applied.skipped, partial: partial);
}

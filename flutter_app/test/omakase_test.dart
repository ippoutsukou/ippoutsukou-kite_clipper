import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:kite_clipper_flutter/ai_segments.dart';
import 'package:kite_clipper_flutter/analysis_service.dart';
import 'package:kite_clipper_flutter/clip_session.dart';
import 'package:kite_clipper_flutter/omakase.dart';
import 'package:kite_clipper_flutter/session_store.dart';

const model = SportModel(
  id: 'kite',
  displayName: 'kite',
  enter: 0.5,
  exit: 0.4,
  smoothWindow: 1,
  frameIntervalMs: 1000,
  preRollMs: 0,
  postRollMs: 0,
  intercept: 0,
  coefficients: [],
);

/// 1秒ごとの点数から解析結果を作る。
AnalysisResult result(List<double> scores,
        {int durationMs = 10000, bool cancelled = false}) =>
    AnalysisResult(
      durationMs: durationMs,
      timesMs: [for (var i = 0; i < scores.length; i++) i * 1000],
      scores: scores,
      modelMs: 0,
      extractMs: 0,
      inferMs: 0,
      totalMs: 0,
      truncated: false,
      cancelled: cancelled,
    );

OmakaseOutcome apply(ClipItem item, AnalysisResult r) => applyOmakaseResult(
    item: item, result: r, model: model, sportId: 'kite', intervalMs: 1000);

void main() {
  group('設定', () {
    test('保存と読み込みで値が変わらない', () {
      const settings =
          OmakaseSettings(enabled: true, sportId: 'wing', intervalMs: 2000);
      final back = OmakaseSettings.fromJson(
          jsonDecode(jsonEncode(settings.toJson())));
      expect(back.enabled, isTrue);
      expect(back.sportId, 'wing');
      expect(back.intervalMs, 2000);
    });

    test('不正な記録はOFFに戻す', () {
      for (final raw in [
        null,
        'x',
        {'enabled': 'yes', 'sportId': 'kite', 'intervalMs': 1000},
        {'enabled': true, 'sportId': 'unknown', 'intervalMs': 1000},
        {'enabled': true, 'sportId': 'kite', 'intervalMs': 1500},
      ]) {
        expect(OmakaseSettings.fromJson(raw).enabled, isFalse, reason: '$raw');
      }
    });

    test('作業記録に残り、古い記録（設定なし）はOFFで読める', () {
      final session = ClipSession()..add(['a.mp4']);
      const settings =
          OmakaseSettings(enabled: true, sportId: 'kite_static', intervalMs: 1000);
      final restored = decodeSession(
          encodeSession(session, omakase: settings),
          exists: (_) => true);
      expect(restored.omakase.enabled, isTrue);
      expect(restored.omakase.sportId, 'kite_static');

      final old = jsonDecode(encodeSession(session)) as Map<String, dynamic>
        ..remove('omakase');
      final fromOld = decodeSession(jsonEncode(old), exists: (_) => true);
      expect(fromOld.omakase.enabled, isFalse);
      expect(fromOld.session.items, hasLength(1));
    });
  });

  group('解析結果の反映', () {
    test('まだ開いていない動画は長さを設定し、初期区間を提案で置き換える', () {
      final item = ClipItem('a.mp4');
      final outcome =
          apply(item, result([0.1, 0.9, 0.9, 0.1, 0.1, 0.8, 0.1]));
      expect(outcome.status, OmakaseStatus.added);
      expect(outcome.added, 2);
      expect(item.durationMs, 10000);
      expect(item.segments.map((s) => [s.startMs, s.endMs]).toList(), [
        [1000, 2000],
        [5000, 5100],
      ]);
      expect(item.analysis!.matches(
          sportId: 'kite', intervalMs: 1000, durationMs: 10000), isTrue);
    });

    test('提案0個なら区間は変えない', () {
      final item = ClipItem('a.mp4')..setDuration(10000);
      final outcome = apply(item, result([0.1, 0.2, 0.3]));
      expect(outcome.status, OmakaseStatus.noProposals);
      expect(item.isUntouchedDefault, isTrue);
      expect(item.analysis, isNotNull);
    });

    test('利用者が触った区間は残し、後ろに足す', () {
      final item = ClipItem('a.mp4')..setDuration(10000);
      item.setRange(3000, 4000);
      apply(item, result([0.9, 0.9, 0.1]));
      expect(item.segments.first.startMs, 3000);
      expect(item.segments, hasLength(2));
    });

    test('上限を超える分は追加せず、数を返す', () {
      final item = ClipItem('a.mp4')..setDuration(100000);
      item.setRange(0, 500);
      while (item.segments.length < ClipItem.maxSegments - 1) {
        item.addSegmentRange(0, 500);
      }
      final outcome = apply(
          item, result([0.9, 0.1, 0.9, 0.1, 0.9], durationMs: 100000));
      expect(outcome.added, 1);
      expect(outcome.overLimit, 2);
      expect(item.segments, hasLength(ClipItem.maxSegments));
    });

    test('中止した結果は途中までとして記録し、再利用の対象にしない', () {
      final item = ClipItem('a.mp4');
      final outcome = apply(item, result([0.9, 0.9], cancelled: true));
      expect(outcome.partial, isTrue);
      expect(outcome.describe(), contains('途中まで'));
      expect(item.analysis!.partial, isTrue);
    });
  });
}

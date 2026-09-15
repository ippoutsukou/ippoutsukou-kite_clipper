import 'dart:convert';
import 'dart:io';
import 'dart:math' as math;

import 'package:flutter_test/flutter_test.dart';
import 'package:kite_clipper_flutter/ai_segments.dart';

String modelJson({
  double enter = 0.5,
  double exit = 0.4,
  int window = 3,
  double intercept = 0.0,
  List<double>? coef,
  double preRoll = 1.0,
  double postRoll = 1.0,
}) =>
    jsonEncode({
      'schema_version': 1,
      'id': 'test',
      'display_name': 'テスト競技',
      'enter': enter,
      'exit': exit,
      'smooth_window': window,
      'pre_roll': preRoll,
      'post_roll': postRoll,
      'frame_interval': 1.0,
      'intercept': intercept,
      'coef': coef ?? List.filled(SportModel.embeddingSize, 0.0),
    });

SportModel model({
  double enter = 0.5,
  double exit = 0.4,
  int window = 3,
  double preRoll = 1.0,
  double postRoll = 1.0,
}) =>
    SportModel.fromJson(modelJson(
        enter: enter,
        exit: exit,
        window: window,
        preRoll: preRoll,
        postRoll: postRoll));

void main() {
  test('同梱した競技モデルを読み込める', () {
    for (final id in ['kite', 'wing', 'windsurf', 'kite_static']) {
      final file = File('assets/models/$id.json');
      expect(file.existsSync(), isTrue, reason: id);
      final loaded = SportModel.fromJson(file.readAsStringSync());
      expect(loaded.id, id);
      expect(loaded.coefficients.length, SportModel.embeddingSize);
      expect(loaded.enter, greaterThan(loaded.exit));
      expect(loaded.frameIntervalMs, 1000);
    }
    final wing = SportModel.fromJson(File('assets/models/wing.json').readAsStringSync());
    expect(wing.enter, closeTo(0.57, 1e-9));
    expect(wing.exit, closeTo(0.52, 1e-9));
    expect(wing.displayName, 'ウィングフォイル');
  });

  test('形式が違う判定モデルは拒否する', () {
    expect(() => SportModel.fromJson('{}'), throwsFormatException);
    expect(() => SportModel.fromJson(jsonEncode({'schema_version': 1, 'id': 'x', 'coef': []})),
        throwsFormatException);
    expect(() => SportModel.fromJson(modelJson(enter: 0.4, exit: 0.4)),
        throwsFormatException);
  });

  test('点数は sklearn と同じ計算（切片のみなら0.5）', () {
    final zero = model();
    final embedding = List.filled(SportModel.embeddingSize, 0.0);
    expect(zero.score(embedding), closeTo(0.5, 1e-12));

    final coef = List.filled(SportModel.embeddingSize, 0.0);
    coef[0] = 2.0;
    final biased = SportModel.fromJson(modelJson(intercept: -0.5, coef: coef));
    final input = List.filled(SportModel.embeddingSize, 0.0);
    input[0] = 1.0;
    expect(biased.score(input), closeTo(1 / (1 + math.exp(-1.5)), 1e-12));
    expect(() => biased.score([0.0]), throwsArgumentError);
  });

  test('移動平均は中央寄せ・端は狭い窓で計算する', () {
    expect(smoothScores([1, 0, 0, 0, 1], 3),
        [0.5, 1 / 3, 0.0, 1 / 3, 0.5]);
    expect(smoothScores([1, 2, 3], 1), [1, 2, 3]);
    expect(smoothScores([], 3), isEmpty);
  });

  test('enter で開始し exit 未満で終了、前後の余白を足す', () {
    final times = [for (var i = 0; i < 10; i++) i * 1000];
    final scores = [0.1, 0.1, 0.9, 0.9, 0.9, 0.1, 0.1, 0.1, 0.1, 0.1];
    final segments = buildProposedSegments(
        timesMs: times,
        scores: scores,
        model: model(window: 1),
        durationMs: 10000);
    expect(segments.length, 1);
    // 2秒で開始し4秒まで継続。前後1秒の余白で 1秒〜5秒。
    expect(segments.single.startMs, 1000);
    expect(segments.single.endMs, 5000);
    expect(segments.single.meanScore, closeTo(0.9, 1e-9));
    expect(segments.single.lengthMs, 4000);
  });

  test('終端まで高い場合は最後のフレームで閉じ、動画の長さを超えない', () {
    final times = [0, 1000, 2000];
    final segments = buildProposedSegments(
        timesMs: times,
        scores: [0.9, 0.9, 0.9],
        model: model(window: 1),
        durationMs: 2500);
    expect(segments.single.startMs, 0);
    expect(segments.single.endMs, 2500);
  });

  test('平滑化で単発の高い点数は区間にしない', () {
    final times = [for (var i = 0; i < 6; i++) i * 1000];
    final scores = [0.1, 0.1, 0.95, 0.1, 0.1, 0.1];
    expect(
        buildProposedSegments(
            timesMs: times,
            scores: scores,
            model: model(window: 3),
            durationMs: 6000),
        isEmpty);
    // 窓1（平滑化なし）なら1区間できる。
    expect(
        buildProposedSegments(
            timesMs: times,
            scores: scores,
            model: model(window: 1),
            durationMs: 6000).length,
        1);
  });

  test('高い区間が2つあれば2区間になり、件数不一致・空入力は安全に扱う', () {
    final times = [for (var i = 0; i < 12; i++) i * 1000];
    final scores = [0.9, 0.9, 0.1, 0.1, 0.1, 0.1, 0.9, 0.9, 0.1, 0.1, 0.1, 0.1];
    final segments = buildProposedSegments(
        timesMs: times,
        scores: scores,
        model: model(window: 1, preRoll: 0, postRoll: 0),
        durationMs: 12000);
    expect(segments.map((s) => [s.startMs, s.endMs]), [
      [0, 1000],
      [6000, 7000],
    ]);
    expect(
        () => buildProposedSegments(
            timesMs: [0],
            scores: [],
            model: model(),
            durationMs: 1000),
        throwsArgumentError);
    expect(
        buildProposedSegments(
            timesMs: [], scores: [], model: model(), durationMs: 1000),
        isEmpty);
  });
}

import 'dart:math' as math;
import 'dart:typed_data';

class ClipSegment {
  ClipSegment(this.startMs, this.endMs);
  int startMs;
  int endMs;
  String? output;
}

/// 1動画分のAI解析結果。同じ条件なら再解析せずに使い回す。
class AnalysisRecord {
  const AnalysisRecord({
    required this.sportId,
    required this.intervalMs,
    required this.durationMs,
    required this.timesMs,
    required this.scores,
    this.partial = false,
  });
  final String sportId;
  final int intervalMs;
  final int durationMs;
  final List<int> timesMs;
  final List<double> scores;

  /// 中止して途中まで解析した結果か。
  final bool partial;

  bool matches(
          {required String sportId,
          required int intervalMs,
          required int durationMs}) =>
      !partial &&
      this.sportId == sportId &&
      this.intervalMs == intervalMs &&
      this.durationMs == durationMs;

  Map<String, Object?> toJson() => {
        'sportId': sportId,
        'intervalMs': intervalMs,
        'durationMs': durationMs,
        'partial': partial,
        'timesMs': timesMs,
        'scores': [for (final score in scores) double.parse(score.toStringAsFixed(3))],
      };

  /// 記録が壊れている場合は FormatException（解析結果だけ捨てて動画は残す）。
  factory AnalysisRecord.fromJson(Object? raw) {
    if (raw is! Map) throw const FormatException('解析結果の記録が不正です');
    final sportId = raw['sportId'];
    final intervalMs = raw['intervalMs'];
    final durationMs = raw['durationMs'];
    final times = raw['timesMs'];
    final scores = raw['scores'];
    if (sportId is! String ||
        sportId.isEmpty ||
        intervalMs is! int ||
        intervalMs <= 0 ||
        durationMs is! int ||
        durationMs <= 0 ||
        times is! List ||
        scores is! List ||
        times.length != scores.length) {
      throw const FormatException('解析結果の記録が不正です');
    }
    return AnalysisRecord(
      sportId: sportId,
      intervalMs: intervalMs,
      durationMs: durationMs,
      partial: raw['partial'] == true,
      timesMs: [
        for (final value in times)
          value is int ? value : throw const FormatException('時刻が不正です')
      ],
      scores: [
        for (final value in scores)
          value is num ? value.toDouble() : throw const FormatException('点数が不正です')
      ],
    );
  }
}

class ClipItem {
  ClipItem(this.path);
  static const maxSegments = 20;
  static const minLengthMs = 100;
  static const newSegmentMs = 5000;
  final String path;
  String get name => path.split(RegExp(r'[/\\]')).last;
  int? durationMs;
  Uint8List? thumbnail;
  String? error;

  /// 直近のAI解析結果（作業記録に保存する）。
  AnalysisRecord? analysis;

  /// 直近のAI解析（最後まで解析したもの）で提案が0個だったか。作業記録に保存する。
  bool noProposals = false;

  /// 提案0個で、区間に手を入れていない動画。全長がそのまま保存・結合されないよう対象から外す。
  /// 区間を変えれば対象に戻る（[noProposals] を消すと全体のまま対象に戻る）。
  bool get excludedNoProposals => noProposals && isUntouchedDefault;

  /// 長さ確定前は空。確定後は常に1〜20件。
  final List<ClipSegment> _segments = [];
  List<ClipSegment> get segments => List.unmodifiable(_segments);
  int _active = 0;
  int get activeSegment => _active;
  ClipSegment? get current => _segments.isEmpty ? null : _segments[_active];

  // 以下3つは選択中区間への窓口。1区間時代の呼び出し元との互換用。
  int? get startMs => current?.startMs;
  set startMs(int? value) => _require().startMs = value!;
  int? get endMs => current?.endMs;
  set endMs(int? value) => _require().endMs = value!;
  String? get output => current?.output;
  set output(String? value) {
    if (current != null) current!.output = value;
  }

  ClipSegment _require() =>
      current ?? (throw StateError('動画の長さが未確認のため区間がありません'));

  /// 長さ外の区間は捨てずに切り詰める。0.1秒未満になる区間だけ全体へ戻す。
  void setDuration(int duration) {
    if (duration <= 0) throw ArgumentError('動画の長さが不正です');
    durationMs = duration;
    if (_segments.isEmpty) {
      _segments.add(ClipSegment(0, duration));
      _active = 0;
      return;
    }
    for (final segment in _segments) {
      final start = segment.startMs.clamp(0, duration);
      final end = segment.endMs.clamp(0, duration);
      if (start == segment.startMs &&
          end == segment.endMs &&
          end - start >= minLengthMs) continue;
      if (end - start >= minLengthMs) {
        segment.startMs = start;
        segment.endMs = end;
      } else {
        segment.startMs = 0;
        segment.endMs = duration;
      }
      segment.output = null;
    }
  }

  /// 作業記録から区間を戻す。不正な値は ArgumentError（呼び出し側でその動画を読み飛ばす）。
  void restore(
      {required int? durationMs,
      required List<ClipSegment> segments,
      required int activeSegment}) {
    if (durationMs == null) {
      if (segments.isNotEmpty) throw ArgumentError('長さ未確認の動画に区間があります');
      return;
    }
    if (durationMs <= 0 ||
        segments.isEmpty ||
        segments.length > maxSegments) {
      throw ArgumentError('長さ・区間数が不正です');
    }
    for (final segment in segments) {
      if (segment.startMs < 0 ||
          segment.endMs > durationMs ||
          segment.endMs - segment.startMs < minLengthMs) {
        throw ArgumentError('区間が不正です');
      }
    }
    if (activeSegment < 0 || activeSegment >= segments.length) {
      throw ArgumentError('選択中の区間が不正です');
    }
    this.durationMs = durationMs;
    _segments
      ..clear()
      ..addAll(segments);
    _active = activeSegment;
  }

  void setRange(int start, int end) {
    if (durationMs == null ||
        start < 0 ||
        end > durationMs! ||
        end - start < minLengthMs) {
      throw ArgumentError('範囲が不正です');
    }
    final segment = _require();
    segment.startMs = start;
    segment.endMs = end;
    segment.output = null;
  }

  /// 再生位置から5秒の区間を追加して選択する。終端付近は終わりの5秒前から。
  int addSegment(int positionMs) {
    final duration = durationMs;
    if (duration == null) throw StateError('動画の長さが未確認です');
    if (_segments.length >= maxSegments) {
      throw StateError('区間は1動画につき最大$maxSegments個です');
    }
    var start = positionMs.clamp(0, duration);
    if (duration <= newSegmentMs) {
      start = 0;
    } else if (duration - start < minLengthMs) {
      start = duration - newSegmentMs;
    }
    _segments.add(
        ClipSegment(start, math.min(duration, start + newSegmentMs)));
    return _active = _segments.length - 1;
  }

  /// 触っていない初期状態（全体1区間・保存結果なし）か。AI提案で置き換えてよい状態。
  bool get isUntouchedDefault =>
      durationMs != null &&
      _segments.length == 1 &&
      _segments[0].startMs == 0 &&
      _segments[0].endMs == durationMs &&
      _segments[0].output == null;

  /// 範囲を指定して区間を追加し、その区間を選ぶ（AIの提案を入れるために使う）。
  int addSegmentRange(int start, int end) {
    final duration = durationMs;
    if (duration == null) throw StateError('動画の長さが未確認です');
    if (_segments.length >= maxSegments) {
      throw StateError('区間は1動画につき最大$maxSegments個です');
    }
    if (start < 0 || end > duration || end - start < minLengthMs) {
      throw ArgumentError('区間が不正です');
    }
    _segments.add(ClipSegment(start, end));
    return _active = _segments.length - 1;
  }

  /// 最後の1区間は消さない。選択中を消した場合は次、末尾なら前を選ぶ。
  void removeSegment(int index) {
    if (index < 0 || index >= _segments.length) {
      throw ArgumentError('区間がありません');
    }
    if (_segments.length <= 1) throw StateError('最後の区間は削除できません');
    _segments.removeAt(index);
    if (index < _active || _active >= _segments.length) _active--;
  }

  void selectSegment(int index) {
    if (index < 0 || index >= _segments.length) {
      throw ArgumentError('区間がありません');
    }
    _active = index;
  }
}

class ClipSession {
  ClipSession({this.caseInsensitivePaths = false});
  final bool caseInsensitivePaths;
  final List<ClipItem> _items = [];
  List<ClipItem> get items => List.unmodifiable(_items);
  ClipItem? active;
  int generation = 0;
  String _key(String path) =>
      caseInsensitivePaths ? path.replaceAll('\\', '/').toLowerCase() : path;

  /// 本数の上限はない。重複パスは追加しない。OSが返した順序を保つ。
  List<ClipItem> add(List<String> paths) {
    final known = _items.map((item) => _key(item.path)).toSet();
    final added = <ClipItem>[];
    for (final path in paths) {
      if (path.isNotEmpty && known.add(_key(path))) added.add(ClipItem(path));
    }
    _items.addAll(added);
    return added;
  }

  int select(ClipItem item) {
    if (!_items.contains(item)) throw ArgumentError('一覧にない動画です');
    active = item;
    return ++generation;
  }

  /// 一覧順のみ変更。選択・編集範囲・非同期世代はそのまま。
  void move(ClipItem item, int delta) {
    if (delta != -1 && delta != 1) throw ArgumentError('移動は1項目ずつです');
    final index = _items.indexOf(item);
    if (index < 0) throw ArgumentError('一覧にない動画です');
    final next = index + delta;
    if (next < 0 || next >= _items.length) return;
    _items.removeAt(index);
    _items.insert(next, item);
  }

  /// メモリ上の一覧からだけ外す。ファイル操作は行わない。
  void remove(ClipItem item) {
    final index = _items.indexOf(item);
    if (index < 0) return;
    _items.removeAt(index);
    if (identical(active, item)) {
      active =
          _items.isEmpty ? null : _items[index.clamp(0, _items.length - 1)];
      invalidate();
    }
  }

  bool accepts(int ticket, ClipItem item) =>
      generation == ticket && identical(active, item);
  void invalidate() => generation++;
}

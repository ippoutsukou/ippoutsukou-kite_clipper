import 'dart:math' as math;
import 'dart:typed_data';

class ClipSegment {
  ClipSegment(this.startMs, this.endMs);
  int startMs;
  int endMs;
  String? output;
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
  static const maximum = 30;
  final bool caseInsensitivePaths;
  final List<ClipItem> _items = [];
  List<ClipItem> get items => List.unmodifiable(_items);
  ClipItem? active;
  int generation = 0;
  String _key(String path) =>
      caseInsensitivePaths ? path.replaceAll('\\', '/').toLowerCase() : path;

  /// 上限超過は追加全体を取り消す。OSが返した順序を保つ。
  List<ClipItem> add(List<String> paths) {
    final known = _items.map((item) => _key(item.path)).toSet();
    final added = <ClipItem>[];
    for (final path in paths) {
      if (path.isNotEmpty && known.add(_key(path))) added.add(ClipItem(path));
    }
    if (_items.length + added.length > maximum) {
      throw StateError('動画は最大30本です。追加する本数を減らしてください。');
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

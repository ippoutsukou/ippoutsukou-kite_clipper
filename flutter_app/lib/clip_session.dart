import 'dart:typed_data';

class ClipItem {
  ClipItem(this.path);
  final String path;
  String get name => path.split(RegExp(r'[/\\]')).last;
  int? durationMs;
  int? startMs;
  int? endMs;
  Uint8List? thumbnail;
  String? error;
  String? output;

  void setDuration(int duration) {
    if (duration <= 0) throw ArgumentError('動画の長さが不正です');
    durationMs = duration;
    if (startMs == null ||
        endMs == null ||
        startMs! < 0 ||
        endMs! > duration ||
        startMs! >= endMs!) {
      startMs = 0;
      endMs = duration;
      output = null;
    }
  }

  void setRange(int start, int end) {
    if (durationMs == null ||
        start < 0 ||
        end > durationMs! ||
        end - start < 100) {
      throw ArgumentError('範囲が不正です');
    }
    startMs = start;
    endMs = end;
    output = null;
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

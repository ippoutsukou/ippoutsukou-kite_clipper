import 'package:flutter/material.dart';
import 'clip_session.dart';

class ClipList extends StatefulWidget {
  const ClipList(
      {super.key,
      required this.items,
      required this.selected,
      required this.enabled,
      required this.onSelect,
      this.onMove,
      this.onRemove,
      this.onRemoveMany,
      this.onInclude});
  final List<ClipItem> items;
  final ClipItem? selected;
  final bool enabled;
  final ValueChanged<ClipItem> onSelect;
  final void Function(ClipItem, int)? onMove;
  final ValueChanged<ClipItem>? onRemove;

  /// 複数選んで外す依頼。確認と実際の取り外しは呼び出し側で行う。
  final ValueChanged<List<ClipItem>>? onRemoveMany;

  /// 提案0個で対象外の動画を、全体のまま対象に戻す。
  final ValueChanged<ClipItem>? onInclude;
  @override
  State<ClipList> createState() => _ClipListState();
}

class _ClipListState extends State<ClipList> {
  /// 複数選択の最中か。
  bool picking = false;
  final checked = <ClipItem>{};

  @override
  void didUpdateWidget(ClipList oldWidget) {
    super.didUpdateWidget(oldWidget);
    // 一覧から外れた動画は選択からも除く。空になったら選択をやめる。
    checked.removeWhere((item) => !widget.items.contains(item));
    if (widget.items.isEmpty) picking = false;
  }

  void toggle(ClipItem item) => setState(
      () => checked.contains(item) ? checked.remove(item) : checked.add(item));

  Widget header() {
    final items = widget.items;
    if (!picking) {
      return Row(children: [
        Text('動画 ${items.length}本',
            style: const TextStyle(fontWeight: FontWeight.w700)),
        const Spacer(),
        if (widget.onRemoveMany != null && items.isNotEmpty)
          TextButton(
              onPressed: widget.enabled
                  ? () => setState(() {
                        picking = true;
                        checked.clear();
                      })
                  : null,
              child: const Text('選択')),
        const Tooltip(
            message: '一覧と区間は自動で記録し、次回起動時に復元します',
            child: Icon(Icons.info_outline, size: 18)),
      ]);
    }
    final all = checked.length == items.length;
    return Row(children: [
      Checkbox(
          tristate: true,
          value: all ? true : (checked.isEmpty ? false : null),
          onChanged: widget.enabled
              ? (_) => setState(() {
                    if (all) {
                      checked.clear();
                    } else {
                      checked.addAll(items);
                    }
                  })
              : null),
      const Text('すべて'),
      const Spacer(),
      FilledButton.tonal(
          onPressed: widget.enabled && checked.isNotEmpty
              ? () => widget.onRemoveMany!(
                  [for (final item in items) if (checked.contains(item)) item])
              : null,
          child: Text('${checked.length}本を外す')),
      IconButton(
          tooltip: '選択をやめる',
          onPressed: () => setState(() {
                picking = false;
                checked.clear();
              }),
          icon: const Icon(Icons.close)),
    ]);
  }

  @override
  Widget build(BuildContext context) {
    final items = widget.items;
    final enabled = widget.enabled;
    return Column(children: [
      Padding(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 4),
          child: SizedBox(height: 40, child: header())),
      Expanded(
          child: ListView.builder(
              itemCount: items.length,
              itemBuilder: (context, index) {
                final item = items[index];
                final active = identical(item, widget.selected);
                return Padding(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 6, vertical: 3),
                    child: Material(
                      color: active ? const Color(0xffedf5ff) : Colors.white,
                      shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(5),
                          side: BorderSide(
                              color: active
                                  ? const Color(0xff2878ef)
                                  : const Color(0xffe4e9f0))),
                      child: ListTile(
                        selected: active,
                        enabled: enabled,
                        dense: true,
                        trailing: picking
                            ? Checkbox(
                                value: checked.contains(item),
                                semanticLabel: '${item.name} を選ぶ',
                                onChanged:
                                    enabled ? (_) => toggle(item) : null)
                            : widget.onMove == null && widget.onRemove == null
                                ? null
                                : PopupMenuButton<String>(
                                    tooltip: '${item.name} の操作',
                                    enabled: enabled,
                                    onSelected: (action) {
                                      if (action == 'up')
                                        widget.onMove?.call(item, -1);
                                      if (action == 'down')
                                        widget.onMove?.call(item, 1);
                                      if (action == 'remove')
                                        widget.onRemove?.call(item);
                                      if (action == 'include')
                                        widget.onInclude?.call(item);
                                    },
                                    itemBuilder: (_) => [
                                      if (widget.onMove != null) ...[
                                        PopupMenuItem(
                                            value: 'up',
                                            enabled: index > 0,
                                            child: const Text('上へ')),
                                        PopupMenuItem(
                                            value: 'down',
                                            enabled: index < items.length - 1,
                                            child: const Text('下へ')),
                                      ],
                                      if (widget.onInclude != null &&
                                          item.excludedNoProposals)
                                        const PopupMenuItem(
                                            value: 'include',
                                            child: Text('全体を対象に戻す')),
                                      if (widget.onRemove != null)
                                        const PopupMenuItem(
                                            value: 'remove',
                                            child: Text('一覧から外す')),
                                    ],
                                  ),
                        // 選択中はタップでチェックを切り替える（動画は開かない）。
                        onTap: !enabled
                            ? null
                            : picking
                                ? () => toggle(item)
                                : () => widget.onSelect(item),
                        leading: SizedBox(
                            width: 64,
                            height: 44,
                            child: item.thumbnail == null
                                ? const ColoredBox(
                                    color: Color(0xffd8e8f6),
                                    child: Icon(Icons.movie_outlined))
                                : Image.memory(item.thumbnail!,
                                    fit: BoxFit.cover,
                                    errorBuilder: (_, __, ___) =>
                                        const Icon(Icons.movie_outlined))),
                        title: Text('${index + 1}. ${item.name}',
                            maxLines: 2, overflow: TextOverflow.ellipsis),
                        subtitle: Text(
                            item.error != null
                                ? '読み込み・処理エラー（選択して確認）'
                                : item.segments.isEmpty
                                    ? '長さ未確認'
                                    : item.excludedNoProposals
                                    ? 'AIの提案なし（保存・まとめる対象外）'
                                    : '区間${item.segments.length} ・ 合計${(item.segments.fold<int>(0, (sum, s) => sum + s.endMs - s.startMs) / 1000).toStringAsFixed(1)}秒',
                            maxLines: 2),
                      ),
                    ));
              })),
      const Padding(
          padding: EdgeInsets.all(12),
          child: Text('一覧と区間は次回起動時に復元します',
              style: TextStyle(fontSize: 11, color: Colors.blueGrey))),
    ]);
  }
}

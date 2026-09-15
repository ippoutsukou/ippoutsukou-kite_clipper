import 'package:flutter/material.dart';
import 'clip_session.dart';

class ClipList extends StatelessWidget {
  const ClipList(
      {super.key,
      required this.items,
      required this.selected,
      required this.enabled,
      required this.onSelect,
      this.onMove,
      this.onRemove});
  final List<ClipItem> items;
  final ClipItem? selected;
  final bool enabled;
  final ValueChanged<ClipItem> onSelect;
  final void Function(ClipItem, int)? onMove;
  final ValueChanged<ClipItem>? onRemove;
  @override
  Widget build(BuildContext context) => Column(children: [
        Padding(
            padding: const EdgeInsets.all(14),
            child: Row(children: [
              Text('動画 ${items.length}本',
                  style: const TextStyle(fontWeight: FontWeight.w700)),
              const Spacer(),
              const Tooltip(
                  message: '一覧と区間は自動で記録し、次回起動時に復元します',
                  child: Icon(Icons.info_outline, size: 18)),
            ])),
        Expanded(
            child: ListView.builder(
                itemCount: items.length,
                itemBuilder: (context, index) {
                  final item = items[index];
                  final active = identical(item, selected);
                  return Padding(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 6, vertical: 3),
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
                          trailing: onMove == null && onRemove == null
                              ? null
                              : PopupMenuButton<String>(
                                  tooltip: '${item.name} の操作',
                                  enabled: enabled,
                                  onSelected: (action) {
                                    if (action == 'up') onMove?.call(item, -1);
                                    if (action == 'down') onMove?.call(item, 1);
                                    if (action == 'remove')
                                      onRemove?.call(item);
                                  },
                                  itemBuilder: (_) => [
                                    if (onMove != null) ...[
                                      PopupMenuItem(
                                          value: 'up',
                                          enabled: index > 0,
                                          child: const Text('上へ')),
                                      PopupMenuItem(
                                          value: 'down',
                                          enabled: index < items.length - 1,
                                          child: const Text('下へ')),
                                    ],
                                    if (onRemove != null)
                                      const PopupMenuItem(
                                          value: 'remove',
                                          child: Text('一覧から外す')),
                                  ],
                                ),
                          onTap: enabled ? () => onSelect(item) : null,
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

import 'package:flutter/material.dart';
import 'clip_session.dart';

/// 区間の選択・追加・削除の依頼を通知するだけ。確認ダイアログと状態変更は呼び出し側。
class SegmentChips extends StatelessWidget {
  const SegmentChips(
      {super.key,
      required this.segments,
      required this.selected,
      required this.enabled,
      required this.onSelect,
      required this.onAdd,
      required this.onRemove});
  final List<ClipSegment> segments;
  final int selected;
  final bool enabled;
  final ValueChanged<int> onSelect;
  final VoidCallback onAdd;
  final ValueChanged<int> onRemove;

  @override
  Widget build(BuildContext context) {
    final full = segments.length >= ClipItem.maxSegments;
    return SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: Row(children: [
          for (var i = 0; i < segments.length; i++) ...[
            ChoiceChip(
                label: Text('区間${i + 1}'),
                avatar: segments[i].output == null
                    ? null
                    : const Icon(Icons.check_circle,
                        size: 16, color: Color(0xff147d55)),
                selected: i == selected,
                showCheckmark: false,
                onSelected: enabled ? (_) => onSelect(i) : null),
            if (i == selected)
              PopupMenuButton<String>(
                  tooltip: '区間${i + 1} の操作',
                  enabled: enabled,
                  icon: const Icon(Icons.more_vert, size: 18),
                  onSelected: (_) => onRemove(i),
                  itemBuilder: (_) => [
                        PopupMenuItem(
                            value: 'remove',
                            enabled: segments.length > 1,
                            child: const Text('この区間を削除')),
                      ]),
            const SizedBox(width: 6),
          ],
          ActionChip(
              avatar: const Icon(Icons.add, size: 16),
              label: const Text('区間を追加'),
              onPressed: enabled && !full ? onAdd : null),
          if (full)
            const Padding(
                padding: EdgeInsets.only(left: 8),
                child: Text('1動画につき最大${ClipItem.maxSegments}区間です',
                    style: TextStyle(fontSize: 12, color: Colors.blueGrey))),
        ]));
  }
}

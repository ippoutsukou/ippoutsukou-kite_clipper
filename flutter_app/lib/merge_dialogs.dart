import 'package:flutter/material.dart';

import 'merge_plan.dart';
import 'merge_service.dart';

const _note = TextStyle(fontSize: 12, color: Colors.blueGrey);

String _seconds(int milliseconds) => (milliseconds / 1000).toStringAsFixed(1);

/// まとめる前の確認。実行ならつなぎ方を返す（キャンセルは null）。
/// 形式の違う動画が混ざっているとき（Windows）だけ、つなぎ方の選択を出す。
class MergeConfirmDialog extends StatefulWidget {
  const MergeConfirmDialog(
      {super.key, required this.plan, this.android = false, this.formats});
  final MergePlan plan;
  final bool android;

  /// 事前に調べた形式の内訳。調べていない・調べられなかった場合は null。
  final MergeFormats? formats;

  @override
  State<MergeConfirmDialog> createState() => _MergeConfirmDialogState();
}

class _MergeConfirmDialogState extends State<MergeConfirmDialog> {
  // 画質が変わらないほうを初期値にする。
  MergeMode mode = MergeMode.grouped;

  MergePlan get plan => widget.plan;
  bool get android => widget.android;
  bool get mixed => widget.formats?.mixed ?? false;

  Widget _choice(MergeMode value, String title, String detail,
      {bool enabled = true}) {
    final selected = mode == value;
    return InkWell(
      onTap: enabled ? () => setState(() => mode = value) : null,
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 4),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Icon(
              selected
                  ? Icons.radio_button_checked
                  : Icons.radio_button_unchecked,
              size: 20,
              color: enabled ? null : Colors.grey),
          const SizedBox(width: 8),
          Expanded(
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                Text(title,
                    style: TextStyle(
                        fontWeight: FontWeight.w700,
                        color: enabled ? null : Colors.grey)),
                Text(detail, style: _note),
              ])),
        ]),
      ),
    );
  }

  /// 形式が混ざっているときの内訳と、つなぎ方の選択。
  List<Widget> _mixedSection(MergeFormats formats) {
    final blocked = formats.forcedBlocked;
    final target = formats.target;
    return [
      const SizedBox(height: 8),
      const Text('形式の違う動画が混ざっています',
          style: TextStyle(
              fontWeight: FontWeight.w700, color: Color(0xffa33a3a))),
      for (final group in formats.groups)
        Text('　・${group.shape.label}（${group.shape.video}'
            '${group.shape.audio.isEmpty ? '・音声なし' : ''}）：'
            '区間${group.indexes.length}個'),
      const SizedBox(height: 8),
      const Text('つなぎ方', style: TextStyle(fontWeight: FontWeight.w700)),
      _choice(
          MergeMode.grouped,
          '形式ごとに分けて保存（画質はそのまま）',
          '形式ごとに1本ずつ、合計${formats.groups.length}本できます。'
              'それぞれの中は一覧の順番のままです。'),
      _choice(
          MergeMode.forced,
          '強制的に1本にする',
          blocked != null
              ? '選べません：$blocked。'
              : formats.frameRateOnly
                  ? '違いはフレームレートだけなので、作り直さずにつなぎます（画質はそのまま）。'
                      '1本の中でフレームレートが切り替わる動画になります。'
                  : '一番大きい ${target.width}x${target.height} にそろえて、'
                      '全区間を作り直します（縦横比が違う動画には黒帯が付きます）。'
                      '時間がかかり、画質はわずかに落ちます。',
          enabled: blocked == null),
    ];
  }

  @override
  Widget build(BuildContext context) {
    final entries = plan.entries;
    final formats = widget.formats;
    const shown = 10;
    return AlertDialog(
      title: const Text('1本にまとめて保存'),
      content: SingleChildScrollView(
          child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
            Text(
                '動画${plan.videoCount}本・区間${entries.length}個'
                '（合計${_seconds(plan.totalMs)}秒）',
                style: const TextStyle(fontWeight: FontWeight.w700)),
            const SizedBox(height: 8),
            const Text('つなぐ順番', style: TextStyle(fontWeight: FontWeight.w700)),
            for (final entry in entries.take(shown))
              Text('　${entries.indexOf(entry) + 1}. ${entry.item.name}'
                  '（区間${entry.segmentNumber}・${_seconds(entry.lengthMs)}秒）'),
            if (entries.length > shown)
              Text('　ほか${entries.length - shown}個'),
            if (plan.missing.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text(
                  '元動画が見つからない動画${plan.missing.length}本は含めません：'
                  '${plan.missing.map((item) => item.name).join('、')}',
                  style: const TextStyle(color: Color(0xffa33a3a))),
            ],
            if (plan.unconfirmedVideos > 0)
              Text('まだ開いていない動画${plan.unconfirmedVideos}本は、長さが未確認のため含まれません。'),
            const SizedBox(height: 8),
            const Text('順番を変えるときは、一覧の「︙」から「上へ」「下へ」で動画を並べ替えてください。',
                style: _note),
            if (formats != null && mixed)
              ..._mixedSection(formats)
            else ...[
              const SizedBox(height: 6),
              Text(
                  android
                      ? '形式（映像の形式・画面の大きさ・フレームレート・音声）がそろった動画だけをつなげます。'
                          '違いがある場合は、その場で理由を出して中止します。'
                      : formats != null
                          ? '形式がそろっているので、作り直さずにそのままつなぎます（画質はそのまま）。'
                          : '形式がそろっていれば、作り直さずにそのままつなぎます（画質はそのまま）。'
                              '違いがある場合は理由を出して中止します。',
                  style: _note),
            ],
            const SizedBox(height: 6),
            const Text('元動画と、これまでに保存したファイルは変更しません。', style: _note),
          ])),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('キャンセル')),
        FilledButton(
            onPressed: () =>
                Navigator.pop(context, mixed ? mode : MergeMode.strict),
            child: const Text('保存先を選ぶ…')),
      ],
    );
  }
}

/// まとめている間の進捗と中止。
class MergeProgressDialog extends StatelessWidget {
  const MergeProgressDialog(
      {super.key,
      required this.phase,
      required this.done,
      required this.total,
      required this.cancelling,
      required this.onCancel,
      this.canCancel = true});
  final MergePhase phase;
  final int done;
  final int total;
  final bool cancelling;
  final VoidCallback onCancel;

  /// Androidは端末側の処理を途中で止められないため中止を出さない。
  final bool canCancel;

  String get _label => switch (phase) {
        MergePhase.cutting => '区間を取り出しています（$done / $total）',
        MergePhase.checking => '形式を確かめています（$done / $total）',
        MergePhase.encoding => '大きさをそろえて作り直しています（$done / $total）',
        MergePhase.joining =>
          total > 0 ? 'つないでいます（$done / $total）' : 'つないでいます…',
      };

  @override
  Widget build(BuildContext context) => AlertDialog(
        content: Column(mainAxisSize: MainAxisSize.min, children: [
          Text(cancelling ? '中止しています…' : _label),
          const SizedBox(height: 12),
          LinearProgressIndicator(
              value: total <= 0
                  ? null
                  : (done / total).clamp(0.0, 1.0)),
        ]),
        actions: [
          if (canCancel)
            TextButton(
                onPressed: cancelling ? null : onCancel,
                child: const Text('中止')),
        ],
      );
}

/// まとめられなかったときの理由。診断の文面も選択・コピーできるようにする。
class MergeFailureDialog extends StatelessWidget {
  const MergeFailureDialog({super.key, required this.message});
  final String message;

  @override
  Widget build(BuildContext context) => AlertDialog(
        title: const Text('1本にまとめられませんでした'),
        content: SingleChildScrollView(
            child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
              SelectableText(message),
              const SizedBox(height: 8),
              const Text('ファイルは作っていません。元動画と一覧の区間は変わっていません。',
                  style: _note),
            ])),
        actions: [
          FilledButton(
              onPressed: () => Navigator.pop(context), child: const Text('閉じる')),
        ],
      );
}

/// まとめ終わったあとの結果。
class MergeResultDialog extends StatelessWidget {
  const MergeResultDialog(
      {super.key, required this.outcome, required this.entries});
  final MergeOutcome outcome;
  final int entries;

  @override
  Widget build(BuildContext context) => AlertDialog(
        title: Text(outcome.outputs.length > 1
            ? '形式ごとに${outcome.outputs.length}本にまとめました'
            : '1本にまとめました'),
        content: SingleChildScrollView(
            child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
              Text('区間$entries個をつなぎました'
                  '（${outcome.lossless ? '作り直しなし' : '作り直しあり'}・'
                  '${(outcome.elapsed.inMilliseconds / 1000).toStringAsFixed(1)}秒）'),
              const SizedBox(height: 8),
              const Text('保存先', style: TextStyle(fontWeight: FontWeight.w700)),
              for (final output in outcome.outputs) SelectableText(output),
            ])),
        actions: [
          FilledButton(
              onPressed: () => Navigator.pop(context), child: const Text('閉じる')),
        ],
      );
}

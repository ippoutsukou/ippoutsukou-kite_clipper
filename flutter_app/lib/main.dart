import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'package:file_selector/file_selector.dart';
import 'package:flutter/material.dart';
import 'package:media_kit/media_kit.dart';
import 'package:media_kit_video/media_kit_video.dart';

import 'export_service.dart';
import 'editor_viewport.dart';
import 'editor_controls.dart';
import 'timeline_frames.dart';
import 'preview_readiness.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  MediaKit.ensureInitialized();
  runApp(const KiteApp());
}

class KiteApp extends StatelessWidget {
  const KiteApp({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'Kite Clipper',
        debugShowCheckedModeBanner: false,
        theme: ThemeData(
          colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xff1266ed)),
          scaffoldBackgroundColor: const Color(0xfff5f7fa),
          fontFamily: 'Yu Gothic UI',
          sliderTheme: const SliderThemeData(
            activeTrackColor: Color(0xff1266ed),
            inactiveTrackColor: Color(0xffdce5f1),
            thumbColor: Color(0xff1266ed),
            rangeThumbShape: RoundRangeSliderThumbShape(enabledThumbRadius: 8),
            trackHeight: 3,
          ),
          outlinedButtonTheme: OutlinedButtonThemeData(
            style: OutlinedButton.styleFrom(
              backgroundColor: Colors.white,
              minimumSize: const Size(48, 44),
              shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(6)),
            ),
          ),
          filledButtonTheme: FilledButtonThemeData(
            style: FilledButton.styleFrom(
              backgroundColor: const Color(0xff0766f7),
              minimumSize: const Size(48, 44),
              shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(6)),
            ),
          ),
        ),
        home: const Workspace(),
      );
}

class Workspace extends StatefulWidget {
  const Workspace({super.key});
  @override
  State<Workspace> createState() => _WorkspaceState();
}

class _WorkspaceState extends State<Workspace> {
  final player = Player();
  late final VideoController controller;
  final subscriptions = <StreamSubscription<dynamic>>[];
  String? source;
  String? output;
  String? error;
  Uint8List? thumbnail;
  int durationMs = 0;
  RangeValues range = const RangeValues(0, 1);
  bool busy = false;
  bool loading = false;
  int? pendingSeek;
  Future<void>? seekTask;
  bool muted = false;
  final timelineLoader = TimelineLoader(extractTimelineFrame);
  List<Uint8List?> timelineFrames = List.filled(8, null);
  String? timelineSource;

  void loadTimeline() {
    final path = source;
    if (path == null || durationMs <= 0 || timelineSource == path) return;
    timelineSource = path;
    unawaited(timelineLoader.load(path, durationMs, (index, bytes) {
      if (!mounted) return;
      setState(() {
        timelineFrames[index] = bytes;
        thumbnail ??= bytes;
      });
    }));
  }

  Future<void> togglePlayback() async {
    try {
      await seekTask;
      if (!mounted || busy || loading) return;
      await player.playOrPause();
    } catch (_) {
      if (mounted) setState(() => error = '再生操作を完了できませんでした。');
    }
  }

  Future<void> toggleMute() async {
    try {
      final next = !muted;
      await player.setVolume(next ? 0 : 100);
      if (mounted) setState(() => muted = next);
    } catch (_) {
      if (mounted) setState(() => error = '音声を切り替えられませんでした。');
    }
  }

  void previewAt(int milliseconds) {
    pendingSeek = milliseconds;
    seekTask ??= drainSeeks();
  }

  Future<void> drainSeeks() async {
    try {
      await player.pause();
      while (mounted && pendingSeek != null) {
        final target = pendingSeek!;
        pendingSeek = null;
        await player.seek(Duration(milliseconds: target));
      }
    } catch (_) {
      if (mounted) setState(() => error = 'プレビュー位置を変更できませんでした。');
    } finally {
      seekTask = null;
    }
  }

  @override
  void initState() {
    super.initState();
    // 初回のVideoウィジェット構築まで遅延させず、起動時に出力を接続する。
    controller = VideoController(player);
    subscriptions.add(
      player.stream.duration.listen((value) {
        if (!mounted || value.inMilliseconds <= 0) return;
        setState(() {
          if (durationMs == 0)
            range = RangeValues(0, value.inMilliseconds.toDouble());
          durationMs = value.inMilliseconds;
        });
        loadTimeline();
      }),
    );
    subscriptions.add(
      player.stream.error.listen((_) {
        if (mounted) setState(() => error = '再生できません。別の動画形式で試してください。');
      }),
    );
  }

  @override
  void dispose() {
    timelineLoader.cancel();
    pendingSeek = null;
    for (final sub in subscriptions) {
      sub.cancel();
    }
    player.dispose();
    super.dispose();
  }

  Future<void> pick() async {
    if (busy || loading) return;
    setState(() {
      loading = true;
      error = null;
    });
    try {
      final file = await openFile(
        acceptedTypeGroups: [
          const XTypeGroup(
            label: '動画',
            extensions: ['mp4', 'mov', 'mkv', 'webm'],
            mimeTypes: ['video/*'],
          ),
        ],
      );
      if (file == null || !mounted) return;
      pendingSeek = null;
      timelineLoader.cancel();
      await seekTask;
      await player.stop();
      setState(() {
        source = file.path;
        output = null;
        thumbnail = null;
        durationMs = 0;
        timelineSource = null;
        timelineFrames = List.filled(8, null);
      });
      await openPreparedPreview(
        ready: () async {
          await controller.platform.future;
          await WidgetsBinding.instance.endOfFrame;
        }(),
        isActive: () => mounted,
        open: () => player.open(Media(file.path), play: false),
      );
      if (!mounted) return;
      // 動画が読み込まれた後に取得。失敗しても再生機能は維持する。
      try {
        final frame = await player.screenshot();
        if (mounted) setState(() => thumbnail = frame);
      } catch (_) {
        /* サムネイル未取得時は動画アイコンを使う。 */
      }
    } catch (_) {
      if (mounted) setState(() => error = '動画を開けませんでした。権限とファイル形式を確認してください。');
    } finally {
      if (mounted) setState(() => loading = false);
    }
  }

  Future<void> save() async {
    if (busy || loading || source == null || durationMs <= 0) return;
    setState(() {
      busy = true;
      error = null;
      output = null;
    });
    try {
      String? directory;
      if (Platform.isWindows) {
        directory = await getDirectoryPath(confirmButtonText: 'ここに保存');
        if (directory == null) return;
      }
      await player.pause();
      final result = await ExportService().export(
        source!,
        ClipRange(range.start.round(), range.end.round()),
        durationMs,
        directory: directory,
      );
      if (mounted && result != null) setState(() => output = result);
    } catch (e) {
      if (mounted) setState(() => error = '保存できませんでした: $e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  String time(double ms) {
    final n = ms.round();
    return '${(n ~/ 60000).toString().padLeft(2, '0')}:${((n ~/ 1000) % 60).toString().padLeft(2, '0')}.${(n % 1000).toString().padLeft(3, '0')}';
  }

  Widget candidates(bool compact) => Container(
        width: compact ? null : 260,
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(6),
          border: Border.all(color: const Color(0xffe4e9f0)),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text('編集中のクリップ',
                style: TextStyle(fontWeight: FontWeight.w700)),
            const SizedBox(height: 14),
            Container(
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(
                color: const Color(0xffedf5ff),
                border: Border.all(color: const Color(0xff2878ef)),
                borderRadius: BorderRadius.circular(5),
              ),
              child: Row(
                children: [
                  const Icon(
                    Icons.check_circle,
                    color: Color(0xff159861),
                    size: 19,
                  ),
                  const SizedBox(width: 8),
                  ClipRRect(
                    borderRadius: BorderRadius.circular(3),
                    child: thumbnail == null
                        ? Container(
                            width: 64,
                            height: 44,
                            color: const Color(0xffd8e8f6),
                            child: const Icon(Icons.movie_outlined),
                          )
                        : Image.memory(
                            thumbnail!,
                            width: 64,
                            height: 44,
                            fit: BoxFit.cover,
                          ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          source!.split(RegExp(r'[/\\]')).last,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(fontSize: 12),
                        ),
                        Text(
                          '${((range.end - range.start) / 1000).toStringAsFixed(1)} 秒',
                          style: const TextStyle(
                            color: Colors.blueGrey,
                            fontSize: 12,
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
            if (!compact)
              const Padding(
                padding: EdgeInsets.only(top: 20),
                child: Text(
                  '開始と終了を調整して、残したい区間を保存',
                  style: TextStyle(color: Colors.blueGrey, fontSize: 12),
                ),
              ),
          ],
        ),
      );

  Widget editor() => EditorViewport(
        preview: ClipRRect(
          borderRadius: BorderRadius.circular(5),
          child: Video(
              controller: controller,
              fit: BoxFit.contain,
              controls: NoVideoControls),
        ),
        controls: [
          StreamBuilder<Duration>(
            stream: player.stream.position,
            initialData: player.state.position,
            builder: (context, position) => StreamBuilder<bool>(
              stream: player.stream.playing,
              initialData: player.state.playing,
              builder: (context, playing) => PlaybackBar(
                positionMs: position.data?.inMilliseconds ?? 0,
                durationMs: durationMs,
                playing: playing.data ?? false,
                muted: muted,
                enabled: !busy && !loading && durationMs > 0,
                onToggle: togglePlayback,
                onMute: toggleMute,
                onSeek: (value) => previewAt(
                    value.clamp(0, durationMs > 0 ? durationMs - 1 : 0)),
              ),
            ),
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              const Icon(Icons.content_cut, size: 18),
              const SizedBox(width: 8),
              const Text('保存する範囲',
                  style: TextStyle(fontWeight: FontWeight.w600)),
              const Spacer(),
              Text(
                  '${((range.end - range.start) / 1000).toStringAsFixed(1)} 秒'),
            ],
          ),
          const SizedBox(height: 6),
          Container(
            height: 48,
            decoration: BoxDecoration(
              color: const Color(0xffedf3fc),
              borderRadius: BorderRadius.circular(4),
            ),
            child: Stack(fit: StackFit.expand, children: [
              TimelineFrames(frames: timelineFrames),
              RangeSlider(
                min: 0,
                max: durationMs > 0 ? durationMs.toDouble() : 1,
                values: RangeValues(
                  range.start
                      .clamp(0, durationMs > 0 ? durationMs.toDouble() : 1),
                  range.end
                      .clamp(0, durationMs > 0 ? durationMs.toDouble() : 1),
                ),
                labels: RangeLabels(time(range.start), time(range.end)),
                onChanged: busy || loading || durationMs == 0
                    ? null
                    : (v) {
                        if (v.end - v.start < 100) return;
                        final target =
                            rangePreviewPosition(range, v, durationMs);
                        setState(() {
                          range = v;
                          output = null;
                        });
                        previewAt(target);
                      },
              ),
            ]),
          ),
          const SizedBox(height: 6),
          Wrap(
            spacing: 16,
            runSpacing: 10,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              _timeBox('開始', time(range.start)),
              _timeBox('終了', time(range.end)),
              const Text(
                '高速コピーでは境界がずれる場合があります',
                style: TextStyle(color: Colors.blueGrey, fontSize: 12),
              ),
            ],
          ),
        ],
      );

  Widget _timeBox(String label, String value) => Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(label),
          const SizedBox(width: 8),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            decoration: BoxDecoration(
              color: Colors.white,
              border: Border.all(color: const Color(0xffd3d9e3)),
              borderRadius: BorderRadius.circular(5),
            ),
            child: Text(value),
          ),
        ],
      );

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(
          backgroundColor: Colors.white,
          surfaceTintColor: Colors.white,
          leading: const Icon(Icons.sailing, color: Color(0xff1664dd)),
          title: Text(source == null ? 'Kite Clipper' : 'クリップを確認'),
          centerTitle: true,
          actions: [
            TextButton(
              onPressed: busy || loading ? null : pick,
              child: const Text('動画を選択…'),
            ),
            const SizedBox(width: 8),
          ],
        ),
        body: Column(
          children: [
            if (busy || loading) const LinearProgressIndicator(),
            if (error != null)
              EditorNotice(
                failed: true,
                details: error!,
                onDismiss: () => setState(() => error = null),
              ),
            if (output != null && error == null)
              EditorNotice(
                failed: false,
                details: '1本の動画を保存しました。再生して範囲と音声を確認してください。\n$output',
                onDismiss: () => setState(() => output = null),
              ),
            Expanded(
              child: source == null
                  ? Center(
                      child: Column(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          const Icon(
                            Icons.video_library_outlined,
                            size: 64,
                            color: Color(0xff2374e8),
                          ),
                          const SizedBox(height: 20),
                          const Text(
                            '動画から、残したい瞬間を。',
                            style: TextStyle(
                              fontSize: 24,
                              fontWeight: FontWeight.w700,
                            ),
                          ),
                          const SizedBox(height: 12),
                          const Text('動画を選んで、確認・範囲調整・保存'),
                          const SizedBox(height: 28),
                          FilledButton.icon(
                            onPressed: loading ? null : pick,
                            icon: const Icon(Icons.add),
                            label: const Text('動画を選択…'),
                          ),
                        ],
                      ),
                    )
                  : LayoutBuilder(
                      builder: (context, box) {
                        final compact = box.maxWidth < 800;
                        return Padding(
                          padding: const EdgeInsets.all(16),
                          child: compact
                              ? Column(
                                  children: [
                                    if (box.maxHeight > 550) ...[
                                      candidates(true),
                                      const SizedBox(height: 8),
                                    ],
                                    Expanded(child: editor()),
                                  ],
                                )
                              : Row(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    SingleChildScrollView(
                                        child: candidates(false)),
                                    const SizedBox(width: 16),
                                    Expanded(child: editor()),
                                  ],
                                ),
                        );
                      },
                    ),
            ),
            Container(
              color: Colors.white,
              padding: const EdgeInsets.all(16),
              child: SafeArea(
                top: false,
                child: Row(
                  children: [
                    Expanded(
                      child: Text(
                        busy ? '保存中…' : '指定した1区間を保存',
                        style: const TextStyle(fontWeight: FontWeight.w600),
                      ),
                    ),
                    FilledButton.icon(
                      onPressed:
                          source == null || busy || loading || durationMs <= 0
                              ? null
                              : save,
                      icon: const Icon(Icons.save_alt, size: 18),
                      label: const Text('1本保存'),
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      );
}

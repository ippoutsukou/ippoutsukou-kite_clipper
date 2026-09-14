package dev.kiteclipper.kite_clipper_flutter

import android.app.Activity
import android.content.Intent
import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaCodec
import android.media.MediaMuxer
import android.os.Handler
import android.os.Looper
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.nio.ByteBuffer
import java.util.concurrent.Executors

class MainActivity : FlutterActivity() {
    private var pending: MethodChannel.Result? = null
    private var arguments: Map<*, *>? = null
    private val worker = Executors.newSingleThreadExecutor()
    private val thumbnailWorker = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())

    override fun configureFlutterEngine(engine: FlutterEngine) {
        super.configureFlutterEngine(engine)
        MethodChannel(engine.dartExecutor.binaryMessenger, "kite_clipper/thumbnails")
            .setMethodCallHandler { call, result ->
                if (call.method != "frame") { result.notImplemented(); return@setMethodCallHandler }
                val source = call.argument<String>("source")
                val position = call.argument<Number>("positionMs")?.toLong()
                if (source == null || position == null || position < 0) {
                    result.error("arguments", "画像取得の指定が不正です", null)
                    return@setMethodCallHandler
                }
                thumbnailWorker.execute {
                    val retriever = android.media.MediaMetadataRetriever()
                    var frame: android.graphics.Bitmap? = null
                    var scaled: android.graphics.Bitmap? = null
                    try {
                        retriever.setDataSource(source)
                        frame = if (android.os.Build.VERSION.SDK_INT >= 27) {
                            retriever.getScaledFrameAtTime(position * 1000,
                                android.media.MediaMetadataRetriever.OPTION_CLOSEST_SYNC, 160, 90)
                        } else {
                            retriever.getFrameAtTime(position * 1000,
                                android.media.MediaMetadataRetriever.OPTION_CLOSEST_SYNC)
                        }
                        val bitmap = frame
                        val bytes = if (bitmap == null) null else {
                            val ratio = minOf(160.0 / bitmap.width, 90.0 / bitmap.height, 1.0)
                            scaled = android.graphics.Bitmap.createScaledBitmap(bitmap,
                                maxOf(1, (bitmap.width * ratio).toInt()),
                                maxOf(1, (bitmap.height * ratio).toInt()), true)
                            val stream = java.io.ByteArrayOutputStream()
                            scaled!!.compress(android.graphics.Bitmap.CompressFormat.JPEG, 75, stream)
                            stream.toByteArray()
                        }
                        main.post { result.success(bytes) }
                    } catch (_: Exception) {
                        main.post { result.success(null) }
                    } finally {
                        if (scaled !== frame) scaled?.recycle()
                        frame?.recycle()
                        runCatching { retriever.release() }
                    }
                }
            }
        MethodChannel(engine.dartExecutor.binaryMessenger, "kite_clipper/export")
            .setMethodCallHandler { call, result ->
                if (call.method != "export") { result.notImplemented(); return@setMethodCallHandler }
                if (pending != null) { result.error("busy", "保存中です", null); return@setMethodCallHandler }
                if (android.os.Build.VERSION.SDK_INT < 26) {
                    result.error("unsupported", "保存にはAndroid 8以降が必要です", null)
                    return@setMethodCallHandler
                }
                pending = result
                arguments = call.arguments as? Map<*, *>
                val intent = Intent(Intent.ACTION_CREATE_DOCUMENT).apply {
                    addCategory(Intent.CATEGORY_OPENABLE)
                    type = "video/mp4"
                    putExtra(Intent.EXTRA_TITLE, "kite_clip_${System.currentTimeMillis()}.mp4")
                }
                try { startActivityForResult(intent, 8102) }
                catch (e: Exception) { pending = null; arguments = null; result.error("picker", "保存先を開けません", null) }
            }
    }

    @Deprecated("Activity result bridge")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != 8102) return
        val callback = pending ?: return
        val args = arguments
        val uri = data?.data
        if (resultCode != Activity.RESULT_OK || uri == null) {
            pending = null; arguments = null; callback.success(null); return
        }
        worker.execute {
            var stage = "引数確認"
            val formats = mutableListOf<String>()
            try {
                require(args != null)
                val source = args["source"] as String
                val start = (args["startMs"] as Number).toLong() * 1000
                val end = (args["endMs"] as Number).toLong() * 1000
                require(start >= 0 && end > start)
                stage = "保存先を開く"
                contentResolver.openFileDescriptor(uri, "w")!!.use { descriptor ->
                    stage = "MP4書き込み準備"
                    val muxer = MediaMuxer(descriptor.fileDescriptor, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
                    val extractor = MediaExtractor()
                    try {
                        stage = "元動画を開く"
                        extractor.setDataSource(source)
                        val tracks = mutableMapOf<Int, Int>()
                        var video = -1
                        var audio = -1
                        var pcmAudio = -1
                        var bufferSize = 8 * 1024 * 1024
                        for (i in 0 until extractor.trackCount) {
                            stage = "トラック形式の確認"
                            val format = extractor.getTrackFormat(i)
                            val mime = format.getString(MediaFormat.KEY_MIME) ?: continue
                            formats.add(if (mime.matches(Regex("[a-zA-Z0-9.+/_-]{1,80}"))) mime else "形式不明")
                            if (mime.startsWith("video/") && video == -1) {
                                require(mime == "video/avc" || mime == "video/hevc") { "未対応の映像形式です" }
                                video = i
                                if (format.containsKey(MediaFormat.KEY_ROTATION)) muxer.setOrientationHint(format.getInteger(MediaFormat.KEY_ROTATION))
                            } else if (mime.startsWith("audio/") && audio == -1) {
                                require(mime == "audio/mp4a-latm" || mime == "audio/raw") { "未対応の音声形式です" }
                                audio = i
                                if (mime == "audio/raw") {
                                    pcmAudio = i
                                    continue
                                }
                            } else continue
                            if (format.containsKey(MediaFormat.KEY_MAX_INPUT_SIZE)) bufferSize = maxOf(bufferSize, format.getInteger(MediaFormat.KEY_MAX_INPUT_SIZE))
                            stage = "MP4トラック登録"
                            tracks[i] = muxer.addTrack(format)
                        }
                        stage = "保存範囲の先頭を検索"
                        require(video >= 0) { "映像トラックがありません" }
                        extractor.selectTrack(video)
                        extractor.seekTo(start, MediaExtractor.SEEK_TO_PREVIOUS_SYNC)
                        val actualStart = extractor.sampleTime
                        require(actualStart >= 0 && actualStart < end)
                        tracks.keys.forEach { extractor.selectTrack(it) }
                        extractor.seekTo(actualStart, MediaExtractor.SEEK_TO_PREVIOUS_SYNC)
                        require(bufferSize <= 64 * 1024 * 1024) { "必要なバッファが大きすぎます" }
                        stage = "バッファ確保"
                        val buffer = ByteBuffer.allocateDirect(bufferSize)
                        val info = MediaCodec.BufferInfo()
                        stage = "MP4書き込み開始"
                        if (pcmAudio >= 0) {
                            stage = "PCM音声をAACへ変換"
                            var aacTrack = -1
                            PcmAac.encode(source, pcmAudio, actualStart, end,
                                onFormat = { format ->
                                    aacTrack = muxer.addTrack(format)
                                    muxer.start()
                                },
                                onSample = { data, sampleInfo ->
                                    muxer.writeSampleData(aacTrack, data, sampleInfo)
                                })
                        } else {
                            muxer.start()
                        }
                        var videoSamples = 0
                        while (extractor.sampleTrackIndex >= 0) {
                            val track = extractor.sampleTrackIndex
                            val pts = extractor.sampleTime
                            if (pts >= actualStart && pts < end) {
                                buffer.clear()
                                stage = "映像・音声データ読み取り"
                                val size = extractor.readSampleData(buffer, 0)
                                if (size < 0) break
                                require(extractor.sampleFlags and MediaExtractor.SAMPLE_FLAG_ENCRYPTED == 0) { "暗号化動画は未対応です" }
                                info.set(0, size, pts - actualStart,
                                    if (extractor.sampleFlags and MediaExtractor.SAMPLE_FLAG_SYNC != 0) MediaCodec.BUFFER_FLAG_KEY_FRAME else 0)
                                stage = "映像・音声データ書き込み"
                                muxer.writeSampleData(tracks.getValue(track), buffer, info)
                                if (track == video) videoSamples++
                            }
                            stage = "次のデータへ移動"
                            if (!extractor.advance()) break
                        }
                        stage = "出力内容確認"
                        require(videoSamples > 0) { "範囲内に映像がありません" }
                        stage = "MP4最終処理"
                        muxer.stop()
                    } finally {
                        // 後片付けの例外で本来の保存失敗の原因を上書きしない。
                        runCatching { extractor.release() }
                        runCatching { muxer.release() }
                    }
                    stage = "保存先を閉じる"
                }
                main.post { pending = null; arguments = null; callback.success(uri.toString()) }
            } catch (e: Exception) {
                val knownReasons = setOf("PCM16以外の音声は未対応です", "音声のサンプルレート・チャンネル数が未対応です", "PCMデータの構造が未対応です", "音声変換がタイムアウトしました", "範囲内に音声がありません", "未対応の映像形式です", "未対応の音声形式です", "映像トラックがありません", "必要なバッファが大きすぎます", "暗号化動画は未対応です", "範囲内に映像がありません")
                val reason = e.message?.takeIf { it in knownReasons } ?: when (e) {
                    is SecurityException -> "アクセスが拒否されました"
                    is java.io.IOException -> "入出力処理に失敗しました（容量不足とは限りません）"
                    is IllegalArgumentException -> "形式・範囲またはAPI引数が受け付けられませんでした"
                    is IllegalStateException -> "動画処理エンジンが処理を完了できませんでした"
                    else -> "詳細は診断コードで確認が必要です"
                }
                val diagnostic = "ビルド4：保存失敗\n段階：$stage\n理由：$reason\n形式：${formats.take(8).joinToString().ifEmpty { "未取得" }}\n種類：${e.javaClass.simpleName}\n保存先に未完成ファイルが残る場合があります。"
                main.post { pending = null; arguments = null; callback.error("export", diagnostic, null) }
            }
        }
    }
}

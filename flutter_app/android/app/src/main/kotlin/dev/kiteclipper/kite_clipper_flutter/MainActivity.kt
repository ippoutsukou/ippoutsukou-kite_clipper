package dev.kiteclipper.kite_clipper_flutter

import android.app.Activity
import android.content.Intent
import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaCodec
import android.media.MediaMuxer
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.provider.DocumentsContract
import android.view.WindowManager
import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import java.io.File
import java.nio.FloatBuffer
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.nio.ByteBuffer
import java.util.concurrent.Executors

/** 保存失敗の診断用。どの段階・どの形式で失敗したかを記録する。 */
private class ExportProgress {
    var stage = "引数確認"
    val formats = mutableListOf<String>()
}

class MainActivity : FlutterActivity() {
    private var pending: MethodChannel.Result? = null
    private var arguments: Map<*, *>? = null
    private var pendingFolder: MethodChannel.Result? = null
    private var folderName: String? = null
    // 一括保存のフォルダ作成・1件書き込み中。メインスレッドでのみ読み書きする。
    private var batchBusy = false
    private val worker = Executors.newSingleThreadExecutor()
    private val thumbnailWorker = Executors.newSingleThreadExecutor()
    // AI候補の測定用。解析は1件ずつ、CLIPのセッションは使い回す。
    private val analyzeWorker = Executors.newSingleThreadExecutor()
    private var analyzing = false
    private var clipSession: OrtSession? = null
    private val main = Handler(Looper.getMainLooper())

    private fun isBusy() = pending != null || pendingFolder != null || batchBusy

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
        MethodChannel(engine.dartExecutor.binaryMessenger, "kite_clipper/analyze")
            .setMethodCallHandler { call, result ->
                if (call.method != "analyze") { result.notImplemented(); return@setMethodCallHandler }
                if (analyzing) { result.error("busy", "解析中です", null); return@setMethodCallHandler }
                val source = call.argument<String>("source")
                val intervalMs = call.argument<Number>("intervalMs")?.toLong() ?: 1000L
                val maxFrames = call.argument<Number>("maxFrames")?.toInt() ?: 300
                val coefficients = call.argument<List<Double>>("coef")
                val intercept = call.argument<Number>("intercept")?.toDouble() ?: 0.0
                if (source == null || coefficients == null || coefficients.size != CLIP_EMBED_DIM ||
                    intervalMs < 100 || maxFrames < 1) {
                    result.error("arguments", "解析の指定が不正です", null)
                    return@setMethodCallHandler
                }
                analyzing = true
                analyzeWorker.execute {
                    try {
                        val measured = analyze(source, intervalMs, maxFrames, coefficients, intercept)
                        main.post { analyzing = false; result.success(measured) }
                    } catch (e: Exception) {
                        val message = "AI解析に失敗しました：${e.javaClass.simpleName} ${e.message ?: ""}"
                        main.post { analyzing = false; result.error("analyze", message, null) }
                    }
                }
            }
        MethodChannel(engine.dartExecutor.binaryMessenger, "kite_clipper/export")
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "export" -> startExport(call.arguments as? Map<*, *>, result)
                    "pickFolder" -> startPickFolder(call.argument<String>("name"), result)
                    "exportTo" -> exportTo(
                        call.argument<String>("folder"), call.argument<String>("name"),
                        call.argument<String>("source"),
                        call.argument<Number>("startMs")?.toLong(),
                        call.argument<Number>("endMs")?.toLong(), result)
                    // 作業記録の保存場所。アプリ専用領域なので権限は不要。
                    "appDataDir" -> result.success(filesDir.absolutePath)
                    "keepScreenOn" -> {
                        if (call.argument<Boolean>("on") == true) {
                            window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                        } else {
                            window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                        }
                        result.success(null)
                    }
                    else -> result.notImplemented()
                }
            }
    }

    /** 1件保存：保存先をその都度選ぶ（ビルド11までと同じ流れ）。 */
    private fun startExport(args: Map<*, *>?, result: MethodChannel.Result) {
        if (isBusy()) { result.error("busy", "保存中です", null); return }
        if (android.os.Build.VERSION.SDK_INT < 26) {
            result.error("unsupported", "保存にはAndroid 8以降が必要です", null)
            return
        }
        pending = result
        arguments = args
        val intent = Intent(Intent.ACTION_CREATE_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = "video/mp4"
            putExtra(Intent.EXTRA_TITLE, "kite_clip_${System.currentTimeMillis()}.mp4")
        }
        try { startActivityForResult(intent, EXPORT_REQUEST) }
        catch (e: Exception) { pending = null; arguments = null; result.error("picker", "保存先を開けません", null) }
    }

    /** 一括保存：フォルダを1回選び、その中に実行ごとのフォルダを作る。 */
    private fun startPickFolder(name: String?, result: MethodChannel.Result) {
        if (isBusy()) { result.error("busy", "保存中です", null); return }
        if (name.isNullOrBlank()) { result.error("arguments", "フォルダ名が不正です", null); return }
        pendingFolder = result
        folderName = name
        try { startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT_TREE), FOLDER_REQUEST) }
        catch (e: Exception) {
            pendingFolder = null; folderName = null
            result.error("picker", "フォルダ選択画面を開けません", null)
        }
    }

    /** 一括保存の1件：実行ごとのフォルダにファイルを作って書き込む。同名は保存先側が別名にする。 */
    private fun exportTo(folder: String?, name: String?, source: String?, startMs: Long?, endMs: Long?,
                         result: MethodChannel.Result) {
        if (isBusy()) { result.error("busy", "保存中です", null); return }
        if (android.os.Build.VERSION.SDK_INT < 26) {
            result.error("unsupported", "保存にはAndroid 8以降が必要です", null)
            return
        }
        if (folder == null || name.isNullOrBlank() || source == null || startMs == null || endMs == null ||
            startMs < 0 || endMs <= startMs) {
            result.error("arguments", "保存の指定が不正です", null)
            return
        }
        batchBusy = true
        worker.execute {
            val progress = ExportProgress()
            var created: Uri? = null
            try {
                progress.stage = "保存先ファイルを作成"
                val file = DocumentsContract.createDocument(contentResolver, Uri.parse(folder), "video/mp4", name)
                    ?: throw java.io.IOException("ファイルを作成できません")
                created = file
                writeClip(file, source, startMs, endMs, progress)
                main.post { batchBusy = false; result.success(file.toString()) }
            } catch (e: Exception) {
                // ファイル自体を作れない場合はフォルダの問題として扱い、Dart側で残りを中止する。
                val code = if (created == null) "folder" else "export"
                val message = diagnostic(progress, e)
                main.post { batchBusy = false; result.error(code, message, null) }
            }
        }
    }

    @Deprecated("Activity result bridge")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        when (requestCode) {
            EXPORT_REQUEST -> onExportDestination(resultCode, data)
            FOLDER_REQUEST -> onFolderChosen(resultCode, data)
        }
    }

    private fun onExportDestination(resultCode: Int, data: Intent?) {
        val callback = pending ?: return
        val args = arguments
        val uri = data?.data
        if (resultCode != Activity.RESULT_OK || uri == null) {
            pending = null; arguments = null; callback.success(null); return
        }
        worker.execute {
            val progress = ExportProgress()
            try {
                require(args != null)
                val source = args["source"] as String
                val start = (args["startMs"] as Number).toLong()
                val end = (args["endMs"] as Number).toLong()
                writeClip(uri, source, start, end, progress)
                main.post { pending = null; arguments = null; callback.success(uri.toString()) }
            } catch (e: Exception) {
                val message = diagnostic(progress, e)
                main.post { pending = null; arguments = null; callback.error("export", message, null) }
            }
        }
    }

    private fun onFolderChosen(resultCode: Int, data: Intent?) {
        val callback = pendingFolder ?: return
        val name = folderName
        pendingFolder = null; folderName = null
        val tree = data?.data
        if (resultCode != Activity.RESULT_OK || tree == null || name == null) {
            callback.success(null); return
        }
        batchBusy = true
        worker.execute {
            try {
                val parent = DocumentsContract.buildDocumentUriUsingTree(tree, DocumentsContract.getTreeDocumentId(tree))
                val parentName = displayName(parent) ?: "選んだフォルダ"
                val folder = DocumentsContract.createDocument(contentResolver, parent,
                    DocumentsContract.Document.MIME_TYPE_DIR, name)
                    ?: throw java.io.IOException("フォルダを作成できません")
                val label = "$parentName/${displayName(folder) ?: name}"
                main.post { batchBusy = false; callback.success(mapOf("uri" to folder.toString(), "label" to label)) }
            } catch (e: Exception) {
                val reason = if (e is SecurityException) "アクセスが拒否されました" else "別のフォルダを選んでください"
                main.post { batchBusy = false; callback.error("folder", "保存先フォルダを作成できませんでした（$reason）", null) }
            }
        }
    }

    /** 同梱のCLIPモデルをアプリ専用領域へ取り出す（毎回コピーしない）。 */
    private fun clipModelFile(): File {
        val target = File(filesDir, "vision_model_quantized.onnx")
        val key = io.flutter.FlutterInjector.instance().flutterLoader()
            .getLookupKeyForAsset("assets/clip/vision_model_quantized.onnx")
        val expected = assets.openFd(key).use { it.length }
        if (target.exists() && target.length() == expected) return target
        val temp = File(filesDir, "vision_model_quantized.onnx.tmp")
        assets.open(key).use { input -> temp.outputStream().use { input.copyTo(it) } }
        if (!temp.renameTo(target)) throw java.io.IOException("モデルを取り出せません")
        return target
    }

    private fun clipSession(): OrtSession {
        clipSession?.let { return it }
        val environment = OrtEnvironment.getEnvironment()
        val created = environment.createSession(clipModelFile().absolutePath, OrtSession.SessionOptions())
        clipSession = created
        return created
    }

    /**
     * 動画から intervalMs ごとにフレームを取り出し、CLIP埋め込み→採用確率を計算する。
     * 速度測定が目的のため、段階ごとの所要時間も返す。保存処理には触れない。
     */
    private fun analyze(
        source: String, intervalMs: Long, maxFrames: Int,
        coefficients: List<Double>, intercept: Double,
    ): Map<String, Any> {
        val started = System.currentTimeMillis()
        val modelStart = System.currentTimeMillis()
        val session = clipSession()
        val environment = OrtEnvironment.getEnvironment()
        val inputName = session.inputNames.first()
        val modelMs = System.currentTimeMillis() - modelStart

        val retriever = android.media.MediaMetadataRetriever()
        val timesMs = mutableListOf<Long>()
        val scores = mutableListOf<Double>()
        var extractMs = 0L
        var inferMs = 0L
        var durationMs = 0L
        var truncated = false
        try {
            retriever.setDataSource(source)
            durationMs = retriever.extractMetadata(
                android.media.MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull() ?: 0L
            require(durationMs > 0) { "動画の長さを取得できません" }
            val width = retriever.extractMetadata(
                android.media.MediaMetadataRetriever.METADATA_KEY_VIDEO_WIDTH)?.toIntOrNull() ?: CLIP_IMAGE_SIZE
            val height = retriever.extractMetadata(
                android.media.MediaMetadataRetriever.METADATA_KEY_VIDEO_HEIGHT)?.toIntOrNull() ?: CLIP_IMAGE_SIZE
            // Python版と同じ前処理：短辺224へ縮小してから中央224x224を切り出す。
            val scale = CLIP_IMAGE_SIZE.toDouble() / minOf(width, height).coerceAtLeast(1)
            val scaledWidth = maxOf(CLIP_IMAGE_SIZE, Math.round(width * scale).toInt())
            val scaledHeight = maxOf(CLIP_IMAGE_SIZE, Math.round(height * scale).toInt())

            val batch = ArrayList<FloatArray>(BATCH_SIZE)
            val batchTimes = ArrayList<Long>(BATCH_SIZE)

            fun runBatch() {
                if (batch.isEmpty()) return
                val inferStart = System.currentTimeMillis()
                val buffer = FloatBuffer.allocate(batch.size * 3 * CLIP_IMAGE_SIZE * CLIP_IMAGE_SIZE)
                batch.forEach { buffer.put(it) }
                buffer.rewind()
                OnnxTensor.createTensor(environment, buffer,
                    longArrayOf(batch.size.toLong(), 3, CLIP_IMAGE_SIZE.toLong(), CLIP_IMAGE_SIZE.toLong())
                ).use { tensor ->
                    session.run(mapOf(inputName to tensor)).use { output ->
                        @Suppress("UNCHECKED_CAST")
                        val embeddings = output[0].value as Array<FloatArray>
                        for ((index, raw) in embeddings.withIndex()) {
                            var norm = 0.0
                            for (value in raw) norm += value.toDouble() * value
                            norm = Math.sqrt(norm)
                            var sum = intercept
                            if (norm > 1e-12) {
                                for (i in raw.indices) sum += coefficients[i] * (raw[i] / norm)
                            }
                            timesMs.add(batchTimes[index])
                            scores.add(1.0 / (1.0 + Math.exp(-sum)))
                        }
                    }
                }
                inferMs += System.currentTimeMillis() - inferStart
                batch.clear()
                batchTimes.clear()
            }

            var positionMs = 0L
            while (positionMs < durationMs) {
                if (timesMs.size + batch.size >= maxFrames) { truncated = true; break }
                val extractStart = System.currentTimeMillis()
                val frame = retriever.getScaledFrameAtTime(
                    positionMs * 1000,
                    android.media.MediaMetadataRetriever.OPTION_CLOSEST_SYNC,
                    scaledWidth, scaledHeight)
                extractMs += System.currentTimeMillis() - extractStart
                if (frame != null) {
                    batch.add(preprocess(frame))
                    batchTimes.add(positionMs)
                    frame.recycle()
                    if (batch.size >= BATCH_SIZE) runBatch()
                }
                positionMs += intervalMs
            }
            runBatch()
        } finally {
            runCatching { retriever.release() }
        }
        return mapOf(
            "durationMs" to durationMs,
            "timesMs" to timesMs,
            "scores" to scores,
            "modelMs" to modelMs,
            "extractMs" to extractMs,
            "inferMs" to inferMs,
            "totalMs" to (System.currentTimeMillis() - started),
            "truncated" to truncated,
        )
    }

    /** Bitmap の中央224x224を CHW float32 に正規化する（CLIPのmean/std）。 */
    private fun preprocess(frame: android.graphics.Bitmap): FloatArray {
        val left = maxOf(0, (frame.width - CLIP_IMAGE_SIZE) / 2)
        val top = maxOf(0, (frame.height - CLIP_IMAGE_SIZE) / 2)
        val pixels = IntArray(CLIP_IMAGE_SIZE * CLIP_IMAGE_SIZE)
        frame.getPixels(pixels, 0, CLIP_IMAGE_SIZE, left, top, CLIP_IMAGE_SIZE, CLIP_IMAGE_SIZE)
        val plane = CLIP_IMAGE_SIZE * CLIP_IMAGE_SIZE
        val output = FloatArray(3 * plane)
        for (i in pixels.indices) {
            val pixel = pixels[i]
            val r = ((pixel shr 16) and 0xff) / 255f
            val g = ((pixel shr 8) and 0xff) / 255f
            val b = (pixel and 0xff) / 255f
            output[i] = (r - CLIP_MEAN[0]) / CLIP_STD[0]
            output[plane + i] = (g - CLIP_MEAN[1]) / CLIP_STD[1]
            output[2 * plane + i] = (b - CLIP_MEAN[2]) / CLIP_STD[2]
        }
        return output
    }

    private fun displayName(uri: Uri): String? = runCatching {
        contentResolver.query(uri, arrayOf(DocumentsContract.Document.COLUMN_DISPLAY_NAME), null, null, null)?.use { cursor ->
            if (cursor.moveToFirst()) cursor.getString(0) else null
        }
    }.getOrNull()

    /** 指定の保存先URIへ範囲を書き込む。1件保存・一括保存で共用（ビルド11までの処理を切り出したもの）。 */
    private fun writeClip(uri: Uri, source: String, startMs: Long, endMs: Long, progress: ExportProgress) {
        val start = startMs * 1000
        val end = endMs * 1000
        require(start >= 0 && end > start)
        progress.stage = "保存先を開く"
        contentResolver.openFileDescriptor(uri, "w")!!.use { descriptor ->
            progress.stage = "MP4書き込み準備"
            val muxer = MediaMuxer(descriptor.fileDescriptor, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
            val extractor = MediaExtractor()
            try {
                progress.stage = "元動画を開く"
                extractor.setDataSource(source)
                val tracks = mutableMapOf<Int, Int>()
                var video = -1
                var audio = -1
                var pcmAudio = -1
                var bufferSize = 8 * 1024 * 1024
                for (i in 0 until extractor.trackCount) {
                    progress.stage = "トラック形式の確認"
                    val format = extractor.getTrackFormat(i)
                    val mime = format.getString(MediaFormat.KEY_MIME) ?: continue
                    progress.formats.add(if (mime.matches(Regex("[a-zA-Z0-9.+/_-]{1,80}"))) mime else "形式不明")
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
                    progress.stage = "MP4トラック登録"
                    tracks[i] = muxer.addTrack(format)
                }
                progress.stage = "保存範囲の先頭を検索"
                require(video >= 0) { "映像トラックがありません" }
                extractor.selectTrack(video)
                extractor.seekTo(start, MediaExtractor.SEEK_TO_PREVIOUS_SYNC)
                val actualStart = extractor.sampleTime
                require(actualStart >= 0 && actualStart < end)
                tracks.keys.forEach { extractor.selectTrack(it) }
                extractor.seekTo(actualStart, MediaExtractor.SEEK_TO_PREVIOUS_SYNC)
                require(bufferSize <= 64 * 1024 * 1024) { "必要なバッファが大きすぎます" }
                progress.stage = "バッファ確保"
                val buffer = ByteBuffer.allocateDirect(bufferSize)
                val info = MediaCodec.BufferInfo()
                progress.stage = "MP4書き込み開始"
                if (pcmAudio >= 0) {
                    progress.stage = "PCM音声をAACへ変換"
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
                        progress.stage = "映像・音声データ読み取り"
                        val size = extractor.readSampleData(buffer, 0)
                        if (size < 0) break
                        require(extractor.sampleFlags and MediaExtractor.SAMPLE_FLAG_ENCRYPTED == 0) { "暗号化動画は未対応です" }
                        info.set(0, size, pts - actualStart,
                            if (extractor.sampleFlags and MediaExtractor.SAMPLE_FLAG_SYNC != 0) MediaCodec.BUFFER_FLAG_KEY_FRAME else 0)
                        progress.stage = "映像・音声データ書き込み"
                        muxer.writeSampleData(tracks.getValue(track), buffer, info)
                        if (track == video) videoSamples++
                    }
                    progress.stage = "次のデータへ移動"
                    if (!extractor.advance()) break
                }
                progress.stage = "出力内容確認"
                require(videoSamples > 0) { "範囲内に映像がありません" }
                progress.stage = "MP4最終処理"
                muxer.stop()
            } finally {
                // 後片付けの例外で本来の保存失敗の原因を上書きしない。
                runCatching { extractor.release() }
                runCatching { muxer.release() }
            }
            progress.stage = "保存先を閉じる"
        }
    }

    private fun diagnostic(progress: ExportProgress, e: Exception): String {
        val knownReasons = setOf("PCM16以外の音声は未対応です", "音声のサンプルレート・チャンネル数が未対応です", "PCMデータの構造が未対応です", "音声変換がタイムアウトしました", "範囲内に音声がありません", "未対応の映像形式です", "未対応の音声形式です", "映像トラックがありません", "必要なバッファが大きすぎます", "暗号化動画は未対応です", "範囲内に映像がありません")
        val reason = e.message?.takeIf { it in knownReasons } ?: when (e) {
            is SecurityException -> "アクセスが拒否されました"
            is java.io.IOException -> "入出力処理に失敗しました（容量不足とは限りません）"
            is IllegalArgumentException -> "形式・範囲またはAPI引数が受け付けられませんでした"
            is IllegalStateException -> "動画処理エンジンが処理を完了できませんでした"
            else -> "詳細は診断コードで確認が必要です"
        }
        return "ビルド4：保存失敗\n段階：${progress.stage}\n理由：$reason\n形式：${progress.formats.take(8).joinToString().ifEmpty { "未取得" }}\n種類：${e.javaClass.simpleName}\n保存先に未完成ファイルが残る場合があります。"
    }

    companion object {
        private const val EXPORT_REQUEST = 8102
        private const val FOLDER_REQUEST = 8103
        // vendor/clip/SOURCE.txt と clip_embedder.py に合わせた前処理仕様。
        private const val CLIP_EMBED_DIM = 512
        private const val CLIP_IMAGE_SIZE = 224
        private const val BATCH_SIZE = 8
        private val CLIP_MEAN = floatArrayOf(0.48145466f, 0.4578275f, 0.40821073f)
        private val CLIP_STD = floatArrayOf(0.26862954f, 0.26130258f, 0.27577711f)
    }
}

package dev.kiteclipper.kite_clipper_flutter

import android.media.AudioFormat
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaExtractor
import android.media.MediaFormat
import android.os.SystemClock
import java.nio.ByteBuffer

/** PCM16 の指定区間を AAC に変換。映像と同じ startUs を時刻原点にする。 */
object PcmAac {
    fun encode(source: String, track: Int, startUs: Long, endUs: Long,
               onFormat: (MediaFormat) -> Unit,
               onSample: (ByteBuffer, MediaCodec.BufferInfo) -> Unit) {
        val extractor = MediaExtractor()
        var encoder: MediaCodec? = null
        try {
            extractor.setDataSource(source)
            extractor.selectTrack(track)
            val input = extractor.getTrackFormat(track)
            require(!input.containsKey(MediaFormat.KEY_PCM_ENCODING) ||
                input.getInteger(MediaFormat.KEY_PCM_ENCODING) == AudioFormat.ENCODING_PCM_16BIT) {
                "PCM16以外の音声は未対応です"
            }
            val rate = input.getInteger(MediaFormat.KEY_SAMPLE_RATE)
            val channels = input.getInteger(MediaFormat.KEY_CHANNEL_COUNT)
            require(rate > 0 && channels in 1..2) { "音声のサンプルレート・チャンネル数が未対応です" }
            val frameBytes = channels * 2
            val format = MediaFormat.createAudioFormat("audio/mp4a-latm", rate, channels).apply {
                setInteger(MediaFormat.KEY_AAC_PROFILE, MediaCodecInfo.CodecProfileLevel.AACObjectLC)
                setInteger(MediaFormat.KEY_BIT_RATE, if (channels == 1) 96000 else 192000)
            }
            val codec = MediaCodec.createEncoderByType("audio/mp4a-latm")
            encoder = codec
            codec.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
            codec.start()
            extractor.seekTo(startUs, MediaExtractor.SEEK_TO_PREVIOUS_SYNC)
            var chunk: ByteBuffer? = null
            var chunkPts = 0L
            var consumedFrames = 0L
            var inputDone = false
            var outputDone = false
            var announced = false
            var samples = 0
            var lastProgress = SystemClock.elapsedRealtime()
            val info = MediaCodec.BufferInfo()
            while (!outputDone) {
                check(SystemClock.elapsedRealtime() - lastProgress < 30000) { "音声変換がタイムアウトしました" }
                if (!inputDone) {
                    val index = codec.dequeueInputBuffer(10000)
                    if (index >= 0) {
                        val destination = codec.getInputBuffer(index)!!
                        destination.clear()
                        while (chunk == null && extractor.sampleTime >= 0 && extractor.sampleTime < endUs) {
                            val pts = extractor.sampleTime
                            val size = extractor.sampleSize
                            require(size in 1..(8L * 1024 * 1024) && size % frameBytes == 0L) { "PCMデータの構造が未対応です" }
                            require(extractor.sampleFlags and MediaExtractor.SAMPLE_FLAG_ENCRYPTED == 0) { "暗号化動画は未対応です" }
                            val buffer = ByteBuffer.allocateDirect(size.toInt())
                            val read = extractor.readSampleData(buffer, 0)
                            check(read == size.toInt())
                            val frames = read / frameBytes
                            val first = (((startUs - pts).coerceAtLeast(0) * rate + 999999) / 1000000).coerceAtMost(frames.toLong()).toInt()
                            val last = (((endUs - pts).coerceAtLeast(0) * rate) / 1000000).coerceAtMost(frames.toLong()).toInt()
                            if (last > first) {
                                buffer.position(first * frameBytes)
                                buffer.limit(last * frameBytes)
                                chunk = buffer
                                chunkPts = pts + first.toLong() * 1000000 / rate - startUs
                                consumedFrames = 0
                            }
                            extractor.advance()
                        }
                        val current = chunk
                        if (current == null) {
                            codec.queueInputBuffer(index, 0, 0, endUs - startUs, MediaCodec.BUFFER_FLAG_END_OF_STREAM)
                            inputDone = true
                        } else {
                            val count = minOf(destination.remaining(), current.remaining()) / frameBytes * frameBytes
                            check(count > 0)
                            val slice = current.duplicate()
                            slice.limit(slice.position() + count)
                            destination.put(slice)
                            codec.queueInputBuffer(index, 0, count, chunkPts + consumedFrames * 1000000 / rate, 0)
                            current.position(current.position() + count)
                            consumedFrames += count / frameBytes
                            if (!current.hasRemaining()) chunk = null
                        }
                        lastProgress = SystemClock.elapsedRealtime()
                    }
                }
                val index = codec.dequeueOutputBuffer(info, 10000)
                if (index == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                    check(!announced)
                    onFormat(codec.outputFormat)
                    announced = true
                    lastProgress = SystemClock.elapsedRealtime()
                } else if (index >= 0) {
                    try {
                        if (info.size > 0 && info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG == 0) {
                            check(announced)
                            if (info.presentationTimeUs >= 0 && info.presentationTimeUs < endUs - startUs) {
                                onSample(codec.getOutputBuffer(index)!!, info)
                                samples++
                            }
                        }
                        outputDone = info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0
                    } finally { codec.releaseOutputBuffer(index, false) }
                    lastProgress = SystemClock.elapsedRealtime()
                }
            }
            check(samples > 0) { "範囲内に音声がありません" }
        } finally {
            runCatching { encoder?.stop() }
            runCatching { encoder?.release() }
            extractor.release()
        }
    }
}

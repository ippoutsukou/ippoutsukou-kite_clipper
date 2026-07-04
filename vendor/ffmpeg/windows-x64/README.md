# Bundled FFmpeg for Windows x64

This directory is reserved for the bundled Windows x64 FFmpeg binaries.

Use an LGPL build only. Do not bundle a build that contains GPL or nonfree
components.

Required files for distribution:

- `ffmpeg.exe`
- `ffprobe.exe`
- FFmpeg license text
- FFmpeg source location and build information

Before distributing, run:

```powershell
.\vendor\ffmpeg\windows-x64\ffmpeg.exe -version
.\vendor\ffmpeg\windows-x64\ffprobe.exe -version
```

The configuration output must not contain:

```text
--enable-gpl
--enable-nonfree
```

If either option appears, do not distribute that build with this application.

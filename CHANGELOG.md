# Changelog

## v0.5.0 — 2026-10-16

One large revision of R4: 20 bugs (B1–B20) fixed and 18 features (F1–F18) added.

### Fixed (R4 bugs, each with a test)
- **B1** A CSV larger than 8 KB with accented text after the first 8 KB (Excel "ANSI") crashed Generate — the whole
  file is now decoded at once (BOM → UTF-16 → UTF-8 → cp1252 → latin-1)
- **B2** Excel's curly quotes, dashes and € became control characters (cp1252 read as latin-1)
- **B3** `;` CSVs (Excel in Brazil and most of Europe) were read as one single column — the separator is detected
- **B4** Excel numbers `1.0` became season / episode 0; `46 min` became no runtime
- **B5** TheTVDB dates (`May 8, 2024`), `8 de maio de 2024`, Excel serial dates were not understood
- **B6** US dates `05/08/2024` were read day-first — the order is decided per file
- **B7** mixed `/` and `\` in paths (the "`\metadata`" note)
- **B8** self-closing template tags (`<plot/>`) were ignored
- **B9** technical data of the template's episode (codec, size, bitrate, dates) was copied into every episode
- **B10** two rows with the same season/episode overwrote each other silently
- **B11** empty IMDb `uniqueid` written when there was no IMDb ID
- **B12** characters XML does not allow (from Excel) made files unreadable for Kodi/Emby
- **B13** `1x02`, `S01E01E02`, `S01 E02`, `Episode 5`, `pt1`/`pt2` were not recognised
- **B14** renaming a video left its subtitles behind; **B15** a failed case-only rename stranded a file;
  **B16** renames in one batch could collide
- **B17** Tk was called from worker threads; **B18** hard-coded show name and output folder, nothing remembered;
  **B19** the launcher used a fixed path and the newest file by date; **B20** long paths failed on Windows

### Added
- Open a **series folder**: videos, episode numbers, segments, multi-episode files, ffprobe info, existing files
- **CSV / Excel import** with a column-matching dialog (suggestions, Portuguese headers, remembered) and an import
  report; **Paste** from TheTVDB's "All seasons" page (specials keep their position)
- **New CSV from a link**: TheTVDB (series page, official episode list, optional episode pages with translations,
  runtime, rating per country, IMDb, writers, directors, guest stars), TVmaze, IMDb, TMDb; CSV export
- **TVmaze** (free): episodes, cast with roles, crew, guest stars, seasons, images; **TMDb** and **OMDb** (own keys)
- **Several sources**: values per source, rules per field, Sources tab to compare and choose, conflict list,
  matching by number then air date and title
- **Built-in writers**: episode NFO (multi-episode, specials' position), episode XML, tvshow.nfo, series.xml,
  season.nfo — cast, crew, guest stars, ratings, all IDs, streamdetails; legacy templates still possible
- **Episode images** for every server (`-thumb.jpg`, `metadata\<episode>.jpg` + `<filename>`): existing image, CSV,
  TheTVDB, TVmaze, TMDb or the best video frame; pick a frame yourself; Media Center Master images kept
- **Artwork**: poster, folder, fanart, backdrop(1..9), banner, logo/clearlogo, clear art, season posters; add your
  own images (sorted by shape)
- **Changes** preview with diffs, backups in `.kfg_backup`, **Undo last run**; **Check** tab; optional **renamer**
- Output **profiles**: Kodi, Jellyfin, Emby, XML, Plex
- Drag and drop of a series folder, an episode list or images onto the window (the side panels are the drop
  targets — found while building the exe: the first version registered the main window, which tkinterdnd2 cannot do)
- Window: dark title bar, transparent title-bar icon, About, **Check for updates** (GitHub), Help, Settings saved in
  `~/.kodi_files_generator.json`, drag and drop, keyboard shortcuts; tabs in clear colours (user request)
- Secure connections use the Windows certificate check (truststore) or certifi — Python's default rejected
  TVmaze's certificate chain on the test PC
- Tests: 96 (no network, no real media) + a GUI harness. Six parser tests need saved TheTVDB pages, which are
  not in this repository; they are skipped on GitHub CI

### Changed (repository)
- Main file `kodi_generator.py` is now the window module; R4 moved to `archive/versions/kodi_generator_R4.py`
- `Kodi Generator.bat` starts `kodi_generator.py` from its own folder

## R4 — 2026-05-01 and earlier
Single-file app: CSV + NFO/XML templates → episode NFO and XML; Rename Checker.

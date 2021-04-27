# jofsarpur

A configurable downloader for video-content from RÚV.

This project is heavily based/inspired by [ruvsarpur](https://github.com/sverrirs/ruvsarpur).

## Installation

### pip
```
pip install --user git+https://github.com/johannfr/jofsarpur
```

### pipx
```
pipx install git+https://github.com/johannfr/jofsarpur
```

## Configuration

The configuration shall reside in `/etc/jofsarpur.toml`. The required elements are a `[global]` section and at least one `[<sid>]` section, where `<sid>` is the series-id used by RÚV.

### `[global]`
 * `download_directory` - This is the root path where everything gets downloaded to.

### `[<sid>]`
 * `filenames` - A template for the filenames the downloaded files should have. This this includes any subdirectory of the global `download_directory`. The following (useful) fields are exposed to the template:
    * `title` - Series title/name (follows the `title` configurable below).
    * `episode_number` - The eposide-number given by RÚV.
    * `episode_count` - Total number of episodes in the series.
    * `sid` - The series ID given by RÚV.
    * `pid` - The eposide (program) ID given by RÚV.
 * `title` - A custom title to give the `filenames` template. Replaces the one given by RÚV.

Example config-file:

```toml
[global]
    download_directory = "/mnt/large_storage/RUV"

[30228]
    filenames = "{title}/S01E{episode_number:02d}.mp4"
```

This configuration will instruct `jofsarpur` to download all episodes of Kúlugúbbarnir (30228) to a path like: `/mnt/large_storage/RUV/Kúlugúbbarnir/S01E04.mp3`

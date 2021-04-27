import json
import re
import threading
import time
from pathlib import Path
from subprocess import DEVNULL, Popen

import click
import requests
import toml
from rich import inspect
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn

CONFIGURATION_TOML = "/etc/jofsarpur.toml"
DOWNLOADS_JSON = ".jofsarpur-downloads.json"


def query_graphql(graphdata):
    while True:
        json_data = requests.get(
            url=f"https://www.ruv.is/gql/{graphdata}",
            headers={
                "content-type": "application/json",
                "Referer": "https://www.ruv.is/sjonvarp",
                "Origin": "https://www.ruv.is",
            },
        ).json()
        if "data" in json_data.keys():
            return json_data
        time.sleep(1)


def get_series_data(sid):
    graphdata = (
        '?operationName=getEpisode&variables={"programID":'
        + str(sid)
        + '}&extensions={"persistedQuery":{"version":1,"sha256Hash":'
        + '"f3f957a3a577be001eccf93a76cf2ae1b6d10c95e67305c56e4273279115bb93"}}'
    )
    return query_graphql(graphdata)["data"]["Program"]


def get_file_data(sid, pid):
    graphdata = (
        '?operationName=getProgramType&variables={"id":'
        + str(sid)
        + ',"episodeId":["'
        + str(pid)
        + '"]}&extensions={"persistedQuery":{"version":1,"sha256Hash":'
        + '"9d18a07f82fcd469ad52c0656f47fb8e711dc2436983b53754e0c09bad61ca29"}}'
    )
    return query_graphql(graphdata)["data"]["Program"]["episodes"][0]


def parse_file_string(file_string):
    prefix, rest = file_string.split("streams=")
    prefix = prefix + "streams="
    bitrates = rest.split(",")
    max_bitrate_suffix = bitrates[-1].split(":")[0]
    return prefix + max_bitrate_suffix


class DownloadWorker:
    def __init__(self, download_configuration, download_log, progress):
        self.download_configuration = download_configuration
        self.download_log = download_log
        self.progress = progress
        output_filename = Path(
            download_configuration["download_directory"],
            download_configuration["filenames"].format(**download_configuration),
        )
        output_filename.parent.mkdir(parents=True, exist_ok=True)
        process_args = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "verbose",
            "-stats",
            "-y",
            "-i",
            download_configuration["url"],
            "-c",
            "copy",
            "-bsf:a",
            "aac_adtstoasc",
            str(output_filename),
        ]

        self.progress_bar = progress.add_task(
            "Downloading {title} {sid}:{pid}".format(**download_configuration),
            total=1,
        )
        self.process = Popen(process_args, stdout=DEVNULL, stderr=DEVNULL)
        threading.Thread(target=self.poll).start()

    def poll(self):
        while True:
            if self.process.poll() != None:
                if self.process.returncode == 0:
                    if (
                        self.download_configuration["sid"]
                        not in self.download_log.keys()
                    ):
                        self.download_log[self.download_configuration["sid"]] = []
                    self.download_log[self.download_configuration["sid"]].append(
                        self.download_configuration["pid"]
                    )
                    self.progress.log(
                        f"Downloading {self.download_configuration['title']} {self.download_configuration['sid']}:{self.download_configuration['pid']}: [green]Done.[/green]"
                    )
                    self.progress.update(self.progress_bar, advance=1)
                else:
                    self.progress.stop_task(self.progress_bar)
                    self.progress.remove_task(self.progress_bar)
                    self.progress.log(
                        f"Downloading {self.download_configuration['title']} {self.download_configuration['sid']}:{self.download_configuration['pid']}: [red]Failed:[/red] ffmpeg returned {self.process.returncode}"
                    )
                break
            time.sleep(0.2)


@click.command()
@click.option(
    "-c",
    "--config",
    "config_filename",
    default=CONFIGURATION_TOML,
    show_default=True,
    type=click.Path(exists=True, file_okay=True, dir_okay=False, readable=True),
)
@click.option(
    "-l",
    "--log",
    "download_log_filename",
    default=Path(Path.home(), DOWNLOADS_JSON),
    show_default=True,
    type=click.Path(
        exists=False, file_okay=True, dir_okay=False, writable=True, readable=True
    ),
)
def main(config_filename, download_log_filename):
    """
    A configurable downloader for video-content from RÚV.
    """

    configuration = toml.load(CONFIGURATION_TOML)

    global_config = configuration["global"]
    series_count = len([k for k in configuration.keys() if k != "global"])

    try:
        download_log = json.load(open(download_log_filename, "r"))
    except FileNotFoundError:
        download_log = {}

    console = Console()
    download_queue = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
        transient=True,
    ) as progress:
        task_metadata = progress.add_task(
            f"[yellow]Fetching series meta-data", total=series_count, start=False
        )
        for sid, sid_config in [
            (k, v) for k, v in configuration.items() if k != "global"
        ]:
            series = get_series_data(sid)
            title = series["title"]
            task_episodes_metadata = progress.add_task(
                f"Fetching metadata for {title}", total=len(series["episodes"])
            )
            for episode in series["episodes"]:
                episode_number, episode_count = re.match(
                    r"\S+\s([0-9]+) af ([0-9]+)", episode["title"]
                ).groups()
                pid = episode["id"]
                if sid in download_log.keys() and pid in download_log[sid]:
                    progress.log(
                        f"Already downloaded {title} {sid}:{pid}: [purple]Skipping.[/purple]"
                    )
                    progress.start_task(task_metadata)
                    progress.update(task_episodes_metadata, advance=1)
                    continue
                file_string = get_file_data(sid, pid)["file"]
                url = parse_file_string(file_string)
                download_queue.append(
                    {
                        "url": url,
                        "title": sid_config["title"]
                        if "title" in sid_config.keys()
                        else title,
                        "episode_number": int(episode_number),
                        "episode_count": int(episode_count),
                        "sid": sid,
                        "pid": pid,
                        "filenames": sid_config["filenames"],
                        "download_directory": global_config["download_directory"],
                    }
                )
                progress.start_task(task_metadata)
                progress.update(task_episodes_metadata, advance=1)
            progress.update(task_metadata, advance=1)

        # Preprocessing done. Let's start downloading.
        download_workers = [
            DownloadWorker(item, download_log, progress) for item in download_queue
        ]
        while not progress.finished:
            time.sleep(0.5)
    json.dump(download_log, open(download_log_filename, "w"))


if __name__ == "__main__":
    main()

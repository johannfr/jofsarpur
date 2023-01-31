import json
import logging
import re
import threading
import time
from datetime import datetime
from enum import Enum
from pathlib import Path
from subprocess import DEVNULL, Popen

import click
import requests
import toml
from rich.logging import RichHandler

CONFIGURATION_TOML = "/etc/jofsarpur.toml"
DOWNLOADS_JSON = ".jofsarpur-downloads.json"

FORMAT = "%(message)s"
logging.basicConfig(
    level="ERROR", format=FORMAT, datefmt="[%X]", handlers=[RichHandler(markup=True)]
)
LOG = logging.getLogger("jofsarpur")


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
    return file_string
    # Gamalt: Breytti 23. agust, 2022 .. nuna virkar strengurinn beint.
    # prefix, rest = file_string.split("streams=")
    # prefix = prefix + "streams="
    # bitrates = rest.split(",")
    # max_bitrate_suffix = bitrates[-1].split(":")[0]
    # return prefix + max_bitrate_suffix


class DownloadState(Enum):
    WAITING = 0
    DOWNLOADING = 1
    DONE = 2
    ERROR = 3


class DownloadWorker(threading.Thread):
    def __init__(
        self,
        download_configuration,
        download_log,
        dry_run,
    ):
        threading.Thread.__init__(self)
        self.download_configuration = download_configuration
        self.download_log = download_log
        filename_field = "filenames"
        try:
            output_filename = Path(
                download_configuration["download_directory"],
                download_configuration[filename_field].format(**download_configuration),
            )
        except KeyError as e:
            LOG.error(
                f"Downloading {self.download_configuration['title']} {self.download_configuration['sid']}:{self.download_configuration['pid']}: [red]Failed:[/red] KeyError when expanding filename: {e}"
            )
            LOG.error(download_configuration)
            self.state = DownloadState.ERROR
            return

        if not dry_run:
            output_filename.parent.mkdir(parents=True, exist_ok=True)
        self.process_args = [
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
        LOG.debug(f"{' '.join(self.process_args)}")
        if dry_run:
            self.state = DownloadState.DONE
        else:
            self.state = DownloadState.WAITING

    def run(self):
        if self.state != DownloadState.WAITING:
            return
        self.state = DownloadState.DOWNLOADING
        LOG.info(
            "Downloading {title} {sid}:{pid}".format(**self.download_configuration),
        )
        self.process = Popen(self.process_args, stdout=DEVNULL, stderr=DEVNULL)
        while True:
            if self.process.poll() is not None:
                if self.process.returncode == 0:
                    if (
                        self.download_configuration["sid"]
                        not in self.download_log.keys()
                    ):
                        self.download_log[self.download_configuration["sid"]] = []
                    self.download_log[self.download_configuration["sid"]].append(
                        self.download_configuration["pid"]
                    )
                    LOG.info(
                        f"Downloading {self.download_configuration['title']} {self.download_configuration['sid']}:{self.download_configuration['pid']}: [green]Done.[/green]"
                    )
                else:
                    LOG.error(
                        f"Downloading {self.download_configuration['title']} {self.download_configuration['sid']}:{self.download_configuration['pid']}: [red]Failed:[/red] ffmpeg returned {self.process.returncode}"
                    )
                    self.state = DownloadState.ERROR
                    break
                self.state = DownloadState.DONE
                break
            time.sleep(1)


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
@click.option("-t", "--threads", "thread_count", default=4, show_default=True)
@click.option("-d", "--dry-run", is_flag=True, default=False, show_default=True)
@click.option("--debug", is_flag=True, default=False, show_default=True)
def main(config_filename, download_log_filename, thread_count, dry_run, debug):
    """
    A configurable downloader for video-content from RÚV.
    """

    if debug:
        LOG.setLevel(logging.DEBUG)
    else:
        LOG.setLevel(logging.INFO)
    configuration = toml.load(config_filename)

    global_config = configuration["global"]

    try:
        download_log = json.load(open(download_log_filename))
    except FileNotFoundError:
        download_log = {}

    download_queue = []
    for sid, sid_config in [(k, v) for k, v in configuration.items() if k != "global"]:
        series = get_series_data(sid)
        title = series["title"]
        LOG.info(f"Fetching metadata for {title}:{sid}")
        for episode in series["episodes"]:
            episode_item = {}
            try:
                episode_number, episode_count = re.match(
                    r"\S+\s([0-9]+) af ([0-9]+)", episode["title"]
                ).groups()
                episode_item.update(
                    {
                        "episode_number": int(episode_number),
                        "episode_count": int(episode_count),
                    }
                )
            except AttributeError:
                try:
                    episode_number = re.match(
                        r"([0-9]+). kafli", episode["title"]
                    ).groups()[0]
                    episode_item.update(
                        {
                            "episode_number": int(episode_number),
                        }
                    )
                except AttributeError:
                    pass
            pid = episode["id"]
            if sid in download_log.keys() and pid in download_log[sid]:
                LOG.info(
                    f"Already downloaded {title} {sid}:{pid}: [purple]Skipping.[/purple]"
                )
                continue
            file_string = get_file_data(sid, pid)["file"]
            url = parse_file_string(file_string)
            episode_item.update(
                {
                    "url": url,
                    "title": sid_config["title"]
                    if "title" in sid_config.keys()
                    else title,
                    "episode_title": episode["title"],
                    "sid": sid,
                    "pid": pid,
                    "airdate": datetime.strptime(
                        episode["firstrun"], "%Y-%m-%d %H:%M:%S"
                    ),
                    "filenames": sid_config["filenames"],
                    "download_directory": global_config["download_directory"],
                }
            )
            for ex_pid, ex_filename in [
                (e.replace("exception-", ""), sid_config[e])
                for e in sid_config.keys()
                if e.startswith("exception-")
            ]:
                if "exceptions" not in episode_item.keys():
                    episode_item["exceptions"] = {}
                episode_item["exceptions"][ex_pid] = ex_filename

            download_queue.append(episode_item)

    # Preprocessing done. Let's start downloading.
    LOG.info("Downloading episodes.")
    download_workers = [
        DownloadWorker(
            item,
            download_log,
            dry_run,
        )
        for item in download_queue
    ]
    while True:
        done_threads = list(
            filter(lambda worker: worker.state == DownloadState.DONE, download_workers)
        )

        running_threads = list(
            filter(
                lambda worker: worker.state == DownloadState.DOWNLOADING,
                download_workers,
            )
        )
        waiting_threads = list(
            filter(
                lambda worker: worker.state == DownloadState.WAITING,
                download_workers,
            )
        )

        error_threads = list(
            filter(lambda worker: worker.state == DownloadState.ERROR, download_workers)
        )

        if len(done_threads) == len(download_workers):
            break

        if len(waiting_threads) + len(running_threads) == 0 and len(error_threads) > 0:
            break

        if len(done_threads) < len(download_workers):
            for i in range(
                0, min(len(waiting_threads), thread_count - len(running_threads))
            ):
                waiting_threads[i].start()

        time.sleep(0.5)
    if not dry_run:
        json.dump(download_log, open(download_log_filename, "w"))


if __name__ == "__main__":
    main()

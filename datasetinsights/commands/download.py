import logging
import re

import click

import datasetinsights.constants as const
from datasetinsights.io.downloader.base import create_downloader

logger = logging.getLogger(__name__)


class SourceURI(click.ParamType):
    """Source URI Parameter.

    Args:
        click ([type]): [description]

    Raises:
        click.BadParameter: [description]

    Returns:
        [type]: [description]
    """

    name = "source_uri"
    PREFIX_PATTERN = r"^gs://|^http(s)?://|^usim://"

    def convert(self, value, param, ctx):
        """ Validate source URI and Converts the value.
        """
        match = re.search(self.PREFIX_PATTERN, value)
        if not match:
            message = (
                f"The source uri {value} is not supported. "
                f"Pattern: {self.PREFIX_PATTERN}"
            )
            self.fail(message, param, ctx)

        return value


@click.command(
    context_settings=const.CONTEXT_SETTINGS,
)
@click.option(
    "-s",
    "--source-uri",
    type=SourceURI(),
    required=True,
    help=(
        "URI of where this data should be downloaded. "
        f"Supported source uri patterns {SourceURI.PREFIX_PATTERN}"
    ),
)
@click.option(
    "-o",
    "--output",
    type=click.Path(exists=True, file_okay=False, writable=True),
    default=const.DEFAULT_DATA_ROOT,
    help="Directory on localhost where datasets should be downloaded.",
)
@click.option(
    "-b",
    "--include-binary",
    is_flag=True,
    default=False,
    help=(
        "Whether to download binary files such as images or LIDAR point "
        "clouds. This flag applies to Datasets where metadata "
        "(e.g. annotation json, dataset catalog, ...) can be separated from "
        "binary files."
    ),
)
@click.option(
    "--access-token",
    type=str,
    default=None,
    help="Unity Simulation access token. "
    "This will override synthetic datasets source-uri for Unity Simulation",
)
def cli(
    source_uri, output, include_binary, access_token,
):
    """Download datasets to localhost from known locations.

    The download command can support downloading from 3 sources
    usim:// http(s):// gs://

    Examples of --source-uri:

    \b
    Unity Simulation
    usim://access_token@project_id/run_execution_id

    Groceries
    https://storage.googleapis.com/datasetinsights/data/groceries/v3.zip

    Synthetic Sample
    https://storage.googleapis.com/datasetinsights/data/synthetic/SynthDet.zip

    """
    ctx = click.get_current_context()
    logger.debug(f"Called download command with parameters: {ctx.params}")

    downloader = create_downloader(
        source_uri=source_uri, access_token=access_token
    )
    downloader.download(
        source_uri=source_uri, output=output, include_binary=include_binary
    )

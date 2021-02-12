import fcntl
import glob
import logging
import os
import shutil
from pathlib import Path

import datasetinsights.constants as const

from .exceptions import DatasetNotFoundError

ANNOTATION_FILE_TEMPLATE = "{}_{}2017.json"
COCO_GCS_PATH = "data/coco"
COCO_LOCAL_PATH = "coco"
logger = logging.getLogger(__name__)


class CocoDetection:
    """COCO dataset for 2D object detection.

    Before the class instantiation, it would assume that the COCO dataset is
    downloaded.

    See COCO dataset `documentation <http://cocodataset.org/#detection-2019>`_
    for more details.

    Attributes:
        root (str): root path of the data.
        transforms: callable transformation that applies to a pair of
            capture, annotation. Capture is the information captured by the
            sensor, in this case an image, and annotations, which in this
            dataset are 2d bounding box coordinates and labels.
        split (str): indicate split type of the dataset (train|val).
        label_mappings (dict): a dict of {label_id: label_name} mapping.
        coco (torchvision.datasets.CocoDetection): COCO dataset.
    """

    def __init__(
        self,
        *,
        data_path=const.DEFAULT_DATA_ROOT,
        split="train",
        transforms=None,
        remove_examples_without_boxes=True,
        **kwargs,
    ):
        """
        Args:
            data_path (str): Directory of the dataset.
            split (str): indicate split type of the dataset (train|val).
            transforms: callable transformation that applies to a pair of
            capture, annotation.
            remove_examples_without_boxes (bool): whether to remove examples
            without boxes. Defaults to True.
        """
        # todo add test split
        self.split = split
        self.root = data_path
        self._preprocess_dataset(data_path=self.root, split=self.split)
        self.transforms = transforms
        self.label_mappings = self._get_label_mappings()

    @staticmethod
    def _preprocess_dataset(data_path, split):
        """ Preprocess dataset inside data_path and un-archive if necessary.

        Args:
            data_path (str): Path where dataset is stored.
            split (str): indicate split type of the dataset (train|val).

        Return:
            Tuple: (unarchived img path, unarchived annotation path)
        """

        archive_img_file = Path(data_path) / f"{split}2017.zip"
        archive_ann_file = Path(data_path) / "annotations_trainval2017.zip"
        if archive_img_file.exists() and archive_ann_file.exists():
            unarchived_img_path = CocoDetection._unarchive_data(
                data_path, archive_img_file
            )
            unarchived_ann_path = CocoDetection._unarchive_data(
                data_path, archive_ann_file
            )
            return (unarchived_img_path, unarchived_ann_path)
        elif CocoDetection._is_dataset_files_present(data_path):
            # This is for dataset generated by unity simulation.
            return data_path
        else:
            raise DatasetNotFoundError(
                f"Expecting a file {archive_img_file} and {archive_ann_file}"
                "under {data_path}"
            )

    def _unarchive_data(self, data_path, archive_file):
        """unarchive downloaded data.
        Args:
            data_path (str): Path where dataset is stored.
            archive_file (str): archived file name.

        Returns:
            str: unarchived path.
        """
        file_descriptor = os.open(archive_file, os.O_RDONLY)
        try:
            fcntl.flock(file_descriptor, fcntl.LOCK_EX)
            unarchived_path = Path(data_path)
            if not CocoDetection._is_dataset_files_present(unarchived_path):
                shutil.unpack_archive(
                    filename=archive_file, extract_dir=unarchived_path,
                )
                logger.info(f"Unpack {archive_file} to {unarchived_path}")
        finally:
            os.close(file_descriptor)
        return unarchived_path

    @staticmethod
    def _is_dataset_files_present(data_path):
        """check whether dataset files exist.

        Args:
            data_path (str): Path where dataset is stored.

        Returns:
            bool: whether dataset files exist.
        """
        return (
            os.path.isdir(data_path)
            and any(glob.glob(f"{data_path}/*.json"))
            and any(glob.glob(f"{data_path}/*.jpg"))
        )

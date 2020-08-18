"""unit test case for frcnn train and evaluate."""

import os
import tempfile
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import torch
import torchvision
from PIL import Image
from tensorboardX import SummaryWriter
from yacs.config import CfgNode as CN

from datasetinsights.datasets.dummy.dummy_object_detection import (
    DummyDetection2D,
)
from datasetinsights.estimators.faster_rcnn import (
    TEST,
    TRAIN,
    VAL,
    FasterRCNN,
    create_dataloader,
    create_dryrun_dataset,
    dataloader_creator,
)
from datasetinsights.io.checkpoint import EstimatorCheckpoint

tmp_dir = tempfile.TemporaryDirectory()
tmp_name = tmp_dir.name


@pytest.fixture
def dataset():
    """prepare dataset."""
    dummy_data = DummyDetection2D(transform=FasterRCNN.get_transform())
    return dummy_data


@pytest.fixture
def config():
    """prepare config."""
    with open("tests/configs/faster_rcnn_groceries_real_test.yaml") as f:
        cfg = CN.load_cfg(f)

    return cfg


def test_faster_rcnn_train_one_epoch(config, dataset):
    """test train one epoch."""
    writer = MagicMock()
    kfp_writer = MagicMock()
    checkpointer = MagicMock()
    estimator = FasterRCNN(
        config=config,
        writer=writer,
        device=torch.device("cpu"),
        checkpointer=checkpointer,
        kfp_writer=kfp_writer,
    )
    train_dataset = dataset
    is_distributed = config.system.distributed
    train_sampler = FasterRCNN.create_sampler(
        is_distributed=is_distributed, dataset=train_dataset, is_train=True
    )
    train_loader = dataloader_creator(
        config, train_dataset, train_sampler, TRAIN
    )
    params = [p for p in estimator.model.parameters() if p.requires_grad]
    optimizer, lr_scheduler = FasterRCNN.create_optimizer_lrs(config, params)
    accumulation_steps = config.train.get("accumulation_steps", 1)
    epoch = 1
    estimator.train_one_epoch(
        optimizer=optimizer,
        data_loader=train_loader,
        epoch=epoch,
        lr_scheduler=lr_scheduler,
        accumulation_steps=accumulation_steps,
    )
    writer.add_scalar.assert_called_with(
        "training/lr", config.optimizer.args.get("lr"), epoch
    )


@patch("datasetinsights.estimators.faster_rcnn.FasterRCNN.train_one_epoch")
@patch("datasetinsights.estimators.faster_rcnn.Loss.compute")
def test_faster_rcnn_train_all(
    mock_loss, mock_train_one_epoch, config, dataset
):
    """test train on all epochs."""
    loss_val = 0.1
    mock_loss.return_value = loss_val
    log_dir = tmp_name + "/train/"
    config.system.logdir = log_dir
    writer = MagicMock()
    kfp_writer = MagicMock()

    checkpointer = EstimatorCheckpoint(
        estimator_name=config.estimator,
        log_dir=log_dir,
        distributed=config.system["distributed"],
    )

    estimator = FasterRCNN(
        config=config,
        writer=writer,
        device=torch.device("cpu"),
        checkpointer=checkpointer,
        kfp_writer=kfp_writer,
    )
    checkpointer.save = MagicMock()
    train_dataset = dataset
    val_dataset = dataset
    label_mappings = train_dataset.label_mappings
    is_distributed = config.system.distributed
    train_sampler = FasterRCNN.create_sampler(
        is_distributed=is_distributed, dataset=train_dataset, is_train=True
    )
    val_sampler = FasterRCNN.create_sampler(
        is_distributed=is_distributed, dataset=val_dataset, is_train=False
    )

    train_loader = dataloader_creator(
        config, train_dataset, train_sampler, TRAIN
    )
    val_loader = dataloader_creator(config, val_dataset, val_sampler, VAL)
    epoch = 0
    estimator.train_loop(
        train_dataloader=train_loader,
        label_mappings=label_mappings,
        val_dataloader=val_loader,
        train_sampler=train_sampler,
    )
    writer.add_scalar.assert_called_with("val/loss", loss_val, epoch)
    mock_train_one_epoch.assert_called_once()


@patch("datasetinsights.estimators.faster_rcnn.FasterRCNN.train_loop")
@patch("datasetinsights.estimators.faster_rcnn.Loss.compute")
@patch("datasetinsights.estimators.faster_rcnn.create_dataset")
def test_faster_rcnn_train(
    mock_create, mock_loss, mock_train_loop, config, dataset
):
    """test train."""
    loss_val = 0.1
    mock_loss.return_value = loss_val
    mock_create.return_value = dataset

    kfp_writer = MagicMock()
    writer = MagicMock
    writer.add_scalar = MagicMock()
    writer.add_scalars = MagicMock()
    writer.add_figure = MagicMock()

    checkpointer = MagicMock()
    estimator = FasterRCNN(
        config=config,
        writer=writer,
        device=torch.device("cpu"),
        checkpointer=checkpointer,
        kfp_writer=kfp_writer,
    )
    estimator.train()
    mock_train_loop.assert_called_once()


@patch("datasetinsights.estimators.faster_rcnn.Loss.compute")
def test_faster_rcnn_evaluate_per_epoch(mock_loss, config, dataset):
    """test evaluate per epoch."""
    loss_val = 0.1
    mock_loss.return_value = loss_val
    ckpt_dir = tmp_name + "/train/FasterRCNN.estimator"
    config.checkpoint_file = ckpt_dir
    writer = MagicMock()
    kfp_writer = MagicMock()
    checkpointer = MagicMock()
    writer.add_scalar = MagicMock()
    estimator = FasterRCNN(
        config=config,
        writer=writer,
        device=torch.device("cpu"),
        checkpointer=checkpointer,
        kfp_writer=kfp_writer,
    )
    test_dataset = dataset
    label_mappings = test_dataset.label_mappings
    is_distributed = config.system.distributed
    test_sampler = FasterRCNN.create_sampler(
        is_distributed=is_distributed, dataset=test_dataset, is_train=False
    )
    test_loader = dataloader_creator(config, test_dataset, test_sampler, TEST)
    sync_metrics = config.get("synchronize_metrics", True)
    epoch = 0
    estimator.evaluate_per_epoch(
        data_loader=test_loader,
        epoch=epoch,
        label_mappings=label_mappings,
        is_distributed=config.system.distributed,
        synchronize_metrics=sync_metrics,
    )
    writer.add_scalar.assert_called_with("val/loss", loss_val, epoch)


@patch("datasetinsights.estimators.faster_rcnn.FasterRCNN.evaluate_per_epoch")
@patch("datasetinsights.estimators.faster_rcnn.Loss.compute")
@patch("datasetinsights.estimators.faster_rcnn.create_dataset")
def test_faster_rcnn_evaluate(
    mock_create, mock_loss, mock_evaluate_per_epoch, config, dataset
):
    """test evaluate."""
    mock_create.return_value = dataset
    loss_val = 0.1
    mock_loss.return_value = loss_val
    ckpt_dir = tmp_name + "/train/FasterRCNN.estimator"
    config.checkpoint_file = ckpt_dir
    writer = MagicMock()
    kfp_writer = MagicMock()
    checkpointer = MagicMock()
    writer.add_scalar = MagicMock()
    estimator = FasterRCNN(
        config=config,
        writer=writer,
        device=torch.device("cpu"),
        checkpointer=checkpointer,
        kfp_writer=kfp_writer,
    )
    estimator.evaluate()
    mock_evaluate_per_epoch.assert_called_once()


def test_faster_rcnn_log_metric_val(config):
    """test log metric val."""
    writer = MagicMock()
    kfp_writer = MagicMock()
    checkpointer = MagicMock()
    writer.add_scalar = MagicMock()
    writer.add_scalars = MagicMock()
    writer.add_figure = MagicMock()
    estimator = FasterRCNN(
        config=config,
        writer=writer,
        device=torch.device("cpu"),
        checkpointer=checkpointer,
        kfp_writer=kfp_writer,
    )
    epoch = 0
    estimator.log_metric_val({"1": "car", "2": "bike"}, epoch)

    writer.add_scalars.assert_called_with("val/APIOU50-per-class", {}, epoch)


def test_faster_rcnn_save(config):
    """test save model."""

    log_dir = tmp_name + "/train/"
    config.system.logdir = log_dir
    kfp_writer = MagicMock()
    writer = MagicMock()
    checkpointer = EstimatorCheckpoint(
        estimator_name=config.estimator,
        log_dir=log_dir,
        distributed=config.system["distributed"],
    )
    estimator = FasterRCNN(
        config=config,
        writer=writer,
        device=torch.device("cpu"),
        checkpointer=checkpointer,
        kfp_writer=kfp_writer,
    )
    estimator.save(log_dir + "FasterRCNN.estimator")

    assert any(
        [
            name.startswith("FasterRCNN.estimator")
            for name in os.listdir(log_dir)
        ]
    )


def test_faster_rcnn_load(config):
    """test load model."""

    ckpt_dir = tmp_name + "/train/FasterRCNN.estimator"
    config.checkpoint_file = ckpt_dir
    log_dir = tmp_name + "/load/"
    config.system.logdir = log_dir
    kfp_writer = MagicMock()
    writer = SummaryWriter(config.system.logdir, write_to_disk=True)
    checkpointer = EstimatorCheckpoint(
        estimator_name=config.estimator,
        log_dir=log_dir,
        distributed=config.system["distributed"],
    )
    estimator = FasterRCNN(
        config=config,
        writer=writer,
        device=torch.device("cpu"),
        checkpointer=checkpointer,
        kfp_writer=kfp_writer,
    )
    estimator.load(ckpt_dir)
    assert os.listdir(log_dir)[0].startswith("events.out.tfevents")


def test_len_dataset(config, dataset):
    """test download data."""
    assert len(dataset.images) == len(dataset)


def test_create_dryrun_dataset(config, dataset):
    """test create dryrun dataset."""
    train_dataset = dataset
    train_dataset = create_dryrun_dataset(config, train_dataset, TRAIN)
    assert config.train.batch_size * 2 == len(train_dataset)


def test_create_sampler(config, dataset):
    """test create sampler."""

    is_distributed = config.system.distributed
    train_sampler = FasterRCNN.create_sampler(
        is_distributed=is_distributed, dataset=dataset, is_train=True
    )
    assert len(dataset.images) == len(train_sampler)


@patch("datasetinsights.estimators.faster_rcnn.torch.utils.data.DataLoader")
def test_dataloader_creator(mock_loader, config, dataset):
    """test create dataloader."""
    mock_loader.return_value = MagicMock()
    is_distributed = config.system.distributed
    train_sampler = FasterRCNN.create_sampler(
        is_distributed=is_distributed, dataset=dataset, is_train=True
    )
    train_loader = dataloader_creator(config, dataset, train_sampler, TRAIN)
    assert isinstance(train_loader, MagicMock)


@patch("datasetinsights.estimators.faster_rcnn.torch.utils.data.DataLoader")
def test_create_dataloader(mock_loader, config, dataset):
    """test load data."""

    mock_loader.return_value = MagicMock()

    is_distributed = config.system.distributed
    train_sampler = FasterRCNN.create_sampler(
        is_distributed=is_distributed, dataset=dataset, is_train=True
    )
    dataloader = create_dataloader(
        config=config,
        dataset=dataset,
        batch_size=config.train.batch_size,
        sampler=train_sampler,
        collate_fn=FasterRCNN.collate_fn,
        train=True,
    )
    assert isinstance(dataloader, MagicMock)


@patch("datasetinsights.estimators.faster_rcnn.torch.optim.Adam")
@patch(
    "datasetinsights.estimators.faster_rcnn.torch.optim.lr_scheduler.LambdaLR"
)
def test_create_optimizer(mock_lr, mock_adm, config, dataset):
    """test create optimizer."""
    mock_lr.return_value = MagicMock()
    mock_adm.return_value = MagicMock()

    writer = MagicMock()
    kfp_writer = MagicMock()
    checkpointer = MagicMock()
    estimator = FasterRCNN(
        config=config,
        writer=writer,
        device=torch.device("cpu"),
        checkpointer=checkpointer,
        kfp_writer=kfp_writer,
    )
    params = [p for p in estimator.model.parameters() if p.requires_grad]
    optimizer, lr_scheduler = FasterRCNN.create_optimizer_lrs(config, params)

    assert isinstance(optimizer, MagicMock)
    assert isinstance(lr_scheduler, MagicMock)


def test_faster_rcnn_predict(config, dataset):
    """test predict."""

    ckpt_dir = tmp_name + "/train/FasterRCNN.estimator"

    config.checkpoint_file = ckpt_dir
    kfp_writer = MagicMock()
    writer = MagicMock()
    checkpointer = EstimatorCheckpoint(
        estimator_name=config.estimator,
        log_dir=config.system.logdir,
        distributed=config.system["distributed"],
    )
    estimator = FasterRCNN(
        config=config,
        writer=writer,
        device=torch.device("cpu"),
        checkpointer=checkpointer,
        kfp_writer=kfp_writer,
    )
    image_size = (256, 256)
    image = Image.fromarray(np.random.random(image_size), "L")
    image = torchvision.transforms.functional.to_tensor(image)
    result = estimator.predict(image)
    assert result == []


def test_clean_dir():
    """clean tmp dir."""
    if os.path.exists(tmp_dir.name):
        tmp_dir.cleanup()
